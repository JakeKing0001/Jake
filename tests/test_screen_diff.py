"""Test unitari per il confronto tra screenshot (v3.6, core/vision/screen_diff.py). Solo PIL,
immagini sintetiche piccole: nessuna vera cattura dello schermo."""
import unittest

from PIL import Image

from core.vision.screen_diff import pixel_change_ratio, screen_visibly_changed


def _solid(color, size=(20, 20)):
    return Image.new("RGB", size, color)


class PixelChangeRatioTests(unittest.TestCase):
    def test_identical_images_have_zero_change(self):
        image = _solid((10, 10, 10))
        self.assertEqual(pixel_change_ratio(image, image.copy()), 0.0)

    def test_completely_different_images_have_full_change(self):
        before = _solid((0, 0, 0))
        after = _solid((255, 255, 255))
        self.assertEqual(pixel_change_ratio(before, after), 1.0)

    def test_small_localized_change_is_proportional(self):
        before = _solid((0, 0, 0), size=(10, 10))
        after = before.copy()
        for x in range(5):
            for y in range(5):
                after.putpixel((x, y), (255, 255, 255))
        # 25 pixel su 100 sono cambiati
        self.assertAlmostEqual(pixel_change_ratio(before, after), 0.25, places=2)

    def test_minor_noise_below_threshold_does_not_count(self):
        before = _solid((100, 100, 100))
        after = _solid((105, 105, 105))  # variazione di luminosita' minima, sotto la soglia
        self.assertEqual(pixel_change_ratio(before, after), 0.0)

    def test_different_sizes_are_resized_before_comparing(self):
        before = _solid((0, 0, 0), size=(10, 10))
        after = _solid((0, 0, 0), size=(20, 20))
        self.assertEqual(pixel_change_ratio(before, after), 0.0)


class ScreenVisiblyChangedTests(unittest.TestCase):
    def test_true_above_threshold(self):
        before = _solid((0, 0, 0))
        after = _solid((255, 255, 255))
        self.assertTrue(screen_visibly_changed(before, after))

    def test_false_when_identical(self):
        image = _solid((10, 10, 10))
        self.assertFalse(screen_visibly_changed(image, image.copy()))


if __name__ == "__main__":
    unittest.main()
