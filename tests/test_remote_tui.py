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

    def test_eject_key_displays_progress_and_persistent_outcome(self):
        for outcome in [(True, 'Recorder ejected'), (False, 'Eject blocked: busy')]:
            screen = Mock()
            screen.getmaxyx.return_value = (24, 80)
            screen.getch.side_effect = [ord('e'), -1, ord('q')]
            ejector = Mock(return_value=outcome)
            with patch('logbook.remote_tui.curses.wrapper', side_effect=lambda fn: fn(screen)), \
                 patch('logbook.remote_tui.curses.curs_set'):
                run_watch(lambda: {'state': 'not_started'}, ejector=ejector)
            ejector.assert_called_once()
            drawn = str(screen.addnstr.call_args_list)
            self.assertIn('Verifying', drawn)
            self.assertIn(outcome[1], drawn)
            self.assertIn('[e] eject', drawn)

    def test_activity_dates_are_local_and_missing_dates_are_explicit(self):
        text = '\n'.join(render_status({'last_success': {'at':'2026-10-11T19:00:00+00:00',
            'activity':'Upload received'}, 'last_failure': {'at':'2026-10-11T18:00:00+00:00',
            'activity':'Upload: transfer_unavailable'}}, 100, 30))
        self.assertIn('2026-10-11', text)
        self.assertIn('Upload received', text)
        self.assertIn('Last success', text)
        self.assertIn('Last failure', text)
        self.assertIn('Not recorded', '\n'.join(render_status({}, 100, 30)))

    def test_eject_without_callback_and_resize_during_progress(self):
        for ejector in (None, Mock(return_value=(True, 'Ejected'))):
            screen = Mock()
            screen.getmaxyx.return_value = (2, 8)
            screen.getch.side_effect = [ord('e'), ord('q')]
            screen.addnstr.side_effect = curses.error('resize')
            with patch('logbook.remote_tui.curses.wrapper', side_effect=lambda fn: fn(screen)), \
                 patch('logbook.remote_tui.curses.curs_set'):
                run_watch(lambda: {}, ejector=ejector)
            if ejector:
                ejector.assert_called_once()
