from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient

from logbook.api import create_app
from logbook.config import ApiConfig, AppConfig, OdinConfig, RecorderConfig
from logbook.ledger import open_ledger
from logbook.recorder import discover_recordings


class StatusApiTests(TestCase):
    def test_openapi_and_swagger_ui_are_configured_for_logbook_api(self) -> None:
        with TemporaryDirectory() as tmp:
            app_config = _app_config(Path(tmp))
            _seed_status_fixture(app_config)
            client = TestClient(create_app(app_config))

            openapi = client.get("/openapi.json")
            docs = client.get("/docs")

            self.assertEqual(openapi.status_code, 200)
            schema = openapi.json()
            self.assertEqual(schema["info"]["title"], "Logbook Status API")
            self.assertEqual(schema["info"]["version"], "0.1.0")
            self.assertIn("/jobs/{job_id}", schema["paths"])
            self.assertIn("/logs/open-date", schema["paths"])
            self.assertIn("/logs/consolidated/latest", schema["paths"])
            self.assertIn("HTTPBearer", schema["components"]["securitySchemes"])
            self.assertEqual(docs.status_code, 200)
            self.assertIn("Swagger UI", docs.text)
            self.assertIn("/openapi.json", docs.text)

    def test_read_token_is_required_when_configured(self) -> None:
        with TemporaryDirectory() as tmp:
            app_config = _app_config(Path(tmp), read_token="read-secret")
            _seed_status_fixture(app_config)
            client = TestClient(create_app(app_config))

            unauthorized = client.get("/jobs")
            authorized = client.get(
                "/jobs",
                headers={"Authorization": "Bearer read-secret"},
            )

            self.assertEqual(unauthorized.status_code, 401)
            self.assertEqual(authorized.status_code, 200)

    def test_health_and_jobs_are_read_only_and_path_safe(self) -> None:
        with TemporaryDirectory() as tmp:
            app_config = _app_config(Path(tmp))
            seeded = _seed_status_fixture(app_config)
            client = TestClient(create_app(app_config))

            health = client.get("/health")
            jobs = client.get("/jobs")
            detail = client.get(f"/jobs/{seeded['consolidated_id']}")

            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["counts_by_status"]["consolidated"], 1)
            self.assertEqual(health.json()["counts_by_status"]["dead_letter_written"], 1)
            self.assertEqual(jobs.status_code, 200)
            self.assertEqual(jobs.json()["count"], 3)
            self.assertEqual(detail.status_code, 200)
            detail_payload = detail.json()
            self.assertEqual(detail_payload["status"], "consolidated")
            self.assertEqual(detail_payload["checksum_prefix"], "checksum-log")

            serialized = str(jobs.json()) + str(detail_payload)
            self.assertNotIn("source_path", serialized)
            self.assertNotIn("copied_path", serialized)
            self.assertNotIn("transcript_path", serialized)
            self.assertNotIn(str(app_config.processing_root), serialized)
            self.assertNotIn(str(app_config.recorder.mount_path), serialized)

    def test_log_status_endpoints_return_inbox_latest_and_dead_letters(self) -> None:
        with TemporaryDirectory() as tmp:
            app_config = _app_config(Path(tmp))
            seeded = _seed_status_fixture(app_config)
            client = TestClient(create_app(app_config))

            inbox = client.get("/logs/inbox")
            open_date = client.get("/logs/open-date")
            latest = client.get("/logs/consolidated/latest")
            dead_letters = client.get("/dead-letters")

            self.assertEqual(inbox.status_code, 200)
            self.assertEqual(inbox.json()["count"], 1)
            self.assertEqual(inbox.json()["items"][0]["job_id"], seeded["inbox_id"])
            self.assertEqual(open_date.status_code, 200)
            self.assertEqual(open_date.json()["date"], "2026-04-29")
            self.assertEqual(open_date.json()["entry_count"], 1)
            self.assertEqual(open_date.json()["job_ids"], [seeded["inbox_id"]])
            self.assertEqual(latest.status_code, 200)
            self.assertEqual(
                latest.json()["daily_log_path"],
                "06 - Timestamps/2026/04-April/2026-04-29-Wednesday-Log.md",
            )
            self.assertEqual(latest.json()["entry_count"], 1)
            self.assertEqual(latest.json()["job_ids"], [seeded["consolidated_id"]])
            self.assertEqual(dead_letters.status_code, 200)
            self.assertEqual(dead_letters.json()["count"], 1)
            self.assertEqual(dead_letters.json()["items"][0]["job_id"], seeded["dead_letter_id"])
            self.assertEqual(dead_letters.json()["items"][0]["review_status"], "needs_review")
            self.assertEqual(dead_letters.json()["items"][0]["delete_after"], "2026-05-27")


def _seed_status_fixture(config: AppConfig) -> dict[str, int]:
    recordings_dir = config.recorder.recordings_dir
    _write_recording(recordings_dir / "260429_0821.mp3", 8, 21)
    _write_recording(recordings_dir / "260429_0900.mp3", 9, 0)
    _write_recording(recordings_dir / "260429_1000.mp3", 10, 0)
    candidates = {
        candidate.filename: candidate
        for candidate in discover_recordings(recordings_dir)
    }
    transcript_dir = config.processing_root / "transcripts"
    transcript_dir.mkdir(parents=True)

    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        log_job = ledger.record_discovery(
            candidates["260429_0821.mp3"],
            "checksum-log",
            "IC RECORDER",
        )
        ledger.mark_copied(
            log_job.checksum_sha256,
            config.processing_root / "inbox" / log_job.source_filename,
        )
        ledger.mark_transcribed(
            log_job.checksum_sha256,
            "odin-log",
            transcript_dir / "job-1.json",
            "fake-large-v3",
        )
        ledger.mark_routed(
            log_job.checksum_sha256,
            "log",
            Path("10 - Logs/00 - Inbox/2026/04-April/2026-04-29/log.md"),
            "inbox_written",
        )
        consolidated = ledger.mark_consolidated(
            log_job.checksum_sha256,
            Path("06 - Timestamps/2026/04-April/2026-04-29-Wednesday-Log.md"),
        )

        inbox_job = ledger.record_discovery(
            candidates["260429_0900.mp3"],
            "checksum-inbox",
            "IC RECORDER",
        )
        ledger.mark_copied(
            inbox_job.checksum_sha256,
            config.processing_root / "inbox" / inbox_job.source_filename,
        )
        ledger.mark_transcribed(
            inbox_job.checksum_sha256,
            "odin-inbox",
            transcript_dir / "job-2.json",
            "fake-large-v3",
        )
        inbox = ledger.mark_routed(
            inbox_job.checksum_sha256,
            "log",
            Path("10 - Logs/00 - Inbox/2026/04-April/2026-04-29/inbox.md"),
            "inbox_written",
        )

        dead_job = ledger.record_discovery(
            candidates["260429_1000.mp3"],
            "checksum-dead",
            "IC RECORDER",
        )
        ledger.mark_copied(
            dead_job.checksum_sha256,
            config.processing_root / "inbox" / dead_job.source_filename,
        )
        ledger.mark_transcribed(
            dead_job.checksum_sha256,
            "odin-dead",
            transcript_dir / "job-3.json",
            "fake-large-v3",
        )
        dead_letter = ledger.mark_routed(
            dead_job.checksum_sha256,
            "dead_letter",
            Path("99 - Dead Letters/2026-04-29T10-00-00-job-000003.md"),
            "dead_letter_written",
        )
    finally:
        ledger.close()

    return {
        "consolidated_id": consolidated.id,
        "inbox_id": inbox.id,
        "dead_letter_id": dead_letter.id,
    }


def _app_config(root: Path, read_token: str | None = None) -> AppConfig:
    mount = root / "IC RECORDER"
    recordings_dir = mount / "REC_FILE" / "FOLDER01"
    recordings_dir.mkdir(parents=True)
    return AppConfig(
        processing_root=root / "VoiceIngest",
        sqlite_path=root / "VoiceIngest" / "voice_ingest.sqlite",
        recorder=RecorderConfig(
            volume_name="IC RECORDER",
            mount_path=mount,
            recordings_path="/REC_FILE/FOLDER01",
        ),
        odin=OdinConfig(
            api_base_url="http://odin.test",
            api_token=None,
            asr_model="large-v3",
            asr_device="cuda",
            asr_compute_type="float16",
            asr_vad_filter=True,
            diarization_model="pyannote/speaker-diarization-3.1",
        ),
        api=ApiConfig(
            bind_host="127.0.0.1",
            port=8787,
            read_token=read_token,
        ),
    )


def _write_recording(path: Path, hour: int, minute: int) -> None:
    path.write_bytes(f"fake mp3 bytes {path.name}".encode("utf-8"))
    timestamp = datetime(2026, 4, 29, hour, minute, 54).timestamp()
    os.utime(path, (timestamp, timestamp))
