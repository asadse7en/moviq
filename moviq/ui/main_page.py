import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from .. import resource
from ..core.providers.settings import PROVIDER_INFO
from .controls import (
    RoundedButton, SearchBox, SegmentedModeToggle, SegmentedPillControl,
    ToolbarIconGroup, ToolTip,
)

MAIN_SHORTCUTS = {
    "<Control-a>": "select_all",
    "<Control-A>": "select_all",
    "<Control-f>": "focus_search",
    "<Control-F>": "focus_search",
    "<Delete>": "remove_selected",
    "<Control-z>": "undo",
    "<Control-Z>": "undo",
    "<Return>": "match_manually",
}

def build_main_page(app, page):
    app.match_btn_var = tk.StringVar(value="Auto Match")
    app.manual_match_btn_var = tk.StringVar(value="Manual Match")
    app.apply_btn_var = tk.StringVar(value="Rename All")
    head = ttk.Frame(page, style="Header.TFrame")
    head.pack(fill="x", pady=(2, 12))
    logo = tk.Canvas(
        head, width=round(40 * app.UI_SCALE),
        height=round(40 * app.UI_SCALE),
        background=app.pal["header_bg"],
        highlightthickness=0, bd=0, takefocus=0,
    )
    logo.ui_scale = app.UI_SCALE
    app.logo_canvas = logo
    logo.pack(side="left")
    logo_size = round(40 * app.UI_SCALE)
    with Image.open(resource("assets/moviq-icon.png")) as icon:
        resized_icon = icon.resize(
            (logo_size, logo_size), Image.Resampling.LANCZOS,
        )
        app.logo_image = ImageTk.PhotoImage(resized_icon)
    logo.create_image(
        round(20 * app.UI_SCALE), round(20 * app.UI_SCALE),
        image=app.logo_image,
    )
    titles = ttk.Frame(head, style="Header.TFrame")
    titles.pack(side="left", padx=(12, 0), pady=1)
    ttk.Label(titles, text="Moviq",
              style="Title.TLabel").pack(anchor="w")
    ttk.Label(titles, style="HeaderSubtitle.TLabel",
              text="Match, review, and organize your media files."
              ).pack(anchor="w", pady=(2, 0))
    header_actions = ttk.Frame(head, style="Header.TFrame")
    header_actions.pack(side="right", padx=(12, 0))
    app.rename_all_button = RoundedButton(
        header_actions,
        app.apply_btn_var, app.apply, font="TkHeadingFont",
        ui_scale=app.UI_SCALE,
        bold=True, variant="accent", padx=18, pady=9, radius=6,
        background=app.pal["header_bg"],
    )
    app.rename_all_button.pack()
    ttk.Separator(
        page, orient="horizontal", style="HeaderDivider.TSeparator",
    ).pack(fill="x", pady=(0, 10))

    bar = ttk.Frame(page, style="Toolbar.TFrame")
    bar.pack(fill="x", pady=(0, 8))
    bar.columnconfigure(0, weight=1)
    left_controls = ttk.Frame(bar, style="Toolbar.TFrame")
    left_controls.grid(row=0, column=0, sticky="w")
    app.add_files_button = RoundedButton(
        left_controls, tk.StringVar(value="Add files"), app.add_files,
        font="TkDefaultFont", ui_scale=app.UI_SCALE,
        padx=12, background=app.pal["header_bg"],
    )
    app.add_files_button.pack(side="left", padx=(0, 6))
    app.add_folder_button = RoundedButton(
        left_controls, tk.StringVar(value="Add folder"), app.add_folder,
        font="TkDefaultFont", ui_scale=app.UI_SCALE,
        padx=12, background=app.pal["header_bg"],
    )
    app.add_folder_button.pack(side="left", padx=(0, 8))
    app.media_mode_selector = SegmentedModeToggle(
        left_controls,
        app.media_mode.get(),
        app.switch_media_mode,
        font="TkDefaultFont",
        background=app.pal["header_bg"],
        ui_scale=app.UI_SCALE,
    )
    app.media_mode_selector.pack(side="left")
    app.update_media_mode_selector()

    match_actions = ttk.Frame(bar, style="Toolbar.TFrame")
    app.match_actions = match_actions
    match_actions.grid(row=0, column=1, sticky="e")
    app.auto_match_button = RoundedButton(
        match_actions, app.match_btn_var, app.match_run,
        font="TkDefaultFont", ui_scale=app.UI_SCALE,
        padx=12, background=app.pal["header_bg"],
    )
    app.auto_match_button.pack(side="left", padx=(0, 6))
    app.manual_match_button = RoundedButton(
        match_actions, app.manual_match_btn_var, app.match_manually,
        font="TkDefaultFont", ui_scale=app.UI_SCALE,
        padx=12, background=app.pal["header_bg"],
    )
    app.manual_match_button.pack(side="left")
    app.toolbar_icon_group = ToolbarIconGroup(
        match_actions,
        (
            ("undo", "undo", app.undo, "Undo"),
            ("clear", "trash", app.clear, "Clear all files"),
            ("theme", app.theme_preference, app.cycle_theme, ""),
            ("settings", "settings", lambda: app.show_page("settings"),
             "Settings"),
        ),
        ui_scale=app.UI_SCALE,
    )
    app.toolbar_icon_group.pack(
        side="left", padx=(round(24 * app.UI_SCALE), 0),
    )
    app.icon_group_tooltip = ToolTip(
        app.toolbar_icon_group, "Undo", app.pal["card"], app.pal["fg"],
    )
    app.toolbar_icon_group.tooltip = app.icon_group_tooltip
    app.update_theme_button()
    app.toolbar_buttons = (
        app.add_files_button, app.add_folder_button,
        app.auto_match_button, app.manual_match_button,
    )
    for widget, text in (
        (app.add_files_button, "Add files"),
        (app.add_folder_button, "Add folder"),
        (app.auto_match_button, "Automatically match files"),
        (
            app.manual_match_button,
            "Manually match selected files, or all files if none are selected",
        ),
    ):
        tooltip = ToolTip(widget, text, app.pal["card"], app.pal["fg"])

    app.filter_row = ttk.Frame(page, style="Toolbar.TFrame")
    app.filter_row.pack(fill="x", pady=(6, 12))
    app.filter_vars = {
        key: tk.StringVar(value=f"{label} 0")
        for key, label in (
            ("all", "All"), ("ready", "Ready"),
            ("review", "Review"), ("done", "Done"),
        )
    }
    app.filter_control = SegmentedPillControl(
        app.filter_row, "all", app.set_status_filter,
        ui_scale=app.UI_SCALE,
        labels=tuple(variable.get() for variable in app.filter_vars.values()),
        values=tuple(app.filter_vars),
    )
    app.filter_control.pack(side="left")
    app.search_slot = ttk.Frame(app.filter_row, style="Toolbar.TFrame")
    app.search_slot.pack(
        side="left", fill="x", expand=True,
        padx=(round(24 * app.UI_SCALE), 0),
    )
    app.search_box = SearchBox(
        app.search_slot, app._search_changed, ui_scale=app.UI_SCALE,
    )
    app.search_box.pack(anchor="w")
    app.search_slot.bind("<Configure>", app._resize_search_box, add="+")
    app.source_control = SegmentedPillControl(
        app.filter_row,
        app.provider_settings.selected,
        app.select_source,
        ui_scale=app.UI_SCALE,
    )
    app.source_control.pack(side="right", padx=(8, 0))
    app.source_control_label = tk.Label(
        app.filter_row, text="Source", font="TkDefaultFont",
        background=app.pal["header_bg"], foreground=app.pal["muted"],
    )
    app.source_control_label.pack(side="right", padx=(0, 7))
    app.search_box.set_palette(app.pal)
    app.source_control.set_palette(app.pal)
    app.refresh_source_control()
    app.key_var.trace_add(
        "write", lambda *_args: app.refresh_source_control(),
    )
    app.reset_columns_button = ttk.Button(
        app.filter_row, text="Reset columns",
        command=app._reset_table_widths,
        style="ResetColumns.TButton",
    )
    app.reset_columns_button.pack(side="right")
    app.reset_columns_button.pack_forget()
    app.root.bind("<Configure>", app._update_toolbar_responsive, add="+")

    # Bottom bars are packed BEFORE the list so they are never pushed
    # out of view when the window is small.
    sb = ttk.Frame(page)
    sb.pack(side="bottom", fill="x", pady=(2, 0))
    app.status_var = tk.StringVar(value="Ready.")
    ttk.Label(sb, textvariable=app.status_var, style="Muted.TLabel"
              ).pack(side="left")

    app.detail_var = tk.StringVar(value="")
    ttk.Label(
        page, textvariable=app.detail_var, style="Muted.TLabel",
        wraplength=1100, justify="left",
    ).pack(side="bottom", fill="x", pady=(8, 6))

    frame = ttk.Frame(page, style="Table.TFrame", padding=1)
    frame.pack(fill="both", expand=True)
    table_content = ttk.Frame(frame, style="TableContent.TFrame")
    table_content.pack(fill="both", expand=True)
    cols = ("status", "new")
    app.tree = ttk.Treeview(table_content, columns=cols, show=("tree", "headings"),
                             selectmode="extended")
    app.status_column_width = app._status_column_width()
    app.table_min_widths = {
        "#0": 178, "status": app.status_column_width, "new": 150,
    }
    app._table_widths = {"#0": 240, "status": 100, "new": 150}
    app._table_columns_initialized = False
    app._table_columns_customized = False
    app.tree.heading(
        "#0", text="\u2610  Original name", anchor="w",
    )
    app.tree.column("#0", width=240, minwidth=178, anchor="w",
                     stretch=False)
    app._table_widths["status"] = app.status_column_width
    for c, text, w, anc in (("status", "Status", app.status_column_width, "w"),
                            ("new", "New Name", 150, "w")):
        app.tree.heading(c, text=text, anchor=anc)
        app.tree.column(
            c, width=w, minwidth=app.table_min_widths[c],
            anchor=anc, stretch=False,
        )
    app.tree.grid(row=0, column=0, sticky="nsew")
    table_content.rowconfigure(0, weight=1)
    table_content.columnconfigure(0, weight=1)
    app.tree.bind("<Configure>", app.fit_table_columns, add="+")
    app.tree.bind("<Configure>", app._schedule_status_cells, add="+")
    app.tree.bind(
        "<ButtonPress-1>", app._hide_tree_tooltip, add="+",
    )
    app.tree.bind("<ButtonPress-1>", app._begin_table_resize, add="+")
    app.tree.bind(
        "<ButtonRelease-1>",
        lambda _event: app.root.after_idle(app._finish_table_resize),
        add="+",
    )
    app.tree.bind("<Motion>", app.on_tree_hover, add="+")
    app.tree.bind("<Leave>", app.clear_tree_hover, add="+")
    app.tree.bind("<MouseWheel>", app.on_mousewheel, add="+")
    app.tree.bind("<Button-4>", lambda _event: app.scroll_rows(-1), add="+")
    app.tree.bind("<Button-5>", lambda _event: app.scroll_rows(1), add="+")
    app.tree.bind("<KeyPress>", app._hide_tooltip_on_scroll_key, add="+")
    app._status_cell_labels = {}
    app.empty_state = ttk.Frame(
        table_content, style="EmptyState.TFrame", padding=(24, 18),
    )
    app.empty_icon = tk.Canvas(
        app.empty_state, width=64, height=54,
        bg=app.pal["card"], highlightthickness=0,
    )
    app.empty_icon.pack(pady=(0, 10))
    app.empty_icon.create_line(
        10, 17, 10, 12, 30, 12, 37, 19, 53, 19, 53, 41,
        10, 41, 10, 17,
        fill=app.pal["muted"], width=3, capstyle="round",
        joinstyle="round",
    )
    app.empty_icon.create_oval(
        38, 30, 58, 50, fill=app.pal["card"],
        outline=app.pal["accent"], width=2,
    )
    app.empty_icon.create_line(
        48, 34, 48, 46, fill=app.pal["accent"], width=2,
        capstyle="round",
    )
    app.empty_icon.create_line(
        42, 40, 54, 40, fill=app.pal["accent"], width=2,
        capstyle="round",
    )
    app.empty_title_var = tk.StringVar(value="No files added yet")
    ttk.Label(
        app.empty_state, textvariable=app.empty_title_var,
        style="EmptyTitle.TLabel", anchor="center",
    ).pack(pady=(0, 6))
    app.empty_lbl = ttk.Label(
        app.empty_state,
        text="Click ‘Add Files’ or ‘Add Folder’ or Drag & Drop files here "
             "to get started.",
        style="EmptyHint.TLabel", anchor="center", justify="center",
        wraplength=520,
    )
    app.empty_lbl.pack()
    app.empty_state.place(relx=0.5, rely=0.5, anchor="center")

    app.tree.bind("<Button-1>", app.on_click, add="+")
    app.tree.bind("<Double-1>", app.on_double)
    app.tree.bind("<<TreeviewSelect>>", app.on_select)
    app.root.bind("<Escape>", lambda _e: app.deselect())
    for sequence, action in MAIN_SHORTCUTS.items():
        app.root.bind_all(
            sequence,
            lambda event, target=action: app._run_main_shortcut(
                event, target,
            ),
            add="+",
        )
    for sequence, action in (
        ("<Control-y>", "redo"),
        ("<Control-Y>", "redo"),
        ("<Control-Shift-z>", "redo"),
        ("<Control-Shift-Z>", "redo"),
    ):
        app.root.bind_all(
            sequence,
            lambda event, target=action: app._run_main_shortcut(
                event, target,
            ),
            add="+",
        )
    app._update_undo_button()
    app.tree.bind("<Motion>", app._schedule_tree_tooltip, add="+")
    app.tree.bind("<Leave>", app._hide_tree_tooltip, add="+")
    app.tree.bind("<Button-3>", app.show_menu, add="+")
    for b in ("<Button-2>", "<Control-Button-1>"):
        app.tree.bind(b, app.show_menu, add="+")
    app.menu = tk.Menu(app.root, tearoff=0)
    app.menu.add_command(
        label="Show file in Explorer", command=app.open_selected_file,
    )
    app.menu.add_separator()
    app.menu.add_command(label="Search / fix match...", command=app.fix_selected)
    app.menu.add_command(label="Accept this match", command=app.accept_selected)
    app.menu.add_command(label="Re-match automatically", command=app.rematch_selected)
    app.menu.add_command(label="Apply to selected now", command=app.apply)
    app.menu.add_separator()
    app.menu.add_command(label="Remove from list", command=app.remove_selected)
    app.column_menu = tk.Menu(app.root, tearoff=0)
    app.column_menu.add_command(
        label="Reset column widths", command=app._reset_table_widths,
    )
