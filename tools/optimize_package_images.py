"""Reduce bundled PNG size locally without changing paths or dimensions.

Developer-only dependency: python -m pip install imagequant==1.1.5
The installed plugin needs only Pillow and never downloads these images.
Run after adding original photos or rebuilding compact portraits.
"""

import io
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    try:
        from imagequant import quantize_pil_image
    except ImportError as exc:
        raise SystemExit(
            "Install the developer tool first: python -m pip install imagequant==1.1.5"
        ) from exc

    paths = [ROOT / "logo.png"]
    paths.extend(sorted((ROOT / "player_photos").glob("*.png")))
    paths.extend(sorted((ROOT / "player_cards").glob("*.png")))
    before = after = changed = 0
    for path in paths:
        original = path.read_bytes()
        before += len(original)
        with Image.open(io.BytesIO(original)) as source:
            # Avoid repeatedly quantizing already optimized images.
            if source.mode == "P":
                after += len(original)
                continue
            rgba = source.convert("RGBA")
            compact = quantize_pil_image(
                rgba,
                max_colors=256,
                min_quality=75,
                max_quality=90,
                dithering_level=0.75,
            )
            # Dithering can otherwise introduce faint alpha outside the photo.
            # Keep every fully transparent source pixel fully transparent.
            transparency = compact.getpalette("RGBA")[3::4]
            if rgba.getchannel("A").getextrema()[0] == 0:
                clear_index = next(
                    index for index, alpha in enumerate(transparency) if alpha == 0
                )
                compact.paste(
                    clear_index,
                    mask=rgba.getchannel("A").point(lambda alpha: 255 if alpha == 0 else 0),
                )
            output = io.BytesIO()
            compact.save(output, format="PNG", optimize=True)
        data = output.getvalue()
        if len(data) < len(original):
            path.write_bytes(data)
            changed += 1
            after += len(data)
        else:
            after += len(original)
    print(f"Optimized {changed} PNGs: {before:,} -> {after:,} bytes")


if __name__ == "__main__":
    main()
