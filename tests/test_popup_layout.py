import unittest
from unittest.mock import Mock

from moviq.ui.popups import MatchProgressPopup


class MatchProgressPopupCompletionTests(unittest.TestCase):
    def test_rename_completion_hides_message_below_counters(self):
        popup = MatchProgressPopup.__new__(MatchProgressPopup)
        popup._completion_pending = False
        popup._completion_after = None
        popup.completed = False
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
        popup._set_completed_footer = Mock()
        popup._center = Mock()
        popup.backdrop = Mock()

        popup._finish_completion(10, 10, 8, 1, 1, cancelled=False)

        popup.message_label.grid_remove.assert_not_called()
        self.assertTrue(popup.completed)


if __name__ == "__main__":
    unittest.main()
