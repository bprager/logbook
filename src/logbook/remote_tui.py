"""Curses observation and explicit guarded eject for a traveling Mac's buffer."""
from __future__ import annotations

import curses
import sqlite3
import time
from datetime import datetime


def render_status(status, width, height):
    if width <= 0 or height <= 0:
        return []
    buffered = status.get('buffered', 0)
    received = status.get('received', 0)
    filled = min(20, int(20 * received / buffered)) if buffered else 0
    lines = [
        'LOGBOOK | Remote recording buffer',
        'Updated ' + datetime.now().strftime('%H:%M:%S') + ' | Refresh every 2 seconds',
        '',
        'UPLOAD QUEUE',
        f"Buffered: {buffered}    Received: {received}    Pending: {status.get('pending', 0)}",
        '[' + '#' * filled + '-' * (20 - filled) + f'] {received}/{buffered} receipts',
        f"Waiting: {status.get('pending_bytes', 0) / 1024**2:.1f} MiB    Retries: {status.get('retries', 0)}",
        '',
        'LAST UPLOAD ERROR',
        status.get('failure') or 'None reported',
        '',
        'LAST RECORDER SCAN (historical)',
        status.get('last_scan') or 'No scan result recorded',
        '',
        'Receipt = delivery to mimir, not transcription or publication.',
        'Unplugged recorder: retained uploads can still retry.',
        '',
        'Last success: ' + _activity_text(status.get('last_success')),
        'Last failure: ' + _activity_text(status.get('last_failure')),
    ]
    if status.get('state') == 'not_started':
        lines[4] = 'No recordings buffered yet'
    elif status.get('state') == 'unavailable':
        lines[4:7] = ['Buffer unavailable; retrying automatically.', '', '']
    if status.get('upload'):
        lines[7:8] = _upload_lines(status['upload'])
    if status.get('message'):
        lines[2] = status['message']
    # Reserve the last line for controls, even in a very small terminal.
    return [line[:width] for line in lines[:height - 1] + ['[q] quit | [e] eject | [r] refresh | Esc / Ctrl-C']]


def _upload_lines(upload):
    stage = upload['stage']
    if stage in ('Uploading', 'Waiting for receipt'):
        done, total = upload['done'], upload['total']
        fraction = min(1, max(0, done / total)) if total else 0
        filled = int(20 * fraction)
        return [stage + ' | current recording (measured)',
                '[' + '#' * filled + '-' * (20 - filled) +
                f'] {fraction:.0%} | {done:,}/{total:,} bytes confirmed']
    if upload.get('next_attempt') is not None:
        seconds = max(0, int(upload['next_attempt'] - time.time()))
        stage += f' | next attempt in {seconds}s'
    return [stage, '']


def _activity_text(activity):
    if not activity:
        return 'Not recorded'
    stamp = datetime.fromisoformat(activity['at']).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
    return stamp + ' | ' + activity['activity']


def run_watch(provider, ejector=None):
    def dashboard(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)
        screen.timeout(2000)
        message = None
        while True:
            try:
                status = provider()
            except (OSError, ValueError, sqlite3.Error):
                status = {'state': 'unavailable'}
            status = {**status, 'message': message}
            height, width = screen.getmaxyx()
            screen.erase()
            for row, line in enumerate(render_status(status, max(0, width - 1), height)):
                try:
                    screen.addnstr(row, 0, line, max(0, width - 1))
                except curses.error:
                    pass  # A resize can race drawing; redraw using the next dimensions.
            screen.refresh()
            key = screen.getch()
            if key in (ord('q'), ord('Q'), 27):
                return
            if key in (ord('e'), ord('E')):
                if ejector is None:
                    message = 'Eject unavailable in this view.'
                    continue
                try:
                    screen.addnstr(min(2, max(0, height - 1)), 0,
                                   'Verifying local copies before eject...', max(0, width - 1))
                    screen.refresh()
                except curses.error:
                    pass
                _, message = ejector()
            # Timeout, refresh key and KEY_RESIZE all redraw a fresh snapshot.
    try:
        curses.wrapper(dashboard)
    except KeyboardInterrupt:
        return
    except curses.error as error:
        raise ValueError('Cannot initialize terminal; use --once or --ui compact.') from error
