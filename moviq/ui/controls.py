import math
import time
import tkinter as tk
import tkinter.font as tkfont

from .popups import set_window_icon
from .table_tooltip import TableTooltip

try:
    from PIL import Image, ImageDraw, ImageTk
    HAVE_PIL = True
except ImportError:
    Image = ImageDraw = ImageTk = None
    HAVE_PIL = False


def _tk_scale(widget):
    """Return the app's pixel-only scale."""
    return getattr(widget, "ui_scale", 1.0)


def _rounded_rectangle_points(x1, y1, x2, y2, radius, corners):
    width = x2 - x1
    height = y2 - y1
    radius = max(0, min(radius, height / 2, width / 2))
    radii = tuple(radius if rounded else 0 for rounded in corners)
    tl, tr, br, bl = radii
    points = []

    def add(x, y):
        points.extend((x, y))

    def arc(center_x, center_y, arc_radius, start, end):
        if not arc_radius:
            return
        for step in range(1, 13):
            angle = math.radians(start + (end - start) * step / 12)
            add(
                center_x + arc_radius * math.cos(angle),
                center_y + arc_radius * math.sin(angle),
            )

    add(x1 + tl, y1)
    add(x2 - tr, y1)
    arc(x2 - tr, y1 + tr, tr, -90, 0)
    add(x2, y2 - br)
    arc(x2 - br, y2 - br, br, 0, 90)
    add(x1 + bl, y2)
    arc(x1 + bl, y2 - bl, bl, 90, 180)
    add(x1, y1 + tl)
    arc(x1 + tl, y1 + tl, tl, 180, 270)
    return points


def _draw_segment_fill(canvas, x1, y1, x2, y2, radius, fill,
                       round_left, round_right):
    if round_left and round_right:
        canvas.create_rectangle(
            x1 + radius, y1, x2 - radius, y2, fill=fill, outline="",
        )
        canvas.create_rectangle(
            x1, y1 + radius, x1 + radius, y2 - radius,
            fill=fill, outline="",
        )
        canvas.create_rectangle(
            x2 - radius, y1 + radius, x2, y2 - radius,
            fill=fill, outline="",
        )
        for x1_arc, y1_arc, x2_arc, y2_arc, start in (
            (x1, y1, x1 + 2 * radius, y1 + 2 * radius, 90),
            (x2 - 2 * radius, y1, x2, y1 + 2 * radius, 0),
            (x2 - 2 * radius, y2 - 2 * radius, x2, y2, 270),
            (x1, y2 - 2 * radius, x1 + 2 * radius, y2, 180),
        ):
            canvas.create_arc(
                x1_arc, y1_arc, x2_arc, y2_arc,
                start=start, extent=90, style=tk.PIESLICE,
                fill=fill, outline="",
            )
        return
    if round_left:
        canvas.create_rectangle(
            x1 + radius, y1, x2, y2, fill=fill, outline="",
        )
        canvas.create_rectangle(
            x1, y1 + radius, x1 + radius, y2 - radius,
            fill=fill, outline="",
        )
        canvas.create_arc(
            x1, y1, x1 + 2 * radius, y1 + 2 * radius,
            start=90, extent=90, style=tk.PIESLICE,
            fill=fill, outline="",
        )
        canvas.create_arc(
            x1, y2 - 2 * radius, x1 + 2 * radius, y2,
            start=180, extent=90, style=tk.PIESLICE,
            fill=fill, outline="",
        )
    elif round_right:
        canvas.create_rectangle(
            x1, y1, x2 - radius, y2, fill=fill, outline="",
        )
        canvas.create_rectangle(
            x2 - radius, y1 + radius, x2, y2 - radius,
            fill=fill, outline="",
        )
        canvas.create_arc(
            x2 - 2 * radius, y1, x2, y1 + 2 * radius,
            start=0, extent=90, style=tk.PIESLICE,
            fill=fill, outline="",
        )
        canvas.create_arc(
            x2 - 2 * radius, y2 - 2 * radius, x2, y2,
            start=270, extent=90, style=tk.PIESLICE,
            fill=fill, outline="",
        )
    else:
        canvas.create_rectangle(
            x1, y1, x2, y2, fill=fill, outline="",
        )


def draw_rounded_rectangle(canvas, x1, y1, x2, y2, radius, fill, outline,
                           width=1, corners=(True, True, True, True),
                           background=None, state="normal"):
    """Draw a cached, supersampled rounded rectangle on a canvas."""
    if HAVE_PIL:
        target_width = max(1, round(x2 - x1))
        target_height = max(1, round(y2 - y1))
        scale = _tk_scale(canvas)
        background = background or canvas.cget("background")
        key = (
            target_width, target_height, radius, fill, outline, scale,
        )
        caches = getattr(canvas, "_rounded_image_cache", None)
        if caches is None:
            caches = {}
            canvas._rounded_image_cache = caches
        cache = caches.setdefault(state, {})
        image = cache.get(key)
        if image is None:
            image = _rounded_rectangle_image(
                canvas, target_width, target_height, radius,
                fill, outline, width, scale, background, corners,
            )
            cache[key] = image
        canvas.create_image(x1, y1, image=image, anchor="nw")
        return image

    _draw_rounded_rectangle_tk(
        canvas, x1, y1, x2, y2, radius, fill, outline, width, corners,
    )


def clear_rounded_image_cache(widget):
    cache = getattr(widget, "_rounded_image_cache", None)
    if cache is not None:
        cache.clear()


def _rounded_rectangle_image(
    canvas, width, height, radius, fill, outline, stroke_width, scale,
    background, corners,
):
    supersampling = 4
    image = Image.new(
        "RGB",
        (width * supersampling, height * supersampling),
        background,
    )
    draw = ImageDraw.Draw(image)
    scaled_stroke = max(1, round(stroke_width * scale * supersampling))
    inset = (
        min(
            scaled_stroke / 2,
            (width * supersampling - 1) / 2,
            (height * supersampling - 1) / 2,
        )
        if outline and outline != fill else 0
    )
    bounds = (
        inset, inset,
        width * supersampling - 1 - inset,
        height * supersampling - 1 - inset,
    )
    scaled_radius = max(
        0,
        min(
            round(radius * scale * supersampling),
            (bounds[2] - bounds[0]) / 2,
            (bounds[3] - bounds[1]) / 2,
        ),
    )
    if fill:
        draw.rounded_rectangle(
            bounds, radius=scaled_radius, fill=fill,
        )
        if not all(corners):
            corner_extent = scaled_radius + 1
            if not corners[0]:
                draw.rectangle(
                    (0, 0, corner_extent, corner_extent), fill=fill,
                )
            if not corners[1]:
                draw.rectangle(
                    (
                        width * supersampling - corner_extent, 0,
                        width * supersampling, corner_extent,
                    ),
                    fill=fill,
                )
            if not corners[2]:
                draw.rectangle(
                    (
                        width * supersampling - corner_extent,
                        height * supersampling - corner_extent,
                        width * supersampling, height * supersampling,
                    ),
                    fill=fill,
                )
            if not corners[3]:
                draw.rectangle(
                    (
                        0, height * supersampling - corner_extent,
                        corner_extent, height * supersampling,
                    ),
                    fill=fill,
                )
    if outline and outline != fill:
        draw.rounded_rectangle(
            bounds, radius=scaled_radius, outline=outline,
            width=scaled_stroke,
        )
    image = image.resize(
        (width, height), Image.Resampling.LANCZOS,
    )
    return ImageTk.PhotoImage(image, master=canvas)


def _draw_rounded_rectangle_tk(
    canvas, x1, y1, x2, y2, radius, fill, outline, width, corners,
):
    scale = _tk_scale(canvas)
    stroke = max(1, width * scale)
    inset = stroke / 2
    x1, y1, x2, y2 = (
        x1 + inset, y1 + inset, x2 - inset, y2 - inset,
    )
    radius = max(0, min(radius * scale, (x2 - x1) / 2, (y2 - y1) / 2))
    points = _rounded_rectangle_points(
        x1, y1, x2, y2, radius, corners,
    )
    if fill:
        canvas.create_polygon(
            points, fill=fill, outline="",
            smooth=True, splinesteps=36,
        )
    if outline and outline != fill:
        line_points = points
        if (
            math.isclose(points[-2], points[0])
            and math.isclose(points[-1], points[1])
        ):
            line_points = points[:-2]
        line_points = line_points + line_points[:2]
        canvas.create_line(
            line_points, fill=outline, width=stroke,
            smooth=False, joinstyle="round", capstyle="round",
        )


class RoundedButton(tk.Canvas):
    def __init__(self, master, textvariable, command, font="TkDefaultFont",
                 bold=False, variant="normal", padx=12, pady=8, radius=6,
                 background=None, ui_scale=1.0):
        super().__init__(
            master, width=1, height=1, highlightthickness=0, bd=0,
            background=background or master.winfo_toplevel().cget("background"),
            takefocus=0, cursor="hand2",
        )
        self.textvariable = textvariable
        self.command = command
        self.ui_scale = ui_scale
        self.font = tkfont.nametofont(font)
        self._bold = bold
        self.variant = variant
        self.padx = padx
        self.pady = pady
        self.radius = radius
        self._state = "normal"
        self._hover = False
        self._pressed = False
        self._appearance = None
        self._icon = None
        self.palette = {}
        self._text_trace = textvariable.trace_add("write", self._text_changed)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Return>", self._on_key)
        self.bind("<space>", self._on_key)
        self.bind("<FocusIn>", lambda _event: self._draw())
        self.bind("<FocusOut>", lambda _event: self._draw())
        self._text_changed()

    def set_theme(self, background, border, hover_background,
                  hover_border, foreground, muted, accent,
                  accent_hover, accent_foreground, canvas_background=None):
        self.palette = {
            "background": background, "border": border,
            "hover_background": hover_background,
            "hover_border": hover_border, "foreground": foreground,
            "muted": muted, "accent": accent,
            "accent_hover": accent_hover,
            "accent_foreground": accent_foreground,
        }
        clear_rounded_image_cache(self)
        self.configure(background=canvas_background or background)
        self._draw()

    def set_state(self, state):
        self._state = state
        self.configure(cursor="arrow" if state == "disabled" else "hand2")
        self._draw()

    def set_padding(self, padx):
        self.padx = padx
        self._size_to_content()

    def set_appearance(self, background, border, foreground, active=False):
        self._appearance = (background, border, foreground, active)
        self._draw()

    def set_text(self, text):
        self._icon = None
        self.textvariable.set(text)

    def set_icon(self, icon):
        self._icon = icon
        self._size_to_content()

    def invoke(self):
        if self._state == "normal":
            self.command()

    def _text_changed(self, *_args):
        self._size_to_content()

    def _size_to_content(self):
        scale = _tk_scale(self)
        if self._icon is None:
            width = (
                self.font.measure(self.textvariable.get())
                + self.padx * scale * 2
            )
        else:
            width = round(20 * scale) + round(self.padx * scale * 2)
        height = self.font.metrics("linespace") + self.pady * scale * 2
        size = (max(1, round(width)), max(1, round(height)))
        if (int(self.cget("width")), int(self.cget("height"))) != size:
            self.configure(width=size[0], height=size[1])
        self._draw()

    def _on_configure(self, _event):
        self._size_to_content()

    def _on_enter(self, _event):
        self._hover = True
        self._draw()

    def _on_leave(self, _event):
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event):
        if self._state == "normal":
            self._pressed = True
            self._draw()

    def _on_release(self, _event):
        self._pressed = False
        self._draw()

    def _on_click(self, _event):
        if self._state == "normal":
            self.invoke()
        return "break"

    def _on_key(self, _event):
        self.invoke()
        return "break"

    def _draw(self):
        if not self.palette or not self.winfo_exists():
            return
        palette = self.palette
        disabled = self._state == "disabled"
        if self._appearance is not None:
            fill, border, foreground, active = self._appearance
            if (self._hover or self._pressed) and not disabled:
                fill = palette["hover_background"]
                if not active:
                    border = palette["hover_border"]
            if disabled:
                foreground = palette["muted"]
        elif self.variant == "accent":
            fill = (
                palette["accent_hover"]
                if (self._hover or self._pressed) and not disabled
                else palette["accent"]
            )
            border = fill
            foreground = (
                palette["muted"] if disabled
                else palette["accent_foreground"]
            )
        else:
            fill = (
                palette["hover_background"]
                if (self._hover or self._pressed) and not disabled
                else palette["background"]
            )
            border = (
                palette["hover_border"]
                if self._hover and not disabled else palette["border"]
            )
            foreground = (
                palette["muted"] if disabled else palette["foreground"]
            )
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        draw_rounded_rectangle(
            self, 0, 0, width, height, self.radius,
            fill, border, background=self.cget("background"),
            state=(
                "pressed" if self._pressed else
                "hover" if self._hover else "normal"
            ),
        )
        if self._icon is None:
            self.create_text(
                width / 2, height / 2, text=self.textvariable.get(),
                fill=foreground,
                font="TkHeadingFont" if self._bold else self.font,
            )
        else:
            self._draw_icon(self._icon, width, height, foreground, fill)

    def _draw_icon(
        self, icon, width, height, color, background, center=None,
    ):
        scale = _tk_scale(self)
        center_x, center_y = center or (width / 2, height / 2)
        line_width = max(1, round(1.5 * scale))
        if icon == "light":
            self.create_oval(
                center_x - 4 * scale, center_y - 4 * scale,
                center_x + 4 * scale, center_y + 4 * scale,
                outline=color, width=line_width,
            )
            for angle in range(0, 360, 45):
                radians = math.radians(angle)
                inner = 7 * scale
                outer = 9 * scale
                self.create_line(
                    center_x + math.cos(radians) * inner,
                    center_y + math.sin(radians) * inner,
                    center_x + math.cos(radians) * outer,
                    center_y + math.sin(radians) * outer,
                    fill=color, width=line_width, capstyle="round",
                )
        elif icon == "dark":
            self.create_oval(
                center_x - 7 * scale, center_y - 7 * scale,
                center_x + 7 * scale, center_y + 7 * scale,
                fill=color, outline=color,
            )
            self.create_oval(
                center_x - 1 * scale, center_y - 9 * scale,
                center_x + 11 * scale, center_y + 3 * scale,
                fill=background, outline=background,
            )
        elif icon == "system":
            self.create_rectangle(
                center_x - 9 * scale, center_y - 7 * scale,
                center_x + 9 * scale, center_y + 5 * scale,
                outline=color, width=line_width,
            )
            self.create_line(
                center_x, center_y + 5 * scale,
                center_x, center_y + 8 * scale,
                fill=color, width=line_width, capstyle="round",
            )
            self.create_line(
                center_x - 4 * scale, center_y + 8 * scale,
                center_x + 4 * scale, center_y + 8 * scale,
                fill=color, width=line_width, capstyle="round",
            )
        elif icon == "settings":
            points = []
            tooth_radii = (7, 7, 10, 10)
            for index in range(32):
                angle = math.radians(index * 360 / 32 - 90)
                radius = tooth_radii[index % len(tooth_radii)] * scale
                points.extend((
                    center_x + math.cos(angle) * radius,
                    center_y + math.sin(angle) * radius,
                ))
            self.create_polygon(
                points, fill=color, outline=color,
            )
            self.create_oval(
                center_x - 3 * scale, center_y - 3 * scale,
                center_x + 3 * scale, center_y + 3 * scale,
                fill=background, outline=background,
            )
        elif icon == "undo":
            self.create_line(
                center_x + 7 * scale, center_y - 5 * scale,
                center_x - 3 * scale, center_y - 5 * scale,
                center_x - 8 * scale, center_y,
                center_x - 3 * scale, center_y + 5 * scale,
                fill=color, width=line_width, capstyle="round",
                joinstyle="round",
            )
            self.create_line(
                center_x - 7 * scale, center_y,
                center_x + 3 * scale, center_y,
                center_x + 7 * scale, center_y + 4 * scale,
                fill=color, width=line_width, capstyle="round",
                joinstyle="round",
            )
        elif icon == "trash":
            self.create_rectangle(
                center_x - 6 * scale, center_y - 5 * scale,
                center_x + 6 * scale, center_y + 8 * scale,
                outline=color, width=line_width,
            )
            self.create_line(
                center_x - 8 * scale, center_y - 7 * scale,
                center_x + 8 * scale, center_y - 7 * scale,
                fill=color, width=line_width, capstyle="round",
            )
            self.create_line(
                center_x - 3 * scale, center_y - 9 * scale,
                center_x + 3 * scale, center_y - 9 * scale,
                fill=color, width=line_width, capstyle="round",
            )
            for offset in (-2, 2):
                self.create_line(
                    center_x + offset * scale, center_y - 2 * scale,
                    center_x + offset * scale, center_y + 5 * scale,
                    fill=color, width=max(1, round(scale)),
                )


class ToolbarIconGroup(tk.Canvas):
    def __init__(self, master, actions, ui_scale=1.0, cell_width=50):
        self.ui_scale = ui_scale
        self.actions = tuple(actions)
        self.cell_width = round(cell_width * ui_scale)
        self.palette = {}
        self.states = {name: "normal" for name, *_rest in self.actions}
        self.icons = {
            name: icon for name, icon, _command, _tooltip in self.actions
        }
        self.commands = {
            name: command for name, _icon, command, _tooltip in self.actions
        }
        self.tooltips = {
            name: tooltip for name, _icon, _command, tooltip in self.actions
        }
        height = (
            tkfont.nametofont("TkDefaultFont").metrics("linespace")
            + round(16 * ui_scale)
        )
        super().__init__(
            master, width=self.cell_width * len(self.actions), height=height,
            highlightthickness=0, bd=0,
            background=master.winfo_toplevel().cget("background"),
            takefocus=0, cursor="hand2",
        )
        self._hover_index = None
        self._pressed_index = None
        self.bind("<Configure>", self._draw)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set_theme(
        self, background, border, hover_background, foreground, muted,
        canvas_background=None,
    ):
        self.palette = {
            "background": background,
            "border": border,
            "hover_background": hover_background,
            "foreground": foreground,
            "muted": muted,
        }
        clear_rounded_image_cache(self)
        self.configure(background=canvas_background or background)
        self._draw()

    def set_icon(self, name, icon):
        if name in self.icons:
            self.icons[name] = icon
            self._draw()

    def set_state(self, name, state):
        if name in self.states:
            self.states[name] = state
            self._draw()

    def set_tooltip(self, name, text):
        if name in self.tooltips:
            self.tooltips[name] = text

    def _index_at(self, x):
        return min(
            max(0, x // max(1, self.cell_width)),
            len(self.actions) - 1,
        )

    def _on_motion(self, event):
        index = self._index_at(event.x)
        self.configure(
            cursor=(
                "arrow" if self.states[self.actions[index][0]] == "disabled"
                else "hand2"
            ),
        )
        if index != self._hover_index:
            self._hover_index = index
            self._draw()
        if hasattr(self, "tooltip"):
            name = self.actions[index][0]
            self.tooltip.set_text(self.tooltips[name])

    def _on_leave(self, _event):
        self._hover_index = None
        self._pressed_index = None
        self.configure(cursor="hand2")
        self._draw()

    def _on_press(self, event):
        self._pressed_index = self._index_at(event.x)
        self._draw()
        return "break"

    def _on_release(self, event):
        index = self._index_at(event.x)
        if (
            self._pressed_index == index
            and self.states[self.actions[index][0]] == "normal"
        ):
            name = self.actions[index][0]
            self.commands[name]()
        self._pressed_index = None
        self._draw()
        return "break"

    def _draw(self, _event=None):
        if not self.palette or not self.winfo_exists():
            return
        palette = self.palette
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        self.delete("all")
        draw_rounded_rectangle(
            self, 0, 0, width, height, 6,
            palette["background"], palette["border"],
            background=self.cget("background"),
        )
        for index, (name, _icon, _command, _tooltip) in enumerate(
            self.actions,
        ):
            left = index * self.cell_width
            right = min(width, left + self.cell_width)
            if (
                index == self._hover_index
                and self.states[name] == "normal"
            ):
                self.create_rectangle(
                    left + (1 if index else 6), 1,
                    right - (1 if index < len(self.actions) - 1 else 6),
                    height - 1, fill=palette["hover_background"],
                    outline="",
                )
            icon_color = (
                palette["muted"] if self.states[name] == "disabled"
                else palette["foreground"]
            )
            icon = self.icons[name]
            if icon in (
                "light", "dark", "system", "settings", "undo", "trash",
            ):
                RoundedButton._draw_icon(
                    self, icon, width, height, icon_color,
                    palette["background"],
                    center=((left + right) / 2, height / 2),
                )
            if index:
                self.create_line(
                    left, 6 * _tk_scale(self), left,
                    height - 6 * _tk_scale(self),
                    fill=palette["border"], width=1,
                )


class SearchBox(tk.Frame):
    def __init__(self, master, on_change, ui_scale=1.0, width=320):
        self.ui_scale = ui_scale
        self.on_change = on_change
        self._count = None
        self._focused = False
        self.palette = {}
        self._updating = False
        height = (
            tkfont.nametofont("TkDefaultFont").metrics("linespace")
            + round(12 * ui_scale)
        )
        super().__init__(
            master, width=width, height=height, bd=0, highlightthickness=0,
        )
        self._moviq_focus_control = True
        self.pack_propagate(False)
        self.canvas = tk.Canvas(
            self, highlightthickness=0, bd=0, takefocus=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.variable = tk.StringVar()
        self.entry = tk.Entry(
            self, textvariable=self.variable, relief="flat", bd=0,
            highlightthickness=0, font="TkDefaultFont",
            insertwidth=1,
        )
        self._showing_placeholder = True
        self.variable.set("Search files")
        self.entry.place(
            y=round(2 * ui_scale),
            height=height - round(4 * ui_scale),
            x=round(36 * ui_scale),
        )
        self.variable.trace_add("write", self._value_changed)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<Escape>", self._escape)
        self.canvas.bind("<Button-1>", self._canvas_click)
        self.canvas.bind("<Motion>", self._canvas_motion)
        self.canvas.bind("<Configure>", self._draw)
        self.bind("<Configure>", self._layout_entry, add="+")

    def set_palette(self, palette):
        self.palette = palette
        self.canvas.configure(background=palette["header_bg"])
        self.entry.configure(
            background=palette["chip"],
            foreground=(
                palette["muted"] if self._showing_placeholder
                else palette["fg"]
            ),
            insertbackground=palette["fg"],
            selectbackground=palette["header_accent"],
            selectforeground=palette["accent_text"],
        )
        self._draw()

    def set_width(self, width):
        self.configure(width=max(1, round(width)))

    def get_query(self):
        return "" if self._showing_placeholder else self.variable.get()

    def set_count(self, count, total):
        self._count = (count, total) if self.get_query() else None
        self._layout_entry()
        self._draw()

    def focus(self):
        self.entry.focus_set()
        if self._showing_placeholder:
            self._showing_placeholder = False
            self.variable.set("")
            self.entry.configure(foreground=self.palette["fg"])
        self.entry.icursor("end")

    def clear_focus(self):
        self.winfo_toplevel().focus_set()

    def clear(self):
        if self._showing_placeholder:
            return
        self.variable.set("")
        self.entry.focus_set()

    def _value_changed(self, *_args):
        query = self.get_query()
        self._count = None
        if not self._showing_placeholder:
            self.entry.configure(foreground=self.palette.get("fg", "#000000"))
        self._layout_entry()
        self._draw()
        if not self._updating:
            self.on_change(query)

    def _focus_in(self, _event=None):
        self._focused = True
        if self._showing_placeholder:
            self._showing_placeholder = False
            self.variable.set("")
            self.entry.configure(foreground=self.palette.get("fg", "#000000"))
        self._draw()

    def _focus_out(self, _event=None):
        self._focused = False
        if not self.variable.get():
            self._showing_placeholder = True
            self._updating = True
            self.variable.set("Search files")
            self._updating = False
            self.entry.configure(
                foreground=self.palette.get("muted", "#666666"),
            )
        self._draw()

    def _escape(self, _event=None):
        self.clear()
        self.clear_focus()
        return "break"

    def _layout_entry(self, _event=None):
        if not self.winfo_exists():
            return
        scale = _tk_scale(self)
        width = self.winfo_width()
        left = round(36 * scale)
        right = round((96 if self.get_query() else 16) * scale)
        self.entry.place_configure(
            x=left, y=round(2 * scale),
            width=max(1, width - left - right),
            height=max(1, self.winfo_height() - round(4 * scale)),
        )

    def _canvas_click(self, event):
        width = self.canvas.winfo_width()
        if self.get_query() and event.x >= width - round(28 * _tk_scale(self)):
            self.clear()
            self.entry.focus_set()
            return "break"
        self.entry.focus_set()

    def _canvas_motion(self, event):
        width = self.canvas.winfo_width()
        clickable = (
            self.get_query()
            and event.x >= width - round(28 * _tk_scale(self))
        )
        self.canvas.configure(cursor="hand2" if clickable else "")

    def _draw(self, _event=None):
        if not self.palette or not self.winfo_exists():
            return
        canvas = self.canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        palette = self.palette
        canvas.delete("all")
        border = (
            palette["header_accent"]
            if self._focused or self.get_query()
            else palette["chip_border"]
        )
        draw_rounded_rectangle(
            canvas, 0, 0, width, height, 10,
            palette["chip"], border, background=palette["header_bg"],
        )
        scale = _tk_scale(self)
        icon_x, icon_y = round(18 * scale), height / 2
        canvas.create_oval(
            icon_x - 5 * scale, icon_y - 5 * scale,
            icon_x + 5 * scale, icon_y + 5 * scale,
            outline=palette["muted"], width=max(1, round(scale)),
        )
        canvas.create_line(
            icon_x + 4 * scale, icon_y + 4 * scale,
            icon_x + 9 * scale, icon_y + 9 * scale,
            fill=palette["muted"], width=max(1, round(1.5 * scale)),
            capstyle="round",
        )
        if self.get_query():
            canvas.create_line(
                width - 19 * scale, height / 2 - 4 * scale,
                width - 11 * scale, height / 2 + 4 * scale,
                fill=palette["muted"], width=max(1, round(1.5 * scale)),
                capstyle="round",
            )
            canvas.create_line(
                width - 11 * scale, height / 2 - 4 * scale,
                width - 19 * scale, height / 2 + 4 * scale,
                fill=palette["muted"], width=max(1, round(1.5 * scale)),
                capstyle="round",
            )
            if self._count is not None:
                canvas.create_text(
                    width - 36 * scale, height / 2,
                    text=f"{self._count[0]} of {self._count[1]}",
                    fill=palette["muted"], anchor="e",
                    font=("TkDefaultFont", 8),
                )


class ToolTip:
    def __init__(self, widget, text, background, foreground):
        self.widget = widget
        self.text = text
        self.background = background
        self.foreground = foreground
        self._after_id = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<FocusIn>", self._schedule, add="+")
        widget.bind("<FocusOut>", self._hide, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def set_text(self, text):
        self.text = text
        if self._window is not None:
            for child in self._window.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(text=text)

    def _schedule(self, _event=None):
        self._cancel_schedule()
        self._after_id = self.widget.after(500, self._show)

    def _show(self):
        self._after_id = None
        if not self.widget.winfo_exists() or self._window is not None:
            return
        window = tk.Toplevel(self.widget)
        set_window_icon(window)
        window.wm_overrideredirect(True)
        window.configure(bg=self.background)
        tk.Label(
            window, text=self.text, bg=self.background, fg=self.foreground,
            padx=7, pady=3,
        ).pack()
        window.update_idletasks()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        window.geometry(f"+{x}+{y}")
        self._window = window

    def _hide(self, _event=None):
        self._cancel_schedule()
        if self._window is not None:
            self._window.destroy()
            self._window = None

    def _cancel_schedule(self):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _on_destroy(self, event):
        if event.widget is self.widget:
            self._hide()


def _mix_color(start, end, amount):
    start_rgb = tuple(int(start[index:index + 2], 16) for index in (1, 3, 5))
    end_rgb = tuple(int(end[index:index + 2], 16) for index in (1, 3, 5))
    values = tuple(
        max(0, min(255, round(first + (last - first) * amount)))
        for first, last in zip(start_rgb, end_rgb)
    )
    return "#" + "".join(f"{value:02x}" for value in values)


class SegmentedModeToggle(tk.Canvas):
    def __init__(self, master, mode, on_select, font="TkDefaultFont",
                 background=None, fit_content=True, ui_scale=1.0,
                 labels=("Movies", "TV Shows"),
                 values=("movie", "tv")):
        super().__init__(
            master, width=1, height=1, highlightthickness=0, bd=0,
            background=background or master.winfo_toplevel().cget("background"),
            takefocus=True,
        )
        self.mode = mode
        self.on_select = on_select
        self.ui_scale = ui_scale
        self.font = tkfont.nametofont(font)
        self.labels = labels
        self.values = values
        self.horizontal_padding = 14
        self.vertical_padding = 8
        self.fit_content = fit_content
        self.palette = {}
        self.position = 0.0 if mode == values[0] else 1.0
        self._animation_id = None
        self._animation_start = 0.0
        self._animation_from = self.position
        self._animation_to = self.position
        self.bind("<Button-1>", self._on_click)
        self.bind("<Left>", lambda _event: self.on_select(self.values[0]))
        self.bind("<Right>", lambda _event: self.on_select(self.values[1]))
        self.bind("<Configure>", self._on_configure)
        self._size_to_content()

    def _segment_widths(self):
        scale = _tk_scale(self)
        padding = self.horizontal_padding * scale
        return tuple(
            self.font.measure(label) + 2 * padding
            for label in self.labels
        )

    def _size_to_content(self):
        scale = _tk_scale(self)
        widths = self._segment_widths()
        height = self.font.metrics("linespace") + (
            2 * self.vertical_padding * scale
        )
        border = max(1, round(scale))
        size = (
            round(sum(widths) + border * 2),
            round(height + border * 2),
        )
        if self.fit_content and (
            int(self.cget("width")), int(self.cget("height"))
        ) != size:
            self.configure(width=size[0], height=size[1])

    def _on_configure(self, _event):
        self._size_to_content()
        self._draw()

    def set_theme(self, background, track, border, active, foreground, muted,
                  accent_foreground=None, canvas_background=None):
        self.palette = {
            "background": background,
            "track": track,
            "border": border,
            "active": active,
            "foreground": foreground,
            "muted": muted,
            "accent_foreground": accent_foreground or foreground,
        }
        clear_rounded_image_cache(self)
        self.configure(background=canvas_background or background)
        self._size_to_content()
        self._draw()

    def set_mode(self, mode, animate=True):
        self.mode = mode
        target = 0.0 if mode == self.values[0] else 1.0
        if self._animation_id is not None:
            self.after_cancel(self._animation_id)
            self._animation_id = None
        if not animate:
            self.position = target
            self._draw()
            return
        self._animation_from = self.position
        self._animation_to = target
        self._animation_start = time.monotonic()
        self._advance_slide()

    def _on_click(self, event):
        self.focus_set()
        widths = self._segment_widths()
        inset = max(1, _tk_scale(self)) / 2
        available = self.winfo_width() - 1 - inset * 2
        boundary = inset + available * widths[0] / sum(widths)
        side = self.values[0] if event.x < boundary else self.values[1]
        self.on_select(side)
        return "break"

    def _advance_slide(self):
        progress = min(1.0, (time.monotonic() - self._animation_start) / 0.25)
        eased = 1 - (1 - progress) ** 3
        self.position = self._animation_from + (
            self._animation_to - self._animation_from
        ) * eased
        self._draw()
        if progress < 1:
            self._animation_id = self.after(16, self._advance_slide)
        else:
            self._animation_id = None

    def _draw(self):
        if not self.palette or not self.winfo_exists():
            return
        palette = self.palette
        width = self.winfo_width()
        height = self.winfo_height()
        scale = _tk_scale(self)
        line_width = max(1, scale)
        inset = line_width / 2
        if width <= line_width or height <= line_width:
            return
        widths = self._segment_widths()
        total_segment_width = sum(widths)
        available_width = width - 1 - line_width
        segment_widths = tuple(
            available_width * part / total_segment_width for part in widths
        )
        outer_x1, outer_y1 = 0, 0
        outer_x2, outer_y2 = width - 1, height - 1
        inner_x1, inner_y1 = inset, inset
        inner_x2, inner_y2 = outer_x2 - inset, outer_y2 - inset
        self.delete("all")
        active_width = (
            segment_widths[0]
            + self.position * (segment_widths[1] - segment_widths[0])
        )
        active_x = inner_x1 + self.position * (
            (inner_x2 - inner_x1) - active_width
        )
        active_right = active_x + active_width
        outer_left = abs(active_x - inner_x1) < 1
        outer_right = abs(active_right - inner_x2) < 1
        active_radius = min(
            max(0, 6 - inset / scale),
            (inner_y2 - inner_y1) / (2 * scale),
            active_width / (2 * scale),
        )
        if HAVE_PIL:
            draw_rounded_rectangle(
                self, outer_x1, outer_y1, width, height, 6,
                palette["track"], palette["border"],
            )
            draw_rounded_rectangle(
                self, active_x, inner_y1, active_right, inner_y2,
                active_radius, palette["active"], "",
                corners=(outer_left, outer_right, outer_right, outer_left),
                background=palette["track"],
                state=("segment", outer_left, outer_right),
            )
        else:
            draw_rounded_rectangle(
                self, outer_x1, outer_y1, outer_x2, outer_y2, 6,
                palette["track"], "",
            )
            _draw_segment_fill(
                self, active_x, inner_y1, active_right, inner_y2,
                active_radius * scale, palette["active"],
                outer_left, outer_right,
            )
            draw_rounded_rectangle(
                self, outer_x1, outer_y1, outer_x2, outer_y2, 6,
                "", palette["border"],
            )
        segment_start = inner_x1
        pill_center = active_x + active_width / 2
        for index, label in enumerate(self.labels):
            segment_width = segment_widths[index]
            center = segment_start + segment_width / 2
            emphasis = max(
                0.0,
                min(
                    1.0,
                    1 - abs(center - pill_center) / max(active_width, 1),
                ),
            )
            text_color = _mix_color(
                palette["muted"], palette.get(
                    "accent_foreground", palette["foreground"],
                ), emphasis,
            )
            self.create_text(
                center, height / 2, text=label, fill=text_color,
                font=self.font,
            )
            segment_start += segment_width


class SegmentedPillControl(tk.Canvas):
    def __init__(
        self, master, selected, on_select, ui_scale=1.0,
        labels=("Auto", "TMDb", "OMDb", "TVmaze", "AniList"),
        values=("automatic", "tmdb", "omdb", "tvmaze", "anilist"),
    ):
        super().__init__(
            master, width=1, height=1, highlightthickness=0, bd=0,
            background=master.winfo_toplevel().cget("background"),
            takefocus=0, cursor="hand2",
        )
        self.ui_scale = ui_scale
        self.labels = labels
        self.values = values
        self.on_select = on_select
        self.selected = selected if selected in values else values[0]
        self.available = {value: True for value in values}
        self.palette = {}
        self.font = tkfont.nametofont("TkDefaultFont")
        self.horizontal_padding = 9
        self.vertical_padding = 6
        self._hover_index = None
        self.bind("<Configure>", self._draw)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._size_to_content()

    def _segment_widths(self):
        scale = _tk_scale(self)
        return tuple(
            self.font.measure(label) + 2 * self.horizontal_padding * scale
            for label in self.labels
        )

    def _size_to_content(self):
        scale = _tk_scale(self)
        widths = self._segment_widths()
        height = (
            self.font.metrics("linespace")
            + 2 * self.vertical_padding * scale
        )
        self.configure(
            width=round(sum(widths)) + 2,
            height=round(height),
        )

    def set_palette(self, palette):
        self.palette = palette
        clear_rounded_image_cache(self)
        self.configure(background=palette["header_bg"])
        self._draw()

    def set_selected(self, selected):
        if selected in self.values:
            self.selected = selected
            self._draw()

    def set_labels(self, labels):
        if len(labels) != len(self.values):
            raise ValueError("Each segment must have a label.")
        self.labels = tuple(labels)
        self._size_to_content()
        self._draw()

    def set_available(self, available):
        self.available = {
            value: bool(available.get(value, False))
            for value in self.values
        }
        self._draw()

    def _index_at(self, x):
        widths = self._segment_widths()
        position = 1
        for index, width in enumerate(widths):
            position += width
            if x < position:
                return index
        return len(widths) - 1

    def _on_motion(self, event):
        index = self._index_at(event.x)
        if index != self._hover_index:
            self._hover_index = index
            self._draw()

    def _on_leave(self, _event):
        self._hover_index = None
        self._draw()

    def _on_click(self, event):
        self.on_select(self.values[self._index_at(event.x)])
        return "break"

    def _draw(self, _event=None):
        if not self.palette or not self.winfo_exists():
            return
        palette = self.palette
        scale = _tk_scale(self)
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 2 or height <= 2:
            return
        self.delete("all")
        widths = self._segment_widths()
        available_width = width - 2
        total = sum(widths)
        positions = [1]
        for segment_width in widths:
            positions.append(
                positions[-1] + available_width * segment_width / total
            )
        draw_rounded_rectangle(
            self, 0, 0, width - 1, height - 1, 6,
            palette["chip"], palette["chip_border"],
            background=self.cget("background"),
        )
        for index, value in enumerate(self.values):
            left, right = positions[index], positions[index + 1]
            selected = value == self.selected
            hovered = index == self._hover_index and self.available[value]
            if selected:
                fill = palette["chip_active"]
                color = (
                    palette["fg"] if self.available[value]
                    else palette["muted"]
                )
            elif hovered:
                fill = palette["header_btn_hi"]
                color = palette["fg"]
            else:
                fill = palette["chip"]
                color = (
                    palette["fg"] if self.available[value]
                    else palette["muted"]
                )
            if selected or hovered:
                draw_rounded_rectangle(
                    self, left, 1, right, height - 1,
                    6,
                    fill,
                    palette["header_accent"] if selected else "",
                    corners=(True, True, True, True),
                    background=palette["chip"],
                    state=("source", value, selected, hovered),
                )
            center = (left + right) / 2
            self.create_text(
                center, height / 2, text=self.labels[index],
                fill=color, font=self.font,
            )
            if index:
                self.create_line(
                    left, 4 * scale, left, height - 4 * scale,
                    fill=palette["chip_border"],
                    width=max(1, round(scale)),
                )


class SettingsDropdown(tk.Frame):
    def __init__(
        self, master, variable, options, command, palette,
        descriptions=None, width=200, height=34,
    ):
        super().__init__(
            master, width=width, height=height, bd=0, highlightthickness=0,
        )
        self._moviq_focus_control = True
        self.pack_propagate(False)
        self.variable = variable
        self.options = tuple(options)
        self.command = command
        self.descriptions = descriptions or {}
        self.palette = palette
        self._popup = None
        self._popup_canvas = None
        self._options_frame = None
        self._option_rows = []
        self._active_index = 0
        self._hover = False
        self._open = False
        self._animation_id = None
        self._chevron_id = None
        self._chevron_angle = 0.0
        self._outside_binding = None
        self._variable_trace = variable.trace_add(
            "write", self._value_changed,
        )
        self._destroying = False
        self.canvas = tk.Canvas(
            self, width=width, height=height, bd=0,
            highlightthickness=0, takefocus=True, cursor="hand2",
            background=palette["card"],
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._draw_control)
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._toggle)
        self.canvas.bind("<Return>", self._toggle)
        self.canvas.bind("<space>", self._toggle)
        self.canvas.bind("<Down>", self._open_down)
        self.canvas.bind("<FocusIn>", self._draw_control)
        self.canvas.bind("<FocusOut>", self._draw_control)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._draw_control()

    def set_palette(self, palette):
        self.palette = palette
        self.canvas.configure(background=palette["card"])
        self._draw_control()
        if self._popup is not None:
            self._refresh_popup_palette()
            self._style_options()

    def _value_changed(self, *_args):
        self._draw_control()
        if self._popup is not None:
            self._style_options()

    def _display_value(self):
        value = self.variable.get()
        return next(
            (label for label, option_value in self.options
             if option_value == value),
            value,
        )

    def _colors(self):
        palette = self.palette
        dark = palette["bg"] == "#1c1c1c"
        return {
            "background": "#242424" if dark else palette["preset_surface"],
            "menu": "#242424" if dark else palette["preset_surface"],
            "hover": "#333333" if dark else palette["preset_hover"],
            "border": palette["accent"] if (
                self._hover or self._open
                or self.canvas == self.canvas.focus_get()
            ) else palette["preset_border"],
            "foreground": palette["fg"],
            "muted": palette["muted"],
            "accent": palette["accent"],
            "outline": palette["accent"],
            "shadow": "#161616" if dark else "#cbd5e1",
        }

    def _draw_control(self, _event=None):
        if not self.winfo_exists():
            return
        colors = self._colors()
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        self.canvas.delete("all")
        draw_rounded_rectangle(
            self.canvas, 0, 0, width, height, 6,
            colors["background"], colors["border"],
            background=self.canvas.cget("background"),
        )
        self.canvas.create_text(
            12, height / 2, anchor="w", text=self._display_value(),
            fill=colors["foreground"], font=("TkDefaultFont", 10),
        )
        center_x, center_y = width - 16, height / 2
        base = ((-4, -2), (0, 2), (4, -2))
        points = []
        for x, y in base:
            rotated_x = x * math.cos(self._chevron_angle) - y * math.sin(
                self._chevron_angle,
            )
            rotated_y = x * math.sin(self._chevron_angle) + y * math.cos(
                self._chevron_angle,
            )
            points.extend((center_x + rotated_x, center_y + rotated_y))
        self.canvas.create_line(
            points, fill=colors["muted"], width=2, capstyle="round",
            joinstyle="round",
        )

    def _on_enter(self, _event):
        self._hover = True
        self._draw_control()

    def _on_leave(self, _event):
        self._hover = False
        self._draw_control()

    def _toggle(self, _event=None):
        self.canvas.focus_set()
        if self._popup is None:
            self.open()
        else:
            self.close()
        return "break"

    def _open_down(self, _event):
        self.canvas.focus_set()
        if self._popup is None:
            self.open()
        return "break"

    def open(self):
        if self._popup is not None:
            return
        self._open = True
        self._active_index = self._selected_index()
        self._build_popup()
        self._open_started = time.monotonic()
        self._animate_chevron(True)
        self._animate_open()
        self._outside_binding = self.bind_all(
            "<Button-1>", self._outside_click, add="+",
        )
        self._popup.focus_set()

    def close(self, restore_focus=False):
        if self._popup is None:
            return
        popup = self._popup
        self._popup = None
        self._open = False
        if self._animation_id is not None:
            self.after_cancel(self._animation_id)
            self._animation_id = None
        if hasattr(self, "_open_started"):
            del self._open_started
        if self._outside_binding is not None:
            self._root()._unbind(
                ("bind", "all", "<Button-1>"), self._outside_binding,
            )
            self._outside_binding = None
        popup.destroy()
        self._popup_canvas = None
        self._options_frame = None
        self._option_rows = []
        if not self._destroying:
            self._animate_chevron(False)
        if restore_focus:
            self.canvas.focus_set()

    def _build_popup(self):
        colors = self._colors()
        popup = tk.Toplevel(self)
        self._popup = popup
        popup._moviq_focus_control = True
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self.winfo_toplevel())
        popup.configure(background=colors["shadow"])
        popup.bind("<Up>", lambda _event: self._move_active(-1))
        popup.bind("<Down>", lambda _event: self._move_active(1))
        popup.bind("<Return>", self._choose_active)
        popup.bind("<space>", self._choose_active)
        popup.bind("<Escape>", self._close_escape)

        row_heights = [
            50 if self.descriptions.get(value) else 32
            for _label, value in self.options
        ]
        popup_width = max(200, self.winfo_width())
        popup_height = sum(row_heights) + len(row_heights) * 4 + 10
        canvas = tk.Canvas(
            popup, width=popup_width, height=popup_height,
            bd=0, highlightthickness=0, background=colors["shadow"],
        )
        canvas.pack()
        self._popup_canvas = canvas
        draw_rounded_rectangle(
            canvas, 1, 1, popup_width - 2, popup_height - 2, 8,
            colors["menu"], self.palette["preset_border"],
            background=colors["shadow"],
        )
        options_frame = tk.Frame(canvas, background=colors["menu"])
        self._options_frame = options_frame
        canvas.create_window(
            5, 5, anchor="nw", width=popup_width - 10,
            height=popup_height - 10, window=options_frame,
        )
        for index, (label, value) in enumerate(self.options):
            row = tk.Frame(
                options_frame, height=row_heights[index],
                background=colors["menu"], highlightthickness=1,
                highlightbackground=colors["menu"],
                highlightcolor=colors["menu"],
            )
            row.pack(fill="x", padx=2, pady=2)
            row.pack_propagate(False)
            text = tk.Frame(row, background=colors["menu"])
            text.pack(side="left", fill="both", expand=True, padx=(8, 2))
            title = tk.Label(
                text, text=label, anchor="w",
                background=colors["menu"], foreground=colors["foreground"],
                font=("TkDefaultFont", 10),
            )
            title.pack(anchor="w", pady=(2, 0))
            description = self.descriptions.get(value)
            detail = None
            if description:
                detail = tk.Label(
                    text, text=description, anchor="w",
                    background=colors["menu"], foreground=colors["muted"],
                    font=("TkDefaultFont", 8),
                )
                detail.pack(anchor="w")
            check = tk.Label(
                row, text="✓" if value == self.variable.get() else "",
                width=2, anchor="center",
                background=colors["menu"], foreground=colors["accent"],
                font=("TkDefaultFont", 10),
            )
            check.pack(side="right", padx=(0, 5))
            widgets = [row, text, title, check]
            if detail is not None:
                widgets.append(detail)
            for widget in widgets:
                widget.bind(
                    "<Enter>",
                    lambda _event, selected=index: self._hover_option(
                        selected, True,
                    ),
                )
                widget.bind(
                    "<Leave>",
                    lambda _event, selected=index: self._hover_option(
                        selected, False,
                    ),
                )
                widget.bind(
                    "<Button-1>",
                    lambda _event, selected=index: self._choose(selected),
                )
            self._option_rows.append((row, widgets, value, detail, check))
        popup.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width() - popup_width
        y = self.winfo_rooty() + self.winfo_height()
        popup.geometry(
            f"{popup_width}x{popup_height}+{x}+{y + 4}",
        )
        popup.deiconify()
        popup.lift()
        self._style_options()

    def _animate_open(self):
        popup = self._popup
        if popup is None:
            return
        geometry = popup.geometry().split("+")
        size = geometry[0]
        x, y = int(geometry[1]), int(geometry[2])
        final_y = self.winfo_rooty() + self.winfo_height()
        elapsed = time.monotonic() - self._open_started
        progress = min(1.0, elapsed / 0.12)
        eased = 1 - (1 - progress) ** 3
        popup.geometry(f"{size}+{x}+{round(final_y + 4 * (1 - eased))}")
        try:
            popup.attributes("-alpha", 0.65 + 0.35 * eased)
        except tk.TclError:
            pass
        if progress < 1:
            self._animation_id = self.after(15, self._animate_open)
        else:
            self._animation_id = None
            if hasattr(self, "_open_started"):
                del self._open_started

    def _animate_chevron(self, opening):
        if self._chevron_id is not None:
            self.after_cancel(self._chevron_id)
            self._chevron_id = None
        start = self._chevron_angle
        end = math.pi if opening else 0.0
        started = time.monotonic()

        def advance():
            progress = min(1.0, (time.monotonic() - started) / 0.12)
            eased = 1 - (1 - progress) ** 3
            self._chevron_angle = start + (end - start) * eased
            self._draw_control()
            if progress < 1:
                self._chevron_id = self.after(15, advance)
            else:
                self._chevron_id = None

        advance()

    def _selected_index(self):
        return next(
            (index for index, (_label, value) in enumerate(self.options)
             if value == self.variable.get()),
            0,
        )

    def _move_active(self, step):
        self._active_index = (
            self._active_index + step
        ) % len(self.options)
        self._style_options()
        return "break"

    def _choose_active(self, _event=None):
        self._choose(self._active_index)
        return "break"

    def _close_escape(self, _event=None):
        self.close(restore_focus=True)
        return "break"

    def _choose(self, index):
        _label, value = self.options[index]
        self.variable.set(value)
        self.close(restore_focus=True)
        self.command(value)
        return "break"

    def _hover_option(self, index, hovered):
        if hovered:
            self._active_index = index
        self._style_options(hovered_index=index if hovered else None)

    def _style_options(self, hovered_index=None):
        if self._popup is None:
            return
        colors = self._colors()
        selected_value = self.variable.get()
        for index, (row, widgets, value, detail, check) in enumerate(
            self._option_rows,
        ):
            focused = index == self._active_index
            hovered = index == hovered_index
            background = (
                colors["hover"] if hovered
                else colors["menu"]
            )
            row.configure(
                background=background,
                highlightbackground=(
                    colors["outline"] if focused else background
                ),
                highlightcolor=(
                    colors["outline"] if focused else background
                ),
            )
            for widget in widgets:
                if widget is row:
                    continue
                widget.configure(background=background)
                if widget is check:
                    widget.configure(
                        foreground=colors["accent"],
                        text="✓" if value == selected_value else "",
                    )
                elif widget is detail:
                    widget.configure(foreground=colors["muted"])
                elif isinstance(widget, tk.Label):
                    widget.configure(
                        foreground=(
                            colors["accent"]
                            if value == selected_value
                            else colors["foreground"]
                        ),
                    )

    def _refresh_popup_palette(self):
        if self._popup is None or self._popup_canvas is None:
            return
        colors = self._colors()
        canvas = self._popup_canvas
        width = int(canvas.cget("width"))
        height = int(canvas.cget("height"))
        self._popup.configure(background=colors["shadow"])
        canvas.configure(background=colors["shadow"])
        canvas.delete("all")
        draw_rounded_rectangle(
            canvas, 1, 1, width - 2, height - 2, 8,
            colors["menu"], self.palette["preset_border"],
            background=colors["shadow"],
        )
        self._options_frame.configure(background=colors["menu"])
        canvas.create_window(
            5, 5, anchor="nw", width=width - 10,
            height=height - 10, window=self._options_frame,
        )

    def _outside_click(self, event):
        widget_path = str(event.widget)
        control_path = str(self)
        popup_path = str(self._popup) if self._popup is not None else ""
        if (
            not widget_path.startswith(control_path)
            and not (popup_path and widget_path.startswith(popup_path))
        ):
            self.after_idle(self.close)

    def _on_destroy(self, event):
        if event.widget != self:
            return
        self._destroying = True
        if self._chevron_id is not None:
            self.after_cancel(self._chevron_id)
            self._chevron_id = None
        self.close()
        try:
            self.variable.trace_remove("write", self._variable_trace)
        except tk.TclError:
            pass
