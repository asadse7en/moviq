import unittest
from unittest.mock import Mock, patch

from moviq.ui.app import App


class ApiKeyPromptTests(unittest.TestCase):
    def make_app(self):
        app = App.__new__(App)
        app._smoke_test = False
        app.cfg = {"api_key_prompt_seen": False}
        app.key_var = Mock()
        app.key_var.get.return_value = ""
        app.provider_settings = Mock()
        app.provider_settings.key.return_value = ""
        app.settings_store = Mock()
        app.show_page = Mock()
        app.settings_view = Mock()
        return app

    def test_cancel_saves_seen_and_stops_action(self):
        app = self.make_app()
        with patch("moviq.ui.app.ApiKeyPromptDialog") as dialog:
            dialog.return_value.result = "cancel"
            self.assertFalse(app._maybe_show_api_key_prompt())
        app.settings_store.set.assert_called_once_with(
            "api_key_prompt_seen", True,
        )
        app.settings_store.save_now.assert_called_once_with()

    def test_open_sources_navigates_and_stops_action(self):
        app = self.make_app()
        with patch("moviq.ui.app.ApiKeyPromptDialog") as dialog:
            dialog.return_value.result = "sources"
            self.assertFalse(app._maybe_show_api_key_prompt())
        app.show_page.assert_called_once_with("settings")
        app.settings_view.focus_provider_source.assert_called_once_with(
            "tmdb",
        )

    def test_run_anyway_continues(self):
        app = self.make_app()
        with patch("moviq.ui.app.ApiKeyPromptDialog") as dialog:
            dialog.return_value.result = "run"
            self.assertTrue(app._maybe_show_api_key_prompt())

    def test_seen_key_or_smoke_test_skips_dialog(self):
        for condition in ("seen", "tmdb", "omdb", "smoke_test"):
            with self.subTest(condition=condition):
                app = self.make_app()
                if condition == "seen":
                    app.cfg["api_key_prompt_seen"] = True
                elif condition == "tmdb":
                    app.key_var.get.return_value = "saved-tmdb-key"
                elif condition == "omdb":
                    app.provider_settings.key.return_value = "saved-omdb-key"
                elif condition == "smoke_test":
                    app._smoke_test = True
                with patch("moviq.ui.app.ApiKeyPromptDialog") as dialog:
                    self.assertTrue(app._maybe_show_api_key_prompt())
                    dialog.assert_not_called()
                app.settings_store.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
