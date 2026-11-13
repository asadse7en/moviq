import unittest
from unittest.mock import Mock

from moviq.ui.popups import MatchProgressPopup, RenameProgressPopup


class MatchProgressPopupCompletionTests(unittest.TestCase):
    def test_all_results_show_finishing_and_hide_waiting_badge(self):
        popup = MatchProgressPopup.__new__(MatchProgressPopup)
        popup.completed = False
        popup.task = "matching"
        popup.current_var = Mock()
        popup.progress_count_var = Mock()
        popup.percent_var = Mock()
        popup.counter_vars = []
        popup.source_var = Mock()
        popup.source_label = Mock()
        popup._truncate_current_text = lambda text: text
        popup._set_progress = Mock()
        popup.window = Mock()
        popup.window.winfo_exists.return_value = False

        popup.update_progress("last.mkv", 3, 3, 1, 1, 1)

        popup.current_var.set.assert_called_once_with("Finishing...")
        popup.source_label.grid_remove.assert_called_once_with()

    def test_incomplete_results_show_the_current_matching_file(self):
        popup = MatchProgressPopup.__new__(MatchProgressPopup)
        popup.completed = False
        popup.task = "matching"
        popup.current_var = Mock()
        popup.progress_count_var = Mock()
        popup.percent_var = Mock()
        popup.counter_vars = []
        popup.source_label = Mock()
        popup._truncate_current_text = lambda text: text
        popup._set_progress = Mock()
        popup.window = Mock()
        popup.window.winfo_exists.return_value = False

        popup.update_progress("next.mkv", 1, 3, 1, 0, 0)

        popup.current_var.set.assert_called_once_with(
            "Matching file 2 of 3: next.mkv",
        )
        popup.source_label.grid_remove.assert_not_called()

    def test_progress_update_clamps_completed_count(self):
        popup = MatchProgressPopup.__new__(MatchProgressPopup)
        popup.completed = False
        popup.current_font = Mock(measure=lambda text: len(text) * 10)
        popup.current_var = Mock()
        popup.progress_count_var = Mock()
        popup.percent_var = Mock()
        popup.counter_vars = [Mock(), Mock(), Mock()]
        popup._set_progress = Mock()
        popup.window = Mock()
        popup.task = "matching"

        popup.update_progress("file.mkv", 5, 1, 0, 0, 0)

        popup.progress_count_var.set.assert_called_once_with("1 of 1")
        popup.percent_var.set.assert_called_once_with("100%")
        popup._set_progress.assert_called_once_with(100.0)

    def test_rename_completion_hides_message_below_counters(self):
        popup = MatchProgressPopup.__new__(MatchProgressPopup)
        popup._completion_pending = False
        popup._completion_after = None
        popup.completed = False
        popup._progress_after = None
        popup._progress_value = 0.0
        popup._progress_target = 0.0
        popup.task = "rename"
        popup.started_at = 0
        popup.title_var = Mock()
        popup.subtitle_var = Mock()
        popup.message_var = Mock()
        popup.message_label = Mock()
        popup.percent_var = Mock()
        popup.progress_count_var = Mock()
        popup.counter_vars = [Mock(), Mock(), Mock()]
        popup.current_var = Mock()
        popup.window = Mock()
        popup.backdrop = Mock()
        popup.icon_label = Mock()
        popup._set_progress = Mock()
        popup._draw_progress = Mock()
        popup._set_completed_footer = Mock()
        popup._center = Mock()

        popup._finish_completion(8, 8, 8, 0, 0, cancelled=False)

        popup.message_var.set.assert_called_once_with("")
        popup.message_label.grid_remove.assert_called_once_with()

    def test_matching_completion_keeps_footer_spacing(self):
        popup = MatchProgressPopup.__new__(MatchProgressPopup)
        popup._completion_pending = False
        popup._completion_after = None
        popup.completed = False
        popup._progress_after = None
        popup._progress_value = 0.0
        popup._progress_target = 0.0
        popup.task = "matching"
        popup.started_at = 0
        popup.title_var = Mock()
        popup.subtitle_var = Mock()
        popup.percent_var = Mock()
        popup.progress_count_var = Mock()
        popup.counter_vars = [Mock(), Mock(), Mock()]
        popup.current_var = Mock()
        popup.window = Mock()
        popup.source_label = Mock()
        popup.icon_label = Mock()
        popup.message_label = Mock()
        popup._set_progress = Mock()
        popup._draw_progress = Mock()
        popup._set_completed_footer = Mock()
        popup._center = Mock()
        popup.backdrop = Mock()

        popup._finish_completion(10, 8, 8, 1, 1, cancelled=False)

        popup.message_label.grid_remove.assert_not_called()
        self.assertTrue(popup.completed)
        popup.progress_count_var.set.assert_called_once_with("8 of 8")
        self.assertEqual(popup._progress_value, 1.0)
        self.assertEqual(popup._progress_target, 1.0)
        popup._draw_progress.assert_called_once_with()


class RenameProgressPopupCancellationTests(unittest.TestCase):
    def test_cancel_closes_popup_when_worker_has_not_started(self):
        popup = RenameProgressPopup.__new__(RenameProgressPopup)
        popup.cancel_requested = False
        popup.on_cancel_rename = Mock(return_value=True)
        popup._close = Mock()

        popup.cancel()

        self.assertTrue(popup.cancel_requested)
        popup.on_cancel_rename.assert_called_once_with()
        popup._close.assert_called_once_with()

    def test_cancel_keeps_popup_open_while_worker_stops(self):
        popup = RenameProgressPopup.__new__(RenameProgressPopup)
        popup.cancel_requested = False
        popup.on_cancel_rename = Mock(return_value=False)
        popup.current_var = Mock()
        popup.window = Mock()
        popup.window.winfo_exists.return_value = True
        popup._close = Mock()

        popup.cancel()

        self.assertTrue(popup.cancel_requested)
        popup.current_var.set.assert_called_once_with(
            "Stopping after the current file...",
        )
        popup._close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
