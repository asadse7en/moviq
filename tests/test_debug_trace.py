import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from moviq import debug_trace

trace = debug_trace


class ParserTraceTests(unittest.TestCase):
    def test_trace_records_only_results_needing_attention(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            trace_path = Path(temp_dir) / "parser_trace.jsonl"
            with patch.multiple(
                trace,
                _TRACE_PATH=trace_path,
                _INITIALIZED=True,
                _RECORDS={},
            ), patch.dict(os.environ, {"PARSER_TRACE": "1"}):
                results = (
                    ("ready.mkv", ("auto", {}, "")),
                    ("needs-review.mkv", ("review", None, "", "uncertain_match")),
                    ("no-match.mkv", ("nomatch", None, "")),
                    ("error.mkv", ("error", None, "provider failed")),
                )
                for raw_name, result in results:
                    trace.trace_start(raw_name)
                    trace.trace_end(raw_name, result)

            records = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [(record["raw_name"], record["result"][0]) for record in records],
                [
                    ("needs-review.mkv", "review"),
                    ("no-match.mkv", "nomatch"),
                    ("error.mkv", "error"),
                ],
            )

    def test_lazy_arguments_are_not_evaluated_when_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            value = Mock(side_effect=AssertionError)
            trace.trace_stage(lambda: value(), "stage", lambda: value())
            trace.trace_start(lambda: value())
            trace.trace_end(lambda: value(), lambda: value())


if __name__ == "__main__":
    unittest.main()
