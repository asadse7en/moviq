import copy
import json
import os
import threading
import uuid

from .apply import apply_item_plan
from .file_ops import rebase, undo_ops
from .undo_journal import UndoJournal


class RenameJournalError(RuntimeError):
    pass


class _OperationJournal:
    def __init__(self, path):
        self.path = path
        self.run_id = uuid.uuid4().hex
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def execute(self, operation, action, on_success=None):
        entry = {
            "run_id": self.run_id,
            "operation": [operation[0], *(str(path) for path in operation[1:])],
        }
        with self._lock:
            self._write({**entry, "phase": "before"})
            try:
                action()
            except Exception as error:
                self._write({
                    **entry, "phase": "after", "success": False,
                    "error": str(error),
                })
                raise
            if on_success is not None:
                on_success()
            self._write({**entry, "phase": "after", "success": True})

    def _write(self, record):
        try:
            self._file.write(json.dumps(record, ensure_ascii=True) + "\n")
            self._file.flush()
            os.fsync(self._file.fileno())
        except Exception as error:
            raise RenameJournalError(
                f"Could not write rename journal: {error}",
            ) from error


class _JournaledOperations(list):
    def __init__(self, journal, undo_journal=None):
        super().__init__()
        self.journal = journal
        self.undo_journal = undo_journal

    def execute(self, operation, action):
        self.journal.execute(
            operation,
            action,
            on_success=lambda: (
                self.append(operation),
                self.undo_journal.append(operation) if self.undo_journal is not None else None,
            ),
        )

    def rollback(self, start):
        original = self[start:]
        remaining = []
        errors = []
        for operation in reversed(original):
            operation_errors = undo_ops(
                [operation], journal_operation=self.journal.execute,
            )
            if operation_errors:
                errors.extend(operation_errors)
                remaining.append(operation)
        self[start:] = reversed(remaining)
        return errors


class RenameService:
    """Execute prepared rename plans away from the Tk thread."""

    def __init__(
        self, items, plans, all_items, rename_subtitles, journal_path,
        events, cancel_event, total, skipped_count, conflict_count,
        storage_gone=None, undo_journal=None,
    ):
        self.items = items
        self.plans = plans
        self.all_items = all_items
        self.rename_subtitles = rename_subtitles
        self.journal_path = journal_path
        self.events = events
        self.cancel_event = cancel_event
        self.total = total
        self.skipped_count = skipped_count
        self.conflict_count = conflict_count
        self.storage_gone = storage_gone
        self.undo_journal = undo_journal or UndoJournal()

    def run(self):
        renamed_count = 0
        failed_count = conflict_count = self.conflict_count
        processed_count = 0
        errors = []
        operations = []
        storage_disconnected = False
        try:
            plans = {
                key: copy.copy(plan) for key, plan in self.plans.items()
            }
            work_items = {
                id(item): copy.copy(item) for item in self.all_items
            }
            with _OperationJournal(self.journal_path) as journal:
                operations = _JournaledOperations(journal, self.undo_journal)

                def apply_folder_renames(start):
                    for operation in operations[start:]:
                        if operation[0] != "rename_dir":
                            continue
                        old_dir, new_dir = operation[1:]
                        for item in work_items.values():
                            item.path = rebase(item.path, old_dir, new_dir)
                            item.root = rebase(item.root, old_dir, new_dir)
                        for plan in plans.values():
                            for name in (
                                "dest", "dest_dir", "rename_dir",
                                "cleanup_root", "source_root", "tv_show_dir",
                                "source_show_dir", "source_episode_dir",
                            ):
                                value = getattr(plan, name, None)
                                if value is not None:
                                    setattr(
                                        plan, name,
                                        rebase(value, old_dir, new_dir),
                                    )
                        self.events.put((
                            "folder_renamed", old_dir, new_dir,
                        ))

                self.events.put((
                    "rename_progress", "", conflict_count, self.total,
                    renamed_count, self.skipped_count, failed_count,
                ))
                for item in self.items:
                    if self.cancel_event.is_set():
                        break
                    work_item = work_items[id(item)]
                    item_path = work_item.path
                    self.events.put((
                        "rename_progress", work_item.path.name,
                        conflict_count + processed_count, self.total,
                        renamed_count, self.skipped_count, failed_count,
                    ))
                    previous_state = (
                        getattr(work_item, "status", None),
                        getattr(work_item, "error", None),
                        getattr(work_item, "error_code", None),
                    )
                    new_path, error = apply_item_plan(
                        work_item, plans[id(item)], operations,
                        self.rename_subtitles, apply_folder_renames,
                    )
                    if error is not None:
                        if (
                            self.storage_gone is not None
                            and self.storage_gone(error, item_path)
                        ):
                            (
                                work_item.status, work_item.error,
                                work_item.error_code,
                            ) = previous_state
                            storage_disconnected = True
                            self.cancel_event.set()
                            break
                        errors.append(
                            f"{work_item.path.name}: "
                            f"{getattr(error, 'strerror', None) or error}"
                        )
                        failed_count += 1
                    else:
                        renamed_count += 1
                    processed_count += 1
                    self.events.put((
                        "rename_item", item, new_path, error,
                    ))
                    self.events.put((
                        "rename_progress", work_item.path.name,
                        conflict_count + processed_count, self.total,
                        renamed_count, self.skipped_count, failed_count,
                    ))
        except Exception as error:
            errors.append(f"Could not complete rename: {error}")
            failed_count += 1

        self.events.put((
            "rename_complete", operations, errors, renamed_count,
            failed_count, processed_count,
            processed_count < len(self.items), self.skipped_count, self.total,
            conflict_count, storage_disconnected,
            len(self.items) - processed_count,
        ))
