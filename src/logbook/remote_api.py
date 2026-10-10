"""Dedicated upload-only API. Never mounted into the OpenClaw action API."""
from __future__ import annotations

import asyncio
import hmac
import json
import time
from hashlib import sha256

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from logbook.ledger import open_ledger, utc_now_iso
from logbook.remote_config import ServerConfig
from logbook.remote_status import FAILURE_CODES
from logbook.remote_store import IngestError, RecordingMetadata, RemoteStore


def create_ingest_app(config: ServerConfig) -> FastAPI:
    config.validate()
    ledger = open_ledger(config.ledger_path, initialize=True)
    ledger.close()
    store = RemoteStore(config.ledger_path, config.root, max_bytes=config.max_bytes,
                        quota_bytes=config.quota_bytes, reserve_bytes=config.reserve_bytes,
                        chunk_bytes=config.chunk_bytes,
                        max_active_uploads=config.max_active_uploads)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def audit(code):
        with store.transaction() as db:
            db.execute('INSERT INTO remote_audit VALUES (?,1,?) ON CONFLICT(code) '
                       'DO UPDATE SET count=count+1,last_seen=excluded.last_seen',
                       (code, utc_now_iso()))

    def authenticate(header):
        token = header.removeprefix('Bearer ') if header.startswith('Bearer ') else ''
        digest = sha256(token.encode()).hexdigest()
        try:
            registry = config.credentials()  # reload on every request: revocation is immediate
        except (ValueError, OSError):
            raise IngestError('credentials_unavailable', 503) from None
        device = next((name for name, expected in registry.items()
                       if hmac.compare_digest(digest, expected)), None)
        if device is None:
            raise IngestError('unauthorized', 401)
        window = int(time.time()) // 60
        with store.transaction() as db:
            db.execute('''INSERT INTO remote_devices (device,last_seen) VALUES (?,?)
                          ON CONFLICT(device) DO UPDATE SET last_seen=excluded.last_seen''',
                       (device, utc_now_iso()))
            db.execute('''UPDATE remote_devices SET requests=requests+1,
                       window_requests=CASE WHEN window=? THEN window_requests+1 ELSE 1 END,
                       window=? WHERE device=?''', (window, window, device))
            count = db.execute('SELECT window_requests FROM remote_devices WHERE device=?',
                               (device,)).fetchone()[0]
        if count > config.requests_per_minute:
            raise IngestError('rate_limited', 429)
        return device

    @app.middleware('http')
    async def protect(request: Request, call_next):
        try:
            request.state.device = await run_in_threadpool(
                authenticate, request.headers.get('authorization', ''))
            return await call_next(request)
        except IngestError as error:
            await run_in_threadpool(audit, error.code)
            return JSONResponse({'error': error.code}, status_code=error.status)

    @app.exception_handler(IngestError)
    async def ingest_error(request, error):
        await run_in_threadpool(audit, error.code)
        return JSONResponse({'error': error.code}, status_code=error.status)

    @app.exception_handler(OSError)
    async def storage_error(request, error):
        await run_in_threadpool(audit, 'storage_unavailable')
        return JSONResponse({'error': 'storage_unavailable'}, status_code=507)

    async def collect_body(request, limit):
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > limit:
                raise IngestError('payload_too_large', 413)
            data.extend(chunk)
        return bytes(data)

    async def body(request, limit):
        try:
            return await asyncio.wait_for(collect_body(request, limit), config.body_timeout_seconds)
        except asyncio.TimeoutError:
            raise IngestError('body_timeout', 408) from None

    async def metadata(request):
        data = await body(request, 4096)
        try:
            meta = RecordingMetadata(**json.loads(data))
            meta.validate(config.max_bytes)
            return meta
        except (TypeError, ValueError, UnicodeError):
            raise IngestError('invalid_metadata') from None

    @app.post('/ingest/recordings/check')
    async def check(request: Request):
        return await run_in_threadpool(store.check, request.state.device, await metadata(request))

    @app.post('/ingest/uploads')
    async def allocate(request: Request):
        return await run_in_threadpool(store.allocate, request.state.device, await metadata(request))

    @app.put('/ingest/uploads/{upload_id}')
    async def append(upload_id: str, request: Request):
        try:
            offset = int(request.headers['x-offset'])
        except (KeyError, ValueError):
            raise IngestError('invalid_offset') from None
        return await run_in_threadpool(store.append, request.state.device, upload_id, offset,
                                       await body(request, config.chunk_bytes),
                                       request.headers.get('x-chunk-sha256', ''))

    @app.post('/ingest/uploads/{upload_id}/complete')
    async def complete(upload_id: str, request: Request):
        await body(request, 0)
        return await run_in_threadpool(store.complete, request.state.device, upload_id)

    @app.post('/ingest/status')
    async def status(request: Request):
        data = await body(request, 4096)
        try:
            payload = json.loads(data)
            if (set(payload) != {'pending_bytes', 'retries', 'failure', 'storage_pressure'} or
                    payload['failure'] not in FAILURE_CODES or
                    type(payload['storage_pressure']) is not bool or
                    any(type(payload[key]) is not int or not 0 <= payload[key] <= 2**53
                        for key in ('pending_bytes', 'retries'))):
                raise ValueError('invalid status')
        except (TypeError, ValueError, UnicodeError):
            raise IngestError('invalid_status') from None
        def save_status():
            with store.transaction() as db:
                db.execute('UPDATE remote_devices SET pending_bytes=?,retries=?,failure=?, '
                           'storage_pressure=? WHERE device=?',
                           (payload['pending_bytes'], payload['retries'], payload['failure'],
                            payload['storage_pressure'], request.state.device))
        await run_in_threadpool(save_status)
        return {'ok': True}

    return app
