import os
import queue
import tempfile
from threading import Event
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from moviq import __version__
from moviq.core.file_ops import (
    is_hidden_path, make_plan, make_tv_plan, perform_plan, scan_dir,
    tv_root_for_folder, undo_ops,
)
from moviq.core.formatting import format_pattern
from moviq.core.matching import (
    group_match_items, is_confident, is_confident_tv, normalize_search_title,
    rank, rank_tv, score, to_choice,
    to_tv_choice, tv_episode_candidates, tv_match_is_applicable,
    match_tv_episode_title,
)
from moviq.core.models import Item
from moviq.core.naming import (
    folder_name_for, parse_name, parse_tv_episode, parse_tv_episode_title,
    tv_episode_name_for,
)
from moviq.core.show_resolver import ShowResult, ShowState
from moviq.core.tmdb import TMDB, TMDBCancelled
from moviq.ui.dialogs import SearchDialog, _match_dialog_sizes
from moviq.ui.table_layout import (
    fit_column_widths, folder_and_file, source_path_display,
    target_name_display, tv_target_name_display,
)


class NamingTests(unittest.TestCase):
    def test_parses_title_and_year_from_release_name(self):
        self.assertEqual(
            parse_name("The.Dark.Knight.2008.1080p.BluRay"),
            ("The Dark Knight", 2008),
        )

    def test_ignores_numbered_list_prefix_when_parsing_movie_titles(self):
        cases = {
            "01 - Inception.2010.1080p.BluRay.x264": (
                "Inception", 2010,
            ),
            "02 - The.Matrix.1999.2160p.WEB-DL": ("The Matrix", 1999),
            "03 - The Batman 2022": ("The Batman", 2022),
            "04 - The Batman 1966": ("The Batman", 1966),
            "05 - Dune 1984": ("Dune", 1984),
            "06 - Dune 2021": ("Dune", 2021),
            "07 - Avengers.Endgame.2019.1080p.WEB-DL": (
                "Avengers Endgame", 2019,
            ),
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(parse_name(filename), expected)

    def test_formats_default_movie_folder_name(self):
        self.assertEqual(
            folder_name_for({"id": 603, "title": "The Matrix", "year": 1999}),
            "The Matrix (1999)",
        )

    def test_parses_common_tv_episode_filename_patterns(self):
        cases = {
            "08 - Breaking.Bad.S01E01.1080p.WEB-DL": (
                "Breaking Bad", None, 1, 1,
            ),
            "14 - The.Office.US.S05E14.720p.WEB-DL": (
                "The Office", None, 5, 14,
            ),
            "Breaking.Bad.S01E01.mkv": ("Breaking Bad", None, 1, 1),
            "Breaking Bad - S01E01.mkv": ("Breaking Bad", None, 1, 1),
            "Breaking.Bad.1x01.mkv": ("Breaking Bad", None, 1, 1),
            "The.Office.US.S02E03.1080p.mkv": (
                "The Office", None, 2, 3,
            ),
            "The Office Season 2 Episode 3.mkv": (
                "The Office", None, 2, 3,
            ),
            "Example.Show.2020.S03E04.mkv": (
                "Example Show", 2020, 3, 4,
            ),
            "One.Piece.S01E1000.1080p.WEB-DL.mkv": (
                "One Piece", None, 1, 1000,
            ),
            "One.Piece.1x1000.mkv": ("One Piece", None, 1, 1000),
            "One.Piece.Season.1.Episode.1000.mkv": (
                "One Piece", None, 1, 1000,
            ),
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(parse_tv_episode(Path(filename).stem), expected)

    def test_parses_tv_episode_title_before_release_metadata(self):
        self.assertEqual(
            parse_tv_episode_title("Echo - S01E01 - Chafa.1080p.WEB-DL"),
            "Chafa",
        )
        self.assertIsNone(parse_tv_episode_title("Echo - S01E01"))

    def test_item_classifies_tv_and_movie_files(self):
        tv_item = Item(
            Path("Breaking.Bad.S01E01.mkv"), Path("Shows"),
        )
        titled_tv_item = Item(
            Path("Echo - S01E01 - Chafa.1080p.WEB-DL.mkv"),
            Path("Shows"), "tv",
        )
        movie_item = Item(Path("The.Matrix.1999.mkv"), Path("Movies"))

        self.assertEqual(
            (tv_item.media_type, tv_item.query, tv_item.season_number,
             tv_item.episode_number),
            ("tv", "Breaking Bad", 1, 1),
        )
        self.assertEqual(titled_tv_item.episode_title, "Chafa")
        long_running_tv_item = Item(
            Path("One.Piece.S01E1000.1080p.WEB-DL.mkv"),
            Path("Shows"),
        )
        self.assertEqual(
            (
                long_running_tv_item.media_type,
                long_running_tv_item.query,
                long_running_tv_item.season_number,
                long_running_tv_item.episode_number,
            ),
            ("tv", "One Piece", 1, 1000),
        )
        contextual_tv_item = Item(
            Path("Shows/One Piece/Season 1/1000.mkv"),
            Path("Shows"),
            "tv",
        )
        self.assertEqual(
            (
                contextual_tv_item.query,
                contextual_tv_item.season_number,
                contextual_tv_item.episode_number,
            ),
            ("One Piece", 1, 1000),
        )
        self.assertEqual(
            (movie_item.media_type, movie_item.query, movie_item.year),
            ("movie", "The Matrix", 1999),
        )
        forced_tv = Item(
            Path("Breaking.Bad.S01E01.mkv"), Path("Shows"), "tv",
        )
        forced_movie = Item(
            Path("Breaking.Bad.S01E01.mkv"), Path("Movies"), "movie",
        )
        self.assertEqual(forced_tv.media_type, "tv")
        self.assertTrue(forced_tv.is_tv_episode)
        self.assertIsNone(forced_tv.episode_title)
        self.assertEqual(forced_movie.media_type, "movie")

    def test_infers_short_tv_episode_names_from_the_show_folder(self):
        show = Path("Library") / "Ms Marvel"
        for filename in ("MM EP1.mkv", "MM1.mkv"):
            with self.subTest(filename=filename):
                item = Item(show / filename, show, "tv")

                self.assertEqual(
                    (
                        item.query, item.season_number, item.episode_number,
                        item.media_type, item.is_tv_episode,
                    ),
                    ("Ms Marvel", 1, 1, "tv", True),
                )

    def test_infers_numbered_episodes_from_nested_show_and_season_folders(self):
        show = Path("Library") / "Secret Invasion"
        item = Item(show / "Season 2" / "New folder" / "3.mkv", show, "tv")

        self.assertEqual(
            (
                item.query, item.season_number, item.episode_number,
                item.is_tv_episode,
            ),
            ("Secret Invasion", 2, 3, True),
        )

    def test_infers_short_episode_names_when_a_file_is_added_directly(self):
        path = Path("Library") / "Secret Invasion" / "New folder" / "3.mkv"
        item = Item(path, path.parent, "tv")

        self.assertEqual(
            (item.query, item.season_number, item.episode_number),
            ("Secret Invasion", 1, 3),
        )

    def test_infers_season_above_a_nested_selected_folder(self):
        path = (
            Path("Library") / "Secret Invasion" / "Season 2"
            / "New folder" / "3.mkv"
        )
        item = Item(path, path.parent, "tv")

        self.assertEqual(
            (item.query, item.season_number, item.episode_number),
            ("Secret Invasion", 2, 3),
        )

    def test_does_not_infer_a_show_from_a_generic_library_folder(self):
        root = Path("Library") / "TV Shows"
        item = Item(root / "3.mkv", root, "tv")

        self.assertFalse(item.is_tv_episode)

    def test_tv_episode_name_requires_complete_metadata(self):
        choice = {
            "id": 1396, "title": "Breaking Bad", "season_number": 1,
            "episode_number": 1, "episode_title": "Pilot",
        }
        self.assertEqual(
            tv_episode_name_for(choice),
            "Breaking Bad - S01E01 - Pilot",
        )
        with self.assertRaises(ValueError):
            tv_episode_name_for({**choice, "episode_title": None})

    def test_tv_episode_name_removes_windows_invalid_characters(self):
        self.assertEqual(
            tv_episode_name_for({
                "id": 1429,
                "title": "Attack on Titan",
                "season_number": 1,
                "episode_number": 1,
                "episode_title": (
                    "To You, in 2000 Years: The Fall of Shiganshina"
                ),
            }),
            (
                "Attack on Titan - S01E01 - "
                "To You, in 2000 Years The Fall of Shiganshina"
            ),
        )


class MatchingTests(unittest.TestCase):
    def test_normalizes_release_metadata_before_search(self):
        self.assertEqual(
            normalize_search_title(
                "The.Matrix.1999.2160p.WEB-DL.x265-GROUP",
            ),
            "The Matrix",
        )
        self.assertEqual(
            normalize_search_title("The Matrix 2010-GROUP"),
            "The Matrix",
        )

    def test_groups_files_by_normalized_title_year_and_media_type(self):
        movies = [
            Item(Path("The.Matrix.1999.1080p.mkv"), Path("Movies")),
            Item(Path("The Matrix (1999) [BluRay].mkv"), Path("Movies")),
            Item(Path("The Matrix 2003.mkv"), Path("Movies")),
        ]

        groups = group_match_items(movies)

        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0][1]), 2)
        self.assertEqual(groups[0][0], ("movie", "the matrix", 1999))

    def test_ranks_title_match_and_checks_year(self):
        results = [
            {
                "id": 2,
                "title": "The Matrix Reloaded",
                "original_title": "The Matrix Reloaded",
                "release_date": "2003-05-15",
                "popularity": 10,
            },
            {
                "id": 1,
                "title": "The Matrix",
                "original_title": "The Matrix",
                "release_date": "1999-03-30",
                "popularity": 5,
            },
        ]
        ranked = rank(results, "The Matrix")

        self.assertEqual(ranked[0]["id"], 1)
        self.assertTrue(is_confident(ranked, "The Matrix", 1999))
        self.assertEqual(to_choice(ranked[0]), {
            "id": 1, "title": "The Matrix", "year": 1999,
            "original_title": "The Matrix",
        })
        self.assertEqual(
            score(to_choice(ranked[0]), "The Matrix"),
            score(ranked[0], "The Matrix"),
        )
        self.assertEqual(
            to_choice({
                "id": 1,
                "title": "The Matrix",
                "release_date": "1999-03-30",
                "poster_path": "/matrix.jpg",
            })["poster_path"],
            "/matrix.jpg",
        )

    def test_ranks_tv_shows_and_checks_first_air_year(self):
        results = [
            {
                "id": 2, "name": "The Office", "first_air_date": "2005-03-24",
                "original_name": "The Office",
                "popularity": 15,
            },
            {
                "id": 1, "name": "The Office", "first_air_date": "2001-07-09",
                "popularity": 2,
            },
        ]
        ranked = rank_tv(results, "The Office")

        self.assertEqual(ranked[0]["id"], 2)
        self.assertTrue(is_confident_tv(ranked, "The Office", 2005))
        choice = to_tv_choice(ranked[0], 1, 1, "Pilot")
        self.assertEqual(choice["title"], "The Office")
        self.assertEqual(choice["year"], 2005)
        self.assertEqual(choice["media_type"], "tv")
        self.assertEqual(choice["original_name"], "The Office")
        self.assertTrue(tv_match_is_applicable("auto", {
            **choice, "season_number": 1, "episode_number": 1,
            "episode_title": "Pilot",
        }))
        self.assertTrue(tv_match_is_applicable("no_change", {
            **choice, "season_number": 1, "episode_number": 1,
            "episode_title": "Pilot",
        }))
        self.assertTrue(tv_match_is_applicable("manual", {
            **choice, "season_number": 1, "episode_number": 1,
            "episode_title": "Pilot",
        }))
        self.assertFalse(tv_match_is_applicable("review", {
            **choice, "season_number": 1, "episode_number": 1,
            "episode_title": "Pilot",
        }))
        self.assertFalse(tv_match_is_applicable("auto", {
            **choice, "season_number": 1, "episode_number": 1,
            "episode_title": None,
        }))

    def test_episode_title_disambiguates_same_named_tv_shows(self):
        results = [
            {
                "id": 1, "name": "Echo", "first_air_date": "2024-01-09",
                "popularity": 20,
            },
            {
                "id": 2, "name": "Echo", "first_air_date": "2014-01-01",
                "popularity": 10,
            },
        ]
        ranked = rank_tv(results, "Echo")
        candidates = tv_episode_candidates(ranked, "Echo", None)
        episodes = {
            1: {
                "season_number": 1, "episode_number": 1,
                "name": "Chafa",
            },
            2: {
                "season_number": 1, "episode_number": 1,
                "name": "Echoes",
            },
        }

        self.assertFalse(is_confident_tv(ranked, "Echo", None))
        self.assertEqual(
            match_tv_episode_title(
                candidates, 1, 1, "Chafa", episodes,
            ),
            (results[0], episodes[1]),
        )
        self.assertIsNone(match_tv_episode_title(
            candidates, 1, 1, "Chafa", {
                **episodes, 2: {
                    "season_number": 1, "episode_number": 1,
                    "name": "Chafa",
                },
            },
        ))


class TvPlanTests(unittest.TestCase):
    def test_duplicate_nested_release_folder_does_not_leak_into_tv_destination(self):
        library = Path("D:/Media/Shows")
        release_folder = (
            "Lanterns.S01.720p.10Bit.WEB-DL.Hindi-English."
            "HEVC.x265-HDHub4u.Ms"
        )
        outer = library / release_folder
        selected = outer / release_folder
        episode = selected / f"{release_folder.replace('.S01.', '.S01E01.')}.mkv"

        root = tv_root_for_folder(selected, [episode])
        plan = make_tv_plan(
            episode, root,
            {
                "id": 123, "title": "Lanterns", "season_number": 1,
                "episode_number": 1, "episode_title": "Pilot",
            },
        )

        self.assertEqual(root, library)
        self.assertEqual(
            plan.dest,
            library / "Lanterns" / "Season 01"
            / "Lanterns - S01E01 - Pilot.mkv",
        )

    def test_selected_single_show_folder_uses_its_parent_as_library_root(self):
        root = Path("D:/Media/Shows")
        selected_show = root / "Dark all 3 seasons"
        files = [
            selected_show / "Downloads" / "Dark - S01E01 - Secrets.mkv",
            selected_show / "Season 2" / "Dark - S02E01 - Secrets.mkv",
        ]

        self.assertEqual(tv_root_for_folder(selected_show, files), root)

    def test_selected_show_with_contextual_episode_names_uses_parent_root(self):
        library = Path("D:/Media/Shows")
        selected_show = library / "Mr marvel"
        files = [
            selected_show / "New folder" / f"MM EP{episode}.mkv"
            for episode in range(1, 7)
        ]

        self.assertEqual(tv_root_for_folder(selected_show, files), library)

    def test_selected_show_alias_with_recognized_episode_names_uses_parent(self):
        library = Path("D:/Media/Shows")
        selected_show = library / "Mr marvel"
        files = [
            selected_show / f"Ms.Marvel.S01E{episode:02d}.mkv"
            for episode in range(1, 3)
        ]

        self.assertEqual(tv_root_for_folder(selected_show, files), library)

    def test_multi_show_folder_remains_the_library_root(self):
        root = Path("D:/Media/Shows")
        files = [
            root / "Dark" / "Season 01" / "Dark - S01E01.mkv",
            root / "The Office" / "Downloads" / "The.Office.S01E01.mkv",
        ]

        self.assertEqual(tv_root_for_folder(root, files), root)

    def test_library_with_one_nested_contextual_show_stays_the_library_root(self):
        library = Path("D:/Media/Dramas")
        files = [
            library / "Secret Invasion" / "New folder" / f"{episode}.mkv"
            for episode in range(1, 4)
        ]

        self.assertEqual(tv_root_for_folder(library, files), library)

    def test_plans_seasons_under_tv_shows_using_episode_metadata(self):
        root = Path("Library")
        show = root / "Breaking Bad Season 1 and 2"
        s1 = show / "Breaking.Bad.S01.720p.WEB-DL"
        s2 = show / "Breaking.Bad.S02.1080p.WEB-DL"
        choices = [
            (s1 / "Breaking.Bad.S01E01.mkv", 1, 1, "Pilot"),
            (s1 / "Breaking.Bad.S01E02.mkv", 1, 2, "Cat's in the Bag..."),
            (s2 / "Breaking.Bad.S02E01.mkv", 2, 1, "Seven Thirty-Seven"),
        ]
        plans = [
            make_tv_plan(
                path, root,
                {
                    "id": 1396, "title": "Breaking Bad",
                    "season_number": season, "episode_number": episode,
                    "episode_title": title,
                },
            )
            for path, season, episode, title in choices
        ]

        self.assertEqual(
            plans[0].dest,
            root / "Breaking Bad" / "Season 01"
            / "Breaking Bad - S01E01 - Pilot.mkv",
        )
        self.assertEqual(plans[1].dest.parent, plans[0].dest.parent)
        self.assertEqual(
            plans[2].dest,
            root / "Breaking Bad" / "Season 02"
            / "Breaking Bad - S02E01 - Seven Thirty-Seven.mkv",
        )
        self.assertNotIn("TV Shows", plans[0].dest.parts)

    def test_plan_uses_existing_tv_shows_root_without_nesting(self):
        library = Path("Library") / "TV Shows"
        choice = {
            "id": 1396, "title": "Breaking Bad",
            "season_number": 1, "episode_number": 1,
            "episode_title": "Pilot",
        }
        plan = make_tv_plan(library / "incoming.mkv", library, choice)

        self.assertEqual(
            plan.dest,
            library / "Breaking Bad" / "Season 01"
            / "Breaking Bad - S01E01 - Pilot.mkv",
        )

    def test_plan_reuses_existing_show_and_season_folder(self):
        season = (
            Path("Library") / "TV Shows" / "Lanterns" / "Season 01"
        )
        source = season / "Lanterns.S01E01.mkv"
        choice = {
            "id": 123, "title": "Lanterns", "season_number": 1,
            "episode_number": 1, "episode_title": "Pilot",
        }

        plan = make_tv_plan(source, Path("Library") / "TV Shows", choice)

        self.assertEqual(plan.dest_dir, season)
        self.assertEqual(
            plan.dest,
            Path("Library") / "TV Shows" / "Lanterns" / "Season 01"
            / "Lanterns - S01E01 - Pilot.mkv",
        )

    def test_correct_episode_path_is_a_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            show = Path(temp) / "Dark"
            source = (
                show / "Season 01" / "Dark - S01E01 - Pilot.mkv"
            )
            source.parent.mkdir(parents=True)
            source.write_bytes(b"episode")
            choice = {
                "id": 123, "title": "Dark", "season_number": 1,
                "episode_number": 1, "episode_title": "Pilot",
            }

            plan = make_tv_plan(source, show, choice)
            operations = []

            self.assertEqual(str(plan.dest), str(source))
            self.assertEqual(perform_plan(source, plan, operations), source)
            self.assertEqual(operations, [])
            self.assertTrue(source.is_file())

    def test_plan_places_episode_under_selected_show_folder(self):
        root = Path("Library")
        show = root / "Dark all 3 seasons"
        source = show / "DARK.S01.720p.WEB-DL" / "Dark.S01E01.mkv"
        choice = {
            "id": 123, "title": "Lanterns", "season_number": 1,
            "episode_number": 1, "episode_title": "Pilot",
        }

        plan = make_tv_plan(source, root, choice)

        self.assertEqual(
            plan.dest,
            root / "Lanterns" / "Season 01"
            / "Lanterns - S01E01 - Pilot.mkv",
        )

    def test_moves_multiple_episodes_across_seasons_and_undoes_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            show = root / "Breaking Bad and seasons"
            episode_data = [
                ("Breaking.Bad.S01.720p/Breaking.Bad.S01E01.mkv", 1, 1, "Pilot"),
                ("Breaking.Bad.S01.720p/Breaking.Bad.S01E02.mkv", 1, 2, "Cat's in the Bag"),
                ("Breaking.Bad.S02.1080p/Breaking.Bad.S02E01.mp4", 2, 1, "Seven Thirty-Seven"),
            ]
            original_sources = [show / entry[0] for entry in episode_data]
            for source in original_sources:
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"episode")
            current_sources = list(original_sources)
            operations = []
            destinations = []
            for index, (_, season, episode, title) in enumerate(episode_data):
                source = current_sources[index]
                choice = {
                    "id": 1396, "title": "Breaking Bad",
                    "season_number": season, "episode_number": episode,
                    "episode_title": title,
                }
                plan = make_tv_plan(source, root, choice)
                destinations.append(perform_plan(source, plan, operations))

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in destinations],
                [
                    "Breaking Bad/Season 01/"
                    "Breaking Bad - S01E01 - Pilot.mkv",
                    "Breaking Bad/Season 01/"
                    "Breaking Bad - S01E02 - Cat's in the Bag.mkv",
                    "Breaking Bad/Season 02/"
                    "Breaking Bad - S02E01 - Seven Thirty-Seven.mp4",
                ],
            )
            self.assertTrue(all(path.is_file() for path in destinations))
            self.assertEqual(undo_ops(operations), [])
            self.assertTrue(all(path.is_file() for path in original_sources))

    def test_moves_context_named_episodes_without_moving_unprocessed_videos(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp)
            selected_show = library / "Mr marvel"
            source_dir = selected_show / "New folder"
            source_paths = [
                source_dir / f"MM EP{episode}.mkv"
                for episode in range(1, 7)
            ]
            source_dir.mkdir(parents=True)
            for source in source_paths:
                source.write_bytes(b"episode")

            root = tv_root_for_folder(selected_show, source_paths)
            self.assertEqual(root, library)
            items = [Item(source, root, "tv") for source in source_paths]
            self.assertEqual(
                [(item.query, item.season_number, item.episode_number)
                 for item in items],
                [("Mr marvel", 1, episode) for episode in range(1, 7)],
            )

            operations = []
            destinations = []
            for episode, item in enumerate(items, start=1):
                self.assertTrue(item.path.is_file())
                choice = {
                    "id": 123,
                    "title": "Ms. Marvel",
                    "season_number": 1,
                    "episode_number": episode,
                    "episode_title": f"Episode {episode}",
                }
                plan = make_tv_plan(item.path, item.root, choice)
                destinations.append(
                    perform_plan(item.path, plan, operations)
                )

            expected_dir = library / "Ms. Marvel" / "Season 01"
            self.assertEqual(
                destinations,
                [
                    expected_dir / f"Ms. Marvel - S01E{episode:02d} - "
                    f"Episode {episode}.mkv"
                    for episode in range(1, 7)
                ],
            )
            self.assertTrue(all(path.is_file() for path in destinations))
            self.assertFalse(selected_show.exists())
            self.assertFalse(
                any(path.name.startswith("MM EP") for path in expected_dir.iterdir())
            )

    def test_renames_selected_show_and_season_folders_in_place(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp)
            selected_show = library / "dark all 3 seasons"
            sources = [
                selected_show / "Season 1" / "Dark.S01E01.mkv",
                selected_show / "Season 2" / "Dark.S02E01.mkv",
            ]
            for source in sources:
                source.parent.mkdir(parents=True)
                source.write_bytes(b"episode")

            episode_data = [
                (1, 1, "The End"),
                (2, 1, "The Beginning"),
            ]
            operations = []
            destinations = []
            for index, (season, episode, title) in enumerate(episode_data):
                choice = {
                    "id": 123, "title": "Dark", "season_number": season,
                    "episode_number": episode, "episode_title": title,
                }
                plan = make_tv_plan(sources[index], library, choice)
                destinations.append(
                    perform_plan(sources[index], plan, operations)
                )

            self.assertEqual(
                [path.relative_to(library).as_posix() for path in destinations],
                [
                    "Dark/Season 01/Dark - S01E01 - The End.mkv",
                    "Dark/Season 02/Dark - S02E01 - The Beginning.mkv",
                ],
            )
            self.assertFalse(selected_show.exists())
            self.assertTrue(all(path.is_file() for path in destinations))
            self.assertEqual(undo_ops(operations), [])
            self.assertTrue(all(path.is_file() for path in sources))
            self.assertTrue(selected_show.is_dir())

    def test_renames_three_show_folders_under_the_selected_library(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp)
            series = [
                ("dark all 3 seasons", "Dark", 1),
                ("the office complete", "The Office", 2),
                ("breaking bad 1080p", "Breaking Bad", 1),
            ]
            operations = []
            targets = []
            for folder, title, season in series:
                selected_show = library / folder
                source = (
                    selected_show / f"{title}.S{season:02d}.720p"
                    / f"{title}.S{season:02d}E01.mkv"
                )
                source.parent.mkdir(parents=True)
                source.write_bytes(b"episode")
                choice = {
                    "id": 123, "title": title, "season_number": season,
                    "episode_number": 1, "episode_title": "Pilot",
                }
                plan = make_tv_plan(source, library, choice)
                targets.append(perform_plan(source, plan, operations))

            self.assertEqual(
                [target.relative_to(library).as_posix() for target in targets],
                [
                    "Dark/Season 01/Dark - S01E01 - Pilot.mkv",
                    "The Office/Season 02/The Office - S02E01 - Pilot.mkv",
                    "Breaking Bad/Season 01/"
                    "Breaking Bad - S01E01 - Pilot.mkv",
                ],
            )
            self.assertFalse((library / "TV Shows").exists())
            self.assertTrue(all(target.is_file() for target in targets))

    def test_normalizes_folder_and_file_case_on_windows(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp)
            show = library / "dark"
            source = show / "Season 1" / "dark - s01e01 - pilot.mkv"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"episode")
            choice = {
                "id": 123, "title": "Dark", "season_number": 1,
                "episode_number": 1, "episode_title": "Pilot",
            }

            plan = make_tv_plan(source, library, choice)
            operations = []
            result = perform_plan(source, plan, operations)

            self.assertEqual(
                str(result),
                str(library / "Dark" / "Season 01"
                    / "Dark - S01E01 - Pilot.mkv"),
            )
            self.assertTrue(result.is_file())


class TmdbTvTests(unittest.TestCase):
    def test_uses_tmdb_tv_search_show_and_episode_endpoints(self):
        client = TMDB("test-key")
        calls = []

        def fake_get(path, **params):
            calls.append((path, params))
            if path == "/search/tv":
                return {"results": [{"id": 1396}]}
            return {"id": 1396}

        client.get = fake_get
        self.assertEqual(client.search_tv("Breaking Bad", 2008), [{"id": 1396}])
        self.assertEqual(client.tv(1396), {"id": 1396})
        self.assertEqual(client.season(1396, 1), {"id": 1396})
        self.assertEqual(client.episode(1396, 1, 2), {"id": 1396})
        self.assertEqual(
            calls,
            [
                ("/search/tv", {
                    "query": "Breaking Bad", "first_air_date_year": 2008,
                    "include_adult": "false",
                }),
                ("/tv/1396", {"append_to_response": "credits"}),
                ("/tv/1396/season/1", {}),
                ("/tv/1396/season/1/episode/2", {}),
            ],
        )

    def test_canceled_request_does_not_start_an_http_call(self):
        cancellation = Event()
        cancellation.set()
        client = TMDB("test-key", cancel_event=cancellation)
        with patch("moviq.core.tmdb._SESSION.get") as request:
            with self.assertRaises(TMDBCancelled):
                client.get("/search/movie", query="Inception")
        request.assert_not_called()

    def test_movie_details_include_director_credits(self):
        client = TMDB("test-key")
        calls = []

        def fake_get(path, **params):
            calls.append((path, params))
            return {"id": 2661, "credits": {"crew": []}}

        client.get = fake_get

        self.assertEqual(
            client.movie(2661),
            {"id": 2661, "credits": {"crew": []}},
        )
        self.assertEqual(
            calls,
            [("/movie/2661", {"append_to_response": "credits"})],
        )


class SearchDialogInputTests(unittest.TestCase):
    def test_match_dialog_opens_large_enough_for_details(self):
        self.assertEqual(
            _match_dialog_sizes(1920, 1080),
            (1100, 600, 1000, 560),
        )

    def test_match_dialog_respects_small_screen_bounds(self):
        self.assertEqual(
            _match_dialog_sizes(1024, 768),
            (984, 600, 984, 560),
        )

    @patch("moviq.ui.dialogs.requests.get")
    def test_poster_download_uses_requests_and_queues_image_data(self, get):
        response = Mock()
        response.content = b"poster image"
        get.return_value = response
        result_queue = Mock()
        dialog = SimpleNamespace(_result_queue=result_queue)

        SearchDialog._load_poster(dialog, 2, 1, "/poster.jpg")

        get.assert_called_once_with(
            "https://image.tmdb.org/t/p/w185/poster.jpg",
            headers={"User-Agent": f"Moviq/{__version__}"},
            timeout=12,
        )
        response.raise_for_status.assert_called_once_with()
        result_queue.put.assert_called_once_with((
            "poster", 2, 1, "/poster.jpg", b"poster image", None,
        ))

    @patch("moviq.ui.dialogs.requests.get")
    def test_poster_http_error_is_queued_for_unavailable_state(self, get):
        response = Mock()
        response.raise_for_status.side_effect = ValueError("HTTP error")
        get.return_value = response
        result_queue = Mock()
        dialog = SimpleNamespace(_result_queue=result_queue)

        SearchDialog._load_poster(dialog, 2, 1, "/poster.jpg")

        result_queue.put.assert_called_once_with((
            "poster", 2, 1, "/poster.jpg", None, "HTTP error",
        ))

    def test_result_widgets_are_not_added_to_result_data(self):
        result = {"id": 27205, "title": "Inception"}
        card, text_column, title = Mock(), Mock(), Mock()
        results_cards = Mock()
        results_cards.winfo_children.return_value = []
        dialog = SimpleNamespace(
            results=[result],
            _result_widgets=[],
            app=SimpleNamespace(pal={"card": "white", "fg": "black"}),
            results_cards=results_cards,
            results_canvas=Mock(),
            _result_title=lambda item: item["title"],
            _bind_results_mousewheel=Mock(),
            _tab_from_results=Mock(),
            _tab_back_from_results=Mock(),
            _select_result=Mock(),
            _double_click_result=Mock(),
            _move_selection=Mock(),
            _confirm_selected=Mock(),
            _fit_results_window=Mock(),
            _update_results_scrollregion=Mock(),
        )
        dialog._clear_result_cards = (
            lambda: SearchDialog._clear_result_cards(dialog)
        )

        with patch(
            "moviq.ui.dialogs.tk.Frame",
            side_effect=[card, text_column],
        ):
            with patch(
                "moviq.ui.dialogs.tk.Label", return_value=title,
            ):
                SearchDialog._populate_result_cards(dialog)

        self.assertEqual(result, {"id": 27205, "title": "Inception"})
        self.assertEqual(dialog._result_widgets, [(card, title)])

    def test_current_match_similarity_uses_the_filename_title(self):
        current_match_var = SimpleNamespace(value="")
        item = Item(
            Path("The.Movie [Directors Cut] 2019.mkv"), Path("Movies"),
        )
        item.match = {
            "title": "My Hero Academia: Heroes Rising",
            "year": 2019,
        }
        dialog = SimpleNamespace(
            item=item,
            is_tv=False,
            current_match_var=SimpleNamespace(
                set=lambda value: setattr(current_match_var, "value", value),
            ),
            file_var=SimpleNamespace(set=lambda value: setattr(
                dialog.file_var, "value", value,
            )),
            _score=lambda result, query: score(result, query),
        )

        SearchDialog._display_current_match(dialog)

        self.assertIn("0%", current_match_var.value)

    def test_match_details_score_against_the_filename_title(self):
        class FakeVar:
            def __init__(self, value=""):
                self.value = value

            def set(self, value):
                self.value = value

            def get(self):
                return self.value

        item = Item(
            Path("The.Movie [Directors Cut] 2019.mkv"), Path("Movies"),
        )
        details_poster = SimpleNamespace(configure=lambda **_kwargs: None)
        dialog = SimpleNamespace(
            item=item,
            is_tv=False,
            query_var=FakeVar("My Hero Academia: Heroes Rising 2019"),
            details_title_var=FakeVar(),
            details_id_var=FakeVar(),
            confidence_var=FakeVar(),
            overview_var=FakeVar(),
            details_poster=details_poster,
            update_button=SimpleNamespace(configure=lambda **_kwargs: None),
            selected_index=None,
            _title=SearchDialog._title,
            _year=lambda _result: None,
            _score=lambda result, query: score(result, query),
            _resize_details_text=lambda: None,
        )

        SearchDialog._show_selected_details(dialog, {
            "id": 1,
            "title": "My Hero Academia: Heroes Rising",
            "year": 2019,
        })

        self.assertEqual(dialog.confidence_var.value, "0% CONFIDENCE")

    def test_search_results_select_without_stealing_search_focus(self):
        class FakeVar:
            def set(self, value):
                self.value = value

        selections = []
        dialog = SimpleNamespace(
            _search_generation=3,
            is_tv=False,
            item=SimpleNamespace(match=None),
            results=[],
            result_count_var=FakeVar(),
            search_status_var=FakeVar(),
            _populate_result_cards=lambda: None,
            _select_result=lambda index, focus=True: selections.append(
                (index, focus),
            ),
            _clear_details=lambda: None,
        )

        SearchDialog._show_search_results(
            dialog,
            3,
            "Inception",
            [{
                "id": 27205,
                "title": "Inception",
                "release_date": "2010-07-16",
            }],
            None,
        )

        self.assertEqual(selections, [(0, False)])

    def test_parses_tmdb_ids_and_rejects_non_id_queries(self):
        cases = {
            "2661": 2661,
            "tmdb:2661": 2661,
            " TMDB: 2661 ": 2661,
            "The Batman 1966": None,
            "tmdb:batman": None,
            "": None,
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(
                    SearchDialog._parse_tmdb_id(query),
                    expected,
                )

    def test_tv_search_can_match_the_current_episode_title(self):
        item = Item(Path("Breaking.Bad.S01E01.mkv"), Path("TV"))

        class FakeTMDB:
            def search_tv(self, query, year=None):
                if query == "Pilot":
                    return []
                return [{
                    "id": 1396,
                    "name": "Breaking Bad",
                    "first_air_date": "2008-01-20",
                }]

            def season(self, show_id, season_number):
                return {
                    "episodes": [{
                        "season_number": season_number,
                        "episode_number": 1,
                        "name": "Pilot",
                    }],
                }

        dialog = SimpleNamespace(
            tmdb=FakeTMDB(),
            app=SimpleNamespace(
                resolve_show=lambda title, _year, _cache: (
                    ShowResult(ShowState.NOT_FOUND)
                    if title == "Pilot"
                    else ShowResult(
                        ShowState.RESOLVED,
                        1396,
                        "Breaking Bad",
                        2008,
                        candidate={
                            "id": 1396,
                            "name": "Breaking Bad",
                            "first_air_date": "2008-01-20",
                        },
                    )
                ),
            ),
            item=item,
            _result_queue=queue.Queue(),
            operation_cache=None,
        )
        dialog._season_episode = lambda show_id: (
            SearchDialog._season_episode(dialog, show_id)
        )
        dialog._search_current_episode_title = (
            lambda query: SearchDialog._search_current_episode_title(
                dialog, query,
            )
        )
        SearchDialog._load_search(dialog, 3, "Pilot", None, True)

        result = dialog._result_queue.get_nowait()
        self.assertEqual(result[0:3], ("search", 3, "Pilot"))
        self.assertEqual(len(result[3]), 1)
        self.assertEqual(result[3][0]["episode_title"], "Pilot")
        self.assertTrue(result[3][0]["_matched_episode_query"])

    def test_tv_show_details_load_independently_from_episode_lookup(self):
        class FakeTMDB:
            def tv(self, show_id):
                return {"id": show_id, "name": "Breaking Bad"}

            def episode(self, *_args):
                raise AssertionError(
                    "Episode lookup must not block show details.",
                )

        class FakeExecutor:
            def __init__(self):
                self.jobs = []

            def submit(self, callback, *args):
                self.jobs.append((callback, args))

        dialog = SimpleNamespace(
            tmdb=FakeTMDB(),
            is_tv=True,
            item=SimpleNamespace(season_number=1, episode_number=2),
            _result_queue=queue.Queue(),
            _executor=FakeExecutor(),
        )
        dialog._load_episode = lambda *args: None

        SearchDialog._load_details(dialog, 4, {"id": 1396})

        details_event = dialog._result_queue.get_nowait()
        self.assertEqual(details_event[0:3], ("details", 4, 1396))
        self.assertEqual(details_event[3]["name"], "Breaking Bad")
        self.assertEqual(dialog._executor.jobs, [])

    def test_tv_match_can_be_confirmed_before_episode_title_loads(self):
        class FakeVar:
            def set(self, value):
                self.value = value

        button = SimpleNamespace(
            configure=lambda **kwargs: setattr(button, "state", kwargs["state"]),
        )
        dialog = SimpleNamespace(
            item=SimpleNamespace(
                query="Lanterns",
                season_number=1,
                episode_number=4,
                episode_end_number=None,
            ),
            is_tv=True,
            details_title_var=FakeVar(),
            details_id_var=FakeVar(),
            confidence_var=FakeVar(),
            genres_var=FakeVar(),
            director_var=FakeVar(),
            cast_var=FakeVar(),
            overview_var=FakeVar(),
            tv_info_var=FakeVar(),
            search_status_var=FakeVar(),
            details_poster=SimpleNamespace(configure=lambda **_kwargs: None),
            selected_index=None,
            update_button=button,
            _title=SearchDialog._title,
            _year=lambda _result: None,
            _score=lambda _result, _query: 0.5,
            _resize_details_text=lambda: None,
        )

        SearchDialog._show_selected_details(dialog, {
            "id": 95350,
            "name": "Lanterns",
        })

        self.assertEqual(button.state, "normal")
        self.assertEqual(dialog.tv_info_var.value, "S01E04")

    def test_confirm_tv_match_uses_filename_episode_title_if_available(self):
        item = SimpleNamespace(
            season_number=1,
            episode_number=4,
            episode_title="Cancer Man",
            episode_end_number=None,
        )
        saved = []
        closed = []
        dialog = SimpleNamespace(
            selected_result={"id": 1396, "name": "Breaking Bad"},
            selected_details={"id": 1396, "name": "Breaking Bad"},
            is_tv=True,
            item=item,
            app=SimpleNamespace(
                set_match=lambda *args, **kwargs: saved.append(
                    (args, kwargs),
                ),
            ),
            _close=lambda outcome: closed.append(outcome),
        )

        SearchDialog.update_match(dialog)

        self.assertEqual(saved[0][0][2], "manual")
        self.assertEqual(saved[0][0][1]["episode_title"], "Cancer Man")
        self.assertEqual(saved[0][1], {"source": "manual"})
        self.assertEqual(closed, ["matched"])

    def test_reuses_episode_title_when_reconfirming_the_same_show_episode(self):
        item = Item(Path("Breaking.Bad.S01E04.mkv"), Path("TV"), "tv")
        item.match = {
            "id": 1396,
            "season_number": 1,
            "episode_number": 4,
            "episode_title": "Cancer Man",
        }
        dialog = SimpleNamespace(
            item=item,
        )

        self.assertEqual(
            SearchDialog._known_episode_title(
                dialog, {"id": 1396},
            ),
            "Cancer Man",
        )
        self.assertIsNone(
            SearchDialog._known_episode_title(
                dialog, {"id": 777},
            ),
        )

    def test_long_overview_is_shortened_to_keep_episode_details_visible(self):
        class FakeFont:
            @staticmethod
            def measure(text):
                return len(text)

        dialog = SimpleNamespace(_details_font=FakeFont())
        overview = "A long overview with enough words to exceed three lines " * 4

        shortened = SearchDialog._fit_overview(
            dialog, overview, width=24,
        )

        self.assertLessEqual(len(shortened.splitlines()), 3)
        self.assertTrue(shortened.endswith("..."))


class TableLayoutTests(unittest.TestCase):
    def test_fit_preserves_minimums_and_available_width(self):
        widths = fit_column_widths(800, (42, 120, 90, 120), (0, 0.42, 0.18, 0.4))

        self.assertEqual(sum(widths), 800)
        self.assertEqual(widths[0], 42)
        self.assertGreaterEqual(widths[1], 120)
        self.assertGreaterEqual(widths[2], 90)
        self.assertGreaterEqual(widths[3], 120)

    def test_movie_name_includes_containing_folder(self):
        self.assertEqual(
            folder_and_file(Path("Movies") / "Inception.mkv"),
            os.path.join("Movies", "Inception.mkv"),
        )

    def test_source_name_includes_all_folders_below_import_root(self):
        root = Path("Library") / "TV"
        source = root / "The Boys" / "_The Boys" / "episode.mkv"

        self.assertEqual(
            source_path_display(source, root),
            os.path.join("The Boys", "_The Boys", "episode.mkv"),
        )
        self.assertEqual(
            source_path_display(source, Path("Other")),
            str(source),
        )

    def test_target_name_displays_destination_folder_and_filename(self):
        self.assertEqual(
            target_name_display(
                Path("Movies") / "The Matrix (1999).mkv",
            ),
            os.path.join("Movies", "The Matrix (1999).mkv"),
        )
        self.assertEqual(
            target_name_display(
                Path("Movies") / "The Matrix (1999).mkv",
            ),
            os.path.join("Movies", "The Matrix (1999).mkv"),
        )
        self.assertEqual(
            target_name_display(
                Path("Season 1") / "Dark - S01E01 - Secrets.mkv",
            ),
            os.path.join("Season 1", "Dark - S01E01 - Secrets.mkv"),
        )

    def test_tv_target_name_includes_show_season_and_episode(self):
        self.assertEqual(
            tv_target_name_display(
                Path("Library") / "Dark" / "Season 1"
                / "Dark - S01E01 - Secrets.mkv",
            ),
            os.path.join(
                "Dark", "Season 1", "Dark - S01E01 - Secrets.mkv",
            ),
        )


class FileOperationTests(unittest.TestCase):
    def test_custom_movie_plan_uses_patterns_and_preserves_extension(self):
        root = Path("Movies")
        source = root / "Inception.2010.avi"
        choice = {"id": 27205, "title": "Inception", "year": 2010}
        folder = format_pattern("{title} {year} {tmdb-{id}}", choice)
        file_stem = format_pattern("{title}", choice)

        plan = make_plan(
            source, root, choice, True, True, True, 1,
            folder_name=folder, file_stem=file_stem,
        )

        self.assertEqual(
            plan.dest, root / "Inception 2010 {tmdb-27205}" / "Inception.avi",
        )

    def test_custom_tv_plan_skips_each_disabled_folder_level(self):
        root = Path("Library")
        source = root / "incoming.mkv"
        choice = {
            "id": 1396, "title": "Breaking Bad", "year": 2008,
            "season_number": 1, "episode_number": 1,
            "episode_title": "Pilot",
        }

        for show, season, expected in (
            (
                False, True,
                root / "Season 01" / "S01E01 - Pilot.mkv",
            ),
            (
                True, False,
                root / "Breaking Bad" / "S01E01 - Pilot.mkv",
            ),
            (False, False, root / "S01E01 - Pilot.mkv"),
        ):
            with self.subTest(show=show, season=season):
                plan = make_tv_plan(
                    source, root, choice, show_folder="Breaking Bad",
                    season_folder="Season 01", file_stem="S01E01 - Pilot",
                    create_show_folder=show, create_season_folder=season,
                )
                self.assertEqual(plan.dest, expected)

    def test_already_correctly_named_movie_is_a_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "The Matrix (1999)"
            source = folder / "The Matrix (1999).mkv"
            folder.mkdir()
            source.write_bytes(b"movie")
            choice = {"id": 603, "title": "The Matrix", "year": 1999}
            plan = make_plan(source, root, choice, True, True, True, 1)
            operations = []

            self.assertEqual(plan.dest, source)
            self.assertEqual(perform_plan(source, plan, operations), source)
            self.assertEqual(operations, [])
            self.assertTrue(source.is_file())

    def test_windows_hidden_attribute_is_detected(self):
        with patch(
            "moviq.core.file_ops.Path.stat",
            return_value=SimpleNamespace(st_file_attributes=0x2),
        ):
            self.assertTrue(is_hidden_path(Path("episode.mkv")))

    def test_scan_skips_hidden_files_and_hidden_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visible = root / "visible.mkv"
            hidden_file = root / ".sonarr-original.mkv"
            hidden_dir = root / ".hidden"
            visible.write_bytes(b"visible")
            hidden_file.write_bytes(b"hidden")
            hidden_dir.mkdir()
            nested = hidden_dir / "episode.mkv"
            nested.write_bytes(b"hidden")

            self.assertTrue(is_hidden_path(hidden_file))
            self.assertEqual(scan_dir(root), [visible])

    def test_scan_includes_small_videos_but_skips_samples_and_extras(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "movie.mkv").write_bytes(b"movie-data")
            (root / "sample.mp4").write_bytes(b"sample-data")
            (root / "small.avi").write_bytes(b"x")
            (root / "empty.mp4").write_bytes(b"")
            extras = root / "extras"
            extras.mkdir()
            (extras / "bonus.mkv").write_bytes(b"bonus-data")

            self.assertEqual(
                scan_dir(root),
                [root / "empty.mp4", root / "movie.mkv", root / "small.avi"],
            )

    def test_apply_and_undo_moves_video_and_sidecar(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "Old.Name.mkv"
            subtitle = root / "Old.Name.en.srt"
            artwork = root / "Old.Name-poster.jpg"
            poster = root / "poster.jpg"
            metadata = root / "movie.nfo"
            video.write_bytes(b"video")
            subtitle.write_text("subtitle", encoding="utf-8")
            artwork.write_bytes(b"artwork")
            poster.write_bytes(b"poster")
            metadata.write_text("metadata", encoding="utf-8")
            choice = {"id": 603, "title": "The Matrix", "year": 1999}
            plan = make_plan(video, root, choice, True, False, True, 1)
            operations = []

            result = perform_plan(video, plan, operations)

            self.assertTrue(result.is_file())
            self.assertTrue((plan.dest_dir / "The Matrix (1999).en.srt").is_file())
            self.assertTrue(
                (plan.dest_dir / "The Matrix (1999)-poster.jpg").is_file()
            )
            self.assertTrue((plan.dest_dir / "poster.jpg").is_file())
            self.assertTrue((plan.dest_dir / "movie.nfo").is_file())
            self.assertEqual(undo_ops(operations), [])
            self.assertTrue(video.is_file())
            self.assertTrue(subtitle.is_file())
            self.assertTrue(artwork.is_file())
            self.assertTrue(poster.is_file())
            self.assertTrue(metadata.is_file())
            self.assertFalse(plan.dest_dir.exists())

    def test_moves_show_and_season_artwork_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp)
            selected_show = library / "dark all 3 seasons"
            source = selected_show / "Season 1" / "Dark.S01E01.mkv"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"episode")
            show_assets = [
                selected_show / "poster.jpg",
                selected_show / "fanart.png",
                selected_show / "tvshow.nfo",
            ]
            season_poster = selected_show / "Season 1-poster.jpg"
            season_assets = [
                source.parent / "poster.jpg",
                source.parent / "season.nfo",
            ]
            screenshots_dir = source.parent / "Screenshots"
            screenshots_dir.mkdir()
            screenshot = screenshots_dir / "capture.jfif"
            screenshot.write_bytes(b"screen grab")
            notes = source.parent / "release-notes.txt"
            notes.write_text("episode notes", encoding="utf-8")
            for asset in show_assets + [season_poster] + season_assets:
                asset.write_bytes(b"asset")
            choice = {
                "id": 123, "title": "Dark", "season_number": 1,
                "episode_number": 1, "episode_title": "Pilot",
            }
            plan = make_tv_plan(source, library, choice)
            operations = []

            result = perform_plan(source, plan, operations)

            self.assertEqual(
                result,
                library / "Dark" / "Season 01" / "Dark - S01E01 - Pilot.mkv",
            )
            self.assertTrue(all((library / "Dark" / asset.name).is_file()
                                for asset in show_assets))
            self.assertTrue((library / "Dark" / season_poster.name).is_file())
            self.assertTrue(all((result.parent / asset.name).is_file()
                                for asset in season_assets))
            self.assertTrue(
                (result.parent / "Screenshots" / screenshot.name).is_file()
            )
            self.assertTrue((result.parent / notes.name).is_file())
            self.assertFalse((library / "Dark" / "Screenshots").exists())
            self.assertFalse(selected_show.exists())
            self.assertEqual(undo_ops(operations), [])
            self.assertTrue(source.is_file())
            self.assertTrue(screenshot.is_file())
            self.assertTrue(notes.is_file())
            self.assertTrue(all(asset.is_file()
                                for asset in show_assets + [season_poster]
                                + season_assets))
            self.assertFalse((library / "Dark").exists())


if __name__ == "__main__":
    unittest.main()
