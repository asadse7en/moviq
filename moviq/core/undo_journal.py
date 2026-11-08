import json
import os
import tempfile
from pathlib import Path

from .config import USER_DATA_DIR

JOURNAL_PATH = USER_DATA_DIR / "undo_journal.json"


def _serialize_op(operation):
    if operation is None:
        return []
    return [operation[0], *(str(path) for path in operation[1:])]


def _deserialize_op(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    kind = raw[0]
    paths = [Path(value) for value in raw[1:]]
    return (kind, *paths)


def _atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".undo-journal-",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True)
            handle.flush()
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_ops(ops, path=JOURNAL_PATH):
    serialized = [_serialize_op(op) for op in ops]
    _atomic_write(path, serialized)
    return serialized


def append_op(operation, path=JOURNAL_PATH):
    ops = read_ops(path)
    if operation is not None:
        ops.append(_serialize_op(operation))
    _atomic_write(path, ops)
    return ops


def read_ops(path=JOURNAL_PATH):
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(payload, list):
        return []
    return [
        _deserialize_op(op)
        for op in payload
        if _deserialize_op(op) is not None
    ]


def clear_journal(path=JOURNAL_PATH):
    path = Path(path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


class PlannedOps(list):
    def execute(self, operation, action):
        self.append(operation)


class UndoJournal:
    def __init__(self, path=JOURNAL_PATH):
        self.path = Path(path)
        self._ops = []

    def start(self, ops=None):
        self._ops = [
            _deserialize_op(op) if isinstance(op, (list, tuple)) else op
            for op in (ops or [])
        ]
        write_ops(self._ops, self.path)
        return self

    def append(self, operation):
        if operation is None:
            return self
        self._ops.append(_deserialize_op(_serialize_op(operation)) or operation)
        write_ops(self._ops, self.path)
        return self

    def finish(self):
        clear_journal(self.path)
        self._ops = []
        return self

    def read(self):
        self._ops = read_ops(self.path)
        return list(self._ops)

    @classmethod
    def restore_pending(cls, path=JOURNAL_PATH):
        return read_ops(path)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.finish()
