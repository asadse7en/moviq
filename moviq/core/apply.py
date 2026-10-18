from .file_ops import perform_plan


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
        apply_folder_renames(ops_start)
        return None, error
    apply_folder_renames(ops_start)
    return new_path, None
