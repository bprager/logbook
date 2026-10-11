"""Read-only curses dashboard for a traveling Mac's recording buffer."""
from __future__ import annotations

import curses
import sqlite3
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
    ]
    if status.get('state') == 'not_started':
        lines[4] = 'No recordings buffered yet'
    elif status.get('state') == 'unavailable':
        lines[4:7] = ['Buffer unavailable; retrying automatically.', '', '']
    # Reserve the last line for controls, even in a very small terminal.
    return [line[:width] for line in lines[:height - 1] + ['[q] quit | Esc / Ctrl-C | [r] refresh']]


def run_watch(provider):
    def dashboard(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)
        screen.timeout(2000)
        while True:
            try:
                status = provider()
            except (OSError, ValueError, sqlite3.Error):
                status = {'state': 'unavailable'}
            height, width = screen.getmaxyx()
            screen.erase()
            for row, line in enumerate(render_status(status, max(0, width - 1), height)):
                try:
                    screen.addnstr(row, 0, line, max(0, width - 1))
                except curses.error:
                    pass  # A resize can race drawing; redraw using the next dimensions.
            screen.refresh()
            if screen.getch() in (ord('q'), ord('Q'), 27):
                return
            # Timeout, refresh key and KEY_RESIZE all redraw a fresh snapshot.
    try:
        curses.wrapper(dashboard)
    except KeyboardInterrupt:
        return
    except curses.error as error:
        raise ValueError('Cannot initialize terminal; use --once or --ui compact.') from error
