import copy
import json
import os
from pathlib import Path


def configure_auto_export(include_all=False):
    previous_trace_setting = os.environ.pop("PARSER_TRACE", None)
    try:
        from . import debug_trace as trace
    finally:
        if previous_trace_setting is not None:
            os.environ["PARSER_TRACE"] = previous_trace_setting

    previous_state = (
        trace._RECORDS,
        trace._INITIALIZED,
        trace.trace_end,
    )
    captured = {}
    trace._RECORDS = {}
    trace._INITIALIZED = True
    os.environ["PARSER_TRACE"] = "1"

    def trace_end(raw_name, result):
        key = str(raw_name)
        with trace._LOCK:
            record = trace._RECORDS.pop(key, None)
        if record is None:
            return
        record = copy.deepcopy(record)
        status = result[0] if isinstance(result, (tuple, list)) else None
        match = (
            result[1]
            if isinstance(result, (tuple, list)) and len(result) > 1
            else None
        )
        reason = None
        if isinstance(result, (tuple, list)):
            if status == "error" and len(result) > 2:
                reason = result[2]
            elif status == "review" and len(result) > 3:
                reason = result[3]
            elif status == "nomatch":
                reason = "no_match"
        record["chosen_id"] = (
            match.get("id") if isinstance(match, dict) else None
        )
        record["fail_reason"] = reason
        record["result"] = result
        captured[key] = record

    trace.trace_end = trace_end

    def export_after_match(app):
        count, output_path = export_app_report(
            app, captured, include_all=include_all,
        )
        print(f"Exported {count} items to {output_path}")

    def cleanup():
        (
            trace._RECORDS,
            trace._INITIALIZED,
            trace.trace_end,
        ) = previous_state
        if previous_trace_setting is None:
            os.environ.pop("PARSER_TRACE", None)
        else:
            os.environ["PARSER_TRACE"] = previous_trace_setting

    return export_after_match, cleanup


def _report_item(app, item, trace, include_all):
    status = "error" if item.error else item.status
    status = {
        "auto": "matched",
        "manual": "confirmed",
        "nomatch": "no_match",
    }.get(status, status)
    ok = status in ("matched", "confirmed", "no_change", "done")
    if not include_all and ok:
        return None

    match = item.match or getattr(item, "review_match", None)
    trace = trace or {}
    path = Path(item.path)
    scan_root = Path(item.root)
    try:
        relative = path.relative_to(scan_root)
        relative_path = str(relative)
        folder_chain = list(relative.parts[:-1])
    except ValueError:
        relative_path = str(path)
        folder_chain = []

    provider_id = None
    if isinstance(match, dict) and isinstance(match.get("id"), int):
        from .core.providers.ids import provider_of

        provider_id = provider_of(match["id"])
    providers = getattr(app, "_diagnostic_provider_used", {})
    return {
        "raw_name": str(path),
        "relative_path": relative_path,
        "scan_root": str(scan_root),
        "folder_chain": folder_chain,
        "siblings": sorted(
            (
                sibling.name for sibling in path.parent.iterdir()
                if sibling.is_file()
            ),
            key=str.casefold,
        )[:30],
        "settings": {
            "source": (
                "auto" if app.provider_settings.selected == "automatic"
                else app.provider_settings.selected
            ),
            "provider_used": providers.get(str(path), provider_id),
            "has_api_key": bool(
                app.key_var.get().strip()
                or any(value for _name, value in app.provider_settings.keys)
            ),
            "media_type_hint": app.media_mode.get(),
        },
        "status": status,
        "fail_reason": (
            trace.get("fail_reason")
            or getattr(item, "review_reason", None)
            or (item.error if item.error else None)
        ),
        "stages": trace.get("stages", []),
        "parsed": {
            "media_type": item.media_type,
            "query": item.query,
            "year": item.year,
            "air_date": getattr(item, "air_date", None),
            "season": item.season_number,
            "episode": item.episode_number,
            "episode_end": item.episode_end_number,
        },
        "top_candidates": trace.get("top_candidates", []),
        "chosen_id": (
            trace.get("chosen_id")
            if trace.get("chosen_id") is not None
            else match.get("id") if isinstance(match, dict) else None
        ),
        "result": trace.get(
            "result",
            (
                status,
                match,
                item.error,
                getattr(item, "review_reason", None),
            ),
        ),
        "expected": {
            "title": None,
            "media_type": None,
            "year": None,
            "season": None,
            "episode": None,
            "provider_id": None,
        },
    }


def export_app_report(app, captured, include_all=False):
    report = []
    for item in app.items:
        row = _report_item(
            app, item, captured.get(str(item.path)), include_all,
        )
        if row is not None:
            report.append(row)

    output_path = Path(__file__).resolve().parent.parent / "unready_report.json"
    output_path.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            for row in report
        ) + ("\n" if report else ""),
        encoding="utf-8",
    )
    return len(report), output_path
