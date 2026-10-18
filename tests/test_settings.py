import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from moviq.core.file_ops import make_tv_plan, move_files
from moviq.core.config import migrate_user_data
from moviq.core.formatting import (
    CUSTOM_ID,
    MOVIE_FORMATS,
    TV_DEFAULT_ID,
    format_component,
)
from moviq.core.naming import NAMING, folder_name_for, tv_episode_name_for
from moviq.core.tmdb import TMDB
from moviq.core.settings_store import (
    DEFAULTS,
    MOVIE_PRESETS,
    TV_PRESETS,
    SettingsStore,
    check_tmdb_key,
    render_pattern,
    validate_pattern,
)
from moviq.core.updater import (
    _version_tuple,
    check_latest_release as check_for_updates,
)
from moviq.ui.settings._common import TMDB_KEY_URL, TMDB_SIGNUP_URL
from moviq.ui.settings import SettingsView


class SettingsStoreTests(unittest.TestCase):
    def test_new_settings_use_system_theme_and_blank_api_key(self):
        self.assertEqual(DEFAULTS["theme"], "system")
        self.assertEqual(DEFAULTS["key"], "")
        self.assertEqual(DEFAULTS["window_state"], "normal")

    def test_loads_legacy_values_and_fills_new_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.json"
            legacy.write_text(
                json.dumps({
                    "key": "remembered-key",
                    "remember": True,
                    "theme": "light",
                    "custom_option": 23,
                }),
                encoding="utf-8",
            )
            store = SettingsStore(
                root / "settings.json", legacy, save_delay=60,
            )
            try:
                self.assertEqual(store.data["theme"], "light")
                self.assertEqual(store.data["key"], "remembered-key")
                self.assertEqual(store.data["custom_option"], 23)
                self.assertEqual(store.data["tv_template"], DEFAULTS["tv_template"])
            finally:
                store.close()

    def test_never_persists_key_when_remember_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(
                path, Path(directory) / "missing.json", save_delay=60,
            )
            store.update({"key": "not-written", "remember": False})
            store.save_now()

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("key", saved)
            self.assertFalse(saved["remember"])
            store.close()

    def test_store_preserves_existing_unknown_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps({"custom_option": "keep me"}),
                encoding="utf-8",
            )
            store = SettingsStore(
                path, Path(directory) / "missing.json", save_delay=60,
            )
            store.set("theme", "system")
            store.save_now()

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["custom_option"], "keep me")
            self.assertEqual(saved["theme"], "system")
            store.close()

    def test_removed_preferences_are_ignored_and_removed_from_saved_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps({
                    "keep_undo": False,
                    "app_language": "fr-FR  French",
                }),
                encoding="utf-8",
            )
            store = SettingsStore(
                path, Path(directory) / "missing.json", save_delay=60,
            )
            try:
                self.assertNotIn("keep_undo", store.data)
                self.assertNotIn("app_language", store.data)
                store.save_now()
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("keep_undo", saved)
                self.assertNotIn("app_language", saved)
            finally:
                store.close()


class UserDataMigrationTests(unittest.TestCase):
    def test_moves_existing_moviq_files_into_user_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            old_files = {
                ".moviq.json": '{"theme": "light"}',
                ".tmdb_renamer.json": '{"key": "saved-key"}',
                "moviq_show_overrides.json": '{"Example Show": 123}',
            }
            for name, contents in old_files.items():
                (home / name).write_text(contents, encoding="utf-8")

            migrate_user_data(home)

            data_dir = home / ".moviq"
            expected = {
                "config.json": old_files[".moviq.json"],
                "legacy_config.json": old_files[".tmdb_renamer.json"],
                "moviq_show_overrides.json": old_files[
                    "moviq_show_overrides.json"
                ],
            }
            for name, contents in expected.items():
                self.assertEqual(
                    (data_dir / name).read_text(encoding="utf-8"),
                    contents,
                )
            for name in old_files:
                self.assertFalse((home / name).exists())

    def test_does_not_overwrite_existing_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".moviq.json").write_text(
                '{"theme": "light"}', encoding="utf-8",
            )
            data_dir = home / ".moviq"
            data_dir.mkdir()
            (data_dir / "config.json").write_text(
                '{"theme": "dark"}', encoding="utf-8",
            )

            migrate_user_data(home)

            self.assertEqual(
                (data_dir / "config.json").read_text(encoding="utf-8"),
                '{"theme": "dark"}',
            )
            self.assertTrue((home / ".moviq.json").exists())


class FormattingSettingsTests(unittest.TestCase):
    def test_empty_custom_patterns_remain_custom_after_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({
                "movie_preset_id": CUSTOM_ID,
                "movie_custom_folder_pattern": "",
                "movie_custom_file_pattern": "",
                "tv_preset_id": CUSTOM_ID,
                "tv_custom_show_folder_pattern": "",
                "tv_custom_season_folder_pattern": "",
                "tv_custom_file_pattern": "",
            }), encoding="utf-8")
            store = SettingsStore(
                path, Path(directory) / "missing.json", save_delay=60,
            )
            try:
                self.assertEqual(store.data["movie_preset_id"], CUSTOM_ID)
                self.assertEqual(store.data["movie_file_pattern"], "")
                self.assertEqual(store.data["tv_preset_id"], CUSTOM_ID)
                self.assertEqual(store.data["tv_file_pattern"], "")
                store.save_now()
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["movie_preset_id"], CUSTOM_ID)
                self.assertEqual(saved["movie_custom_file_pattern"], "")
                self.assertEqual(saved["tv_preset_id"], CUSTOM_ID)
                self.assertEqual(saved["tv_custom_file_pattern"], "")
            finally:
                store.close()

    def test_empty_custom_file_field_does_not_change_selected_preset(self):
        view = SettingsView.__new__(SettingsView)
        view._updating_format = set()
        view._current_format_id = {"movie": CUSTOM_ID}
        view.vars = {
            "movie_file_pattern": Mock(get=Mock(return_value="")),
            "movie_create_folder": Mock(get=Mock(return_value=True)),
        }
        view._update_pattern_editing = Mock()
        view._refresh_preset_cards = Mock()
        view._formatting_values = Mock(return_value={
            "movie_folder_pattern": "",
            "movie_file_pattern": "",
        })
        view._format_preview_components = Mock(
            return_value=("Before", ["Movie.mkv"]),
        )
        view._format_preview_before = {"movie": Mock()}
        view._format_preview_after = {"movie": Mock()}
        view.movie_preview_var = Mock()
        view._select_format_preset = Mock()
        view._save_custom_patterns = Mock()
        view._save_format_settings = Mock()

        view._formatting_changed("movie")

        self.assertEqual(view._current_format_id["movie"], CUSTOM_ID)
        view._select_format_preset.assert_not_called()
        view._save_custom_patterns.assert_called_once_with("movie")

    def test_formatter_replaces_known_tokens_and_sanitizes_components(self):
        values = {
            "title": "Breaking Bad", "year": 2008, "id": 1396,
            "season": 1, "episode": 4, "episode_title": "Cancer Man",
        }
        self.assertEqual(
            format_component(
                "{tmdb-{id}} {season:00} {season} {episode:00} {unknown}",
                values,
            ),
            "{tmdb-1396} 01 1 04 {unknown}",
        )
        self.assertEqual(format_component('a\\b/c:*?"<>|. ', {}), "abc")
        self.assertEqual(
            format_component("{year}   -   {title}  ", values),
            "2008 - Breaking Bad",
        )

    def test_year_first_preset_uses_year_folder_and_title_only_file(self):
        year_first = next(
            preset for preset in MOVIE_FORMATS if preset[0] == "year_first"
        )
        self.assertEqual(year_first[2], "{year} - {title}")
        self.assertEqual(year_first[3], "{title}")
        self.assertEqual(
            format_component(year_first[2], {
                "title": "Inception", "year": 2010,
            }),
            "2010 - Inception",
        )
        self.assertEqual(
            format_component(year_first[3], {
                "title": "Inception", "year": 2010,
            }),
            "Inception",
        )

    def test_movie_preview_path_uses_separate_folder_and_file_patterns(self):
        view = SettingsView.__new__(SettingsView)
        view.vars = {
            "movie_create_folder": Mock(get=Mock(return_value=True)),
        }
        preview = view._format_output_preview("movie", {
            "movie_folder_pattern": "{title} ({year})",
            "movie_file_pattern": "{title}",
        })
        self.assertEqual(
            preview,
            "Before: Inception.2010.mkv\n"
            "After: Inception (2010)\\Inception.mkv",
        )

    def test_tv_preview_path_formats_each_enabled_level(self):
        view = SettingsView.__new__(SettingsView)
        view.vars = {
            "tv_create_show_folder": Mock(get=Mock(return_value=True)),
            "tv_create_season_folder": Mock(get=Mock(return_value=True)),
        }
        preview = view._format_output_preview("tv", {
            "tv_show_folder_pattern": "{title}",
            "tv_season_folder_pattern": "Season {season:00}",
            "tv_file_pattern": "{title} - S{season:00}E{episode:00} - {episode_title}",
        })
        self.assertEqual(
            preview,
            "Before: Breaking.Bad.S01E01.mkv\n"
            "After: Breaking Bad\\Season 01\\Breaking Bad - S01E01 - Pilot.mkv",
        )

    def test_legacy_patterns_migrate_and_unknown_preset_uses_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({
                "name_template": "{title} -- {year}",
                "tv_template": (
                    "{title} - S{season:02d}E{episode:02d} - {episode_title}"
                ),
                "tv_preset_id": "removed-preset",
            }), encoding="utf-8")
            store = SettingsStore(
                path, Path(directory) / "missing.json", save_delay=60,
            )
            try:
                self.assertEqual(store.data["movie_preset_id"], "custom")
                self.assertEqual(
                    store.data["movie_folder_pattern"], "{title} ({year})",
                )
                self.assertEqual(
                    store.data["movie_file_pattern"], "{title} -- {year}",
                )
                self.assertEqual(store.data["tv_preset_id"], TV_DEFAULT_ID)
                self.assertEqual(
                    store.data["tv_file_pattern"],
                    "{title} - S{season:00}E{episode:00} - {episode_title}",
                )
            finally:
                store.close()

    def test_empty_custom_file_pattern_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({
                "movie_preset_id": "custom",
                "movie_custom_folder_pattern": "Custom folder",
                "movie_custom_file_pattern": "",
            }), encoding="utf-8")
            store = SettingsStore(
                path, Path(directory) / "missing.json", save_delay=60,
            )
            try:
                self.assertEqual(store.data["movie_preset_id"], CUSTOM_ID)
                self.assertEqual(store.data["movie_file_pattern"], "")
            finally:
                store.close()

    def test_all_movie_and_tv_presets_render(self):
        movie = {"title": "Inception", "year": 2010, "id": 27205}
        tv = {
            "title": "Breaking Bad", "year": 2008, "id": 1396,
            "season": 1, "episode": 4, "episode_title": "Cancer Man",
        }
        self.assertEqual(
            [render_pattern(pattern, movie) for pattern in MOVIE_PRESETS],
            [
                "Inception (2010)",
                "Inception (2010) [tmdbid-27205]",
                "Inception.2010",
                "Inception - 2010",
                "Inception [2010]",
                "Inception (2010) - 27205",
                "2010 - Inception",
            ],
        )
        self.assertEqual(
            [render_pattern(pattern, tv) for pattern in TV_PRESETS],
            [
                "Breaking Bad - S01E04 - Cancer Man",
                "Breaking Bad - S01E04",
                "Breaking Bad (2008) - S01E04 - Cancer Man",
                "Breaking Bad - 1x04",
                "S01E04 - Cancer Man",
                "S01E04 Cancer Man",
                "1x04 Cancer Man",
            ],
        )

    def test_selecting_each_tv_preset_fills_the_pattern(self):
        view = SettingsView.__new__(SettingsView)
        view.tv_preset_var = Mock()
        view._refresh_preset_cards = Mock()

        for pattern in TV_PRESETS:
            with self.subTest(pattern=pattern):
                pattern_variable = Mock()
                view._choose_preset(
                    "tv", pattern, pattern_variable, TV_PRESETS,
                )
                view.tv_preset_var.set.assert_called_with(pattern)
                pattern_variable.set.assert_called_once_with(pattern)

    def test_tv_preview_keeps_show_and_zero_padded_season_folders(self):
        view = SettingsView.__new__(SettingsView)
        view.vars = {"tv_folder": Mock(get=Mock(return_value=True))}
        preview = view._format_preview(
            "tv",
            TV_PRESETS[4],
            {
                "title": "Breaking Bad",
                "year": 2008,
                "id": 1396,
                "season": 1,
                "episode": 1,
                "episode_title": "Pilot",
            },
            "",
        )

        self.assertEqual(
            preview,
            "Before: Breaking.Bad.S01E01.mkv   →   After: "
            "Breaking Bad\\Season 01\\S01E01 - Pilot.mkv",
        )

    def test_api_key_help_links_open_their_expected_pages(self):
        event = type("Event", (), {"x": 4, "y": 8})()
        text = Mock()
        text.index.return_value = "1.5"
        text.tag_names.side_effect = [
            ("tmdb_link",),
            ("api_link",),
        ]

        with patch(
            "moviq.ui.settings.general.webbrowser.open",
        ) as open_url:
            self.assertEqual(
                SettingsView._open_api_help_link(event, text), "break",
            )
            self.assertEqual(
                SettingsView._open_api_help_link(event, text), "break",
            )

        self.assertEqual(
            [call.args[0] for call in open_url.call_args_list],
            [TMDB_SIGNUP_URL, TMDB_KEY_URL],
        )

    def test_pattern_validation_covers_empty_unknown_and_missing_title(self):
        tokens = {"title", "year", "id"}
        self.assertTrue(validate_pattern("  ", tokens))
        self.assertIn("Unknown token", validate_pattern(
            "{title} {bad}", tokens,
        ))
        self.assertIn("Warning", validate_pattern("{year}", tokens))
        self.assertEqual(validate_pattern("{title} ({year})", tokens), "")

    def test_rendering_strips_illegal_windows_filename_characters(self):
        self.assertEqual(
            render_pattern("{title}", {"title": 'A<>:"/\\|?*B'}),
            "AB",
        )

    def test_tv_preset_without_episode_title_is_usable(self):
        old_template = NAMING["tv_template"]
        try:
            NAMING["tv_template"] = TV_PRESETS[1]
            self.assertEqual(
                tv_episode_name_for({
                    "id": 1396,
                    "title": "Breaking Bad",
                    "season_number": 1,
                    "episode_number": 4,
                    "episode_title": None,
                }),
                "Breaking Bad - S01E04",
            )
        finally:
            NAMING["tv_template"] = old_template

    def test_movie_folder_format_and_id_tag_do_not_duplicate(self):
        old_template, old_tag = NAMING["template"], NAMING["tag_id"]
        try:
            NAMING["template"] = MOVIE_PRESETS[1]
            NAMING["tag_id"] = True
            self.assertEqual(
                folder_name_for({
                    "id": 27205, "title": "Inception", "year": 2010,
                }),
                "Inception (2010) [tmdbid-27205]",
            )
        finally:
            NAMING["template"], NAMING["tag_id"] = old_template, old_tag

    def test_tv_folder_option_can_flatten_output_into_selected_root(self):
        root = Path("Library")
        plan = make_tv_plan(
            root / "incoming.mkv",
            root,
            {
                "id": 1396,
                "title": "Breaking Bad",
                "season_number": 1,
                "episode_number": 1,
                "episode_title": "Pilot",
            },
            organize_folders=False,
        )

        self.assertEqual(plan.dest_dir, root)

    def test_subtitles_keep_their_names_when_setting_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Old Name.mkv"
            subtitle = Path(directory) / "Old Name.en.srt"
            destination = Path(directory) / "output"
            destination.mkdir()
            source.write_bytes(b"video")
            subtitle.write_text("subtitle", encoding="utf-8")
            ops = []

            move_files(
                source, destination, "New Name", ops,
                rename_subtitles=False,
            )

            self.assertTrue((destination / "New Name.mkv").exists())
            self.assertTrue((destination / "Old Name.en.srt").exists())


class UpdateAndKeyCheckTests(unittest.TestCase):
    def test_version_tuple_ignores_leading_v(self):
        self.assertEqual(_version_tuple("v1.2.0"), (1, 2, 0))

    @patch("moviq.core.updater.requests.get")
    def test_update_check_detects_newer_release_and_uses_github_url(self, get):
        response = Mock()
        response.json.return_value = {
            "tag_name": "v0.2.0",
            "html_url": "https://github.com/asadse7en/Moviq/releases/tag/v0.2.0",
        }
        get.return_value = response
        state, version, url = check_for_updates("0.1.0")

        self.assertEqual((state, version), ("available", "v0.2.0"))
        self.assertEqual(url, "https://github.com/asadse7en/Moviq/releases/tag/v0.2.0")
        response.raise_for_status.assert_called_once_with()
        self.assertIn("User-Agent", get.call_args.kwargs["headers"])

    @patch("moviq.core.updater.requests.get")
    def test_update_check_reports_same_version(self, get):
        response = Mock()
        response.json.return_value = {
            "tag_name": "v0.1.0",
            "html_url": "https://github.com/asadse7en/Moviq/releases/tag/v0.1.0",
        }
        get.return_value = response
        self.assertEqual(check_for_updates("0.1.0"), (
            "current", "v0.1.0",
            "https://github.com/asadse7en/Moviq/releases/tag/v0.1.0",
        ))

    @patch("moviq.core.updater.requests.get")
    def test_update_check_handles_offline_and_rate_limit(self, get):
        get.side_effect = requests.ConnectionError("offline")
        self.assertEqual(check_for_updates("0.1.0"), ("error", None, None))
        get.side_effect = requests.HTTPError("rate limit")
        self.assertEqual(check_for_updates("0.1.0"), ("error", None, None))

    def test_invalid_v3_key_is_rejected_without_a_network_request(self):
        self.assertEqual(check_tmdb_key("not-a-valid-v3-key"), "Wrong format")
        self.assertEqual(check_tmdb_key("a" * 41), "Wrong format")

    @patch("moviq.core.tmdb._SESSION.get")
    def test_key_check_reports_offline_network_error(self, request):
        request.side_effect = requests.ConnectionError("offline")
        with patch("moviq.core.tmdb._REQUEST_LIMITER.acquire"), patch.object(
            TMDB, "_wait",
        ):
            self.assertEqual(
                check_tmdb_key("a" * 32),
                "Couldn't reach TMDB",
            )

    @patch("moviq.core.tmdb._SESSION.get")
    def test_key_check_maps_authorization_failure_to_rejected(self, request):
        request.return_value = Mock(status_code=401)

        with patch("moviq.core.tmdb._REQUEST_LIMITER.acquire"):
            self.assertEqual(check_tmdb_key("a" * 32), "Rejected")

        request.assert_called_once()
        self.assertEqual(
            request.call_args.kwargs["params"]["api_key"],
            "a" * 32,
        )
        self.assertEqual(
            request.call_args.kwargs["timeout"],
            (5, 15),
        )


if __name__ == "__main__":
    unittest.main()
