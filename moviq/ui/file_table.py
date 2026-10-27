import math
import tkinter as tk
from tkinter import font as tkfont

from .design import PALETTES, TABLE_METRICS


class FileTable(tk.Canvas):
    """Canvas-backed, Treeview-compatible virtual file table."""

    def __init__(
        self, master, columns=(), displaycolumns=(), selectmode="extended",
        ui_scale=1.0, palette=None, **kwargs,
    ):
        kwargs.pop("style", None)
        kwargs.pop("show", None)
        kwargs.pop("height", None)
        kwargs.pop("width", None)
        kwargs.pop("selectmode", None)
        kwargs.pop("columns", None)
        kwargs.pop("displaycolumns", None)
        super().__init__(
            master, highlightthickness=0, borderwidth=0,
            takefocus=True, **kwargs,
        )
        self.ui_scale = ui_scale
        self._column_names = ("#0",) + tuple(columns)
        self._displaycolumns = tuple(displaycolumns)
        self._selectmode = selectmode
        self._headings = {"#0": {}}
        self._widths = {"#0": self._metric("name_min_width")}
        self._minwidths = {"#0": self._metric("name_min_width")}
        self._stretch = {"#0": True}
        self._anchors = {"#0": "w"}
        self._rows = {}
        self._order = []
        self._detached = set()
        self._selection = set()
        self._focus_iid = ""
        self._tags = {}
        self._palette = palette or PALETTES["dark"]
        self._name_font = tkfont.nametofont("TkTextFont")
        self._aux_font = tkfont.nametofont("TkDefaultFont")
        self._heading_font = tkfont.nametofont("TkHeadingFont")
        self._row_height = self._metric("row_height")
        self._header_height = self._metric("header_height")
        self._status_width = self._metric("status_width")
        self._source_width = self._metric("source_width")
        self._scroll_width = self._metric("scrollbar_width")
        self._scroll_offset = 0
        self._hover_iid = ""
        self._scrollbar_visible = False
        self._scrollbar_drag = False
        self._scrollbar_drag_offset = 0
        self._scrollbar_hide_id = None
        self._redraw_id = None
        self._update_depth = 0
        self._widths.update({name: self._metric("name_min_width")
                             for name in columns})
        self._minwidths.update({name: self._metric("name_min_width")
                                for name in columns})
        self._stretch.update({name: name == "new" for name in columns})
        self._anchors.update({name: "w" for name in columns})

        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Button-1>", self._on_button_press, add="+")
        self.bind("<B1-Motion>", self._on_drag, add="+")
        self.bind("<ButtonRelease-1>", self._on_button_release, add="+")
        self.bind("<Motion>", self._on_motion, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        for sequence in (
            "<Up>", "<Down>", "<Home>", "<End>", "<Prior>", "<Next>",
            "<space>", "<Control-a>", "<Control-A>",
        ):
            self.bind(sequence, self._on_key, add="+")
        self._queue_redraw()

    def _scale(self, value):
        return round(value * self.ui_scale)

    def _metric(self, name):
        return self._scale(TABLE_METRICS[name])

    def _column_key(self, column):
        if column in self._column_names:
            return column
        try:
            index = int(column[1:]) - 1
        except (TypeError, ValueError, IndexError):
            return column
        if 0 <= index < len(self._column_names) - 1:
            return self._column_names[index + 1]
        return column

    def _active(self):
        return [iid for iid in self._order if iid not in self._detached]

    def _flatten_ids(self, items):
        result = []
        for item in items:
            if isinstance(item, (tuple, list, set)):
                result.extend(item)
            else:
                result.append(item)
        return result

    def _queue_redraw(self):
        if self._update_depth or self._redraw_id is not None:
            return
        try:
            self._redraw_id = self.after_idle(self._redraw)
        except tk.TclError:
            self._redraw_id = None

    def begin_update(self):
        self._update_depth += 1

    def end_update(self):
        self._update_depth = max(0, self._update_depth - 1)
        if not self._update_depth:
            self._queue_redraw()

    def redraw(self):
        if self._redraw_id is not None:
            try:
                self.after_cancel(self._redraw_id)
            except tk.TclError:
                pass
            self._redraw_id = None
        if not self._update_depth:
            self._redraw()

    def set_theme(self, palette, name_font=None, aux_font=None,
                  heading_font=None):
        self._palette = palette
        if name_font is not None:
            self._name_font = name_font
        if aux_font is not None:
            self._aux_font = aux_font
        if heading_font is not None:
            self._heading_font = heading_font
        self.redraw()

    def heading(self, column, option=None, **options):
        column = self._column_key(column)
        if option:
            return self._headings.get(column, {}).get(option)
        if not options:
            return self._headings.get(column, {}).copy()
        self._headings.setdefault(column, {}).update(options)
        self._queue_redraw()

    def column(self, column, option=None, **options):
        column = self._column_key(column)
        current = {
            "width": self._widths.get(column, 0),
            "minwidth": self._minwidths.get(column, 0),
            "stretch": self._stretch.get(column, False),
            "anchor": self._anchors.get(column, "w"),
        }
        if option:
            if options:
                current[option] = options
            else:
                return current.get(option)
        elif options:
            current.update(options)
        self._widths[column] = int(current.get("width", 0))
        self._minwidths[column] = int(current.get("minwidth", 0))
        self._stretch[column] = bool(current.get("stretch", False))
        self._anchors[column] = current.get("anchor", "w")
        self._queue_redraw()
        return current

    def insert(self, parent, index, iid=None, **options):
        iid = str(iid) if iid is not None else f"I{len(self._order) + 1:06d}"
        while iid in self._rows:
            iid += "_"
        position = len(self._order) if index in ("end", "END") else int(index)
        position = max(0, min(position, len(self._order)))
        self._order.insert(position, iid)
        self._rows[iid] = {
            "text": options.get("text", ""),
            "values": tuple(options.get("values", ())),
            "tags": tuple(options.get("tags", ())),
        }
        self._queue_redraw()
        return iid

    def item(self, iid, option=None, **options):
        row = self._rows.get(iid)
        if row is None:
            return {} if option is None else ""
        if option is not None and not options:
            return row.get(option, "")
        if option is None and not options:
            return row.copy()
        if option is not None:
            row[option] = options
        else:
            row.update(options)
        if "values" in row:
            row["values"] = tuple(row["values"])
        if "tags" in row:
            row["tags"] = tuple(row["tags"])
        self._queue_redraw()
        return row.copy()

    def exists(self, iid):
        return iid in self._rows

    def get_children(self, item=""):
        return tuple(self._active())

    def index(self, iid):
        try:
            return self._order.index(iid)
        except ValueError:
            return 0

    def move(self, iid, parent, index):
        if iid not in self._rows:
            return
        self._order.remove(iid)
        position = len(self._order) if index in ("end", "END") else int(index)
        self._order.insert(max(0, min(position, len(self._order))), iid)
        self._queue_redraw()

    def delete(self, *items):
        for iid in self._flatten_ids(items):
            if iid in self._rows:
                self._rows.pop(iid, None)
                self._detached.discard(iid)
                self._selection.discard(iid)
                self._order.remove(iid)
        if self._focus_iid not in self._rows:
            self._focus_iid = ""
        self._queue_redraw()

    def detach(self, *items):
        self._detached.update(
            iid for iid in self._flatten_ids(items) if iid in self._rows
        )
        self._selection.difference_update(self._detached)
        self._queue_redraw()

    def reattach(self, iid, parent="", index="end"):
        if iid in self._rows:
            self._detached.discard(iid)
            if index != "end":
                self.move(iid, parent, index)
            self._queue_redraw()

    def tag_configure(self, tag, **options):
        self._tags.setdefault(tag, {}).update(options)
        self._queue_redraw()

    def selection(self):
        return tuple(iid for iid in self._order if iid in self._selection)

    def selection_set(self, *items):
        targets = set(
            iid for iid in self._flatten_ids(items)
            if iid in self._rows and iid not in self._detached
        )
        changed = targets != self._selection
        if self._selectmode == "browse" and targets:
            targets = {next(iter(targets))}
        self._selection = targets
        if changed:
            self._queue_redraw()
            self.event_generate("<<TreeviewSelect>>", when="tail")

    def selection_remove(self, *items):
        targets = set(self._flatten_ids(items))
        changed = bool(self._selection.intersection(targets))
        self._selection.difference_update(targets)
        if changed:
            self._queue_redraw()
            self.event_generate("<<TreeviewSelect>>", when="tail")

    def selection_toggle(self, iid):
        if iid not in self._rows or iid in self._detached:
            return
        if iid in self._selection:
            self._selection.remove(iid)
        else:
            if self._selectmode != "extended":
                self._selection.clear()
            self._selection.add(iid)
        self._focus_iid = iid
        self._queue_redraw()
        self.event_generate("<<TreeviewSelect>>", when="tail")

    def focus(self, iid=None):
        if iid is None:
            return self._focus_iid
        self._focus_iid = iid if iid in self._rows else ""
        self._queue_redraw()

    def identify_column(self, x):
        self._layout_columns()
        for index, name in enumerate(self._column_names):
            left, right = self._column_edges[index:index + 2]
            if left <= x < right:
                return "#0" if index == 0 else f"#{index}"
        return ""

    def identify_row(self, y):
        if y < self._header_height or y >= self.winfo_height():
            return ""
        index = int((y - self._header_height + self._scroll_offset)
                    // self._row_height)
        active = self._active()
        return active[index] if 0 <= index < len(active) else ""

    def identify_region(self, x, y):
        if (
            x >= self.winfo_width() - self._scroll_width
            and y >= self._header_height
            and self._scroll_metrics()[3] > 0
        ):
            return "scrollbar"
        if y < self._header_height:
            return "heading"
        if self.identify_row(y):
            return "tree" if self.identify_column(x) == "#0" else "cell"
        return "nothing"

    def identify_checkbox(self, x, y):
        if self.identify_region(x, y) == "heading":
            return x < self._metric("header_checkbox_hit_width")
        if not self.identify_row(y):
            return False
        return x < self._metric("checkbox_hit_width")

    def bbox(self, iid, column=None):
        if iid not in self._rows or iid in self._detached:
            return ()
        active = self._active()
        try:
            index = active.index(iid)
        except ValueError:
            return ()
        y = self._header_height + index * self._row_height - self._scroll_offset
        if y + self._row_height <= self._header_height or y >= self.winfo_height():
            return ()
        column = self._column_key(column or "#0")
        try:
            column_index = self._column_names.index(column)
        except ValueError:
            return ()
        self._layout_columns()
        left, right = self._column_edges[column_index:column_index + 2]
        return left, y, right - left, self._row_height

    def yview(self):
        total = len(self._active()) * self._row_height
        viewport = max(1, self.winfo_height() - self._header_height)
        if total <= 0 or total <= viewport:
            return 0.0, 1.0
        return (
            self._scroll_offset / total,
            min(1.0, (self._scroll_offset + viewport) / total),
        )

    def yview_moveto(self, fraction):
        total = len(self._active()) * self._row_height
        viewport = max(1, self.winfo_height() - self._header_height)
        self._scroll_to(int(max(0.0, min(1.0, fraction)) * total),
                        total, viewport)

    def yview_scroll(self, number, what):
        viewport = max(1, self.winfo_height() - self._header_height)
        amount = (
            int(number * viewport * 0.9) if what == "pages"
            else int(number * self._row_height)
        )
        total = len(self._active()) * self._row_height
        self._scroll_to(self._scroll_offset + amount, total, viewport)

    def see(self, iid):
        active = self._active()
        if iid not in active:
            return
        index = active.index(iid)
        top = index * self._row_height
        bottom = top + self._row_height
        viewport = max(1, self.winfo_height() - self._header_height)
        if top < self._scroll_offset:
            self._scroll_to(top, len(active) * self._row_height, viewport)
        elif bottom > self._scroll_offset + viewport:
            self._scroll_to(bottom - viewport,
                            len(active) * self._row_height, viewport)

    def _scroll_to(self, offset, total, viewport):
        maximum = max(0, total - viewport)
        offset = max(0, min(int(offset), maximum))
        if offset == self._scroll_offset:
            return
        self._scroll_offset = offset
        self._show_scrollbar()
        self.redraw()

    def _scroll_metrics(self):
        viewport = max(0, self.winfo_height() - self._header_height)
        total = len(self._active()) * self._row_height
        maximum = max(0, total - viewport)
        if not viewport or not maximum:
            return viewport, total, 0, 0, maximum
        thumb_height = min(
            viewport,
            max(
                self._metric("scrollbar_min_thumb"),
                int(viewport * viewport / total),
            ),
        )
        return (
            viewport, total, thumb_height,
            max(0, viewport - thumb_height), maximum,
        )

    def _scrollbar_thumb_bounds(self):
        viewport, _total, thumb_height, travel, maximum = (
            self._scroll_metrics()
        )
        if not thumb_height:
            return 0, 0
        top = self._header_height
        if maximum:
            top += round(travel * self._scroll_offset / maximum)
        return top, top + thumb_height

    def _layout_columns(self):
        width = max(1, self.winfo_width())
        fixed = self._widths.get("status", self._status_width)
        source = self._widths.get("source", self._source_width)
        names = max(0, width - fixed - source)
        original = self._widths.get("#0", names // 2)
        new = self._widths.get("new", names - original)
        if names >= self._minwidths.get("#0", 0) + self._minwidths.get("new", 0):
            if original + new != names:
                original = names // 2
                new = names - original
        else:
            original = max(self._minwidths.get("#0", 0), original)
            new = max(self._minwidths.get("new", 0), names - original)
        self._column_edges = [0, original, original + fixed,
                              original + fixed + new, width]
        self._widths["#0"] = original
        self._widths["status"] = fixed
        self._widths["new"] = new
        self._widths["source"] = source

    def _on_configure(self, _event=None):
        self._layout_columns()
        self._queue_redraw()

    def _on_button_press(self, event):
        if (
            event.x >= self.winfo_width() - self._scroll_width
            and event.y >= self._header_height
            and self._scroll_metrics()[3] > 0
        ):
            self._show_scrollbar()
        if self.identify_region(event.x, event.y) == "scrollbar":
            thumb_top, thumb_bottom = self._scrollbar_thumb_bounds()
            self._scrollbar_drag = True
            if thumb_top <= event.y < thumb_bottom:
                self._scrollbar_drag_offset = event.y - thumb_top
            else:
                self._scrollbar_drag_offset = (
                    self._scroll_metrics()[2] // 2
                )
            self._drag_scroll(event.y)
            self.event_generate("<<TableScroll>>", when="tail")
            return "break"
        self.focus_set()
        return None

    def _on_drag(self, event):
        if self._scrollbar_drag:
            self._drag_scroll(event.y)
            return "break"
        return None

    def _on_button_release(self, _event):
        self._scrollbar_drag = False

    def _on_motion(self, event):
        self._layout_columns()
        if not self._scrollbar_visible and len(self._active()) * self._row_height > (
            self.winfo_height() - self._header_height
        ):
            self._show_scrollbar()
        iid = self.identify_row(event.y)
        if iid != self._hover_iid:
            self._hover_iid = iid
            self._queue_redraw()

    def _on_leave(self, _event=None):
        self._hover_iid = ""
        if self._scrollbar_visible:
            self._queue_redraw()
        self._hide_scrollbar_later()
        self._queue_redraw()

    def _on_key(self, event):
        active = self._active()
        if not active:
            return "break"
        if event.state & 0x0004 and event.keysym.lower() == "a":
            self.selection_set(active)
            return "break"
        current = self._focus_iid if self._focus_iid in active else (
            next(iter(self._selection), active[0])
        )
        index = active.index(current)
        if event.keysym == "space":
            self.selection_toggle(current)
            return "break"
        if event.keysym == "Home":
            index = 0
        elif event.keysym == "End":
            index = len(active) - 1
        elif event.keysym == "Up":
            index = max(0, index - 1)
        elif event.keysym == "Down":
            index = min(len(active) - 1, index + 1)
        elif event.keysym == "Prior":
            index = max(
                0, index - max(
                    1, (self.winfo_height() - self._header_height)
                    // self._row_height,
                ),
            )
        elif event.keysym == "Next":
            index = min(
                len(active) - 1,
                index + max(
                    1, (self.winfo_height() - self._header_height)
                    // self._row_height,
                ),
            )
        else:
            return None
        iid = active[index]
        if event.state & 0x0001:
            self._selection.add(iid)
            self._focus_iid = iid
            self._queue_redraw()
            self.event_generate("<<TreeviewSelect>>", when="tail")
        else:
            self.selection_set(iid)
            self._focus_iid = iid
        self.see(iid)
        return None

    def _show_scrollbar(self):
        was_visible = self._scrollbar_visible
        self._scrollbar_visible = True
        if self._scrollbar_hide_id is not None:
            self.after_cancel(self._scrollbar_hide_id)
            self._scrollbar_hide_id = None
        self._hide_scrollbar_later()
        if not was_visible:
            self._queue_redraw()

    def _hide_scrollbar_later(self):
        if self._scrollbar_hide_id is not None:
            try:
                self.after_cancel(self._scrollbar_hide_id)
            except tk.TclError:
                pass
        self._scrollbar_hide_id = self.after(
            self._metric("scrollbar_hide_delay_ms"), self._hide_scrollbar,
        )

    def _hide_scrollbar(self):
        self._scrollbar_hide_id = None
        if self._scrollbar_drag:
            self._hide_scrollbar_later()
            return
        pointer_widget = self.winfo_containing(
            self.winfo_pointerx(), self.winfo_pointery(),
        )
        if str(pointer_widget) != str(self):
            self._scrollbar_visible = False
            self._queue_redraw()
        else:
            self._hide_scrollbar_later()

    def _drag_scroll(self, y):
        self.event_generate("<<TableScroll>>", when="tail")
        viewport, _total, _thumb_height, travel, maximum = (
            self._scroll_metrics()
        )
        if not travel or not maximum:
            return
        thumb_top = max(
            self._header_height,
            min(
                self._header_height + travel,
                y - self._scrollbar_drag_offset,
            ),
        )
        offset = round(
            (thumb_top - self._header_height) * maximum / travel,
        )
        self._scroll_to(offset, maximum + viewport, viewport)

    def _rounded_rectangle(
        self, x1, y1, x2, y2, radius, fill, outline="", width=1,
    ):
        points = []
        for center_x, center_y, start in (
            (x2 - radius, y1 + radius, -90),
            (x2 - radius, y2 - radius, 0),
            (x1 + radius, y2 - radius, 90),
            (x1 + radius, y1 + radius, 180),
        ):
            for step in range(5):
                angle = math.radians(start + step * 22.5)
                points.extend((
                    center_x + radius * math.cos(angle),
                    center_y + radius * math.sin(angle),
                ))
        return self.create_polygon(
            points, smooth=True, splinesteps=8, fill=fill,
            outline=outline, width=width,
        )

    def _checkbox(self, x, y, checked=False, partial=False):
        size = self._metric("checkbox_size")
        radius = self._metric("checkbox_radius")
        if checked:
            color = self._palette["accent"]
            self._rounded_rectangle(
                x, y, x + size, y + size, radius, color,
            )
            self.create_line(
                x + size * 0.25, y + size * 0.5,
                x + size * 0.44, y + size * 0.7,
                x + size * 0.78, y + size * 0.3,
                fill=self._palette["accent_text"],
                width=self._metric("checkbox_stroke"), capstyle="round",
                joinstyle="round",
            )
        elif partial:
            color = self._palette["accent"]
            self._rounded_rectangle(
                x, y, x + size, y + size, radius, color,
            )
            self.create_line(
                x + size * 0.25, y + size * 0.5,
                x + size * 0.75, y + size * 0.5,
                fill=self._palette["accent_text"],
                width=self._metric("checkbox_stroke"), capstyle="round",
            )
        else:
            self._rounded_rectangle(
                x, y, x + size, y + size, radius,
                "", outline=self._palette.get(
                    "header_muted", self._palette["muted"],
                ), width=self._metric("checkbox_stroke"),
            )

    def _middle_clip(self, text, font, width):
        text = str(text or "")
        if font.measure(text) <= width:
            return text
        ellipsis = "\u2026"
        low, high = 0, len(text)
        while low < high:
            keep = (low + high + 1) // 2
            left = (keep + 1) // 2
            right = keep // 2
            candidate = text[:left] + ellipsis + (
                text[-right:] if right else ""
            )
            if font.measure(candidate) <= width:
                low = keep
            else:
                high = keep - 1
        left = (low + 1) // 2
        right = low // 2
        return text[:left] + ellipsis + (text[-right:] if right else "")

    def _status_colors(self, status):
        key = status.strip().casefold()
        if key in ("confirmed", "matched", "ready"):
            key = "ready"
        elif key in ("matching",):
            key = "matching"
        elif key in ("review",):
            key = "review"
        elif key in ("no match", "error"):
            key = "no_match"
        elif key in ("done", "no change"):
            key = "done"
        else:
            key = "pending"
        colors = self._palette.get("table_chip_colors", {})
        return colors.get(key, (
            self._palette.get("table_status_ready", self._palette["accent"]),
            self._palette.get("table_status_ready", self._palette["accent"]),
        ))

    def _redraw(self):
        self._redraw_id = None
        if self._update_depth or not self.winfo_exists():
            return
        tk.Canvas.delete(self, "all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        self._layout_columns()
        self.create_rectangle(
            0, 0, width, height, fill=self._palette["card"], outline="",
        )
        self.create_rectangle(
            0, 0, width, self._header_height,
            fill=self._palette["card"], outline="",
        )
        active = self._active()
        selected = self._selection.intersection(active)
        body_height = max(0, height - self._header_height)
        total_height = len(active) * self._row_height
        self._scroll_offset = max(
            0, min(self._scroll_offset, max(0, total_height - body_height)),
        )
        if self._row_height > 0 and body_height:
            first = max(0, self._scroll_offset // self._row_height)
            visible_count = body_height // self._row_height + 2
            last = min(len(active), first + visible_count)
            y = (
                self._header_height + first * self._row_height
                - self._scroll_offset
            )
            for index in range(first, last):
                iid = active[index]
                self._draw_row(iid, index, y, width)
                y += self._row_height
        if self._scrollbar_visible and len(active) * self._row_height > body_height:
            self._draw_scrollbar(height, body_height, len(active))
        self.create_rectangle(
            0, 0, width, self._header_height,
            fill=self._palette["card"], outline="",
        )
        self._draw_header(width, selected, len(active))

    def _draw_header(self, width, selected, total):
        original_end = self._column_edges[1]
        heading = self._headings.get("#0", {}).get("text", "Original name")
        heading = str(heading).replace("\u2610", "").replace("\u2611", "")
        heading = heading.replace("\u25e9", "").strip()
        self._checkbox(
            self._metric("header_checkbox_left"),
            (self._header_height - self._metric("checkbox_size")) // 2,
            bool(total and len(selected) == total),
            bool(selected and len(selected) < total),
        )
        self.create_text(
            min(
                original_end - self._metric("cell_padding"),
                self._metric("header_name_left"),
            ),
            self._header_height // 2, anchor="w", text=heading,
            fill=self._palette.get("header_muted", self._palette["muted"]),
            font=self._heading_font,
        )
        for index, name in enumerate(self._column_names[1:], start=1):
            left, right = self._column_edges[index:index + 2]
            text = self._headings.get(name, {}).get("text", name)
            self.create_text(
                left + self._metric("cell_padding"),
                self._header_height // 2,
                anchor="w", text=text,
                fill=self._palette.get("header_muted", self._palette["muted"]),
                font=self._heading_font,
            )

    def _draw_row(self, iid, index, y, width):
        row = self._rows[iid]
        selected = iid in self._selection
        hovered = iid == self._hover_iid
        background = (
            self._palette["row_selected"] if selected
            else self._palette["row_hover"] if hovered
            else self._palette["row_zebra"] if index % 2
            else self._palette["card"]
        )
        self.create_rectangle(
            0, y, width, y + self._row_height,
            fill=background, outline="",
        )
        text = str(row.get("text", ""))
        text = text.replace("\u2610", "").replace("\u2611", "")
        text = text.replace("\u25e9", "").strip()
        self._checkbox(
            self._metric("row_checkbox_left"),
            y + (self._row_height - self._metric("checkbox_size")) // 2,
            selected,
        )
        original_end = self._column_edges[1]
        name = self._middle_clip(
            text, self._name_font,
            max(
                1, original_end - self._metric("row_name_left")
                - self._metric("cell_padding"),
            ),
        )
        self.create_text(
            self._metric("row_name_left"), y + self._row_height // 2,
            anchor="w", text=name, fill=self._palette["rowfg"],
            font=self._name_font,
        )
        values = row.get("values", ())
        status = str(values[0]) if len(values) > 0 else ""
        new_name = str(values[1]) if len(values) > 1 else ""
        source = str(values[2]) if len(values) > 2 else ""
        self._draw_status(status, y)
        new_start = self._column_edges[2]
        new_end = self._column_edges[3]
        if status.casefold() == "review" and new_name == "Pick a match":
            new_color = self._palette["table_chip_colors"]["review"][1]
        elif status.casefold() == "no match":
            new_name = "No result found"
            new_color = self._palette["muted"]
        elif status.casefold() == "matching":
            new_name = "Matching..."
            new_color = self._palette["muted"]
        else:
            new_color = self._palette["rowfg"]
        new_name = self._middle_clip(
            new_name, self._name_font,
            max(
                1, new_end - new_start
                - 2 * self._metric("cell_padding"),
            ),
        )
        self.create_text(
            new_start + self._metric("cell_padding"),
            y + self._row_height // 2,
            anchor="w", text=new_name, fill=new_color,
            font=self._name_font,
        )
        source_start = self._column_edges[3]
        source_end = self._column_edges[4]
        source = self._middle_clip(
            source, self._aux_font,
            max(
                1, source_end - source_start
                - 2 * self._metric("cell_padding"),
            ),
        )
        self.create_text(
            source_start + self._metric("cell_padding"),
            y + self._row_height // 2,
            anchor="w", text=source, fill=self._palette["muted"],
            font=self._aux_font,
        )

    def _draw_status(self, label, y):
        if not label:
            return
        bg, fg = self._status_colors(label)
        label_width = self._aux_font.measure(label)
        horizontal = self._metric("status_pill_padding")
        dot = self._metric("status_dot_size")
        gap = self._metric("status_dot_gap")
        pill_width = label_width + horizontal * 2 + dot + gap
        pill_height = self._metric("status_pill_height")
        left = self._column_edges[1] + self._metric("cell_padding")
        top = y + (self._row_height - pill_height) // 2
        self._rounded_rectangle(
            left, top, left + pill_width, top + pill_height,
            self._metric("status_pill_radius"), bg,
        )
        center_y = top + pill_height // 2
        self.create_oval(
            left + horizontal, center_y - dot // 2,
            left + horizontal + dot, center_y + (dot + 1) // 2,
            fill=fg, outline="",
        )
        self.create_text(
            left + horizontal + dot + gap, center_y,
            anchor="w", text=label, fill=fg, font=self._aux_font,
        )

    def _draw_scrollbar(self, height, body_height, total_rows):
        _viewport, total, thumb_height, _travel, _maximum = (
            self._scroll_metrics()
        )
        if not thumb_height or total <= body_height:
            return
        top, _bottom = self._scrollbar_thumb_bounds()
        left = self.winfo_width() - self._scroll_width
        self._rounded_rectangle(
            left, top, self.winfo_width(),
            min(height, top + thumb_height),
            self._scroll_width // 2, self._palette["muted"],
        )
