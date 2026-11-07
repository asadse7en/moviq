import queue
import socket
import threading

from . import network_state


class NetworkMonitor(threading.Thread):
    def __init__(self, events: queue.Queue, interval=8.0):
        super().__init__(daemon=True)
        self.events = events
        self.interval = interval
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._is_online = None

    @property
    def is_online(self):
        with self._state_lock:
            if self._is_online is not None:
                return self._is_online
        return not network_state.is_down()

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                with socket.create_connection(
                    ("api.themoviedb.org", 443), timeout=2,
                ):
                    pass
                is_online = True
            except OSError:
                is_online = False

            with self._state_lock:
                changed = is_online != self._is_online
                self._is_online = is_online
            if changed:
                if is_online:
                    network_state.reset()
                else:
                    network_state.mark_down()
                self.events.put(("network", is_online))
            if self._stop_event.wait(self.interval):
                return
