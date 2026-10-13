"""Verified official team marks; unknown teams keep their data-source image."""

import base64
import json
import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGO_DIR = ROOT / "pages" / "management" / "logos"


def normalize_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").casefold())


class TeamLogoCatalog:
    def __init__(self):
        self.images = {}
        self.web_images = {}
        self.web_dark_names = []
        path = ROOT / "team_logos.json"
        if not path.is_file():
            return
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(catalog, dict):
            return
        for name, entry in catalog.items():
            if not isinstance(entry, dict):
                continue
            filename = entry.get("file", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.(png|webp|jpg)", filename):
                continue
            try:
                data = (LOGO_DIR / filename).read_bytes()
            except OSError:
                continue
            if not data or len(data) > 1024 * 1024:
                continue
            try:
                from PIL import Image, ImageStat
                with Image.open(io.BytesIO(data)) as image:
                    mark = image.convert("RGBA")
                    channels = ImageStat.Stat(
                        mark.convert("RGB"), mask=mark.getchannel("A")).mean
                    pale = sum(value * weight for value, weight in
                               zip(channels, (0.2126, 0.7152, 0.0722))) > 175
            except (ImportError, OSError, ValueError):
                continue
            mime = "image/jpeg" if filename.endswith(".jpg") else "image/" + filename.rsplit(".", 1)[1]
            web_image = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
            for alias in [name, *entry.get("aliases", [])]:
                key = normalize_name(alias)
                if key:
                    self.images[key] = data
                    self.web_images[key] = web_image
                    if pale:
                        self.web_dark_names.append(key)

    def get(self, name):
        return self.images.get(normalize_name(name))
