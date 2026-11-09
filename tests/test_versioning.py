import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from moviq.versioning import (
    bump_version, read_version, write_version,
)


class VersioningTests(unittest.TestCase):
    def test_bump_parts_reset_lower_components(self):
        self.assertEqual(bump_version("2.3.4", "major"), "3.0.0")
        self.assertEqual(bump_version("2.3.4", "minor"), "2.4.0")
        self.assertEqual(bump_version("2.3.4", "patch"), "2.3.5")

    def test_exact_semver_must_be_strictly_greater(self):
        self.assertEqual(bump_version("2.3.4", "2.4.0"), "2.4.0")
        for value in ("2.3.4", "2.3.3", "02.4.0", "2.4", "v2.4.0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bump_version("2.3.4", value)

    def test_write_version_changes_only_the_declaration_line(self):
        with tempfile.TemporaryDirectory() as temp:
            init_path = Path(temp) / "__init__.py"
            original = (
                '"""Example package."""\r\n'
                '__version__ = "2.3.4"\r\n'
                'OTHER = "2.3.4"\r\n'
            )
            init_path.write_bytes(original.encode("utf-8"))

            write_version("2.4.0", init_path)

            self.assertEqual(
                init_path.read_bytes(),
                original.replace(
                    '__version__ = "2.3.4"',
                    '__version__ = "2.4.0"',
                ).encode("utf-8"),
            )
            self.assertEqual(read_version(init_path), "2.4.0")

    def test_cli_status_and_bump_do_not_import_tkinter(self):
        from moviq import __main__

        with patch.dict("sys.modules", {"tkinter": None}), patch(
            "builtins.print",
        ) as print_output:
            result = __main__.main(["--bump", "status"])

        self.assertEqual(result, 0)
        print_output.assert_called_once_with(
            f"Moviq version: {read_version()}",
        )

    def test_cli_bump_writes_source_version_and_prints_one_line(self):
        from moviq import __main__, versioning

        with tempfile.TemporaryDirectory() as temp:
            init_path = Path(temp) / "__init__.py"
            init_path.write_text(
                '__version__ = "2.0.0"\nOTHER = True\n',
                encoding="utf-8",
            )
            with patch.object(versioning, "INIT_PATH", init_path), patch(
                "moviq.versioning.is_writable", return_value=True,
            ), patch("builtins.print") as print_output:
                result = __main__.main(["--bump", "minor"])

            self.assertEqual(result, 0)
            self.assertEqual(
                init_path.read_text(encoding="utf-8"),
                '__version__ = "2.1.0"\nOTHER = True\n',
            )
            print_output.assert_called_once_with(
                "Version: 2.0.0 -> 2.1.0 (moviq/__init__.py)",
            )

    def test_cli_invalid_bump_is_one_line_and_returns_two(self):
        from moviq import __main__

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr,
        ):
            result = __main__.main(["--bump", "2.0"])

        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "Invalid version or bump part: 2.0\n",
        )

    def test_cli_rejects_frozen_builds(self):
        from moviq import __main__, versioning

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "frozen", True, create=True), patch.object(
            versioning, "is_writable", return_value=True,
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr,
        ):
            result = __main__.main(["--bump", "status"])

        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "Version bump only works in a source checkout\n",
        )


if __name__ == "__main__":
    unittest.main()
