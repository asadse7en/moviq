from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageTk
import resvg_py

from .. import resource


@lru_cache(maxsize=128)
def _render_icon(name, size, color):
    path = Path(resource(f"moviq/ui/assets/icons/{name}.svg"))
    if not path.is_file():
        raise FileNotFoundError(f"Icon asset not found: {path}")
    svg = path.read_text(encoding="utf-8").replace("currentColor", color)
    return resvg_py.svg_to_bytes(
        svg_string=svg,
        width=size * 2,
        height=size * 2,
    )


def draw_icon(canvas, name, size, color, center):
    size = max(1, round(size))
    cache = getattr(canvas, "_moviq_svg_icons", None)
    if cache is None:
        cache = {}
        canvas._moviq_svg_icons = cache
    key = (name, size, color)
    image = cache.get(key)
    if image is None:
        with Image.open(BytesIO(_render_icon(name, size, color))) as source:
            rendered = source.convert("RGBA").resize(
                (size, size), Image.Resampling.LANCZOS,
            )
        image = ImageTk.PhotoImage(rendered, master=canvas)
        cache[key] = image
    canvas.create_image(*center, image=image)
