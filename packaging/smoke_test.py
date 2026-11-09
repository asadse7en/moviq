#!/usr/bin/env python3
"""Run a GUI smoke test and retain its output and desktop screenshot."""

import os
import platform
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab


TIMEOUT_SECONDS = 60
SCREENSHOT_TIMEOUT_SECONDS = 15
EXPECTED_STAGES = (
    "window created",
    "main page built",
    "icons loaded",
    "settings dir writable",
)


def _platform_name():
    return {
        "Darwin": "macos",
        "Linux": "linux",
        "Windows": "windows",
    }.get(platform.system(), platform.system().lower())


def _capture_screenshot(path):
    if sys.platform == "darwin":
        subprocess.run(
            ["screencapture", "-x", str(path)],
            check=True,
            capture_output=True,
            timeout=SCREENSHOT_TIMEOUT_SECONDS,
        )
    else:
        ImageGrab.grab().save(path)

    with Image.open(path) as screenshot:
        image = screenshot.convert("RGB")
        background = Image.new("RGB", image.size, image.getpixel((0, 0)))
        if ImageChops.difference(image, background).getbbox() is None:
            raise RuntimeError("screenshot is blank")


def _last_stage_or_error(lines, fallback):
    for _stream_name, line in reversed(lines):
        lowered = line.lower()
        if "stage:" in lowered or any(
            marker in lowered
            for marker in ("error", "exception", "traceback", "failed")
        ):
            return line.strip()
    return fallback


def _append_summary(path, name, platform_name, message):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write(
            f"\n### Smoke test failed: {platform_name} {name}\n\n"
            f"{message}\n\nLog: `{path.as_posix()}`\n"
        )


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or not re.fullmatch(r"[A-Za-z0-9._-]+", args[0]):
        print(
            "Usage: smoke_test.py <name> <command...>",
            file=sys.stderr,
        )
        return 2
    name = args[0]
    command = args[1:]
    if command[0] == "--":
        command = command[1:]
    if not command:
        print(
            "Usage: smoke_test.py <name> <command...>",
            file=sys.stderr,
        )
        return 2

    platform_name = _platform_name()
    log_directory = Path("smoke-logs")
    log_path = log_directory / f"{name}.log"
    screenshot_path = log_directory / f"{name}.png"
    try:
        log_directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        message = f"{platform_name} {name}: {type(error).__name__}: {error}"
        print(
            f"::error title=Smoke test failed::{message}",
            flush=True,
        )
        try:
            _append_summary(log_path, name, platform_name, message)
        except OSError as summary_error:
            print(
                f"Could not update GitHub step summary: {summary_error}",
                file=sys.stderr,
            )
        return 1

    output_lines = []
    events = queue.Queue()
    screenshot_error = None
    screenshot_captured = False
    timed_out = False
    return_code = None
    launch_error = None
    process = None
    reader_threads = []
    fallback = "command did not complete successfully"
    log_error = None

    print(f"::group::Smoke test: {platform_name} {name}", flush=True)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        def read_stream(stream_name, stream):
            try:
                for line in stream:
                    events.put((stream_name, line))
            finally:
                events.put((stream_name, None))

        for stream_name, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            thread = threading.Thread(
                target=read_stream,
                args=(stream_name, stream),
                daemon=True,
            )
            thread.start()
            reader_threads.append(thread)

        deadline = time.monotonic() + TIMEOUT_SECONDS
        streams_open = 2
        while streams_open:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and process.poll() is None:
                timed_out = True
                process.kill()
                fallback = f"command timed out after {TIMEOUT_SECONDS} seconds"
                deadline = float("inf")
            try:
                stream_name, line = events.get(timeout=min(
                    0.1, max(0.01, remaining),
                ))
            except queue.Empty:
                continue
            if line is None:
                streams_open -= 1
                continue
            output_lines.append((stream_name, line))
            if (
                stream_name == "stdout"
                and "STAGE: settings dir writable ok" in line
                and not screenshot_captured
            ):
                try:
                    _capture_screenshot(screenshot_path)
                    screenshot_captured = True
                    screenshot_error = None
                except Exception as error:
                    screenshot_error = str(error)

        return_code = process.wait()
        for thread in reader_threads:
            thread.join()
    except Exception as error:
        launch_error = error
        fallback = f"{type(error).__name__}: {error}"
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
    finally:
        if not screenshot_captured:
            try:
                _capture_screenshot(screenshot_path)
                screenshot_captured = True
                screenshot_error = None
            except Exception as error:
                screenshot_error = str(error)

        for stream_name in ("stdout", "stderr"):
            print(f"--- {stream_name} ---", flush=True)
            print(
                "".join(
                    line for stream, line in output_lines
                    if stream == stream_name
                ),
                end="",
                flush=True,
            )
        print("::endgroup::", flush=True)

        log_text = (
            f"Command: {subprocess.list2cmdline(command)}\n"
            f"Exit code: {return_code}\n"
            f"Timed out: {timed_out}\n"
            f"Screenshot: {screenshot_path.as_posix()}\n\n"
            f"--- stdout ---\n"
            + "".join(
                line for stream, line in output_lines
                if stream == "stdout"
            )
            + "\n--- stderr ---\n"
            + "".join(
                line for stream, line in output_lines
                if stream == "stderr"
            )
        )
        if launch_error is not None:
            log_text += f"\n{type(launch_error).__name__}: {launch_error}\n"
        if screenshot_error:
            log_text += f"\nScreenshot error: {screenshot_error}\n"
        try:
            log_path.write_text(log_text, encoding="utf-8")
        except OSError as error:
            log_error = f"Could not write smoke log: {error}"

    failure = None
    if launch_error is not None:
        failure = fallback
    elif timed_out:
        failure = fallback
    elif return_code != 0:
        failure = _last_stage_or_error(
            output_lines, f"command exited with status {return_code}",
        )
    elif any(
        not any(
            f"STAGE: {stage} ok" in line
            for _stream, line in output_lines
        )
        for stage in EXPECTED_STAGES
    ):
        failure = _last_stage_or_error(
            output_lines, "command exited before all smoke stages completed",
        )
    elif screenshot_error:
        failure = screenshot_error
    elif not screenshot_captured:
        failure = "screenshot was not captured"
    elif log_error:
        failure = log_error

    if failure:
        annotation_message = f"{platform_name} {name}: {failure}"
        escaped_message = (
            annotation_message.replace("%", "%25")
            .replace("\r", "%0D")
            .replace("\n", "%0A")
        )
        print(
            f"::error title=Smoke test failed::{escaped_message}",
            flush=True,
        )
        try:
            _append_summary(
                log_path, name, platform_name, annotation_message,
            )
        except OSError as error:
            print(f"Could not update GitHub step summary: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
