import argparse
import ctypes
import os
import tkinter as tk

from . import resource


def main(argv=None):
    parser = argparse.ArgumentParser()
    action_group = parser.add_mutually_exclusive_group()
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
    export_after_match = None
    cleanup_diagnostics = None
    if args.diagnostic_export:
        from .diagnostics import configure_auto_export

        export_after_match, cleanup_diagnostics = configure_auto_export(
            include_all=args.diagnostic_export == "all",
        )
    if args.parser_trace:
        os.environ["PARSER_TRACE"] = "1"

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
    main()
