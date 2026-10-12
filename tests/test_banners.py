from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from tests.helpers import load_module

banners = load_module("banners")
formatters = load_module("formatters")


class BannerTests(unittest.TestCase):
    def test_long_name_truncation_terminates_and_fits(self):
        renderer = banners.BannerRenderer()
        draw = ImageDraw.Draw(Image.new("RGB", (320, 88)))
        font = renderer._pick_banner_font(14)
        name = "Extremely long team name " * 50
        result = renderer._shorten_banner_text(draw, name, font, 112)
        bbox = draw.textbbox((0, 0), result, font=font)
        self.assertLessEqual(bbox[2] - bbox[0], 112)
        self.assertTrue(result.endswith("..."))

    def test_corrupt_logos_render_placeholders(self):
        renderer = banners.BannerRenderer()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "banner.png")
            self.assertEqual(renderer.render([b"broken", None], ["One", "Two"], "VS", path), path)
            with Image.open(path) as image:
                self.assertEqual(image.size, (320, 88))

    def test_timezone_missing_in_old_data_is_utc(self):
        dt = formatters.parse_dt("2026-10-07T12:00:00")
        self.assertEqual(dt.utcoffset().total_seconds(), 0)
        self.assertEqual(formatters.fmt_time("2026-10-07T12:00:00"), "10-07 20:00")
