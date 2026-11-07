import queue
import unittest
from unittest.mock import MagicMock, patch

from moviq.core import network_state
from moviq.core.network_monitor import NetworkMonitor


class NetworkMonitorTests(unittest.TestCase):
    def setUp(self):
        network_state.reset()

    def tearDown(self):
        network_state.reset()

    @patch(
        "moviq.core.network_monitor.socket.create_connection",
        side_effect=[MagicMock(), OSError("offline")],
    )
    def test_reports_connectivity_changes(self, create_connection):
        events = queue.Queue()
        monitor = NetworkMonitor(events, interval=0.01)
        monitor.start()
        self.addCleanup(monitor.join, 1)
        self.addCleanup(monitor.stop)

        self.assertEqual(events.get(timeout=1), ("network", True))
        self.assertFalse(network_state.is_down())
        self.assertEqual(events.get(timeout=1), ("network", False))
        monitor.stop()
        monitor.join(1)
        self.assertTrue(network_state.is_down())
        self.assertEqual(create_connection.call_count, 2)
