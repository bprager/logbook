import curses
from unittest import TestCase
from unittest.mock import Mock, patch

from logbook.remote_tui import render_status, run_watch


class RemoteTuiTests(TestCase):
    def test_dashboard_reports_receipts_and_upload_failure_without_paths(self):
        status = dict(state='buffered', buffered=28, received=27, pending=1,
                      pending_bytes=3261670, retries=4, failure='transfer_unavailable', last_scan='')
        text = '\n'.join(render_status(status, 80, 24))
        for value in ('28', '27', '1', '3.1 MiB', '4', 'transfer_unavailable', '[q] quit'):
            self.assertIn(value, text)
        self.assertIn('not transcription', text)
        self.assertIn('No scan result', text)

    def test_empty_and_small_terminal_keep_quit_visible(self):
        self.assertIn('No recordings buffered yet', '\n'.join(render_status({'state':'not_started'}, 80, 24)))
        for width, height in [(40, 8), (8, 2), (0, 0)]:
            lines = render_status({'state':'not_started'}, width, height)
            self.assertLessEqual(len(lines), height)
            self.assertTrue(all(len(line) <= width for line in lines))
            if width >= 8:
                self.assertIn('[q] quit', lines[-1])

    def test_loop_refreshes_resizes_and_quits_without_printing(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.getch.side_effect = [-1, curses.KEY_RESIZE, ord('q')]
        provider = Mock(return_value={'state':'not_started'})
        with patch('logbook.remote_tui.curses.wrapper', side_effect=lambda fn: fn(screen)), \
             patch('logbook.remote_tui.curses.curs_set'), patch('builtins.print') as output:
            run_watch(provider)
        self.assertEqual(provider.call_count, 3)
        self.assertEqual(screen.erase.call_count, 3)
        output.assert_not_called()

    def test_escape_interrupt_and_terminal_drawing_errors(self):
        for key in (27, KeyboardInterrupt()):
            screen = Mock()
            screen.getmaxyx.return_value = (2, 8)
            screen.getch.side_effect = [key]
            screen.addnstr.side_effect = curses.error('small terminal')
            with patch('logbook.remote_tui.curses.wrapper', side_effect=lambda fn: fn(screen)), \
                 patch('logbook.remote_tui.curses.curs_set', side_effect=curses.error):
                run_watch(lambda: {'state':'not_started'})

    def test_unavailable_buffer_is_visible_and_retries(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.getch.side_effect = [-1, ord('q')]
        provider = Mock(side_effect=[OSError('/private/path'), {'state':'not_started'}])
        with patch('logbook.remote_tui.curses.wrapper', side_effect=lambda fn: fn(screen)), \
             patch('logbook.remote_tui.curses.curs_set'):
            run_watch(provider)
        drawn = str(screen.addnstr.call_args_list)
        self.assertIn('Buffer unavailable', drawn)
        self.assertNotIn('/private/path', drawn)
        self.assertEqual(provider.call_count, 2)

    def test_terminal_initialization_error_has_actionable_message(self):
        with patch('logbook.remote_tui.curses.wrapper', side_effect=curses.error):
            with self.assertRaisesRegex(ValueError, '--once'):
                run_watch(lambda: {})
