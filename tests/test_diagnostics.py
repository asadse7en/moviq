import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from moviq.diagnostics import (
    _report_item,
    configure_auto_export,
    export_app_report,
)
from moviq.ui.app import App


def _app():
    return SimpleNamespace(
        settings_store=SimpleNamespace(data={}),
        provider_settings=SimpleNamespace(
            selected="automatic",
            keys=(("omdb", ""),),
        ),
        key_var=SimpleNamespace(get=lambda: ""),
        media_mode=SimpleNamespace(get=lambda: "movie"),
        _diagnostic_provider_used={},
    )


def _item(path, root, status="review", error=""):
    return SimpleNamespace(
        path=Path(path),
        root=Path(root),
        status=status,
        error=error,
        media_type="movie",
        query="Example",
        year=2020,
        season_number=None,
        episode_number=None,
        episode_end_number=None,
        match=None,
        review_match={"id": 42, "title": "Example"},
        review_reason="similar",
    )


class DiagnosticsTests(unittest.TestCase):
    def test_unready_report_contains_scan_and_trace_details(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "Show" / "Season 02"
            folder.mkdir(parents=True)
            video = folder / "Example.2020.mkv"
            video.touch()
            (folder / "02.mkv").touch()
            trace = {
                "fail_reason": "similar",
                "stages": [{"stage": "parsed_query", "value": {"query": "Example"}}],
                "top_candidates": [{"title": "Example", "id": 42}],
                "chosen_id": 42,
                "result": ["review", {"id": 42}, "", "similar"],
            }
            app = _app()
            app.settings_store.data = {"providers": {"selected": "automatic"}}
            app._diagnostic_provider_used[str(video)] = "tmdb"

            report = _report_item(
                app, _item(video, root), trace, include_all=False,
            )

        self.assertEqual(report["raw_name"], str(video))
        self.assertEqual(
            report["relative_path"],
            os.path.join("Show", "Season 02", video.name),
        )
        self.assertEqual(report["folder_chain"], ["Show", "Season 02"])
        self.assertEqual(report["siblings"], ["02.mkv", video.name])
        self.assertEqual(report["settings"]["provider_used"], "tmdb")
        self.assertIs(report["settings"]["has_api_key"], False)
        self.assertEqual(report["status"], "review")
        self.assertEqual(report["fail_reason"], "similar")
        self.assertEqual(report["stages"], trace["stages"])
        self.assertEqual(report["top_candidates"], trace["top_candidates"])
        self.assertEqual(report["chosen_id"], 42)
        self.assertEqual(report["result"], trace["result"])
        self.assertEqual(report["expected"], {
            "title": None,
            "media_type": None,
            "year": None,
            "season": None,
            "episode": None,
            "provider_id": None,
        })

    def test_ready_statuses_are_omitted_or_marked_ok(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "Example.mkv"
            video.touch()
            item = _item(video, root, status="auto")
            app = _app()
            omitted = _report_item(app, item, {}, include_all=False)
            all_items = _report_item(app, item, {}, include_all=True)

        self.assertIsNone(omitted)
        self.assertEqual(all_items["status"], "matched")
        self.assertNotIn("ok", all_items)

    def test_no_change_is_excluded_and_marked_ok_in_all_export(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "Example.mkv"
            video.touch()
            app = _app()
            item = _item(video, root, status="no_change")
            omitted = _report_item(app, item, {}, include_all=False)
            included = _report_item(app, item, {}, include_all=True)

        self.assertIsNone(omitted)
        self.assertEqual(included["status"], "no_change")
        self.assertNotIn("ok", included)

    def test_trace_capture_is_in_memory_and_restored(self):
        from moviq import trace

        prior_trace_end = trace.trace_end
        prior_records = trace._RECORDS
        prior_initialized = trace._INITIALIZED
        previous_setting = os.environ.get("PARSER_TRACE")
        with patch(
            "moviq.diagnostics.export_app_report",
            return_value=(1, Path("unready_report.json")),
        ) as writer:
            export_after_match, cleanup = configure_auto_export()
            try:
                trace.trace_start("example.mkv")
                trace.trace_stage(
                    "example.mkv", "parsed_query", {"query": "Example"},
                )
                trace.trace_end(
                    "example.mkv", ("review", None, "", "similar"),
                )
                export_after_match(_app())
                captured = writer.call_args[0][1]
                self.assertEqual(
                    captured["example.mkv"]["stages"],
                    [{
                        "stage": "parsed_query",
                        "value": {"query": "Example"},
                    }],
                )
                self.assertEqual(
                    captured["example.mkv"]["fail_reason"], "similar",
                )
            finally:
                cleanup()

        self.assertIs(trace.trace_end, prior_trace_end)
        self.assertIs(trace._RECORDS, prior_records)
        self.assertEqual(trace._INITIALIZED, prior_initialized)
        self.assertEqual(os.environ.get("PARSER_TRACE"), previous_setting)

    def test_test_flag_opens_the_app_and_configures_auto_export(self):
        from moviq.__main__ import main

        root = Mock()
        exporter = Mock()
        cleanup = Mock()
        app = Mock()
        with ExitStack() as stack:
            stack.enter_context(patch(
                "moviq.diagnostics.configure_auto_export",
                return_value=(exporter, cleanup),
            ))
            stack.enter_context(patch("moviq.ui.app.App", return_value=app))
            stack.enter_context(patch("moviq.ui.app.HAVE_DND", False))
            stack.enter_context(patch("moviq.__main__.tk.Tk", return_value=root))
            stack.enter_context(patch(
                "moviq.__main__.tk.PhotoImage",
                side_effect=tk.TclError("no image"),
            ))
            main(["--test"])

        self.assertIs(app._diagnostic_export_after_match, exporter)
        root.mainloop.assert_called_once_with()
        cleanup.assert_called_once_with()

    def test_match_completion_triggers_the_configured_export(self):
        app = App.__new__(App)
        run = {
            "completed": set(),
            "done": 0,
            "total": 0,
            "show_dialog": False,
            "manual_kept": 0,
        }
        app._matching_run = run
        app.status = Mock()
        app._record_match_history = Mock()
        app._discard_match_cache = Mock()
        app.job_total = app.job_done = 0
        app._update_undo_button = Mock()
        exporter = Mock()
        app._diagnostic_export_after_match = exporter

        app._finish_match_run(run)

        exporter.assert_called_once_with(app)

    def test_export_writes_the_project_report_with_json_format(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "Example.mkv"
            video.touch()
            app = _app()
            app.items = [_item(video, root)]
            with patch.object(Path, "write_text", autospec=True) as write_text:
                count, output_path = export_app_report(app, {})

        self.assertEqual(count, 1)
        self.assertEqual(
            output_path,
            Path(__file__).resolve().parents[1] / "unready_report.json",
        )
        self.assertEqual(write_text.call_args.args[0], output_path)
        json_text, = write_text.call_args.args[1:]
        self.assertEqual(write_text.call_args.kwargs, {"encoding": "utf-8"})
        self.assertEqual(len(json_text.splitlines()), 1)
        decoded = json.loads(json_text)
        self.assertEqual(decoded["status"], "review")
        self.assertEqual(
            set(decoded),
            {
                "raw_name", "relative_path", "scan_root", "folder_chain",
                "siblings", "settings", "status", "fail_reason", "stages",
                "parsed", "top_candidates", "chosen_id", "result", "expected",
            },
        )
        self.assertNotIn("\n", json_text.rstrip("\n"))
        self.assertIsInstance(
            decoded["settings"]["has_api_key"], bool,
        )


if __name__ == "__main__":
    unittest.main()
