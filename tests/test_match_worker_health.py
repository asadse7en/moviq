import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch

from moviq.ui.app import App, MAX_WORKERS


class MatchWorkerHealthTests(unittest.TestCase):
    def test_restarts_exited_workers_and_keeps_active_workers(self):
        failed = Future()
        failed.set_exception(RuntimeError("worker crashed"))
        active = Future()
        pool = Mock()
        pool.submit.side_effect = lambda _worker: Future()
        app = App.__new__(App)
        app._worker_futures = [failed, active]
        app._workers_closing = False
        app._worker_pool = pool
        app.worker = Mock()

        with patch("moviq.ui.app.logging.getLogger") as get_logger:
            App._ensure_match_workers(app)

        self.assertIn(active, app._worker_futures)
        self.assertEqual(len(app._worker_futures), MAX_WORKERS)
        self.assertEqual(pool.submit.call_count, MAX_WORKERS - 1)
        get_logger.return_value.error.assert_called_once()

    def test_does_not_restart_workers_during_shutdown(self):
        stopped = Future()
        stopped.set_result(None)
        pool = Mock()
        app = App.__new__(App)
        app._worker_futures = [stopped]
        app._workers_closing = True
        app._worker_pool = pool

        App._ensure_match_workers(app)

        pool.submit.assert_not_called()
        self.assertEqual(app._worker_futures, [])

    def test_restarts_a_canceled_worker(self):
        canceled = Future()
        canceled.cancel()
        pool = Mock()
        pool.submit.side_effect = lambda _worker: Future()
        app = App.__new__(App)
        app._worker_futures = [canceled]
        app._workers_closing = False
        app._worker_pool = pool
        app.worker = Mock()

        with patch("moviq.ui.app.logging.getLogger") as get_logger:
            App._ensure_match_workers(app)

        self.assertEqual(len(app._worker_futures), MAX_WORKERS)
        get_logger.return_value.warning.assert_called_once()

    def test_poll_continues_after_progress_processing_error(self):
        app = App.__new__(App)
        app.root = Mock()
        app.results = Mock()
        app.results.empty.return_value = True
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


if __name__ == "__main__":
    unittest.main()
