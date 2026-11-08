import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from moviq.core.match_pipeline import match_tv_items
from moviq.core.matching import is_confident_tv, rank_tv
from moviq.core.models import Item
from moviq.core.providers.base import Provider
from moviq.core.providers.tvmaze import TVmazeProvider
from moviq.core.show_resolver import ShowResult, ShowState, ShowResolver
from moviq.core.tmdb import TMDBOperationCache
from moviq.core.naming import parse_tv_air_date, parse_tv_name
from moviq.diagnostics import _report_item


class TVDateParserTests(unittest.TestCase):
    def test_extracts_supported_air_dates_without_using_date_year(self):
        cases = {
            "The.Tonight.Show.Starring.Jimmy.Fallon.2024-03-15.-.Guest.Name.720p.HDTV.x264.mkv":
                "2024-03-15",
            "The.Tonight.Show.Starring.Jimmy.Fallon.2024.03.15.mkv":
                "2024-03-15",
            "The.Tonight.Show.Starring.Jimmy.Fallon.20240315.mkv":
                "2024-03-15",
            "The.Tonight.Show.Starring.Jimmy.Fallon.15.03.2024.mkv":
                "2024-03-15",
            "The.Tonight.Show.Starring.Jimmy.Fallon.15-03-2024.mkv":
                "2024-03-15",
        }
        for filename, air_date in cases.items():
            with self.subTest(filename=filename):
                item = Item(Path(filename), Path("."), "tv")
                self.assertEqual(item.query, "The Tonight Show Starring Jimmy Fallon")
                self.assertIsNone(item.year)
                self.assertEqual(item.air_date, air_date)
                self.assertIsNone(item.season_number)
                self.assertIsNone(item.episode_number)
                self.assertTrue(item.is_tv_episode)
                self.assertEqual(
                    parse_tv_name(Path(filename))[0],
                    "The Tonight Show Starring Jimmy Fallon",
                )

    def test_rejects_invalid_or_out_of_range_dates(self):
        for value in (
            "The.Show.2024-13-15",
            "The.Show.2024-02-31",
            "The.Show.1949-12-31",
            "The.Show.2101-01-01",
        ):
            with self.subTest(value=value):
                self.assertIsNone(parse_tv_air_date(value))

    def test_accepts_separator_variants_and_compact_season_episode_codes(self):
        for marker in (
            "S01xE01", "S01-E01", "S01.E01", "S01_E01",
            "S01 E01", "s1e01", "S1E1",
        ):
            with self.subTest(marker=marker):
                self.assertEqual(
                    parse_tv_name(Path(f"Fringe.{marker}.mkv")),
                    ("Fringe", None, 1, 1),
                )
        for number, expected in (
            ("101", (1, 1, 101)),
            ("0101", (1, 1, 101)),
            ("1001", (10, 1, 1001)),
        ):
            with self.subTest(number=number):
                item = Item(Path(f"Fringe.{number}.mkv"), Path("."), "tv")
                self.assertEqual(
                    (
                        item.season_number, item.episode_number,
                        item.compact_episode_number,
                    ),
                    expected,
                )
        for suffix in ("720p", "1080p", "2160p"):
            item = Item(Path(f"Fringe.{suffix}.mkv"), Path("."), "tv")
            self.assertIsNone(item.compact_episode_number)
        for year in (1900, 2024, 2100):
            item = Item(
                Path(f"Fringe.{year}.mkv"), Path("."), "tv",
            )
            self.assertIsNone(item.compact_episode_number)
            self.assertIsNone(item.episode_number)


class TVDateAndCompactMatchingTests(unittest.TestCase):
    @staticmethod
    def resolver():
        return Mock(resolve_show=Mock(return_value=ShowResult(
            ShowState.RESOLVED,
            7,
            "Fringe",
            2008,
            1.0,
            candidate={"id": 7, "name": "Fringe"},
        )))

    def test_air_date_fills_the_episode_coordinates_and_title(self):
        item = Item(
            Path("Fringe.2024-03-15.-.Guest.Name.720p.mkv"),
            Path("."), "tv",
        )
        provider = Mock()
        provider.episodes_by_date.return_value = [{
            "season_number": 5,
            "episode_number": 12,
            "name": "Example Episode",
            "air_date": "2024-03-15",
        }]

        result = match_tv_items(
            provider, self.resolver(), [item], TMDBOperationCache(),
            0.9, lambda *_args: "partial",
        )[0]

        self.assertEqual(provider.episodes_by_date.call_args.args, (
            7, "2024-03-15",
        ))
        self.assertEqual(result[0], "auto")
        self.assertEqual(
            (
                result[1]["season_number"], result[1]["episode_number"],
                result[1]["episode_title"],
            ),
            (5, 12, "Example Episode"),
        )
        self.assertEqual(
            (item.season_number, item.episode_number, item.episode_title),
            (5, 12, "Example Episode"),
        )

    def test_tvmaze_uses_episodes_by_date_endpoint(self):
        provider = TVmazeProvider()
        provider._get = Mock(return_value=[{
            "id": 123,
            "season": 5,
            "number": 12,
            "name": "Example Episode",
            "airdate": "2024-03-15",
        }])
        provider.cached = Mock(side_effect=lambda _path, _params, load: load())

        episodes = provider.episodes_by_date(7, "2024-03-15")

        provider._get.assert_called_once_with(
            "/shows/7/episodesbydate", {"date": "2024-03-15"},
        )
        self.assertEqual(
            (episodes[0]["season_number"], episodes[0]["episode_number"]),
            (5, 12),
        )

    def test_other_providers_match_date_from_their_episode_lists(self):
        class EpisodeProvider(Provider):
            @staticmethod
            def tv(_show_id):
                return {"seasons": [{"season_number": 2}]}

            @staticmethod
            def season(_show_id, season_number):
                return {"episodes": [
                    {
                        "season_number": season_number,
                        "episode_number": 1,
                        "air_date": "2024-03-14",
                    },
                    {
                        "season_number": season_number,
                        "episode_number": 2,
                        "air_date": "2024-03-15",
                    },
                ]}

        episodes = EpisodeProvider().episodes_by_date(7, "2024-03-15")

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["episode_number"], 2)

    def test_compact_number_validates_season_then_falls_back_to_absolute(self):
        item = Item(Path("Fringe.102.mkv"), Path("."), "tv")

        class Provider:
            @staticmethod
            def tv(_show_id):
                return {"seasons": [{"season_number": 1}]}

            @staticmethod
            def season(_show_id, season_number):
                return {
                    "episodes": [{
                        "season_number": season_number,
                        "episode_number": 1,
                        "name": "Pilot",
                    }],
                }

            @staticmethod
            def absolute_episode(_show_id, number):
                if number == 102:
                    return {
                        "season_number": 2,
                        "episode_number": 2,
                        "name": "Second Season Episode",
                    }
                return None

        result = match_tv_items(
            Provider(), self.resolver(), [item], TMDBOperationCache(),
            0.9, lambda *_args: "partial",
        )[0]

        self.assertEqual(result[0], "auto")
        self.assertEqual(
            (result[1]["season_number"], result[1]["episode_number"]),
            (2, 2),
        )

    def test_invalid_explicit_episode_stays_review_with_parsed_coordinates(self):
        item = Item(Path("Fringe.S01E100.mkv"), Path("."), "tv")
        provider = Mock()
        provider.tv.return_value = {"seasons": [{"season_number": 1}]}
        provider.season.return_value = {"episodes": [
            {"season_number": 1, "episode_number": 1, "name": "Pilot"},
        ]}

        result = match_tv_items(
            provider, self.resolver(), [item], TMDBOperationCache(),
            0.9, lambda *_args: "partial",
        )[0]

        self.assertEqual(result[0], "review")
        self.assertEqual(result[1]["id"], 7)
        self.assertEqual(
            (result[1]["season_number"], result[1]["episode_number"]),
            (1, 100),
        )
        provider.absolute_episode.assert_not_called()

    def test_exact_title_with_year_mismatch_remains_confident(self):
        ranked = rank_tv([{
            "id": 7,
            "name": "Fringe",
            "first_air_date": "2013-01-01",
        }], "Fringe")

        self.assertTrue(is_confident_tv(ranked, "Fringe", 2008))

    def test_identical_show_queries_are_cached_for_a_scan(self):
        class Client:
            def __init__(self):
                self.calls = []

            def search_tv_strict(self, query, year=None):
                self.calls.append((query, year))
                return [{
                    "id": 7,
                    "name": "Fringe",
                    "first_air_date": "2008-09-09",
                }]

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            resolver = ShowResolver(
                "", "en", Path(directory) / "overrides.json",
                client_factory=lambda *_args: client,
            )
            cache = TMDBOperationCache()
            first = resolver.resolve_show("Fringe", operation_cache=cache)
            second = resolver.resolve_show("Fringe", operation_cache=cache)

        self.assertEqual(client.calls, [("Fringe", None)])
        self.assertEqual(first.tmdb_id, second.tmdb_id)

    def test_report_and_trace_keep_date_parser_data_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Fringe.2024-03-15.mkv"
            path.touch()
            item = Item(path, Path(directory), "tv")
            app = Mock()
            app.provider_settings.selected = "automatic"
            app.provider_settings.keys = ()
            app.key_var.get.return_value = ""
            app.media_mode.get.return_value = "tv"
            app._diagnostic_provider_used = {}

            report = _report_item(
                app, item,
                {
                    "fail_reason": "episode lookup failed",
                    "stages": [
                        {"stage": "show_query", "value": item.query},
                        {
                            "stage": "episode_lookup",
                            "value": {"air_date": item.air_date},
                        },
                    ],
                },
                include_all=False,
            )

        self.assertEqual(report["parsed"]["air_date"], "2024-03-15")
        self.assertEqual(
            [stage["stage"] for stage in report["stages"]],
            ["show_query", "episode_lookup"],
        )

    def test_episode_lookup_failure_keeps_trace_stages(self):
        item = Item(
            Path("Fringe.2024-03-15.mkv"), Path("."), "tv",
        )
        provider = Mock()
        provider.episodes_by_date.side_effect = RuntimeError(
            "Episode service unavailable",
        )
        trace = Mock()

        with (
            patch("moviq.core.match_pipeline.trace", trace),
            self.assertRaisesRegex(
                RuntimeError, "Episode service unavailable",
            ),
        ):
            match_tv_items(
                provider, self.resolver(), [item], TMDBOperationCache(),
                0.9, lambda *_args: "partial",
            )

        stages = [
            call.args[1] for call in trace.trace_stage.call_args_list
        ]
        self.assertIn("show_query", stages)
        self.assertIn("episode_lookup", stages)


if __name__ == "__main__":
    unittest.main()
