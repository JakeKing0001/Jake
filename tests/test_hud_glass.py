"""Test unitari per core/gui/hud/glass.py: nessuna suite esisteva finora, nessun bug trovato.
Serve una QApplication reale per costruire QFrame/GlassPanel (Qt lo richiede): ne viene creata
una sola, condivisa con ogni altro test PySide6 di questa sessione (Qt non ammette due
QApplication nello stesso processo). Mai una vera finestra mostrata: si chiama solo repaint()
su un widget mai reso visibile, per verificare che il codice di disegno non sollevi eccezioni."""
import unittest
from unittest import mock

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from core.gui.hud import glass

_app = QApplication.instance() or QApplication([])


class GrainTextureTests(unittest.TestCase):
    def setUp(self):
        glass._grain_tile = None

    def tearDown(self):
        glass._grain_tile = None

    def test_returns_a_non_null_pixmap(self):
        pixmap = glass._grain_texture()
        self.assertFalse(pixmap.isNull())

    def test_is_built_once_and_cached(self):
        first = glass._grain_texture()
        second = glass._grain_texture()
        self.assertIs(first, second)


class BackdropRefreshTests(unittest.TestCase):
    def test_a_successful_capture_sets_the_pixmap(self):
        backdrop = glass.Backdrop()
        fake_screen = mock.MagicMock()
        fake_screen.geometry.return_value.x.return_value = 0
        fake_screen.geometry.return_value.y.return_value = 0
        fake_screen.geometry.return_value.width.return_value = 100
        fake_screen.geometry.return_value.height.return_value = 100
        fake_screen.devicePixelRatio.return_value = 1.0

        fake_image = mock.MagicMock()
        fake_image.width = 100
        fake_image.height = 100
        fake_resized = mock.MagicMock()
        fake_image.resize.return_value = fake_resized
        fake_filtered = mock.MagicMock()
        fake_resized.filter.return_value = fake_filtered
        fake_filtered.convert.return_value = fake_filtered
        fake_filtered.width = 20
        fake_filtered.height = 20
        fake_filtered.tobytes.return_value = b"\x00" * (20 * 20 * 4)

        with mock.patch.object(QApplication, "primaryScreen", return_value=fake_screen):
            with mock.patch("PIL.ImageGrab.grab", return_value=fake_image):
                result = backdrop.refresh()

        self.assertTrue(result)
        self.assertIsNotNone(backdrop.pixmap)
        self.assertGreater(backdrop.captured_at, 0.0)

    def test_a_capture_failure_returns_false_without_crashing(self):
        backdrop = glass.Backdrop()
        with mock.patch("PIL.ImageGrab.grab", side_effect=RuntimeError("nessuno schermo")):
            result = backdrop.refresh()
        self.assertFalse(result)
        self.assertIsNone(backdrop.pixmap)


class GlassPanelTests(unittest.TestCase):
    def test_set_glow_updates_the_color_and_schedules_a_repaint(self):
        panel = glass.GlassPanel()
        with mock.patch.object(panel, "update") as update:
            panel.set_glow(QColor(255, 0, 0))
        self.assertEqual(panel.glow, QColor(255, 0, 0))
        update.assert_called_once()

    def test_rounded_path_matches_the_panel_size(self):
        panel = glass.GlassPanel()
        panel.resize(200, 100)
        path = panel.rounded_path()
        bounds = path.boundingRect()
        self.assertAlmostEqual(bounds.width(), 199, delta=1)
        self.assertAlmostEqual(bounds.height(), 99, delta=1)

    def test_paint_without_a_backdrop_does_not_raise(self):
        panel = glass.GlassPanel()
        panel.resize(200, 100)
        panel.repaint()

    def test_paint_with_a_backdrop_pixmap_does_not_raise(self):
        from PySide6.QtGui import QPixmap
        backdrop = glass.Backdrop()
        backdrop.pixmap = QPixmap(50, 50)
        backdrop.pixmap.fill(QColor(10, 10, 10))
        panel = glass.GlassPanel(backdrop=backdrop)
        panel.resize(200, 100)
        panel.repaint()

    def test_paint_with_a_glow_color_does_not_raise(self):
        panel = glass.GlassPanel(glow=QColor(255, 200, 0))
        panel.resize(200, 100)
        panel.repaint()


if __name__ == "__main__":
    unittest.main()
