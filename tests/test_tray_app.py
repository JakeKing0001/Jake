"""Test unitari per core/gui/tray_app.py: nessuna suite esisteva finora, nessun bug trovato.
'pystray' e' un pacchetto vero installato (patchato direttamente, mai sys.modules - vedi
tests/test_character_tts_provider.py per il perche'); StatusPanel e' sempre mockata (mai una
vera finestra Tkinter aperta)."""
import unittest
from unittest import mock

from PIL import Image

from core.gui.tray_app import _build_icon_image, run_tray


class BuildIconImageTests(unittest.TestCase):
    def test_returns_a_64x64_rgb_image(self):
        image = _build_icon_image()
        self.assertIsInstance(image, Image.Image)
        self.assertEqual(image.size, (64, 64))
        self.assertEqual(image.mode, "RGB")


class RunTrayTests(unittest.TestCase):
    def test_builds_a_two_item_menu_and_starts_the_tray_thread(self):
        fake_icon = mock.MagicMock()
        fake_menu_item = mock.MagicMock()
        fake_pystray = mock.MagicMock()
        fake_pystray.MenuItem.return_value = fake_menu_item
        fake_pystray.Icon.return_value = fake_icon

        fake_panel = mock.MagicMock()
        fake_panel_class = mock.MagicMock(return_value=fake_panel)

        jake_core = mock.MagicMock()

        with mock.patch.dict("sys.modules", {"pystray": fake_pystray}):
            with mock.patch("core.gui.status_panel.StatusPanel", fake_panel_class):
                with mock.patch("threading.Thread") as thread_class:
                    run_tray(jake_core)

        fake_panel_class.assert_called_once_with(jake_core)
        self.assertEqual(fake_pystray.MenuItem.call_count, 2)
        fake_pystray.Icon.assert_called_once()
        thread_class.assert_called_once()
        _, kwargs = thread_class.call_args
        self.assertTrue(kwargs["daemon"])
        thread_class.return_value.start.assert_called_once()
        fake_panel.run_forever.assert_called_once()

    def test_open_and_quit_menu_callbacks_queue_the_right_commands(self):
        fake_pystray = mock.MagicMock()
        captured_items = {}

        def fake_menu_item(label, callback, **kwargs):
            captured_items[label] = callback
            return mock.MagicMock()

        fake_pystray.MenuItem.side_effect = fake_menu_item
        fake_icon = mock.MagicMock()
        fake_pystray.Icon.return_value = fake_icon

        fake_panel = mock.MagicMock()

        with mock.patch.dict("sys.modules", {"pystray": fake_pystray}):
            with mock.patch("core.gui.status_panel.StatusPanel", return_value=fake_panel):
                with mock.patch("threading.Thread"):
                    run_tray(mock.MagicMock())

        captured_items["Apri pannello"](fake_icon, None)
        captured_items["Esci"](fake_icon, None)

        queued = list(fake_panel.run_forever.call_args.args[0].queue)
        self.assertEqual(queued, ["show", "quit"])
        fake_icon.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
