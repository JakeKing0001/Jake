"""Test unitari per core/gui/hud/theme.py: nessuna suite esisteva finora, nessun bug trovato.
QColor/QFont/QRectF/QLinearGradient sono tipi-valore di PySide6 che non richiedono una
QApplication in esecuzione per essere costruiti (verificato prima di scrivere questi test)."""
import unittest

from PySide6.QtCore import QRectF

from core.gui.hud import theme


class StateColorTests(unittest.TestCase):
    def test_a_known_state_returns_its_color(self):
        self.assertEqual(theme.state_color("listening"), theme.STATE_COLORS["listening"])

    def test_an_unknown_state_falls_back_to_idle(self):
        self.assertEqual(theme.state_color("stato_inesistente"), theme.STATE_COLORS["idle"])


class StateLabelTests(unittest.TestCase):
    def test_a_known_state_returns_its_label(self):
        self.assertEqual(theme.state_label("thinking"), "Ci penso...")

    def test_an_unknown_state_falls_back_to_idle(self):
        self.assertEqual(theme.state_label("stato_inesistente"), theme.STATE_LABELS["idle"])

    def test_every_state_color_has_a_matching_label(self):
        self.assertEqual(set(theme.STATE_COLORS), set(theme.STATE_LABELS))


class GradientTests(unittest.TestCase):
    def test_applies_every_configured_stop(self):
        rect = QRectF(0, 0, 100, 40)
        gradient = theme.gradient(rect)
        stops = gradient.stops()
        self.assertEqual(len(stops), len(theme.GRADIENT_STOPS))
        first_position, first_color = stops[0]
        self.assertAlmostEqual(first_position, theme.GRADIENT_STOPS[0][0])
        self.assertEqual(first_color.alpha(), theme.GRADIENT_STOPS[0][1][3])


class FontTests(unittest.TestCase):
    def test_uses_the_configured_family_and_requested_size(self):
        result = theme.font(14)
        self.assertEqual(result.family(), theme.FONT_FAMILY)
        self.assertEqual(result.pointSize(), 14)


class RgbaTests(unittest.TestCase):
    def test_formats_a_color_with_its_own_alpha(self):
        color = theme.QColor(10, 20, 30, 128)
        self.assertEqual(theme.rgba(color), "rgba(10,20,30,0.50)")

    def test_an_explicit_alpha_overrides_the_colors_own_alpha(self):
        color = theme.QColor(10, 20, 30, 255)
        self.assertEqual(theme.rgba(color, alpha=0), "rgba(10,20,30,0.00)")


if __name__ == "__main__":
    unittest.main()
