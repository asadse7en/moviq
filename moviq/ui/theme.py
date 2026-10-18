import tkinter.font as tkfont
from tkinter import ttk

from .design import PALETTES
from .popups import apply_titlebar_theme

try:
    import sv_ttk
    HAVE_SV = True
except ImportError:
    sv_ttk = None
    HAVE_SV = False


def apply_theme(app, mode):
    app.mode, app.pal = mode, PALETTES[mode]
    pal = app.pal
    app._status_tooltip.set_theme(
        pal["card"], pal["fg"], pal["chip_border"],
    )
    style = ttk.Style()
    if HAVE_SV:
        sv_ttk.set_theme(mode)
        style.layout("Accent.TButton", style.layout("TButton"))
    else:
        style.theme_use("clam")
        style.configure(".", background=pal["bg"], foreground=pal["fg"],
                        fieldbackground=pal["card"], bordercolor=pal["btn"],
                        lightcolor=pal["bg"], darkcolor=pal["bg"],
                        font="TkDefaultFont")
        style.configure("TButton", padding=(14, 7), background=pal["btn"],
                        borderwidth=0, relief="flat")
        style.map("TButton", background=[("active", pal["btn_hi"])])
        style.configure("Accent.TButton", background=pal["accent"],
                        foreground="#ffffff", padding=(20, 8),
                        font="TkHeadingFont")
        style.map("Accent.TButton",
                  background=[("active", pal["accent_hi"])])
        style.configure("TEntry", padding=6)
        style.configure("Treeview", background=pal["card"],
                        fieldbackground=pal["card"], foreground=pal["fg"],
                        borderwidth=0)
    style.configure(".", font="TkDefaultFont")
    for style_name in (
        "TLabel", "TButton", "TCheckbutton", "TRadiobutton",
        "TEntry", "TCombobox", "TSpinbox",
    ):
        style.configure(style_name, font="TkDefaultFont")
    if not HAVE_SV:
        style.configure(
            "Treeview.Heading", background=pal["btn"],
            foreground=pal["fg"], relief="flat", padding=(8, 8),
        )
        style.map(
            "Treeview", background=[("selected", pal["accent"])],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Horizontal.TProgressbar",
            background=pal["accent"], troughcolor=pal["btn"],
            borderwidth=0,
        )
    compact = getattr(app, "_toolbar_compact", False)
    style.configure("TFrame", background=pal["bg"])
    style.configure(
        "TLabel", background=pal["bg"], foreground=pal["fg"],
    )
    style.configure("Toolbar.TFrame", background=pal["bg"])
    style.configure(
        "Toolbar.TButton",
        background=pal["btn"],
        foreground=pal["fg"],
        padding=(3, 5) if compact else (9, 8),
        borderwidth=0,
        relief="flat",
        font="TkDefaultFont",
    )
    style.map(
        "Toolbar.TButton",
        background=[
            ("pressed", pal["accent"]),
            ("active", pal["btn_hi"]),
        ],
    )
    style.configure(
        "Icon.TButton",
        background=pal["btn"],
        foreground=pal["fg"],
        padding=(7, 6),
        borderwidth=0,
        relief="flat",
    )
    style.map(
        "Icon.TButton",
        background=[("active", pal["btn_hi"])],
    )
    style.configure(
        "ResetColumns.TButton",
        background=pal["header_bg"],
        foreground=pal["header_muted"],
        borderwidth=0,
        padding=(3, 1),
        font="TkDefaultFont",
    )
    style.map(
        "ResetColumns.TButton",
        background=[("active", pal["header_bg"])],
        foreground=[("active", pal["muted"])],
    )
    style.configure(
        "Table.TFrame",
        background=pal["border"],
        bordercolor=pal["border"],
        lightcolor=pal["border"],
        darkcolor=pal["border"],
        borderwidth=1,
        relief="solid",
    )
    style.configure("TableContent.TFrame", background=pal["card"])
    style.configure("EmptyState.TFrame", background=pal["card"])
    style.configure(
        "EmptyTitle.TLabel",
        background=pal["card"],
        foreground=pal["fg"],
        font="TkHeadingFont",
    )
    style.configure(
        "EmptyHint.TLabel",
        background=pal["card"],
        foreground=pal["muted"],
        font="TkDefaultFont",
    )
    style.configure(
        "Stats.TLabel",
        background=pal["bg"],
        foreground=pal["fg"],
        font="TkDefaultFont",
    )
    style.configure(
        "Stats.Ready.TLabel",
        background=pal["bg"],
        foreground=pal["accent"],
        font="TkDefaultFont",
    )
    style.configure(
        "PendingCount.TLabel",
        background=pal["bg"],
        foreground=pal["accent"],
        font="TkDefaultFont",
    )
    style.configure(
        "Treeview",
        background=pal["bg"],
        fieldbackground=pal["bg"],
        foreground=pal["rowfg"],
        borderwidth=0,
        font="TkTextFont",
        rowheight=tkfont.nametofont("TkTextFont").metrics("linespace") + 12,
        padding=(12, 2),
        indent=0,
    )
    style.map(
        "Treeview",
        background=[("selected", pal.get("selection", pal["zone_hi"]))],
        foreground=[("selected", pal["fg"])],
    )
    style.configure(
        "Accent.TButton",
        background=pal["accent"],
        foreground="#ffffff",
        padding=(20, 8),
        borderwidth=0,
        font="TkHeadingFont",
    )
    style.map(
        "Accent.TButton",
        background=[
            ("disabled", pal["btn"]),
            ("active", pal["accent_hi"]),
            ("!disabled", pal["accent"]),
        ],
        foreground=[
            ("disabled", pal["muted"]),
            ("active", "#ffffff"),
            ("!disabled", "#ffffff"),
        ],
    )
    style.configure("Treeview.Heading", font="TkHeadingFont",
                    padding=(14, 10), background=pal["card"],
                    foreground=pal["fg"], borderwidth=0)
    style.configure(
        "Title.TLabel", font="MoviqTitleFont",
        background=pal["header_bg"], foreground=pal["fg"],
    )
    style.configure(
        "HeaderSubtitle.TLabel",
        font="TkDefaultFont",
        foreground=pal["header_muted"],
        background=pal["header_bg"],
    )
    style.configure("Section.TLabel", font="TkHeadingFont",
                    foreground=pal["accent"])
    style.configure("Muted.TLabel", foreground=pal["muted"])
    style.configure(
        "MatchedCount.TLabel",
        foreground=pal["status_colors"]["auto"][0],
        background=pal["bg"],
    )
    style.configure(
        "PendingCount.TLabel",
        foreground=pal["accent"],
        background=pal["bg"],
    )
    style.configure(
        "PendingCount.Active.TLabel",
        foreground=pal["accent_hi"],
        background=pal["bg"],
        font="TkHeadingFont",
    )
    style.configure(
        "DoneCount.TLabel",
        foreground=pal.get("done", pal["muted"]),
        background=pal["bg"],
    )
    style.configure("Link.TLabel", foreground=pal["accent"],
                    font="TkDefaultFont")
    style.configure("Divider.TSeparator", background=pal["border"])
    app.root.configure(bg=pal["bg"])
    style.configure("Header.TFrame", background=pal["header_bg"])
    style.configure("Toolbar.TFrame", background=pal["header_bg"])
    style.configure(
        "HeaderDivider.TSeparator", background=pal["header_border"],
    )
    app.media_mode_selector.set_theme(
        background=pal["header_bg"], track=pal["header_bg"],
        border=pal["header_accent"], active=pal["header_accent"],
        foreground=pal["fg"],
        muted=pal["header_muted"],
        accent_foreground=pal["accent_text"],
        canvas_background=pal["header_bg"],
    )
    app.media_mode_selector.set_mode(app.media_mode.get(), animate=False)
    app._apply_header_theme()
    apply_titlebar_theme(app.root, mode == "dark")
