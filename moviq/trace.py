import json
import os
from pathlib import Path
import threading


_LOCK = threading.Lock()
_TRACE_PATH = Path.home() / ".moviq" / "parser_trace.jsonl"
_RECORDS = {}
_INITIALIZED = False


def _enabled():
    return os.environ.get("PARSER_TRACE") == "1"


def _initialize():
    global _INITIALIZED
    with _LOCK:
        if _INITIALIZED:
            return
        _TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TRACE_PATH.write_text("", encoding="utf-8")
        _INITIALIZED = True


def _candidate_details(value):
    details = []
    for candidate_value in value or []:
        candidate = candidate_value
        candidate_score = None
        if (
            isinstance(candidate_value, tuple)
            and len(candidate_value) == 4
            and isinstance(candidate_value[3], dict)
        ):
            candidate_score, candidate = candidate_value[0], candidate_value[3]
        elif (
            isinstance(candidate_value, tuple)
            and len(candidate_value) == 2
            and isinstance(candidate_value[0], dict)
        ):
            candidate, candidate_score = candidate_value
        if not isinstance(candidate, dict):
            continue
        year_value = str(
            candidate.get("release_date")
            or candidate.get("first_air_date")
            or ""
        )[:4]
        year = int(year_value) if year_value.isdigit() else candidate.get("year")
        details.append({
            "title": (
                candidate.get("title") or candidate.get("name")
                or candidate.get("original_title")
                or candidate.get("original_name")
            ),
            "year": year,
            "id": candidate.get("id"),
            "score": candidate_score,
        })
    return details[:5]


def _matching_records(raw_name):
    if isinstance(raw_name, (list, tuple)):
        return [
            record for name in raw_name
            if (record := _RECORDS.get(str(name))) is not None
        ]
    record = _RECORDS.get(str(raw_name))
    if record is not None:
        return [record]
    return [
        record for record in _RECORDS.values()
        if any(
            stage["stage"] == "parsed_query"
            and isinstance(stage["value"], dict)
            and stage["value"].get("query") == raw_name
            for stage in record["stages"]
        )
    ]


def trace_start(raw_name):
    try:
        if not _enabled():
            return
        if callable(raw_name):
            raw_name = raw_name()
        _initialize()
        with _LOCK:
            key = str(raw_name)
            _RECORDS[key] = {
                "raw_name": key,
                "stages": [],
                "final_query": None,
                "top_candidates": [],
            }
    except Exception:
        return


def trace_stage(raw_name, stage, value):
    try:
        if not _enabled():
            return
        if callable(raw_name):
            raw_name = raw_name()
        if callable(value):
            value = value()
        _initialize()
        if stage == "top_candidates":
            value = _candidate_details(value)
        with _LOCK:
            for record in _matching_records(raw_name):
                entry = {"stage": str(stage), "value": value}
                record["stages"].append(entry)
                if stage == "provider_query":
                    record["final_query"] = value
                elif stage == "top_candidates":
                    record["top_candidates"] = value
    except Exception:
        return


def trace_end(raw_name, result):
    try:
        if not _enabled():
            return
        if callable(raw_name):
            raw_name = raw_name()
        if callable(result):
            result = result()
        _initialize()
        with _LOCK:
            key = str(raw_name)
            record = _RECORDS.pop(key, None)
            if record is None:
                return
            status = result[0] if isinstance(result, (tuple, list)) else None
            if status == "auto":
                return
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
            if isinstance(match, dict) and match.get("offline"):
                record["chosen_id"] = match.get("id")
            elif isinstance(match, dict):
                record["chosen_id"] = match.get("id")
            else:
                record["chosen_id"] = None
            record["fail_reason"] = reason
            record["result"] = result
            with _TRACE_PATH.open("a", encoding="utf-8") as trace_file:
                trace_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return


if os.environ.get("PARSER_TRACE") == "1":
    try:
        _initialize()
    except Exception:
        pass
