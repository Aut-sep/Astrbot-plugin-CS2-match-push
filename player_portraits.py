"""Deterministic compact headshots with a deliberately small honours caption."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

DISPLAY_WIDTH = 160
SCALE = 2
CAPTION_FONT_SIZE = 10


def wrap_caption(text, font, width):
    lines, current = [], ""
    for fact in text.split(" · "):
        candidate = current + " · " + fact if current else fact
        if current and font.getlength(candidate) > width:
            lines.append(current)
            current = ""
        elif current:
            current = candidate
            continue
        for character in fact:
            if current and font.getlength(current + character) > width:
                lines.append(current)
                current = ""
            current += character
    if current:
        lines.append(current)
    return lines


def build_compact_portrait(source: Path, output: Path, caption: str, font_path: Path):
    """Remove transparent margins, keep the face, and crop away the lower torso.

    Export at twice the displayed resolution so caption text remains
    crisp. The caption is part of the PNG, including when QQ uses image fallback.
    Original portraits and daily-draw snapshots are never modified.
    """
    side = DISPLAY_WIDTH * SCALE
    with Image.open(source) as image:
        original = image.convert("RGBA")
        bounds = original.getchannel("A").getbbox()
        if bounds:
            original = original.crop(bounds)
        headshot = ImageOps.fit(
            original,
            (side, side),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.0),
        )
    font = ImageFont.truetype(str(font_path), CAPTION_FONT_SIZE * SCALE)
    lines = wrap_caption("生涯成就：" + caption, font, side - 12 * SCALE)
    line_height = 13 * SCALE
    canvas = Image.new(
        "RGBA", (side, side + 8 * SCALE + len(lines) * line_height), (255, 255, 255, 0)
    )
    canvas.alpha_composite(headshot)
    draw = ImageDraw.Draw(canvas)
    y = side + 3 * SCALE
    for line in lines:
        draw.text(
            ((side - font.getlength(line)) / 2, y), line, font=font, fill="#7b8492"
        )
        y += line_height
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)
    return canvas.size
