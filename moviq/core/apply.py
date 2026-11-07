from .file_ops import perform_plan, undo_ops


def build_plans(items, plan_for):
    plans = {}
    for item in items:
        plans[id(item)] = plan_for(item)
    return plans


def apply_item_plan(item, plan, ops, rename_subtitles, apply_folder_renames):
    ops_start = len(ops)
    try:
        new_path = perform_plan(
            item.path, plan, ops,
            rename_subtitles=rename_subtitles,
        )
    except Exception as error:
        rollback = getattr(ops, "rollback", None)
        if rollback is not None:
            rollback_errors = rollback(ops_start)
        else:
            rollback_errors = []
            remaining = []
            for operation in reversed(ops[ops_start:]):
                operation_errors = undo_ops([operation])
                if operation_errors:
                    rollback_errors.extend(operation_errors)
                    remaining.append(operation)
            ops[ops_start:] = reversed(remaining)
        if rollback_errors:
            error = RuntimeError(
                f"{error}; rollback failed: {'; '.join(rollback_errors)}",
            )
        apply_folder_renames(ops_start)
        return None, error
    apply_folder_renames(ops_start)
    return new_path, None
