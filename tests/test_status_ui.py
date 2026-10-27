import base64
import queue
import struct
import tempfile
import threading
import tkinter as tk
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from moviq.ui.app import App, _detect_media_mode
from moviq.core.models import Item
from moviq.ui.design import (
    DARK_BACKGROUND,
    PALETTES,
    STATUS_GLYPHS,
    STATUS_ICONS,
    STATUS_TEXT,
)
from moviq.ui.popups import MatchProgressPopup, apply_titlebar_theme
from moviq.ui.settings.general import GeneralTabMixin
from moviq.ui.settings import SettingsView
from moviq.ui.theme import (
    _configure_scale_style,
    _configure_switch_style,
    _scale_thumb_png,
    _switch_indicator_png,
)


class SwitchThemeTests(unittest.TestCase):
    @staticmethod
    def _pixels(encoded_image):
        image = base64.b64decode(encoded_image)
        width, height = struct.unpack(">II", image[16:24])
        offset = 8
        compressed = bytearray()
        while offset < len(image):
            size = struct.unpack(">I", image[offset:offset + 4])[0]
            kind = image[offset + 4:offset + 8]
            if kind == b"IDAT":
                compressed.extend(image[offset + 8:offset + 8 + size])
            offset += size + 12
        return width, height, zlib.decompress(compressed)

    def test_switch_indicator_uses_the_theme_accent(self):
        width, height, pixels = self._pixels(
            _switch_indicator_png("#0faaa0", "#04201d", active=True),
        )
        self.assertEqual((width, height), (40, 20))
        accent_pixel = pixels[
            10 * (1 + width * 4) + 1 + 20 * 4:
            10 * (1 + width * 4) + 1 + 21 * 4
        ]
        self.assertEqual(tuple(accent_pixel), (15, 170, 160, 255))

    def test_scale_thumb_uses_theme_accent(self):
        width, height, pixels = self._pixels(
            _scale_thumb_png("#0faaa0", "#1c1c1c"),
        )
        center = 10 * (1 + width * 4) + 1 + 10 * 4

        self.assertEqual((width, height), (20, 20))
        self.assertEqual(tuple(pixels[center:center + 4]), (15, 170, 160, 255))

    def test_scale_style_replaces_sun_valley_slider_element(self):
        class Style:
            def __init__(self):
                self.layouts = {
                    "Horizontal.TScale": [
                        ("Horizontal.Scale.trough", {"children": [
                            ("Scale.slider", {"side": "top"}),
                        ]}),
                    ],
                }

            def element_names(self):
                return []

            def element_create(self, *args, **kwargs):
                pass

            def layout(self, name, layout=None):
                if layout is not None:
                    self.layouts[name] = layout
                return self.layouts[name]

        style = Style()
        app = SimpleNamespace(
            mode="dark",
            root=object(),
            _moviq_scale_images={},
        )
        with (
            patch("moviq.ui.theme.HAVE_SV", True),
            patch("moviq.ui.theme.tk.PhotoImage", return_value=object()),
        ):
            _configure_scale_style(app, style, PALETTES["dark"])

        slider = style.layouts["Moviq.Horizontal.TScale"][0][1]["children"][0]
        self.assertEqual(slider[0], "Moviq.Dark.Scale.slider")

    def test_theme_options_redraw_with_selected_theme_and_palette(self):
        light = Mock()
        light_indicator = Mock()
        dark_indicator = Mock()
        view = SimpleNamespace(
            app=SimpleNamespace(pal=PALETTES["dark"]),
            vars={"theme": Mock(get=Mock(return_value="dark"))},
            _theme_option_indicators={
                "light": (light_indicator, "light-ring", "light-dot"),
                "dark": (dark_indicator, "dark-ring", "dark-dot"),
            },
        )

        GeneralTabMixin._update_theme_options(view, PALETTES["dark"])

        light_indicator.configure.assert_called_once_with(
            background=PALETTES["dark"]["bg"],
        )
        light_indicator.itemconfigure.assert_any_call(
            "light-ring", outline=PALETTES["dark"]["header_border_hi"],
        )
        light_indicator.itemconfigure.assert_any_call(
            "light-dot", fill=PALETTES["dark"]["accent"], state="hidden",
        )
        dark_indicator.itemconfigure.assert_any_call(
            "dark-ring", outline=PALETTES["dark"]["accent"],
        )
        dark_indicator.itemconfigure.assert_any_call(
            "dark-dot", fill=PALETTES["dark"]["accent"], state="normal",
        )


class MissingSourceTests(unittest.TestCase):
    def test_prune_removes_only_missing_sources_from_the_ui_list(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            existing_path = root / "existing.mkv"
            existing_path.write_bytes(b"video")
            existing = SimpleNamespace(path=existing_path, iid="existing")
            missing = SimpleNamespace(path=root / "missing.mkv", iid="missing")
            hidden_path = root / ".sonarr-original.mkv"
            hidden_path.write_bytes(b"hidden")
            hidden = SimpleNamespace(path=hidden_path, iid="hidden")

            class Tree:
                def __init__(self):
                    self.rows = {"existing", "missing", "hidden"}

                def exists(self, iid):
                    return iid in self.rows

                def delete(self, iid):
                    self.rows.remove(iid)

            app = App.__new__(App)
            app.tree = Tree()
            app.items = [existing, missing, hidden]
            app.by_iid = {
                "existing": existing,
                "missing": missing,
                "hidden": hidden,
            }
            app._filtered_out = set()
            app._row_tooltip_paths = {}
            app.schedule_summary = lambda: None

            removed, unavailable = app._prune_missing_items()

            self.assertEqual(removed, [missing, hidden])
            self.assertEqual(unavailable, [])
            self.assertEqual(app.items, [existing])
            self.assertEqual(app.tree.rows, {"existing"})


class FileDialogInitialDirectoryTests(unittest.TestCase):
    def test_uses_remembered_folder_before_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            app = App.__new__(App)
            app._last_folder = str(folder)

            self.assertEqual(app._dialog_initialdir(), str(folder))

    def test_uses_downloads_when_no_folder_has_been_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            downloads = home / "Downloads"
            downloads.mkdir()
            app = App.__new__(App)
            app._last_folder = ""

            with patch("moviq.ui.app.Path.home", return_value=home):
                self.assertEqual(app._dialog_initialdir(), str(downloads))

    def test_falls_back_to_home_when_downloads_does_not_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            app = App.__new__(App)
            app._last_folder = ""

            with patch("moviq.ui.app.Path.home", return_value=home):
                self.assertEqual(app._dialog_initialdir(), str(home))


class WindowStateTests(unittest.TestCase):
    def test_restores_maximized_state_after_clamping_geometry(self):
        class Root:
            def __init__(self):
                self.geometry_value = None
                self.state_value = None

            def winfo_screenwidth(self):
                return 1920

            def winfo_screenheight(self):
                return 1080

            def minsize(self, *_args):
                pass

            def geometry(self, value):
                self.geometry_value = value

            def state(self, value):
                self.state_value = value

        app = App.__new__(App)
        app.root = Root()
        app.UI_SCALE = 1

        app._restore_window_geometry("1200x800+50+40", "zoomed")

        self.assertEqual(app.root.geometry_value, "1200x800+50+40")
        self.assertEqual(app.root.state_value, "zoomed")


class TitlebarThemeTests(unittest.TestCase):
    def test_theme_changes_update_one_map_handler_without_delayed_reapplies(self):
        class Window:
            def __init__(self):
                self.bind = Mock()
                self.after = Mock()

            def winfo_viewable(self):
                return False

        window = Window()
        with patch("moviq.ui.popups.sys.platform", "win32"):
            apply_titlebar_theme(window, True)
            apply_titlebar_theme(window, False)

        window.bind.assert_called_once()
        window.after.assert_not_called()
        self.assertEqual(
            window._moviq_titlebar_theme,
            (
                False,
                {
                    "caption": "#fafafa",
                    "text": "#1f2937",
                    "border": "#d1d5db",
                },
            ),
        )


class SystemThemePollingTests(unittest.TestCase):
    def test_system_theme_change_is_applied_and_poll_rescheduled(self):
        app = App.__new__(App)
        app.theme_preference = "system"
        app.mode = "dark"
        app.root = SimpleNamespace(after=Mock(return_value="after-id"))
        app.apply_theme = Mock()
        app._system_theme_after = "old-after-id"

        with patch(
            "moviq.ui.app.resolve_system_theme",
            return_value="light",
        ):
            app._poll_system_theme()

        app.apply_theme.assert_called_once_with("light")
        app.root.after.assert_called_once_with(1000, app._poll_system_theme)
        self.assertEqual(app._system_theme_after, "after-id")

    def test_manual_theme_selection_disables_system_following(self):
        app = App.__new__(App)
        app.theme_preference = "system"
        app.settings_view = object()
        app.apply_theme = Mock()

        app._setting_changed("theme", "dark")

        self.assertEqual(app.theme_preference, "dark")
        app.apply_theme.assert_called_once_with("dark")

    def test_explicit_theme_does_not_follow_system_changes(self):
        app = App.__new__(App)
        app.theme_preference = "dark"
        app.mode = "dark"
        app.root = SimpleNamespace(after=Mock(return_value="after-id"))
        app.apply_theme = Mock()

        with patch("moviq.ui.app.resolve_system_theme") as resolve:
            app._poll_system_theme()

        resolve.assert_not_called()
        app.apply_theme.assert_not_called()
        app.root.after.assert_called_once_with(1000, app._poll_system_theme)


class FormatPresetLayoutTests(unittest.TestCase):
    def test_format_presets_always_use_two_columns(self):
        view = SettingsView.__new__(SettingsView)
        cards = [Mock(), Mock(), Mock()]
        view._format_grid_columns = {}
        view._format_preset_grids = {"tv": Mock()}
        view._preset_cards = {
            "tv": [{"card": card} for card in cards],
        }

        view._layout_format_presets("tv", 1600)

        self.assertEqual(view._format_grid_columns["tv"], 2)
        self.assertEqual(
            [card.grid_configure.call_args.kwargs for card in cards],
            [
                {
                    "row": 0, "column": 0, "sticky": "nsew",
                    "padx": (0, 3), "pady": 3,
                },
                {
                    "row": 0, "column": 1, "sticky": "nsew",
                    "padx": (3, 0), "pady": 3,
                },
                {
                    "row": 1, "column": 0, "sticky": "nsew",
                    "padx": (0, 3), "pady": 3,
                },
            ],
        )
        self.assertEqual(
            view._format_preset_grids["tv"].columnconfigure.call_args_list,
            [
                call(0, weight=1, uniform="format_presets"),
                call(1, weight=1, uniform="format_presets"),
            ],
        )

    def test_format_body_stacks_below_1000_pixels(self):
        view = SettingsView.__new__(SettingsView)
        body = Mock()
        preset_grid = Mock()
        left_pane = Mock()
        preset_grid.master = left_pane
        panel = Mock()
        view._format_bodies = {"movie": body}
        view._format_body_modes = {}
        view._format_preset_grids = {"movie": preset_grid}
        view._format_right_panes = {"movie": panel}
        left_pane = Mock()
        preset_grid.master = left_pane

        view._layout_format_body("movie", 999)

        self.assertEqual(body.columnconfigure.call_args_list[:2], [
            call(0, weight=1, uniform=""),
            call(1, weight=0, uniform=""),
        ])
        preset_grid.master.grid_configure.assert_called_once_with(
            row=0, column=0, sticky="new", padx=0, pady=(0, 8),
        )
        panel.grid_configure.assert_called_once_with(
            row=1, column=0, sticky="new", padx=0, pady=0,
        )

        view._layout_format_body("movie", 1000)
        self.assertEqual(body.columnconfigure.call_args_list[-2:], [
            call(0, weight=3, uniform="movie_format_panes"),
            call(1, weight=2, uniform="movie_format_panes"),
        ])

    def test_theme_recolor_refreshes_movie_and_tv_preset_cards(self):
        view = SettingsView.__new__(SettingsView)
        view.app = SimpleNamespace(pal=PALETTES["light"])
        view._configure_settings_styles = Mock()
        view.content_canvas = Mock()
        view._update_theme_options = Mock()
        view._set_format_selector_theme = Mock()
        view._set_library_selector_theme = Mock()
        view._recolor_styled_entries = Mock()
        view._recolor_format_panel = Mock()
        view._rich_texts = []
        view._labels = []
        view.key_status_label = Mock()
        view.key_status_var = Mock()
        view.key_status_var.get.return_value = "● Connected"
        view._refresh_preset_cards = Mock()
        view._set_key_status = Mock()

        view.recolor(PALETTES["light"])

        self.assertEqual(
            [
                args.args[0]
                for args in view._refresh_preset_cards.call_args_list
            ],
            ["movie", "tv"],
        )

    def test_hover_updates_only_when_pointer_enters_or_leaves_card(self):
        card = SimpleNamespace(
            winfo_rootx=lambda: 10,
            winfo_rooty=lambda: 20,
            winfo_width=lambda: 100,
            winfo_height=lambda: 60,
        )
        record = {"card": card, "hover": False}
        view = SettingsView.__new__(SettingsView)
        view.winfo_pointerxy = Mock(return_value=(50, 40))
        view._preset_cards = {"movie": [record], "tv": []}
        view._refresh_preset_cards = Mock()

        view._update_preset_hover(record)
        view._update_preset_hover(record)

        self.assertTrue(record["hover"])
        view._refresh_preset_cards.assert_called_once_with("movie")


class HistorySnapshotTests(unittest.TestCase):
    def test_finish_manual_match_skips_tk_widgets_in_history_snapshot(self):
        item = Item(Path("movie.mkv"), Path("Movies"))
        item.status = "manual"
        item.status_detail = tk.Tcl()
        item.iid = "movie"
        app = App.__new__(App)
        app.items = [item]
        app.tree = SimpleNamespace(exists=lambda _iid: False)
        app._row_tooltip_paths = {}
        app._manual_match_saved = {item}
        app._manual_match_before = [app._capture_record(item)]
        app._manual_match_renamed_skipped = 0
        app.push_undo = Mock()
        app.schedule_summary = Mock()
        app._manual_operation_cache = None

        app._finish_manual_match()

        app.push_undo.assert_called_once()
        self.assertEqual(app.push_undo.call_args.args[0], "Manual Match (1 files)")


class SettingsViewCloseTests(unittest.TestCase):
    def test_unbinds_global_scroll_callbacks_through_tk_root(self):
        root = SimpleNamespace(_unbind=Mock())
        view = SimpleNamespace(
            _root=lambda: root,
            _scroll_bindings=[("<MouseWheel>", "callback-id")],
            _key_after=None,
            _saved_key_status_after=None,
            _poll_id=None,
            _preference_traces=[],
            _executor=Mock(),
        )

        SettingsView.close(view)

        root._unbind.assert_called_once_with(
            ("bind", "all", "<MouseWheel>"), "callback-id",
        )
        view._executor.shutdown.assert_called_once_with(
            wait=False, cancel_futures=True,
        )


class MediaModeDetectionTests(unittest.TestCase):
    def test_detects_tv_episodes_added_in_movie_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "Alice in Borderland - S01E01 - Episode 1.mkv").touch()
            (folder / "Alice in Borderland - S01E02 - Episode 2.mkv").touch()

            self.assertEqual(_detect_media_mode([folder], "movie"), "tv")

    def test_detects_year_named_movies_added_in_tv_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "Dune (2021).mkv").touch()
            (folder / "The Matrix (1999).mkv").touch()

            self.assertEqual(_detect_media_mode([folder], "tv"), "movie")

    def test_mixed_media_does_not_suggest_switching(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "Alice in Borderland - S01E01.mkv").touch()
            (folder / "Dune (2021).mkv").touch()

            self.assertIsNone(_detect_media_mode([folder], "movie"))

    def test_accepting_prompt_switches_mode_and_saves_it(self):
        with tempfile.TemporaryDirectory() as temp:
            episode = Path(temp) / "Alice in Borderland - S01E01.mkv"
            episode.touch()
            app = App.__new__(App)
            app.media_mode = Mock()
            app.media_mode.get.return_value = "movie"
            app.items = [object()]
            app.clear = Mock()
            app.update_media_mode_selector = Mock()
            app.save_config = Mock()

            with patch(
                "moviq.ui.app.ask_yes_no", return_value=True,
            ) as confirm:
                app._confirm_detected_media_mode([episode])

            app.media_mode.set.assert_called_once_with("tv")
            app.clear.assert_called_once_with(record=False)
            app.update_media_mode_selector.assert_called_once_with()
            app.save_config.assert_called_once_with()
            self.assertIn("Switch to TV Shows", confirm.call_args.args[2])
            self.assertIn("clear the current file list", confirm.call_args.args[2])

    def test_declining_prompt_keeps_current_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            episode = Path(temp) / "Alice in Borderland - S01E01.mkv"
            episode.touch()
            app = App.__new__(App)
            app.media_mode = Mock()
            app.media_mode.get.return_value = "movie"
            app.items = []
            app.update_media_mode_selector = Mock()
            app.save_config = Mock()

            with patch("moviq.ui.app.ask_yes_no", return_value=False):
                app._confirm_detected_media_mode([episode])

            app.media_mode.set.assert_not_called()
            app.update_media_mode_selector.assert_not_called()
            app.save_config.assert_not_called()


class StatusPresentationTests(unittest.TestCase):
    def test_dark_theme_uses_one_background_for_all_surfaces(self):
        dark = PALETTES["dark"]

        self.assertEqual(dark["bg"], DARK_BACKGROUND)
        self.assertEqual(dark["card"], DARK_BACKGROUND)

    def test_status_names_and_icons_match_the_ui_labels(self):
        self.assertEqual(
            STATUS_TEXT,
            {
                "pending": "Pending",
                "matching": "Matching",
                "auto": "Matched",
                "no_change": "No change",
                "review": "Review",
                "manual": "Confirmed",
                "nomatch": "No Match",
                "error": "Error",
                "done": "Done",
            },
        )
        self.assertEqual(
            STATUS_ICONS,
            {
                "pending": "clock",
                "matching": "spinner",
                "auto": "check_circle",
                "no_change": "check_circle",
                "review": "warning",
                "manual": "verified",
                "nomatch": "circle_minus",
                "error": "error",
                "done": "check_circle",
            },
        )
        self.assertEqual(
            set(STATUS_GLYPHS),
            set(STATUS_TEXT),
        )
        self.assertTrue(all(STATUS_GLYPHS.values()))
        self.assertEqual(STATUS_GLYPHS["nomatch"], "\u2296")

    def test_each_theme_has_colors_for_every_status_badge(self):
        for palette in PALETTES.values():
            self.assertEqual(set(palette["status_colors"]), set(STATUS_TEXT))
            self.assertTrue(
                all(len(colors) == 2 for colors in palette["status_colors"].values())
            )

    def test_light_theme_uses_teal_for_matching_and_accent_controls(self):
        light = PALETTES["light"]

        self.assertEqual(light["accent"], "#0f766e")
        self.assertEqual(light["accent_hi"], "#0d9488")
        self.assertEqual(light["status_colors"]["matching"], (
            light["accent"], "#ccfbf1",
        ))

    def test_filter_chips_use_visible_status_colors(self):
        app = App.__new__(App)
        app._status_filter = None
        app._filter_counts = {
            "all": 0, "ready": 0, "review": 0, "error": 0, "done": 0,
        }
        app.filter_control = Mock()
        app.pal = PALETTES["dark"]

        app._refresh_filter_chip_styles()

        app.filter_control.set_labels.assert_called_once_with(
            ("All 0", "Ready 0", "Review 0", "Error 0", "Done 0"),
        )
        table_colors = app.pal["table_chip_colors"]
        app.filter_control.set_segment_colors.assert_called_once_with({
            "ready": (table_colors["ready"][1], table_colors["ready"][0]),
            "review": app.pal["status_colors"]["review"],
            "error": app.pal["status_colors"]["error"],
            "done": (table_colors["done"][1], table_colors["done"][0]),
        })
        app.filter_control.set_selected.assert_called_once_with("all")


class MatchCancellationTests(unittest.TestCase):
    def test_cancel_restores_unfinished_rows_and_keeps_completed_matches(self):
        completed = Item(Path("completed.mkv"), Path("."))
        completed.status = "auto"
        completed.match = {"id": 1}
        unfinished = Item(Path("unfinished.mkv"), Path("."))
        unfinished.status = "matching"
        unfinished.match = {"id": 2}
        unfinished.error = ""
        previous_match = {"id": 3}
        cancellation = threading.Event()
        run = {
            "id": 7,
            "items": [completed, unfinished],
            "originals": {
                completed: ("pending", None, "", None, None),
                unfinished: (
                    "review", previous_match, "previous error", None, None,
                ),
            },
            "completed": {completed},
            "done": 1,
            "total": 2,
            "cancel_event": cancellation,
        }

        class ProgressDialog:
            def __init__(self):
                self.completion = None

            def complete(self, *args, **kwargs):
                self.completion = (args, kwargs)

        dialog = ProgressDialog()
        app = App.__new__(App)
        app.results = queue.Queue()
        app.jobs = queue.Queue()
        app.jobs.put((7, cancellation, unfinished, "key", "en-US", 0.9))
        app._matching_run = run
        app.job_total = 2
        app.job_done = 1
        app.items = [completed, unfinished]
        app.refresh_row = Mock()
        app._record_match_history = Mock()
        app._update_undo_button = Mock()
        app._match_progress_dialog = dialog
        app.status = Mock()

        app.cancel_matching()

        self.assertTrue(cancellation.is_set())
        self.assertEqual(completed.status, "auto")
        self.assertEqual(completed.match, {"id": 1})
        self.assertEqual(unfinished.status, "review")
        self.assertEqual(unfinished.match, previous_match)
        self.assertEqual(unfinished.error, "previous error")
        self.assertTrue(app.jobs.empty())
        self.assertEqual((app.job_total, app.job_done), (0, 0))
        self.assertEqual(
            dialog.completion,
            ((1, 2, 1, 0, 0), {"cancelled": True}),
        )

    def test_invalid_api_key_stops_run_and_restores_unfinished_items(self):
        item = Item(Path("pending.mkv"), Path("."))
        item.status = "matching"
        cancellation = threading.Event()
        run = {
            "id": 9,
            "items": [item],
            "originals": {item: ("pending", None, "", None, None)},
            "completed": set(),
            "done": 0,
            "total": 1,
            "cancel_event": cancellation,
        }
        app = App.__new__(App)
        app.jobs = queue.Queue()
        app.jobs.put((9, cancellation, [item], "key", "en-US", 0.9))
        app._matching_run = run
        app.job_total = app.job_done = 1
        app.items = [item]
        app.refresh_row = Mock()
        app._record_match_history = Mock()
        app._update_undo_button = Mock()
        app.key_var = SimpleNamespace(get=lambda: "")
        app._match_progress_dialog = None
        app.status = Mock()

        with patch("moviq.ui.app.show_error") as show_error:
            app._stop_matching_for_auth(
                run,
                "Invalid TMDB API key. Check the key in Settings.",
            )

        self.assertTrue(cancellation.is_set())
        self.assertIsNone(app._matching_run)
        self.assertEqual((item.status, item.match, item.error), (
            "error", None, "",
        ))
        self.assertTrue(app.jobs.empty())
        show_error.assert_called_once_with(
            app,
            "Invalid TMDB API key",
            "Invalid TMDB API key. Check the key in Settings.",
        )


class MatchCountTests(unittest.TestCase):
    def test_successfully_matched_items_count_even_when_no_rename_is_needed(self):
        correct_named_movie = Item(
            Path("The Matrix (1999).mkv"),
            Path("Movies") / "The Matrix (1999)",
        )
        correct_named_movie.status = "auto"
        already_formatted_movie = Item(
            Path("The Matrix (1999).mkv"),
            Path("Movies") / "The Matrix (1999)",
        )
        already_formatted_movie.status = "no_change"
        confirmed_movie = Item(Path("Confirmed.mkv"), Path("Movies"))
        confirmed_movie.status = "manual"
        needs_review = Item(Path("Review.mkv"), Path("Movies"))
        needs_review.status = "review"
        failed = Item(Path("Failed.mkv"), Path("Shows"))
        failed.status = "review"
        failed.error = "TMDB episode title unavailable"

        self.assertEqual(
            App._match_counts({
                "completed": {
                    correct_named_movie, already_formatted_movie,
                    confirmed_movie, needs_review, failed,
                },
            }),
            (3, 1, 1),
        )

    def test_automatic_match_is_no_change_when_its_destination_is_unchanged(self):
        item = Item(Path("formatted.mkv"), Path("."))
        choice = {"id": 1, "title": "Formatted", "year": 2024}
        run = {
            "id": 1,
            "completed": set(),
            "done": 0,
            "total": 1,
        }
        app = App.__new__(App)
        app.results = queue.Queue()
        app.results.put(("finished", 1, item, "auto", choice, ""))
        app._matching_run = run
        app.items = [item]
        app.refresh_row = lambda current: App._update_no_change_status(
            app, current,
        )
        app._update_match_progress = Mock()
        app._finish_match_run = Mock()
        app.plan_for = Mock(return_value=SimpleNamespace(dest=item.path))
        app.thr_var = SimpleNamespace(get=lambda: 90)
        app.key_var = SimpleNamespace(get=lambda: "")

        app._process_match_events()

        self.assertEqual(item.status, "no_change")
        self.assertEqual(item.match, choice)

    def test_low_confidence_auto_result_is_left_unmatched_for_review(self):
        item = Item(
            Path("The.Movie [Directors Cut] 2019.mkv"), Path("Movies"),
        )
        match = {
            "id": 1,
            "title": "My Hero Academia: Heroes Rising",
            "year": 2019,
        }
        app = App.__new__(App)
        app.results = queue.Queue()
        app.results.put(("finished", 1, item, "auto", match, ""))
        app._matching_run = {
            "id": 1,
            "completed": set(),
            "total": 2,
        }
        app.items = [item]
        app.thr_var = SimpleNamespace(get=lambda: 90)
        app.key_var = SimpleNamespace(get=lambda: "")
        app.refresh_row = Mock()
        app._update_match_progress = Mock()
        app._finish_match_run = Mock()
        app._is_already_formatted = Mock(return_value=False)

        app._process_match_events()

        self.assertEqual(item.status, "review")
        self.assertIsNone(item.match)


class MatchProgressTextTests(unittest.TestCase):
    def test_long_current_filename_is_truncated_to_one_line(self):
        popup = MatchProgressPopup.__new__(MatchProgressPopup)
        popup.completed = False
        popup.current_font = SimpleNamespace(
            measure=lambda text: len(text) * 10,
        )
        popup.current_var = Mock()
        popup.percent_var = Mock()
        popup.counter_vars = [Mock(), Mock(), Mock()]
        popup._set_progress = Mock()

        popup.update_progress("A" * 80 + ".mkv", 0, 1, 0, 0, 0)

        current_text = popup.current_var.set.call_args.args[0]
        self.assertTrue(current_text.startswith("Matching file 1 of 1: "))
        self.assertTrue(current_text.endswith("..."))
        self.assertLessEqual(popup.current_font.measure(current_text), 440)


class SelectionActionTests(unittest.TestCase):
    def test_action_labels_distinguish_subset_from_all_selection(self):
        for selected, expected_match, expected_rename in (
            (0, "Auto Match", "Rename All"),
            (3, "Auto Match (3)", "Rename (3)"),
            (8, "Auto Match", "Rename All"),
        ):
            with self.subTest(selected=selected):
                app = App.__new__(App)
                app.items = [
                    SimpleNamespace(
                        iid=index, status="auto", match={"id": index},
                    )
                    for index in range(8)
                ]
                app.by_iid = {item.iid: item for item in app.items}
                app.tree = SimpleNamespace(
                    selection=lambda: tuple(range(selected)),
                )
                app.match_btn_var = Mock()
                app.manual_match_btn_var = Mock()
                app.apply_btn_var = Mock()

                app.update_action_labels()

                app.match_btn_var.set.assert_called_once_with(expected_match)
                app.manual_match_btn_var.set.assert_called_once_with(
                    "Manual Match" if selected != 3 else "Manual Match (3)",
                )
                app.apply_btn_var.set.assert_called_once_with(expected_rename)

    def test_toolbar_statistics_are_recomputed_from_file_statuses(self):
        app = App.__new__(App)
        app._sum_pending = True
        app.items = [
            SimpleNamespace(status="auto"),
            SimpleNamespace(status="review"),
            SimpleNamespace(status="error"),
            SimpleNamespace(status="done"),
            SimpleNamespace(status="review", error="network failure"),
        ]
        app.tree = SimpleNamespace(selection=lambda: ())
        app.filter_vars = {
            key: Mock()
            for key in (
                "all", "ready", "review", "error", "done",
            )
        }
        app.update_action_labels = Mock()
        app.update_detail = Mock()
        app._schedule_status_cells = Mock()
        app._matches_current_filter = Mock(return_value=True)
        app._status_filter = None
        app.empty_state = Mock()

        app.update_summary()

        app.filter_vars["all"].set.assert_called_once_with("All 5")
        app.filter_vars["ready"].set.assert_called_once_with("Ready 1")
        app.filter_vars["review"].set.assert_called_once_with("Review 1")
        app.filter_vars["error"].set.assert_called_once_with("Error 2")
        app.filter_vars["done"].set.assert_called_once_with("Done 1")
        app.empty_state.place_forget.assert_called_once_with()

    def test_filter_categories_match_their_file_statuses(self):
        for status, selected_filter, expected in (
            ("auto", "ready", True),
            ("no_change", "ready", False),
            ("manual", "ready", True),
            ("pending", "review", False),
            ("review", "review", True),
            ("error", "review", False),
            ("error", "error", True),
            ("review_error", "review", False),
            ("review_error", "error", True),
            ("nomatch", "nomatch", True),
            ("nomatch", "review", False),
            ("nomatch_error", "nomatch", False),
            ("nomatch_error", "error", True),
            ("auto_error", "ready", False),
            ("done", "done", True),
            ("no_change", "done", True),
            ("done_error", "done", False),
            ("done", "ready", False),
        ):
            with self.subTest(status=status, filter=selected_filter):
                status_value, error = {
                    "review_error": ("review", "provider error"),
                    "nomatch_error": ("nomatch", "provider error"),
                    "auto_error": ("auto", "provider error"),
                    "done_error": ("done", "provider error"),
                }.get(status, (status, ""))
                self.assertEqual(
                    App._matches_status_filter(
                        SimpleNamespace(
                            status=status_value, error=error,
                        ),
                        selected_filter,
                    ),
                    expected,
                )

    def test_rename_all_skips_no_change_items(self):
        item = Item(Path("The Matrix (1999).mkv"), Path("Movies"))
        item.status = "no_change"
        item.match = {"id": 1, "title": "The Matrix", "year": 1999}

        app = App.__new__(App)
        app.selected = lambda: [item]
        app.items = [item]
        app.tree = SimpleNamespace(selection=lambda: ["row"])
        app._prune_missing_items = Mock(return_value=([], []))
        app.confirm_apply = SimpleNamespace(get=lambda: False)
        app.status = Mock()
        app.refresh_row = Mock()
        app.refresh_all = Mock()
        app._vcount = {}
        app.show_page = Mock()
        app.plan_for = Mock()
        app.by_iid = {}
        app._filtered_out = set()
        app.undo_stack = []

        with patch("moviq.ui.app.show_info") as show_info:
            app.apply(all_matched=False)

        self.assertEqual(item.status, "no_change")
        app.status.assert_called_once_with("Nothing to rename")
        show_info.assert_not_called()

    def test_auto_match_uses_only_selected_rows(self):
        selected = SimpleNamespace(status="pending")
        unselected = SimpleNamespace(status="pending")
        app = App.__new__(App)
        app.selected = lambda: [selected]
        app.items = [selected, unselected]
        app.start_match = Mock()

        app.match_run()

        app.start_match.assert_called_once_with([selected], show_dialog=True)

    def test_auto_match_uses_all_eligible_rows_when_selection_is_empty(self):
        pending = SimpleNamespace(status="pending")
        done = SimpleNamespace(status="done")
        confirmed = SimpleNamespace(status="manual")
        formatted = SimpleNamespace(status="no_change")
        auto = SimpleNamespace(status="auto")
        app = App.__new__(App)
        app.selected = lambda: []
        app._status_filter = None
        app.items = [pending, done, confirmed, formatted, auto]
        app.start_match = Mock()

        app.match_run()

        app.start_match.assert_called_once_with(
            [pending, done, confirmed, formatted, auto],
            show_dialog=True,
        )

    def test_auto_match_skips_already_ready_rows(self):
        pending = SimpleNamespace(
            status="pending", media_type="movie", is_tv_episode=False,
            error_code=None,
        )
        matched = SimpleNamespace(
            status="auto", media_type="movie", is_tv_episode=False,
            error_code=None,
        )
        formatted = SimpleNamespace(
            status="no_change", media_type="movie", is_tv_episode=False,
            error_code=None,
        )
        confirmed = SimpleNamespace(
            status="manual", media_type="movie", is_tv_episode=False,
            error_code=None,
        )
        app = App.__new__(App)
        app.selected = lambda: [pending, matched, formatted, confirmed]
        app.items = [pending, matched, formatted, confirmed]
        app.start_match = Mock()

        app.match_run()

        app.start_match.assert_called_once_with(
            [pending, matched, formatted, confirmed],
            show_dialog=True,
        )
        self.assertEqual(
            [
                item for item in app.items
                if App._auto_match_eligible(item)
            ],
            [pending],
        )

    def test_manual_match_uses_only_selected_rows(self):
        selected = SimpleNamespace(
            status="pending", media_type="movie", is_tv_episode=False,
        )
        unselected = SimpleNamespace(
            status="pending", media_type="movie", is_tv_episode=False,
        )
        app = App.__new__(App)
        app._manual_match_active = False
        app.job_total = 0
        selected.iid = "selected"
        unselected.iid = "unselected"
        app.items = [selected, unselected]
        app.by_iid = {"selected": selected, "unselected": unselected}
        app._filtered_out = set()
        app._status_filter = None
        app.tree = SimpleNamespace(
            selection=lambda: ("selected",),
            exists=lambda iid: iid in app.by_iid,
        )
        app._start_manual_matches = Mock()

        app.match_manually()

        app._start_manual_matches.assert_called_once_with(
            [selected], renamed_skipped=0, queue_source="selected",
            view_count=2,
        )

    def test_manual_match_allows_a_confirmed_row_to_be_rematched(self):
        confirmed = SimpleNamespace(
            status="manual", media_type="movie", is_tv_episode=False,
        )
        app = App.__new__(App)
        app._manual_match_active = False
        app.job_total = 0
        confirmed.iid = "confirmed"
        app.items = [confirmed]
        app.by_iid = {"confirmed": confirmed}
        app._filtered_out = set()
        app._status_filter = None
        app.tree = SimpleNamespace(
            selection=lambda: ("confirmed",),
            exists=lambda iid: iid in app.by_iid,
        )
        app._start_manual_matches = Mock()

        app.match_manually()

        app._start_manual_matches.assert_called_once_with(
            [confirmed], renamed_skipped=0, queue_source="selected",
            view_count=1,
        )

    def test_confirmed_row_match_cell_opens_match_flow(self):
        confirmed = SimpleNamespace(status="manual")

        class Tree:
            def identify_region(self, _x, _y):
                return "cell"

            def identify_row(self, _y):
                return "movie-row"

            def bbox(self, _iid, _column):
                return (0, 0, 100, 20)

            def focus_set(self):
                pass

            def selection_set(self, _iid):
                pass

        app = App.__new__(App)
        app.tree = Tree()
        app.by_iid = {"movie-row": confirmed}
        app._is_manual_match_cell = (
            lambda iid, x: App._is_manual_match_cell(app, iid, x)
        )
        app.fix_selected = Mock()

        app.on_click(SimpleNamespace(x=5, y=5, state=0))

        app.fix_selected.assert_called_once_with()

    def test_set_match_replaces_a_confirmed_match(self):
        old_match = {"id": 1, "title": "Old movie"}
        new_match = {"id": 2, "title": "New movie"}
        item = SimpleNamespace(
            path=Path("movie.mkv"),
            match=old_match,
            status="manual",
            error="",
            match_source="manual",
            error_code=None,
            review_reason=None,
        )
        app = App.__new__(App)
        app.refresh_row = Mock()
        app._restore_visible_row_order = Mock()

        app.set_match(item, new_match, "manual", source="manual")

        self.assertIs(item.match, new_match)
        self.assertEqual(item.status, "manual")
        app.refresh_row.assert_called_once_with(item)


class MatchVisibilityTests(unittest.TestCase):
    def test_review_count_toggle_filters_and_restores_reviewable_files(self):
        items = [
            SimpleNamespace(iid="ready", status="auto"),
            SimpleNamespace(iid="review", status="review"),
            SimpleNamespace(iid="pending", status="pending"),
        ]

        class Tree:
            def __init__(self):
                self.rows = [item.iid for item in items]
                self.nodes = set(self.rows)

            def exists(self, iid):
                return iid in self.nodes

            def selection(self):
                return ()

            def selection_remove(self, _selection):
                pass

            def detach(self, iid):
                self.rows.remove(iid)

            def reattach(self, iid, _parent, _index):
                self.rows.append(iid)

            def move(self, iid, _parent, _index):
                if iid in self.rows:
                    self.rows.remove(iid)
                    self.rows.append(iid)

        app = App.__new__(App)
        app.items = items
        app.tree = Tree()
        app._filtered_out = set()
        app._review_filter = False
        app._status_filter = None
        app.deselect = Mock()
        app._restore_visible_row_order = Mock()
        app._schedule_status_cells = Mock()
        app.update_summary = Mock()

        app.toggle_review_filter()

        self.assertTrue(app._review_filter)
        self.assertEqual(app.tree.rows, ["review"])
        self.assertEqual(app._filtered_out, {"ready", "pending"})

        app.toggle_review_filter()

        self.assertFalse(app._review_filter)
        self.assertEqual(set(app.tree.rows), {"ready", "review", "pending"})
        self.assertEqual(app._filtered_out, set())

    def test_starting_matching_restores_all_rows_in_original_order(self):
        items = [
            SimpleNamespace(
                iid="first", query="first", orig_path=Path("first"),
            ),
            SimpleNamespace(
                iid="second", query="second", orig_path=Path("second"),
            ),
            SimpleNamespace(
                iid="third", query="third", orig_path=Path("third"),
            ),
        ]

        class Tree:
            def __init__(self):
                self.rows = ["first", "third"]
                self.nodes = {"first", "second", "third"}

            def exists(self, iid):
                return iid in self.nodes

            def reattach(self, iid, parent, index):
                if parent:
                    raise AssertionError("Unexpected tree parent")
                if index != "end":
                    raise AssertionError("Unexpected tree index")
                self.rows.append(iid)

            def move(self, iid, parent, index):
                if parent:
                    raise AssertionError("Unexpected tree parent")
                self.rows.remove(iid)
                self.rows.insert(index, iid)

        app = App.__new__(App)
        app.items = items
        app.tree = Tree()
        app.media_mode = SimpleNamespace(get=lambda: "movie")
        app._review_filter = True
        app._filtered_out = {"second"}
        app._refresh_pending_label_style = lambda: None
        app.update_summary = lambda: None
        app._sync_row_visibility = lambda item: App._sync_row_visibility(
            app, item,
        )

        app._clear_review_filter()

        self.assertFalse(app._review_filter)
        self.assertEqual(app._filtered_out, set())
        self.assertEqual(app.tree.rows, ["first", "second", "third"])


class ReviewFlaggedTests(unittest.TestCase):
    def test_review_flagged_starts_manual_flow_only_for_review_rows(self):
        review_item = SimpleNamespace(
            iid="review", status="review", media_type="movie",
            is_tv_episode=False,
        )
        failed_item = SimpleNamespace(
            iid="failed", status="error", media_type="movie",
            is_tv_episode=False,
        )
        pending_item = SimpleNamespace(
            iid="pending", status="pending", media_type="movie",
            is_tv_episode=False,
        )

        class Tree:
            def __init__(self):
                self.rows = {"review", "failed", "pending"}
                self.focused = None

            def exists(self, iid):
                return iid in self.rows

            def see(self, iid):
                self.seen = iid

            def selection_set(self, iid):
                self.selected = iid

            def focus(self, iid):
                self.focused = iid

            def move(self, _iid, _parent, _index):
                pass

        app = App.__new__(App)
        app.items = [review_item, failed_item, pending_item]
        app.tree = Tree()
        app._review_filter = False
        app._status_filter = None
        app._filtered_out = set()
        app._restore_visible_row_order = Mock()
        app._schedule_status_cells = Mock()
        app.deselect = Mock()
        app._sync_row_visibility = Mock()
        app.update_summary = Mock()
        app.on_select = Mock()
        app._start_manual_matches = Mock()

        app.show_flagged_matches()

        self.assertTrue(app._review_filter)
        self.assertEqual(app.tree.seen, "review")
        self.assertEqual(app.tree.selected, "review")
        app._start_manual_matches.assert_called_once_with([review_item])


class ErrorInspectionTests(unittest.TestCase):
    def test_error_detail_includes_full_path_and_explorer_hint(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "New folder" / "3.mkv"
            item = Item(path, path.parent, "tv")
            item.error = "TMDB lookup failed"
            app = App.__new__(App)
            app.selected = lambda: [item]
            app.detail_var = Mock()

            app.update_detail()

            detail = app.detail_var.set.call_args.args[0]
            self.assertIn("TMDB lookup failed", detail)
            self.assertIn(str(path.absolute()), detail)
            self.assertIn("Double-click", detail)

    def test_open_selected_file_uses_the_selected_source_path(self):
        path = Path("Library") / "Secret Invasion" / "New folder" / "3.mkv"
        app = App.__new__(App)
        app.selected = lambda: [SimpleNamespace(path=path)]

        with patch("moviq.ui.app._show_in_file_manager") as show_file:
            app.open_selected_file()

        show_file.assert_called_once_with(path)

    def test_double_clicking_an_error_row_opens_its_file_location(self):
        item = SimpleNamespace(error="TMDB lookup failed")

        class Tree:
            def identify_region(self, _x, _y):
                return "cell"

            def identify_row(self, _y):
                return "failed"

            def selection_set(self, iid):
                self.selected = iid

        app = App.__new__(App)
        app.tree = Tree()
        app.by_iid = {"failed": item}
        app.open_selected_file = Mock()
        app.fix_selected = Mock()

        app.on_double(SimpleNamespace(x=1, y=1))

        self.assertEqual(app.tree.selected, "failed")
        app.open_selected_file.assert_called_once_with()
        app.fix_selected.assert_not_called()


class ProviderRowInteractionTests(unittest.TestCase):
    def test_edited_key_debounces_automatic_check(self):
        view = SettingsView.__new__(SettingsView)
        settings = Mock()
        settings.enabled = ("omdb",)
        settings.with_key.return_value = settings
        view.app = SimpleNamespace(
            provider_settings=settings,
            set_provider_settings=Mock(),
        )
        view._provider_key_vars = {
            "omdb": Mock(get=Mock(return_value="updated-key")),
        }
        view._provider_status = {}
        view._provider_key_check_after = {"omdb": "old-timer"}
        view._provider_test_generation = {"omdb": 4}
        view._set_provider_status = Mock()
        view.after = Mock(return_value="new-timer")
        view.after_cancel = Mock()
        view._test_provider = Mock()

        view._provider_key_edited("omdb")

        view.after_cancel.assert_called_once_with("old-timer")
        self.assertEqual(view._provider_test_generation["omdb"], 5)
        self.assertEqual(view._provider_key_check_after["omdb"], "new-timer")
        self.assertEqual(view.after.call_args.args[0], 800)
        view.after.call_args.args[1]()
        view._test_provider.assert_called_once_with("omdb")

    def test_disabling_provider_sets_disabled_status(self):
        settings = Mock()
        settings.enabled = ("omdb",)
        settings.with_enabled.return_value = "disabled-settings"
        view = SimpleNamespace(
            app=SimpleNamespace(
                provider_settings=settings,
                set_provider_settings=Mock(),
            ),
            _provider_enabled={"omdb": Mock(get=Mock(return_value=False))},
            _provider_test_generation={"omdb": 3},
            _set_provider_status=Mock(),
        )

        SettingsView._provider_toggled(view, "omdb")

        view.app.set_provider_settings.assert_called_once_with(
            "disabled-settings",
        )
        self.assertEqual(view._provider_test_generation["omdb"], 4)
        view._set_provider_status.assert_called_once_with("omdb", "Disabled")

    def test_manual_test_does_not_connect_disabled_provider(self):
        view = SettingsView.__new__(SettingsView)
        view.app = SimpleNamespace(
            provider_settings=SimpleNamespace(enabled=()),
        )
        view._set_provider_status = Mock()
        view._provider_test_job = Mock()
        view._executor = Mock()

        SettingsView._test_provider(view, "omdb")

        view._set_provider_status.assert_called_once_with("omdb", "Disabled")
        view._executor.submit.assert_not_called()

    def test_drag_reorders_live_and_saves_when_released(self):
        view = SettingsView.__new__(SettingsView)
        view._provider_order = [
            "tmdb", "tvmaze", "anilist", "omdb", "offline",
        ]
        view._provider_drag_name = "tvmaze"
        view._provider_drag_original = tuple(view._provider_order)
        view._provider_blocks = {
            name: SimpleNamespace(
                winfo_rooty=lambda y=y: y,
                winfo_height=lambda: 20,
            )
            for name, y in (
                ("tmdb", 0), ("tvmaze", 25), ("anilist", 50),
                ("omdb", 75), ("offline", 100),
            )
        }
        view.update_idletasks = Mock()
        view._layout_providers = Mock()
        view.app = SimpleNamespace(
            provider_settings=Mock(),
            set_provider_settings=Mock(),
        )
        view.app.provider_settings.with_order.return_value = "updated-settings"

        view._provider_drag_motion("tvmaze", SimpleNamespace(y_root=66))

        self.assertEqual(
            view._provider_order,
            ["tmdb", "anilist", "tvmaze", "omdb", "offline"],
        )
        self.assertEqual(view._provider_blocks["offline"].winfo_rooty(), 100)
        view._provider_drag_end("tvmaze", None)
        view.app.provider_settings.with_order.assert_called_once_with(
            ["tmdb", "anilist", "tvmaze", "omdb", "offline"],
        )
        view.app.set_provider_settings.assert_called_once_with(
            "updated-settings",
        )

    def test_offline_provider_cannot_start_drag(self):
        view = SettingsView.__new__(SettingsView)
        view._provider_drag_name = None

        result = view._provider_drag_start("offline", None)

        self.assertEqual(result, "break")
        self.assertIsNone(view._provider_drag_name)


if __name__ == "__main__":
    unittest.main()
