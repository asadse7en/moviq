import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from moviq.ui.app import App
from moviq.ui.dialogs import SearchDialog


class ManualErrorMatchingTests(unittest.TestCase):
    def test_manual_match_opens_for_every_status_except_done(self):
        for status in ("auto", "manual", "review", "nomatch", "error"):
            with self.subTest(status=status):
                item = SimpleNamespace(
                    status=status,
                    media_type="tv",
                    is_tv_episode=False,
                    error_code="wrong_mode_movie_in_tv",
                    error="provider unavailable" if status == "error" else "",
                )
                app = App.__new__(App)
                app.selected = lambda: [item]
                app._configure_resolver = Mock()
                app._start_manual_matches = Mock()

                app.fix_selected()

                app._configure_resolver.assert_called_once_with()
                app._start_manual_matches.assert_called_once_with([item])

    def test_done_row_stays_out_of_manual_match(self):
        item = SimpleNamespace(status="done")
        app = App.__new__(App)
        app.selected = lambda: [item]

        with patch("moviq.ui.app.show_info") as show_info:
            app.fix_selected()

        show_info.assert_called_once()

    def test_manual_tv_choice_applies_user_coordinates_and_clears_error(self):
        item = SimpleNamespace(
            path=Path("example.mkv"),
            status="error",
            error="provider unavailable",
            error_code="tmdb_unavailable",
            review_reason=None,
            match=None,
            query="",
            year=None,
            show_title="",
            episode_title=None,
            season_number=None,
            episode_number=None,
            episode_end_number=None,
            episode_numbers=(),
            season_was_explicit=False,
            media_type="movie",
            is_tv_episode=False,
            match_source=None,
        )
        app = App.__new__(App)
        app.refresh_row = Mock()
        app._restore_visible_row_order = Mock()
        dialog = SimpleNamespace(
            selected_result={"id": 123, "name": "Example Show"},
            selected_details={"id": 123, "name": "Example Show"},
            is_tv=True,
            _error_edit=True,
            _selected_tv_coordinates=lambda: (3, 15, None),
            item=item,
            query_var=SimpleNamespace(get=lambda: "Example Show"),
            _split_query=SearchDialog._split_query,
            app=app,
            _close=Mock(),
        )

        SearchDialog.update_match(dialog)

        self.assertEqual(item.status, "manual")
        self.assertEqual(item.error, "")
        self.assertEqual(item.media_type, "tv")
        self.assertEqual(
            (item.season_number, item.episode_number), (3, 15),
        )
        self.assertEqual(item.match["id"], 123)
        self.assertEqual(item.match["season_number"], 3)
        self.assertEqual(item.match["episode_number"], 15)
        self.assertEqual(item.match["episode_title"], "Episode 15")
        app.refresh_row.assert_called_once_with(item)
        dialog._close.assert_called_once_with("matched")


if __name__ == "__main__":
    unittest.main()
