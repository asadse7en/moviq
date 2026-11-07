import threading
import time

_DOWN_FOR_SECONDS = 20
_down_at = None
_lock = threading.Lock()


def mark_down():
    global _down_at
    with _lock:
        _down_at = time.monotonic()


def mark_up():
    global _down_at
    with _lock:
        _down_at = None


def reset():
    mark_up()


def is_down():
    global _down_at
    with _lock:
        if _down_at is None:
            return False
        if time.monotonic() - _down_at >= _DOWN_FOR_SECONDS:
            _down_at = None
            return False
        return True
