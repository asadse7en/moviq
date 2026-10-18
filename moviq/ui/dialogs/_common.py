import re


IMAGE_BASE = "https://image.tmdb.org/t/p"

DETAIL_POSTER_SIZE = (88, 132)

ID_RE = re.compile(r"^(?:tmdb\s*:\s*)?(\d+)$", re.IGNORECASE)

def _match_dialog_sizes(screen_width, screen_height):
    width = min(1100, max(1, screen_width - 40))
    height = min(600, max(1, screen_height - 80))
    return width, height, min(1000, width), min(560, height)
