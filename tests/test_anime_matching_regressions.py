import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from moviq.core.models import Item
from moviq.core.match_pipeline import match_movie_items, match_tv_items
from moviq.core.naming import parse_name, parse_tv_name
from moviq.core.providers.ids import encode
from moviq.core.show_resolver import ShowResolver, ShowResult, ShowState


class AnimeParserRegressionTests(unittest.TestCase):
    def test_numeric_season_folder_and_specials(self):
        root = Path("Shows")
        seasonless = Item(root / "Bluey" / "Seasons" / "4.mkv", root, "tv")
        special = Item(root / "Bluey" / "Specials" / "Episode 0.mkv", root, "tv")

        self.assertEqual(seasonless.query, "Bluey")
        self.assertEqual(
            (seasonless.season_number, seasonless.episode_number), (1, 4),
        )
        self.assertEqual((special.kind, special.season_number), ("special", 0))
        self.assertIsNone(special.episode_number)

    def test_title_season_hint_applies_to_numbered_files_in_folder(self):
        root = Path("Shows")
        item = Item(root / "Bluey Season 2" / "12.mkv", root)

        self.assertEqual(item.query, "Bluey")
        self.assertEqual(
            (item.season_hint, item.season_number, item.episode_number),
            (2, 2, 12),
        )

    def test_title_season_hints_are_removed_from_search_title(self):
        cases = (
            ("Bluey 2nd Season - 12.mkv", "Bluey", 2),
            ("Bluey Season 3 - 12.mkv", "Bluey", 3),
            ("Bluey S4 - 12.mkv", "Bluey", 4),
            ("Bluey IV - 12.mkv", "Bluey", 4),
            ("Bluey Part 2 - 12.mkv", "Bluey", 2),
            ("Bluey Cour 3 - 12.mkv", "Bluey", 3),
            ("Bluey Final Season - 12.mkv", "Bluey", "final"),
        )
        for filename, title, hint in cases:
            with self.subTest(filename=filename):
                item = Item(Path(filename), Path("Shows"), "tv")
                self.assertEqual(item.query, title)
                self.assertEqual(item.season_hint, hint)
                self.assertEqual(item.episode_number, 12)

    def test_anime_episode_markers_and_release_tags(self):
        cases = (
            ("Bluey - 12v2 (WEB 1080p) [A1B2].mkv", 12),
            ("Bluey - 13 END.mkv", 13),
            ("Bluey - 05 FINAL.mkv", 5),
            ("Bluey - 1000話.mkv", 1000),
            ("Bluey - #1100.mkv", 1100),
            ("Bluey - 第12集.mkv", 12),
            ("Bluey - 第13话.mkv", 13),
        )
        for filename, episode in cases:
            with self.subTest(filename=filename):
                item = Item(Path(filename), Path("Shows"), "tv")
                self.assertEqual(item.query, "Bluey")
                self.assertEqual(item.episode_number, episode)

    def test_numeric_title_prefix_and_decimal_dots_are_preserved(self):
        self.assertEqual(
            parse_tv_name(Path("86 - Eighty Six - 01.mkv"))[0],
            "86 Eighty Six",
        )
        self.assertEqual(parse_name("3.0+1.0"), ("3.0+1.0", None))

    def test_extras_are_classified_and_not_given_episode_zero(self):
        cases = (
            ("Bluey - NCOP1.mkv", "extra"),
            ("Bluey - ED2.mkv", "extra"),
            ("Bluey - PV.mkv", "extra"),
            ("Bluey - Interview.mkv", "extra"),
            ("Bluey - NCOP1 (WEB 1080p).mkv", "extra"),
            ("Bluey - Web Preview.mkv", "extra"),
            ("Bluey - Picture Drama.mkv", "extra"),
            ("Bluey - OVA 2.mkv", "special"),
            ("Bluey - Special 1.mkv", "special"),
            ("Bluey - Episode 0.mkv", "special"),
            ("Bluey - Ep00.mkv", "special"),
        )
        for filename, kind in cases:
            with self.subTest(filename=filename):
                item = Item(Path(filename), Path("Shows"), "tv")
                self.assertEqual(item.kind, kind)
                self.assertEqual(item.season_number, 0)
                self.assertIsNone(item.episode_number)
                self.assertEqual(item.query, "Bluey")

    def test_movie_year_and_movies_folder_are_not_tv_episodes(self):
        year_named = Item(Path("Akira (1988).mkv"), Path("Shows"), "tv")
        folder_named = Item(
            Path("Movies") / "Akira.mkv", Path("Movies"), "tv",
        )

        self.assertFalse(year_named.is_tv_episode)
        self.assertEqual(year_named.media_type, "movie")
        self.assertFalse(folder_named.is_tv_episode)
        self.assertEqual(folder_named.media_type, "movie")

    def test_year_only_movie_does_not_become_a_season_or_episode(self):
        item = Item(Path("Akira.1988.mkv"), Path("Shows"), "tv")

        self.assertEqual(item.media_type, "movie")
        self.assertEqual(item.year, 1988)
        self.assertIsNone(item.season_number)
        self.assertIsNone(item.episode_number)


class AnimeShowMatchingRegressionTests(unittest.TestCase):
    def make_resolver(self, client, directory):
        return ShowResolver(
            "", "en", Path(directory) / "overrides.json",
            client_factory=lambda *_args: client,
        )

    def test_rejects_candidates_without_shared_title_tokens(self):
        class Client:
            @staticmethod
            def search_tv_strict(_query, _year=None):
                return [{
                    "id": 1, "name": "Frieden",
                    "first_air_date": "2023-01-01",
                }]

        with tempfile.TemporaryDirectory() as directory:
            resolver = self.make_resolver(Client(), directory)
            result = resolver.resolve_show("Frieren")

        self.assertEqual(result.state, ShowState.NOT_FOUND)

    def test_unhinted_anime_prefers_base_entry(self):
        class Client:
            @staticmethod
            def search_tv_strict(_query, _year=None):
                return [
                    {
                        "id": 2, "name": "Show Season 2",
                        "first_air_date": "2021-01-01",
                        "format": "TV", "popularity": 100,
                        "provider": "anilist",
                    },
                    {
                        "id": 1, "name": "Show",
                        "first_air_date": "2020-01-01",
                        "format": "TV", "popularity": 1,
                        "provider": "anilist",
                    },
                ]

        with tempfile.TemporaryDirectory() as directory:
            resolver = self.make_resolver(Client(), directory)
            result = resolver.resolve_show("Show")

        self.assertEqual(result.state, ShowState.RESOLVED)
        self.assertEqual(result.tmdb_id, 1)

    def test_season_hint_selects_anilist_season_entry(self):
        class Client:
            @staticmethod
            def search_tv_strict(_query, _year=None):
                return [
                    {
                        "id": 1, "name": "Show",
                        "first_air_date": "2020-01-01",
                        "format": "TV", "provider": "anilist",
                    },
                    {
                        "id": 2, "name": "Show Season 2",
                        "first_air_date": "2021-01-01",
                        "format": "TV", "provider": "anilist",
                    },
                ]

        with tempfile.TemporaryDirectory() as directory:
            resolver = self.make_resolver(Client(), directory)
            result = resolver.resolve_show("Show", season_hint=2)

        self.assertEqual(result.state, ShowState.RESOLVED)
        self.assertEqual(result.tmdb_id, 2)

    def test_numeric_suffix_selects_sequel_and_does_not_become_base(self):
        class Client:
            @staticmethod
            def search_tv_strict(_query, _year=None):
                return [
                    {
                        "id": 1, "name": "Show",
                        "first_air_date": "2020-01-01",
                        "provider": "anilist",
                    },
                    {
                        "id": 2, "name": "Show 2",
                        "first_air_date": "2021-01-01",
                        "provider": "anilist",
                    },
                ]

        with tempfile.TemporaryDirectory() as directory:
            resolver = self.make_resolver(Client(), directory)
            base = resolver.resolve_show("Show")
            sequel = resolver.resolve_show("Show", season_hint=2)

        self.assertEqual(base.tmdb_id, 1)
        self.assertEqual(sequel.tmdb_id, 2)

    def test_native_title_can_match_a_translated_candidate(self):
        class Client:
            @staticmethod
            def search_tv_strict(_query, _year=None):
                return [{
                    "id": 3, "name": "Jujutsu Kaisen",
                    "original_name": "Jujutsu Kaisen",
                    "native_name": "呪術廻戦",
                    "first_air_date": "2020-01-01",
                    "provider": "anilist",
                    "format": "TV",
                }]

        with tempfile.TemporaryDirectory() as directory:
            resolver = self.make_resolver(Client(), directory)
            result = resolver.resolve_show("呪術廻戦")

        self.assertEqual(result.state, ShowState.RESOLVED)
        self.assertEqual(result.tmdb_id, 3)

    def test_same_title_candidates_are_disambiguated_by_episode_availability(self):
        class Client:
            @staticmethod
            def search_tv_strict(_query, _year=None):
                return [
                    {
                        "id": 1, "name": "Show",
                        "first_air_date": "2000-01-01",
                    },
                    {
                        "id": 2, "name": "Show",
                        "first_air_date": "2010-01-01",
                    },
                ]

            @staticmethod
            def season(show_id, season_number):
                return {
                    "episodes": [{
                        "season_number": season_number,
                        "episode_number": 12,
                    }] if show_id == 2 else [],
                }

        with tempfile.TemporaryDirectory() as directory:
            resolver = self.make_resolver(Client(), directory)
            result = resolver.resolve_show(
                "Show", episode_number=12, season_number=1,
            )

        self.assertEqual(result.tmdb_id, 2)
        self.assertFalse(result.ambiguous)


class AnimePipelineRegressionTests(unittest.TestCase):
    def test_extras_are_skipped_without_a_show_search(self):
        item = Item(
            Path("Bluey - NCOP1.mkv"), Path("Shows"), "tv",
        )
        resolver = Mock()

        result = match_tv_items(
            object(), resolver, [item], None, 0.9,
            lambda *_args: "partial",
        )

        self.assertEqual(result[0][0], "nomatch")
        resolver.resolve_show.assert_not_called()

    def test_movie_matcher_skips_extras(self):
        item = Item(
            Path("Bluey - NCOP1 (WEB 1080p).mkv"),
            Path("Movies"), "movie",
        )
        client = Mock()

        result = match_movie_items(
            client, [item], 0.9, "Bluey",
        )

        self.assertEqual(result[0][0], "nomatch")
        client.search.assert_not_called()

    def test_anilist_season_entries_use_relative_episode_numbers(self):
        class SearchClient:
            @staticmethod
            def search_tv_strict(_query, _year=None):
                return [
                    {
                        "id": encode("anilist", 1),
                        "name": "Bluey",
                        "first_air_date": "2018-01-01",
                        "provider": "anilist",
                        "format": "TV",
                    },
                    {
                        "id": encode("anilist", 2),
                        "name": "Bluey Season 2",
                        "first_air_date": "2020-01-01",
                        "provider": "anilist",
                        "format": "TV",
                    },
                ]

        class EpisodeClient:
            @staticmethod
            def tv(_show_id):
                return {"seasons": [{"season_number": 1, "episode_count": 12}]}

            @staticmethod
            def season(_show_id, season_number):
                if season_number != 1:
                    raise AssertionError(season_number)
                return {"episodes": [{
                    "season_number": 1,
                    "episode_number": episode_number,
                    "name": f"Episode {episode_number}",
                } for episode_number in range(1, 13)]}

        with tempfile.TemporaryDirectory() as directory:
            resolver = ShowResolver(
                "", "en", Path(directory) / "overrides.json",
                client_factory=lambda *_args: SearchClient(),
            )
            item = Item(
                Path("Bluey Season 2 - 12.mkv"), Path("Shows"), "tv",
            )
            result = match_tv_items(
                EpisodeClient(), resolver, [item], None, 0.9,
                lambda *_args: "partial",
            )[0]

        self.assertEqual(result[0], "auto")
        self.assertEqual(result[1]["season_number"], 2)
        self.assertEqual(result[1]["episode_number"], 12)
        self.assertEqual(result[1]["episode_title"], "Episode 12")

    def test_anilist_missing_episode_and_mismatched_season_need_review(self):
        class SearchClient:
            @staticmethod
            def search_tv_strict(_query, _year=None):
                return [{
                    "id": encode("anilist", 1),
                    "name": "Bluey",
                    "first_air_date": "2018-01-01",
                    "provider": "anilist",
                    "format": "TV",
                }]

        class EpisodeClient:
            @staticmethod
            def tv(_show_id):
                return {"seasons": [{"season_number": 1, "episode_count": 12}]}

            @staticmethod
            def season(_show_id, season_number):
                return {"episodes": [{
                    "season_number": season_number,
                    "episode_number": episode_number,
                    "name": f"Episode {episode_number}",
                } for episode_number in range(1, 13)]}

        with tempfile.TemporaryDirectory() as directory:
            resolver = ShowResolver(
                "", "en", Path(directory) / "overrides.json",
                client_factory=lambda *_args: SearchClient(),
            )
            item = Item(
                Path("Bluey Season 2 - 13.mkv"), Path("Shows"), "tv",
            )
            result = match_tv_items(
                EpisodeClient(), resolver, [item], None, 0.9,
                lambda *_args: "partial",
            )[0]

        self.assertEqual(result[0], "review")
        self.assertEqual(result[1]["season_number"], 2)
        self.assertEqual(result[1]["episode_number"], 13)

    def test_absolute_episode_mapping_uses_sequential_season_index(self):
        item = Item(Path("Bluey - 03.mkv"), Path("Shows"), "tv")
        resolver = Mock(resolve_show=Mock(
            return_value=ShowResult(
                ShowState.RESOLVED, 3, "Bluey", 2018, 1.0,
                candidate={"id": 3, "name": "Bluey"},
            ),
        ))

        class EpisodeClient:
            @staticmethod
            def tv(_show_id):
                return {"seasons": [
                    {"season_number": 1, "episode_count": 2},
                    {"season_number": 5, "episode_count": 2},
                ]}

            @staticmethod
            def season(_show_id, season_number):
                return {"episodes": [{
                    "season_number": season_number,
                    "episode_number": number,
                    "name": f"Episode {number}",
                } for number in (1, 2)]}

            @staticmethod
            def absolute_episode(_show_id, _number):
                raise AssertionError("Cumulative episode mapping should win.")

        result = match_tv_items(
            EpisodeClient(), resolver, [item], None, 0.9,
            lambda *_args: "partial",
        )[0]

        self.assertEqual(result[0], "auto")
        self.assertEqual(result[1]["season_number"], 2)
        self.assertEqual(result[1]["episode_number"], 1)


if __name__ == "__main__":
    unittest.main()
