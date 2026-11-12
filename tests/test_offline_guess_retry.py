import unittest
from contextlib import nullcontext
from queue import Queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from moviq.core.models import Item
from moviq.core.providers.ids import encode
from moviq.ui.app import App


class OfflineGuessRetryTests(unittest.TestCase):
    def make_item(self, status, match, **kwargs):
        return SimpleNamespace(
            status=status,
            media_type="movie",
            match=kwargs.get("current_match"),
            review_match=match,
            match_source=kwargs.get("match_source"),
            error_code=None,
            is_tv_episode=False,
        )

    def test_auto_match_retries_offline_result_without_provider_preflight(self):
        item = self.make_item(
            "auto", {"id": encode("offline", 1), "title": "The Matrix"},
        )

        self.assertTrue(App._auto_match_eligible(
            item, retry_offline=True,
        ))

    def test_auto_match_does_not_retry_offline_result_without_online_source(self):
        item = self.make_item(
            "auto", {"id": encode("offline", 1), "title": "The Matrix"},
        )

        self.assertFalse(App._auto_match_eligible(item))

    def test_offline_retry_does_not_override_manual_or_done_status(self):
        offline = {"id": encode("offline", 1), "title": "The Matrix"}
        manual = self.make_item(
            "auto", offline, match_source="manual",
        )
        done = self.make_item("done", offline)

        self.assertFalse(App._auto_match_eligible(
            manual, retry_offline=True,
        ))
        self.assertFalse(App._auto_match_eligible(
            done, retry_offline=True,
        ))

    def test_current_match_takes_precedence_over_stale_offline_review(self):
        item = self.make_item(
            "auto",
            {"id": encode("offline", 1), "title": "The Matrix"},
            current_match={"id": 603, "title": "The Matrix"},
        )

        self.assertFalse(App._auto_match_eligible(
            item, retry_offline=True,
        ))

    def test_auto_match_includes_offline_rows_even_when_filter_hides_them(self):
        offline = self.make_item(
            "review", {"id": encode("offline", 1), "title": "The Matrix"},
        )
        app = App.__new__(App)
        app._maybe_show_api_key_prompt = lambda: True
        app.selected = Mock(return_value=[])
        app._action_scope_items = Mock(return_value=[])
        app.items = [offline]
        app.start_match = Mock()

        App.match_run(app)

        app.start_match.assert_called_once_with(
            [offline], show_dialog=True,
        )

    def test_offline_review_reason_identifies_guess_without_match_payload(self):
        item = self.make_item("review", None)
        item.review_reason = "offline"

        self.assertTrue(App._is_offline_guess(item))
        self.assertTrue(App._auto_match_eligible(item))

    def test_start_match_queues_offline_result_without_provider_preflight(self):
        item = Item(Path("The Matrix.mkv"), Path("Movies"))
        item.status = "auto"
        item.review_match = {
            "id": encode("offline", 1),
            "title": "The Matrix",
        }
        item.review_reason = "offline"
        jobs = Queue()
        app = App.__new__(App)
        app.provider_settings = SimpleNamespace(selected="automatic")
        app.tmdb = Mock(return_value=SimpleNamespace(
            chain=Mock(return_value=["offline"]),
        ))
        app.key_var = SimpleNamespace(get=lambda: "configured-key")
        app._configure_resolver = Mock()
        app.status = Mock()
        app._clear_review_filter = Mock()
        app._matching_run = None
        app._match_run_id = 0
        app.job_total = app.job_done = 0
        app._bulk_refresh = nullcontext
        app.items = [item]
        app._capture_record = Mock(return_value={})
        app.refresh_row = Mock()
        app.jobs = jobs
        app.language = Mock(return_value="en")
        app.threshold = Mock(return_value=0.9)
        app._update_undo_button = Mock()
        app._update_match_progress = Mock()

        App.start_match(app, [item])

        self.assertEqual(app._matching_run["total"], 1)
        self.assertEqual(item.status, "matching")
        app.tmdb.assert_not_called()
        job = jobs.get_nowait()
        self.assertIs(job[2][0], item)
        self.assertEqual(job[3], "configured-key")

    def test_auto_match_does_not_expand_an_explicit_selection(self):
        selected = self.make_item(
            "pending", None,
        )
        offline = self.make_item(
            "review", {"id": encode("offline", 1), "title": "The Matrix"},
        )
        app = App.__new__(App)
        app._maybe_show_api_key_prompt = lambda: True
        app.selected = Mock(return_value=[selected])
        app._action_scope_items = Mock(return_value=[selected])
        app.items = [selected, offline]
        app.start_match = Mock()

        App.match_run(app)

        app.start_match.assert_called_once_with(
            [selected], show_dialog=True,
        )


if __name__ == "__main__":
    unittest.main()
