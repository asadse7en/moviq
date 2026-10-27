import base64
import struct
import tkinter as tk
import tkinter.font as tkfont
import zlib
from weakref import WeakKeyDictionary, WeakSet
from tkinter import ttk

import customtkinter as ctk
from PIL import ImageGrab, ImageTk

from ..core.settings_store import resolve_system_theme
from .design import PALETTES
from .popups import apply_titlebar_theme

_THEME_LISTENERS = WeakKeyDictionary()
_THEME_CHANGES_IN_PROGRESS = WeakSet()


def register_theme_listener(root, listener):
    listeners = _THEME_LISTENERS.setdefault(root, [])
    if listener not in listeners:
        listeners.append(listener)


def unregister_theme_listener(root, listener):
    listeners = _THEME_LISTENERS.get(root)
    if listeners is None:
        return
    if listener in listeners:
        listeners.remove(listener)
    if not listeners:
        del _THEME_LISTENERS[root]


def apply_theme(root, mode):
    if root in _THEME_CHANGES_IN_PROGRESS:
        return None
    if mode == "system":
        mode = resolve_system_theme()
    if mode not in PALETTES:
        raise ValueError(f"Unknown theme mode: {mode}")

    cover = tk.Frame(
        root, bg=PALETTES[mode]["bg"], bd=0, highlightthickness=0,
    )
    _THEME_CHANGES_IN_PROGRESS.add(root)
    cover_scheduled_for_removal = False
    try:
        if root.winfo_viewable():
            bounds = (
                root.winfo_rootx(),
                root.winfo_rooty(),
                root.winfo_rootx() + root.winfo_width(),
                root.winfo_rooty() + root.winfo_height(),
            )
            try:
                snapshot = ImageGrab.grab(bbox=bounds)
            except OSError:
                snapshot = None
            if snapshot is not None:
                cover.snapshot = ImageTk.PhotoImage(snapshot, master=cover)
                tk.Label(
                    cover, image=cover.snapshot, bd=0, highlightthickness=0,
                ).place(relx=0, rely=0, relwidth=1, relheight=1)
        cover.place(relx=0, rely=0, relwidth=1, relheight=1)
        cover.lift()
        root.update_idletasks()
        ctk.set_appearance_mode(mode.capitalize())
        for listener in tuple(_THEME_LISTENERS.get(root, ())):
            listener(mode)
        apply_titlebar_theme(root, mode == "dark")
        root.update_idletasks()
        root.after(80, cover.destroy)
        cover_scheduled_for_removal = True
        return mode
    finally:
        _THEME_CHANGES_IN_PROGRESS.discard(root)
        if not cover_scheduled_for_removal:
            cover.destroy()


try:
    import sv_ttk
    HAVE_SV = True
except ImportError:
    sv_ttk = None
    HAVE_SV = False


def _switch_indicator_png(track_color, knob_color, active=False):
    width, height, scale = 40, 20, 4
    track = tuple(
        int(track_color[index:index + 2], 16)
        for index in (1, 3, 5)
    )
    knob = tuple(
        int(knob_color[index:index + 2], 16)
        for index in (1, 3, 5)
    )
    knob_x = 29.5 if active else 10.5
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            red = green = blue = alpha = 0
            for sample_y in range(scale):
                for sample_x in range(scale):
                    point_x = x + (sample_x + 0.5) / scale
                    point_y = y + (sample_y + 0.5) / scale
                    track_center_x = min(max(point_x, 9), 31)
                    on_track = (
                        (point_x - track_center_x) ** 2
                        + (point_y - 10) ** 2 <= 8 ** 2
                    )
                    on_knob = (
                        (point_x - knob_x) ** 2
                        + (point_y - 10) ** 2 <= 6.25 ** 2
                    )
                    if on_track or on_knob:
                        color = knob if on_knob else track
                        red += color[0]
                        green += color[1]
                        blue += color[2]
                        alpha += 255
            samples = scale ** 2
            coverage = alpha // 255
            pixels.extend((
                round(red / coverage) if coverage else 0,
                round(green / coverage) if coverage else 0,
                round(blue / coverage) if coverage else 0,
                round(alpha / samples),
            ))
    return _encode_png_rgba(width, height, pixels)


def _scale_thumb_png(accent_color, outline_color):
    size = 20
    scale = 4
    accent = tuple(
        int(accent_color[index:index + 2], 16)
        for index in (1, 3, 5)
    )
    outline = tuple(
        int(outline_color[index:index + 2], 16)
        for index in (1, 3, 5)
    )
    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            red = green = blue = alpha = 0
            for sample_y in range(scale):
                for sample_x in range(scale):
                    point_x = x + (sample_x + 0.5) / scale
                    point_y = y + (sample_y + 0.5) / scale
                    distance = (point_x - 10) ** 2 + (point_y - 10) ** 2
                    color = (
                        accent if distance <= 6.5 ** 2
                        else outline if distance <= 8.5 ** 2
                        else None
                    )
                    if color is not None:
                        red += color[0]
                        green += color[1]
                        blue += color[2]
                        alpha += 255
            samples = scale ** 2
            coverage = alpha // 255
            pixels.extend((
                round(red / coverage) if coverage else 0,
                round(green / coverage) if coverage else 0,
                round(blue / coverage) if coverage else 0,
                round(alpha / samples),
            ))
    return _encode_png_rgba(size, size, pixels)


def _encode_png_rgba(width, height, pixels):
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    def chunk(name, data):
        return (
            struct.pack(">I", len(data)) + name + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png)


def _configure_switch_style(app, style, palette):
    if not HAVE_SV:
        style.configure(
            "TCheckbutton",
            background=palette["preset_surface"],
            foreground=palette["fg"],
        )
        style.map(
            "TCheckbutton",
            indicatorcolor=[
                ("selected", palette["accent"]),
                ("!selected", palette["header_border_hi"]),
            ],
            background=[
                ("active", palette["preset_surface"]),
                ("!active", palette["preset_surface"]),
            ],
        )
        return

    mode = app.mode
    image_sets = getattr(app, "_moviq_switch_images", None)
    if image_sets is None:
        image_sets = {}
        app._moviq_switch_images = image_sets
    if mode not in image_sets:
        root = app.root
        element_prefix = f"Moviq.{mode.title()}"
        active_color = palette["accent"]
        active_hover = palette["accent_hi"]
        knob_color = palette["accent_text"]
        off_color = palette["header_border_hi"]
        image_sets[mode] = {
            "switch_element": f"{element_prefix}.Switch.indicator",
            "off": _tk_photoimage(
                root, off_color, palette["fg"], active=False,
            ),
            "off_hover": _tk_photoimage(
                root, palette["btn_hi"], palette["fg"], active=False,
            ),
            "off_pressed": _tk_photoimage(
                root, off_color, palette["fg"], active=False,
            ),
            "on": _tk_photoimage(
                root, active_color, knob_color, active=True,
            ),
            "on_hover": _tk_photoimage(
                root, active_hover, knob_color, active=True,
            ),
            "on_pressed": _tk_photoimage(
                root, active_color, knob_color, active=True,
            ),
            "disabled": _tk_photoimage(
                root, palette["btn"], palette["muted"], active=False,
            ),
            "on_disabled": _tk_photoimage(
                root, palette["btn"], palette["muted"], active=True,
            ),
        }
    images = image_sets[mode]
    if images["switch_element"] not in style.element_names():
        style.element_create(
            images["switch_element"],
            "image",
            images["off"],
            ("selected", "disabled", images["on_disabled"]),
            ("disabled", images["disabled"]),
            ("pressed", "selected", images["on_pressed"]),
            ("active", "selected", images["on_hover"]),
            ("focus", "selected", images["on_hover"]),
            ("selected", images["on"]),
            ("pressed", images["off_pressed"]),
            ("active", images["off_hover"]),
            ("focus", images["off_hover"]),
            width=40,
            sticky="",
        )
    layout = [
        (images["switch_element"], {"side": "left"}),
        ("Switch.label", {"side": "right", "expand": 1}),
    ]
    for switch_style in (
        "Moviq.Switch.TCheckbutton",
        "Moviq.Formatting.Switch.TCheckbutton",
    ):
        style.layout(switch_style, layout)
def _replace_scale_slider(layout, slider_element):
    replaced = []
    for element_name, options in layout:
        updated_options = dict(options)
        if "children" in updated_options:
            updated_options["children"] = _replace_scale_slider(
                updated_options["children"], slider_element,
            )
        if element_name.endswith("Scale.slider"):
            element_name = slider_element
        replaced.append((element_name, updated_options))
    return replaced


def _configure_scale_style(app, style, palette):
    style.configure(
        "Moviq.Horizontal.TScale",
        background=palette["preset_surface"],
        troughcolor=palette["preset_border"],
    )
    style.map(
        "Moviq.Horizontal.TScale",
        background=[
            ("disabled", palette["preset_surface"]),
            ("active", palette["preset_surface"]),
            ("!disabled", palette["preset_surface"]),
        ],
    )
    if not HAVE_SV:
        return

    mode = app.mode
    image_sets = getattr(app, "_moviq_scale_images", None)
    if image_sets is None:
        image_sets = {}
        app._moviq_scale_images = image_sets
    if mode not in image_sets:
        root = app.root
        element_name = f"Moviq.{mode.title()}.Scale.slider"
        image_sets[mode] = {
            "element": element_name,
            "normal": tk.PhotoImage(
                master=root,
                data=_scale_thumb_png(
                    palette["accent"], palette["preset_surface"],
                ),
            ),
            "hover": tk.PhotoImage(
                master=root,
                data=_scale_thumb_png(
                    palette["accent_hi"], palette["preset_surface"],
                ),
            ),
            "disabled": tk.PhotoImage(
                master=root,
                data=_scale_thumb_png(
                    palette["muted"], palette["preset_surface"],
                ),
            ),
        }
    images = image_sets[mode]
    if images["element"] not in style.element_names():
        style.element_create(
            images["element"],
            "image",
            images["normal"],
            ("disabled", images["disabled"]),
            ("pressed", images["hover"]),
            ("active", images["hover"]),
            sticky="",
        )
    default_layout = style.layout("Horizontal.TScale")
    style.layout(
        "Moviq.Horizontal.TScale",
        _replace_scale_slider(default_layout, images["element"]),
    )


def _tk_photoimage(root, track_color, knob_color, active):
    return tk.PhotoImage(
        master=root,
        data=_switch_indicator_png(
            track_color, knob_color, active=active,
        ),
    )


def _configure_table_fonts(app):
    family = tkfont.nametofont("TkTextFont").actual("family")
    for attribute, name, size, weight in (
        ("table_name_font", "MoviqTableNameFont", 16, "normal"),
        ("table_aux_font", "MoviqTableAuxFont", 13.5, "normal"),
        ("table_heading_font", "MoviqTableHeadingFont", 13.5, "bold"),
    ):
        font = getattr(app, attribute, None)
        options = {
            "family": family,
            "size": round(size * 0.75),
            "weight": weight,
        }
        if font is None:
            font = tkfont.Font(app.root, name=name, **options)
            setattr(app, attribute, font)
        else:
            font.configure(**options)


def apply_app_palette(app, mode):
    app.mode, app.pal = mode, PALETTES[mode]
    pal = app.pal
    _configure_table_fonts(app)
    app._status_tooltip.set_theme(
        pal["tooltip_bg"], pal["fg"], pal["chip_border"],
        pal["muted"],
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
                        foreground=pal["accent_text"], padding=(20, 8),
                        font="TkHeadingFont")
        style.map("Accent.TButton",
                  background=[("active", pal["accent_hi"])])
        style.configure("TEntry", padding=6)
    style.configure(".", font="TkDefaultFont")
    for style_name in (
        "TLabel", "TButton", "TCheckbutton",
        "TEntry", "TCombobox", "TSpinbox",
    ):
        style.configure(style_name, font="TkDefaultFont")
    _configure_switch_style(app, style, pal)
    _configure_scale_style(app, style, pal)
    if not HAVE_SV:
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
        "Table.TFrame",
        background=pal["header_separator"],
        bordercolor=pal["header_separator"],
        lightcolor=pal["header_separator"],
        darkcolor=pal["header_separator"],
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
        "Accent.TButton",
        background=pal["accent"],
        foreground=pal["accent_text"],
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
            ("active", pal["accent_text"]),
            ("!disabled", pal["accent_text"]),
        ],
    )
    if hasattr(app, "tree"):
        app.tree.set_theme(
            pal, app.table_name_font, app.table_aux_font,
            app.table_heading_font,
        )
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
    style.configure(
        "HeaderMuted.TLabel",
        foreground=pal["header_muted"],
        background=pal["header_bg"],
    )
    if hasattr(app, "source_control"):
        app.source_control_label.configure(
            background=pal["header_bg"], foreground=pal["muted"],
        )
        app.source_control.set_palette(pal)
        app.filter_control.set_palette(pal)
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
        background=pal["header_bg"], track=pal["switch_track"],
        border=pal["switch_border"], active=pal["switch_active"],
        foreground=pal["fg"],
        muted=pal["header_muted"],
        accent_foreground=pal["header_accent"],
        canvas_background=pal["header_bg"],
        active_border=pal["header_accent"],
    )
    app.media_mode_selector.set_mode(app.media_mode.get(), animate=False)
    app._apply_header_theme()
