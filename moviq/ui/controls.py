import math
import time
import tkinter as tk
import tkinter.font as tkfont

from .popups import set_window_icon

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
        self.textvariable.set(text)

    def invoke(self):
        if self._state == "normal":
            self.command()

    def _text_changed(self, *_args):
        self._size_to_content()

    def _size_to_content(self):
        scale = _tk_scale(self)
        text = self.textvariable.get()
        width = self.font.measure(text) + self.padx * scale * 2
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
        self.create_text(
            width / 2, height / 2, text=self.textvariable.get(),
            fill=foreground,
            font="TkHeadingFont" if self._bold else self.font,
        )


class TableTooltip:
    def __init__(self, master, background, foreground, border, muted=None):
        self.master = master
        self.background = background
        self.foreground = foreground
        self.border = border
        self.muted = muted or foreground
        self._after_id = None
        self._window = None
        self._key = None

    def set_theme(self, background, foreground, border, muted=None):
        self.background = background
        self.foreground = foreground
        self.border = border
        self.muted = muted or foreground
        self.hide()

    def schedule(self, key, content, x, y, delay=400):
        if key == self._key:
            return
        if self._after_id is not None:
            self.master.after_cancel(self._after_id)
            self._after_id = None
        if not content:
            self.hide()
            return
        self._key = key
        self._after_id = self.master.after(
            delay, lambda: self._show(key, content, x, y),
        )

    def _show(self, key, content, x, y):
        self._after_id = None
        if key != self._key:
            return
        window = self._window
        if window is None:
            window = tk.Toplevel(self.master)
            set_window_icon(window)
            window.wm_overrideredirect(True)
            window.configure(background=self.border)
            self._window = window
        for child in window.winfo_children():
            child.destroy()
        panel = tk.Frame(window, background=self.background, padx=9, pady=7)
        panel.pack(padx=1, pady=1)
        if isinstance(content, str):
            tk.Label(
                panel, text=content, justify="left", anchor="w",
                background=self.background, foreground=self.foreground,
                wraplength=400,
            ).pack()
        else:
            normal_font = tkfont.nametofont("TkTextFont")
            label_font = (
                normal_font.actual("family"),
                normal_font.actual("size"),
                "bold",
            )
            panel.grid_columnconfigure(0, minsize=92, weight=0)
            panel.grid_columnconfigure(1, weight=1)
            for row, (label, value) in enumerate(content):
                if not label:
                    tk.Label(
                        panel, text=value, justify="left", anchor="w",
                        background=self.background, foreground=self.muted,
                        font=normal_font, wraplength=400,
                    ).grid(
                        row=row, column=0, columnspan=2, sticky="ew",
                        pady=(2, 0),
                    )
                    continue
                color = (
                    self.muted
                    if label in ("How", "Year", "Moves with it")
                    else self.foreground
                )
                tk.Label(
                    panel, text=label, justify="left", anchor="nw",
                    background=self.background, foreground=self.foreground,
                    font=label_font,
                ).grid(row=row, column=0, sticky="nw", padx=(0, 8))
                tk.Label(
                    panel, text=value, justify="left", anchor="nw",
                    background=self.background, foreground=color,
                    font=normal_font, wraplength=300,
                ).grid(row=row, column=1, sticky="ew")
        window.update_idletasks()
        screen_x = min(x + 12, self.master.winfo_screenwidth() - window.winfo_width() - 8)
        screen_y = min(y + 18, self.master.winfo_screenheight() - window.winfo_height() - 8)
        window.wm_geometry(f"+{max(8, screen_x)}+{max(8, screen_y)}")

    def hide(self, _event=None):
        if self._after_id is not None:
            self.master.after_cancel(self._after_id)
            self._after_id = None
        self._key = None
        if self._window is not None:
            self._window.destroy()
            self._window = None


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
