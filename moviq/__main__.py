import argparse
import os
import sys
import tempfile
import traceback
from pathlib import Path
from uuid import uuid4

from . import resource


def _run_smoke_test(name):
    import ctypes
    import tkinter as tk

    original_home = {
        key: os.environ.get(key)
        for key in ("HOME", "USERPROFILE")
    }
    exit_code = 0
    root = None
    app = None
    watchdog_id = None

    def stage(name):
        print(f"STAGE: {name} ok", flush=True)

    with tempfile.TemporaryDirectory(prefix="moviq-smoke-home-") as home:
        os.environ["HOME"] = home
        os.environ["USERPROFILE"] = home

        def finish(code, message=None, error=None):
            nonlocal exit_code
            exit_code = code
            if error is not None:
                traceback.print_exception(error)
            if message:
                print(message, flush=True)
            if app is not None and not getattr(
                app, "_smoke_test_closing", False,
            ):
                app._smoke_test_closing = True
                try:
                    app.on_close()
                except Exception as cleanup_error:
                    traceback.print_exception(cleanup_error)
            try:
                root.destroy()
            except (tk.TclError, AttributeError):
                pass

        def ready():
            nonlocal app
            try:
                root.update_idletasks()
                root.deiconify()
                root.lift()
                root.focus_force()
                root.update_idletasks()
                stage("window created")
                app = App(root)
                root.lift()
                root.focus_force()
                root.attributes("-topmost", True)
                root.update()
                stage("main page built")

                icon_paths = sorted(icons_directory.glob("*.svg"))
                if not icon_paths:
                    raise MissingIconError(
                        f"No SVG icons found in {icons_directory}",
                    )
                canvas = tk.Canvas(root)
                try:
                    for icon_path in icon_paths:
                        try:
                            draw_icon(
                                canvas, icon_path.stem, 24, "#ffffff",
                                (12, 12),
                            )
                        except FileNotFoundError as error:
                            raise MissingIconError(str(error)) from error
                finally:
                    canvas.destroy()
                root.update_idletasks()
                stage("icons loaded")

                user_data_dir.mkdir(parents=True, exist_ok=True)
                test_path = user_data_dir / f".smoke-test-{uuid4().hex}"
                test_path.write_text("ok", encoding="utf-8")
                test_path.unlink()
                stage("settings dir writable")
                root.after_cancel(watchdog_id)
                root.after(1500, shutdown)
            except MissingIconError as error:
                finish(3, error=error)
            except Exception as error:
                finish(1, error=error)

        def shutdown():
            try:
                app._smoke_test_closing = True
                app.on_close()
            except Exception as error:
                finish(1, error=error)
            else:
                nonlocal exit_code
                exit_code = 0

        def watchdog():
            finish(2, "STAGE: timeout, UI never ready")

        try:
            from .core.config import USER_DATA_DIR as user_data_dir
            from .ui.app import App, HAVE_DND, TkinterDnD
            from .ui.icons import draw_icon

            icons_directory = Path(
                resource("moviq/ui/assets/icons"),
            )
            if not icons_directory.is_dir():
                raise MissingIconError(
                    f"Icon directory not found: {icons_directory}",
                )
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "moviq.app",
                )
            except (AttributeError, OSError):
                pass

            root = TkinterDnD.Tk() if HAVE_DND else tk.Tk()
            root._moviq_icon = tk.PhotoImage(
                file=resource("assets/moviq-icon.png"),
            )
            root.iconphoto(True, root._moviq_icon)
            user_data_dir = Path(user_data_dir)
            watchdog_id = root.after(30000, watchdog)
            root.after_idle(ready)
            root.mainloop()
        except MissingIconError as error:
            traceback.print_exception(error)
            exit_code = 3
        except Exception as error:
            traceback.print_exception(error)
            exit_code = 1
        finally:
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass
            for key, value in original_home.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    return exit_code


class MissingIconError(FileNotFoundError):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser()
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--bump",
        metavar="{major,minor,patch,status}|X.Y.Z",
        help="show or update the source version",
    )
    action_group.add_argument(
        "--smoke-test",
        metavar="NAME",
        help="start the UI in an isolated home and run smoke checks",
    )
    action_group.add_argument(
        "--parser-trace",
        action="store_true",
        help="trace filename matching and record only items needing attention",
    )
    action_group.add_argument(
        "--export-unready",
        "--test",
        dest="diagnostic_export",
        action="store_const",
        const="unready",
        help="export items needing attention after Auto Match in the app",
    )
    action_group.add_argument(
        "--export-all",
        dest="diagnostic_export",
        action="store_const",
        const="all",
        help="export every item after Auto Match in the app",
    )
    args = parser.parse_args(argv)
    if args.bump is not None:
        from . import versioning

        if getattr(sys, "frozen", False) or not versioning.is_writable():
            print(
                "Version bump only works in a source checkout",
                file=sys.stderr,
            )
            return 1
        if args.bump == "status":
            try:
                print(f"Moviq version: {versioning.read_version()}")
            except (OSError, ValueError) as error:
                print(str(error).replace("\n", " "), file=sys.stderr)
                return 1
            return 0
        try:
            current = versioning.read_version()
            next_version = versioning.bump_version(current, args.bump)
            versioning.write_version(next_version)
        except ValueError as error:
            print(str(error).replace("\n", " "), file=sys.stderr)
            return 2
        except OSError as error:
            print(str(error).replace("\n", " "), file=sys.stderr)
            return 1
        print(
            f"Version: {current} -> {next_version} (moviq/__init__.py)",
        )
        return 0
    if args.smoke_test:
        return _run_smoke_test(args.smoke_test)
    export_after_match = None
    cleanup_diagnostics = None
    if args.diagnostic_export:
        from .diagnostics import configure_auto_export

        export_after_match, cleanup_diagnostics = configure_auto_export(
            include_all=args.diagnostic_export == "all",
        )
    if args.parser_trace:
        os.environ["PARSER_TRACE"] = "1"

    import ctypes
    import tkinter as tk

    from .ui.app import App, HAVE_DND, TkinterDnD

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "moviq.app",
        )
    except (AttributeError, OSError):
        pass

    root = TkinterDnD.Tk() if HAVE_DND else tk.Tk()
    try:
        root._moviq_icon = tk.PhotoImage(
            file=resource("assets/moviq-icon.png"),
        )
        root.iconphoto(True, root._moviq_icon)
    except (OSError, tk.TclError):
        pass
    app = App(root)
    if export_after_match is not None:
        app._diagnostic_export_after_match = export_after_match
        app._diagnostic_provider_used = {}
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.on_close()
    finally:
        if cleanup_diagnostics is not None:
            cleanup_diagnostics()


if __name__ == "__main__":
    raise SystemExit(main() or 0)
