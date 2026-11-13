import os
import queue
import threading
import unittest
from unittest.mock import Mock, patch

from moviq.ui.app import App, MAX_WORKERS


class MatchWorkerHealthTests(unittest.TestCase):
    def test_restarts_exited_workers_and_keeps_active_workers(self):
        stopped = Mock()
        stopped.is_alive.return_value = False
        active = Mock()
        active.is_alive.return_value = True
        app = App.__new__(App)
        app._worker_threads = [stopped, active]
        app._match_worker_counter = 0
        app._workers_closing = False
        app._worker_pool = Mock()
        app._run_match_worker = Mock()
        started = []

        def make_thread(**kwargs):
            thread = Mock()
            thread.is_alive.return_value = True
            started.append((thread, kwargs))
            return thread

        with patch("moviq.ui.app.logging.getLogger") as get_logger:
            with patch(
                "moviq.ui.app.threading.Thread", side_effect=make_thread,
            ):
                App._ensure_match_workers(app)

        self.assertIn(active, app._worker_threads)
        self.assertEqual(len(app._worker_threads), MAX_WORKERS)
        self.assertEqual(len(started), MAX_WORKERS - 1)
        self.assertEqual(
            [kwargs["name"] for _thread, kwargs in started],
            [f"moviq-match-{index}" for index in range(1, MAX_WORKERS)],
        )
        self.assertTrue(all(kwargs["daemon"] for _thread, kwargs in started))
        app._worker_pool.submit.assert_not_called()
        get_logger.return_value.warning.assert_called_once()

    def test_does_not_restart_workers_during_shutdown(self):
        stopped = Mock()
        stopped.is_alive.return_value = False
        app = App.__new__(App)
        app._worker_threads = [stopped]
        app._workers_closing = True
        app._worker_pool = Mock()

        with patch("moviq.ui.app.threading.Thread") as thread:
            App._ensure_match_workers(app)

        thread.assert_not_called()
        app._worker_pool.submit.assert_not_called()
        self.assertEqual(app._worker_threads, [])

    def test_restarts_exited_worker_on_a_new_named_daemon_thread(self):
        stopped = Mock()
        stopped.is_alive.return_value = False
        app = App.__new__(App)
        app._worker_threads = [stopped]
        app._match_worker_counter = 3
        app._workers_closing = False
        app._worker_pool = Mock()
        app._run_match_worker = Mock()
        started = []

        def make_thread(**kwargs):
            thread = Mock()
            thread.is_alive.return_value = True
            started.append(kwargs)
            return thread

        with patch("moviq.ui.app.logging.getLogger") as get_logger:
            with patch(
                "moviq.ui.app.threading.Thread", side_effect=make_thread,
            ):
                App._ensure_match_workers(app)

        self.assertEqual(len(app._worker_threads), MAX_WORKERS)
        self.assertEqual(started[0]["name"], "moviq-match-4")
        self.assertTrue(started[0]["daemon"])
        get_logger.return_value.warning.assert_called_once()

    def test_poll_continues_after_progress_processing_error(self):
        app = App.__new__(App)
        app.root = Mock()
        app.results = Mock()
        app.results.empty.return_value = True
        app.network_events = queue.Queue()
        app._ensure_match_workers = Mock()
        app._process_match_events = Mock(
            side_effect=RuntimeError("bad progress event"),
        )

        with patch("moviq.ui.app.logging.getLogger") as get_logger:
            App.poll(app)

        app.root.after.assert_called_once_with(100, app.poll)
        get_logger.return_value.exception.assert_called_once_with(
            "Could not process matching progress events.",
        )


class RenameShutdownTests(unittest.TestCase):
    @staticmethod
    def _app(rename_thread):
        app = App.__new__(App)
        app._rename_running = True
        app._rename_thread = rename_thread
        app._rename_cancel_event = threading.Event()
        app._network_monitor = Mock()
        app._hide_tree_tooltip = Mock()
        app._workers_closing = False
        app._search_after = None
        app._system_theme_after = None
        app._matching_run = None
        app.jobs = queue.Queue()
        app._worker_pool = Mock()
        app._file_log_handler = Mock()
        app.save_config = Mock()
        app.root = Mock()
        app._theme_listener = Mock()
        return app

    def test_running_rename_requires_confirmation_before_close(self):
        rename_thread = Mock()
        rename_thread.is_alive.return_value = True
        app = self._app(rename_thread)

        with patch(
            "moviq.ui.app.ask_yes_no", return_value=False,
        ) as ask_yes_no:
            App.on_close(app)

        ask_yes_no.assert_called_once_with(
            app, "Rename in progress",
            "Moviq is still renaming files. Quit anyway?",
        )
        self.assertFalse(app._rename_cancel_event.is_set())
        app.root.destroy.assert_not_called()

    def test_confirmed_close_cancels_running_rename(self):
        rename_thread = Mock()
        rename_thread.is_alive.return_value = True
        app = self._app(rename_thread)

        with patch(
            "moviq.ui.app.ask_yes_no", return_value=True,
        ), patch("moviq.ui.app.unregister_theme_listener"), patch(
            "moviq.ui.app.close_cache",
        ), patch(
            "moviq.ui.app.threading.Timer",
        ) as timer_factory:
            App.on_close(app)

        self.assertTrue(app._rename_cancel_event.is_set())
        rename_thread.join.assert_called_once_with(timeout=10)
        app.root.destroy.assert_called_once_with()
        timer_factory.assert_called_once_with(
            1.5, os._exit, args=(0,),
        )
        timer_factory.return_value.start.assert_called_once_with()

    def test_finished_rename_does_not_prompt_before_close(self):
        rename_thread = Mock()
        rename_thread.is_alive.return_value = False
        app = self._app(rename_thread)

        with patch(
            "moviq.ui.app.ask_yes_no",
        ) as ask_yes_no, patch(
            "moviq.ui.app.unregister_theme_listener",
        ), patch("moviq.ui.app.close_cache"), patch(
            "moviq.ui.app.threading.Timer",
        ) as timer_factory:
            App.on_close(app)

        ask_yes_no.assert_not_called()
        self.assertFalse(app._rename_running)
        app.root.destroy.assert_called_once_with()
        timer_factory.return_value.start.assert_called_once_with()


class RenameCancellationTests(unittest.TestCase):
    def test_cancel_before_worker_starts_resets_running_state(self):
        app = App.__new__(App)
        app._rename_cancel_event = threading.Event()
        app._rename_thread = None
        app._rename_running = True
        app._rename_progress = Mock()
        app._update_undo_button = Mock()

        should_close = App._cancel_rename(app)

        self.assertTrue(should_close)
        self.assertTrue(app._rename_cancel_event.is_set())
        self.assertFalse(app._rename_running)
        self.assertIsNone(app._rename_thread)
        self.assertIsNone(app._rename_progress)
        app._update_undo_button.assert_called_once_with()

    def test_cancel_during_worker_leaves_rename_running(self):
        app = App.__new__(App)
        app._rename_cancel_event = threading.Event()
        app._rename_thread = Mock()
        app._rename_thread.is_alive.return_value = True
        app._rename_running = True
        app._update_undo_button = Mock()

        should_close = App._cancel_rename(app)

        self.assertFalse(should_close)
        self.assertTrue(app._rename_cancel_event.is_set())
        self.assertTrue(app._rename_running)
        app._update_undo_button.assert_not_called()

    def test_rename_preparation_runs_before_service_execution(self):
        app = App.__new__(App)
        app._rename_cancel_event = threading.Event()
        app._rename_events = queue.Queue()
        app.items = []
        order = []

        def build_ops(*args):
            order.append("prepare")
            return []

        app._build_rename_ops = Mock(side_effect=build_ops)
        journal = Mock()
        journal.start.side_effect = lambda _ops: order.append("journal")

        with patch("moviq.ui.app.UndoJournal", return_value=journal), patch(
            "moviq.ui.app.RenameService",
        ) as service_class:
            service_class.return_value.run.side_effect = (
                lambda: order.append("run")
            )
            App._run_rename(app, [], {}, False, 0, 0, 0)

        self.assertEqual(order, ["prepare", "journal", "run"])
        self.assertIs(app._undo_journal, journal)


if __name__ == "__main__":
    unittest.main()
