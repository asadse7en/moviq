import unittest
import tkinter as tk
from pathlib import Path
from unittest.mock import Mock, patch

from moviq import resource
from moviq.ui.settings import TMDB_ATTRIBUTION
from moviq.ui.popups import set_window_icon


class TMDBAttributionTests(unittest.TestCase):
    def test_about_page_has_required_attribution_text(self):
        self.assertEqual(
            TMDB_ATTRIBUTION,
            "This product uses the TMDB API but is not endorsed or certified by TMDB.",
        )

    def test_runtime_branding_assets_resolve(self):
        for asset in (
            "moviq-icon.ico", "moviq-icon.png",
        ):
            self.assertTrue(Path(resource(f"assets/{asset}")).is_file())

    @patch(
        "moviq.ui.window_utils.resource",
        side_effect=tk.TclError("missing"),
    )
    def test_missing_window_icon_does_not_raise(self, _resource):
        window = Mock()

        set_window_icon(window)

        window.iconphoto.assert_not_called()

    @patch("moviq.ui.popups.tk.PhotoImage")
    def test_window_icon_uses_photoimage_and_keeps_reference(self, photo_image):
        icon = object()
        photo_image.return_value = icon
        window = Mock()

        set_window_icon(window)

        self.assertIs(window._moviq_icon, icon)
        window.iconphoto.assert_called_once_with(True, icon)
