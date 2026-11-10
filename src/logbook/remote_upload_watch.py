"""Background, recorder-independent uploads for the interactive remote watcher."""
from __future__ import annotations

import sqlite3
from threading import Event, Lock, Thread

from logbook.remote_agent import UploadCancelled, run_agent


class WatchUploads:
    def __init__(self, config):
        self.config = config
        self.stop = Event()
        self.lock = Lock()
        self.activity = {'stage': 'Checking retained uploads'}
        self.thread = Thread(target=self.run, name='logbook-watch-upload', daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        # In-flight requests have a bounded HTTP timeout. Do not freeze terminal
        # exit on a broken connection; unacknowledged chunks reconcile next run.
        self.thread.join(timeout=0.2)

    def snapshot(self):
        with self.lock:
            return dict(self.activity)

    def update(self, activity):
        if self.stop.is_set():
            raise UploadCancelled()
        with self.lock:
            self.activity = dict(activity)

    def run(self):
        initial = True
        while not self.stop.is_set():
            try:
                if (self.config.root / 'outbox.sqlite').exists():
                    result = run_agent(self.config, scan=False, force=initial, progress=self.update)
                    if not result.get('busy'):
                        initial = False
                else:
                    self.update({'stage': 'No recordings buffered yet'})
            except UploadCancelled:
                return
            except (OSError, ValueError, sqlite3.Error):
                with self.lock:
                    self.activity = {'stage': 'Upload unavailable; check configuration and storage'}
                initial = False
            self.stop.wait(2)
