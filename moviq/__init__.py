"""Moviq movie organizer application package."""

import sys
from pathlib import Path

__version__ = "1.1.1"


def resource_path(rel):
    base = (
        Path(getattr(sys, "_MEIPASS"))
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    return base / rel


def resource(path):
    base = (
        Path(sys._MEIPASS)
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent
    )
    return str(base / path)
