import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from moviq.core.match_pipeline import match_tv_items
from moviq.core.models import Item
from moviq.core.show_resolver import ShowResolver, ShowState
from moviq.core.tmdb import TMDBError, TMDBOperationCache


class TVFilenameRegressionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("TV Shows")

    def test_numeric_filename_title_wins_over_release_folder(self):
        path = (
            self.root / "Show Folders"
            / "24.S08.1080p.BRRip-AMIABLE"
            / "24.S08E23.576P.EAC3.HDR.AV1-EVO.mkv"
        )

        item = Item(path, self.root)

        self.assertEqual(
            (
                item.media_type, item.query, item.year,
                item.season_number, item.episode_number,
            ),
            ("tv", "24", None, 8, 23),
        )

    def test_release_tagged_folder_supplies_its_clean_title(self):
        path = (
            self.root / "Show Folders"
            / "24.S08.1080p.BRRip-AMIABLE" / "S08E23.mkv"
        )

        item = Item(path, self.root)

        self.assertEqual(
            (item.media_type, item.query, item.season_number,
             item.episode_number),
            ("tv", "24", 8, 23),
        )

    def test_folder_title_is_cleaned_and_keeps_its_year_for_scoring(self):
        path = (
            self.root / "Fringe (2008)" / "Season 02"
            / "S02E04.1080p.BRRip-AMIABLE.mkv"
        )

        item = Item(path, self.root)

        self.assertEqual(
            (
                item.media_type, item.query, item.year,
                item.season_number, item.episode_number,
            ),
            ("tv", "Fringe", 2008, 2, 4),
        )

    def test_generic_folders_and_scan_root_are_not_show_titles(self):
        root = self.root / "Library"
        path = (
            root / "Show Folders" / "Context" / "Season 1"
            / "E04.mkv"
        )

        item = Item(path, root)

        self.assertEqual(item.query, "")
        self.assertFalse(item.is_tv_episode)

    def test_bracketed_title_before_episode_marker_is_used(self):
        paths = (
            self.root / "Bluey" / "[Bluey] - 1x04.mkv",
            self.root / "Show Folders" / "[Bluey] - 1x04.mkv",
        )

        for path in paths:
            with self.subTest(path=path):
                item = Item(path, self.root)
                self.assertEqual(
                    (item.media_type, item.query, item.season_number,
                     item.episode_number),
                    ("tv", "Bluey", 1, 4),
                )

    def test_bare_numbers_and_episode_words_use_folder_season(self):
        cases = (
            ("Season 02", "4.mkv", 2, 4),
            ("Season 03", "15.mkv", 3, 15),
            ("Season 02", "E04.mkv", 2, 4),
            ("Season 03", "Ep04.mkv", 3, 4),
            ("Season 02", "Episode 4.mkv", 2, 4),
            ("Season 03", "Episode.04.mkv", 3, 4),
        )
        for season_folder, filename, season, episode in cases:
            with self.subTest(filename=filename):
                item = Item(
                    self.root / "Bluey" / season_folder / filename,
                    self.root,
                )
                self.assertEqual(
                    (
                        item.media_type, item.query, item.season_number,
                        item.episode_number,
                    ),
                    ("tv", "Bluey", season, episode),
                )


class ShowSearchRegressionTests(unittest.TestCase):
    def make_resolver(self, client, directory):
        return ShowResolver(
            "", "en", Path(directory) / "overrides.json",
            client_factory=lambda *_args: client,
        )

    def test_provider_query_is_clean_and_exact_year_ranks_candidate(self):
        class Client:
            def __init__(self):
                self.calls = []

            def search_tv_strict(self, query, year=None):
                self.calls.append((query, year))
                return [
                    {
                        "id": 1, "name": "Fringe",
                        "first_air_date": "2008-09-09",
                        "popularity": 100,
                    },
                    {
                        "id": 2, "name": "Fringe",
                        "first_air_date": "2013-01-01",
                        "popularity": 1,
                    },
                ]

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            resolver = self.make_resolver(client, directory)
            operation_cache = TMDBOperationCache()

            result = resolver.resolve_show(
                "Fringe (2013)", operation_cache=operation_cache,
            )
            repeated = resolver.resolve_show(
                "Fringe", 2013, operation_cache=operation_cache,
            )
            different_year = resolver.resolve_show(
                "Fringe", 2008, operation_cache=operation_cache,
            )

        self.assertEqual(client.calls, [("Fringe", None)])
        self.assertEqual(result.state, ShowState.RESOLVED)
        self.assertEqual(result.tmdb_id, 2)
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(repeated.tmdb_id, 2)
        self.assertEqual(different_year.tmdb_id, 1)

    def test_exact_tv_title_and_year_auto_accepts_episode(self):
        class SearchClient:
            def search_tv_strict(self, query, year=None):
                self.assert_clean_query(query, year)
                return [{
                    "id": 9, "name": "Fringe",
                    "first_air_date": "2013-01-01",
                }]

            @staticmethod
            def assert_clean_query(query, year):
                if (query, year) != ("Fringe", None):
                    raise AssertionError((query, year))

        class EpisodeClient:
            @staticmethod
            def season(_show_id, season_number):
                return {
                    "episodes": [{
                        "season_number": season_number,
                        "episode_number": 1,
                        "name": "Pilot",
                    }],
                }

        with tempfile.TemporaryDirectory() as directory:
            resolver = self.make_resolver(SearchClient(), directory)
            item = Item(
                Path("Fringe (2013).S01E01.mkv"), Path("TV"), "tv",
            )
            result = match_tv_items(
                EpisodeClient(), resolver, [item], TMDBOperationCache(),
                0.9, lambda *_args: "partial",
            )[0]

        self.assertEqual(result[0], "auto")
        self.assertEqual(result[1]["title"], "Fringe")

    def test_punctuation_and_first_result_alternate_title_are_retried(self):
        class Client:
            def __init__(self):
                self.calls = []

            def search_tv_strict(self, query, year=None):
                self.calls.append((query, year))
                if query == "Long: Show":
                    return [{
                        "id": 1, "name": "Long Show",
                        "original_name": "Alias",
                    }]
                if query == "Alias":
                    return [{
                        "id": 2, "name": "Alias",
                        "original_name": "Long Show",
                        "first_air_date": "2000-01-01",
                    }]
                return []

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            resolver = self.make_resolver(client, directory)
            result = resolver.resolve_show("Long: Show")

        self.assertEqual(
            client.calls,
            [
                ("Long: Show", None),
                ("Long Show", None),
                ("Alias", None),
            ],
        )
        self.assertEqual(result.state, ShowState.RESOLVED)
        self.assertEqual(result.tmdb_id, 1)
        self.assertEqual(result.confidence, 1.0)

    def test_trace_records_query_stages_and_error_completion(self):
        class Client:
            @staticmethod
            def search_tv_strict(_query, _year=None):
                raise TMDBError("Provider unavailable.")

        with tempfile.TemporaryDirectory() as directory:
            resolver = self.make_resolver(Client(), directory)
            item = Item(Path("Fringe.S01E01.mkv"), Path("TV"), "tv")
            with (
                patch("moviq.trace.trace_stage") as stage,
                patch("moviq.trace.trace_end") as end,
            ):
                result = match_tv_items(
                    object(), resolver, [item], TMDBOperationCache(),
                    0.9, lambda *_args: "partial",
                )

        stages = [
            call.args[1] for call in stage.call_args_list
        ]
        self.assertLess(stages.index("show_query"), stages.index("provider_query"))
        self.assertIn("top_candidates", stages)
        end.assert_called_once()
        self.assertEqual(result[0][0], "error")


if __name__ == "__main__":
    unittest.main()
