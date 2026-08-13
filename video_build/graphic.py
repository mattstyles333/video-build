"""Render simple transparent graphics for EDL overlays.

Lower-thirds, title cards, and stat cards. Full-frame PNG with alpha so they
composite at x=0,y=0 without extra positioning. Not a replacement for
HyperFrames/Remotion/Manim — use those when the graphic has to move.

Usage:
    python helpers/graphic.py lower-third -o lt.png --title "Ada" --subtitle "Founder"
    python helpers/graphic.py title -o title.png --title "WE FIXED THIS"
    python helpers/graphic.py card -o card.png --title "3×" --subtitle "faster"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from video_build.timeline_view import load_font


def parse_hex(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    raw = color.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    if len(raw) != 6:
        raise ValueError(f"expected #RRGGBB, got {color!r}")
    r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    return (r, g, b, alpha)


def _rounded_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def render_lower_third(
    out: Path,
    title: str,
    subtitle: str,
    width: int,
    height: int,
    accent: str,
    fg: str,
    bg: str,
) -> None:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    accent_c = parse_hex(accent)
    fg_c = parse_hex(fg)
    bg_c = parse_hex(bg, alpha=210)
    dim_c = parse_hex(fg, alpha=200)

    title_font = load_font(max(28, height // 28))
    sub_font = load_font(max(18, height // 42))

    pad_x = int(width * 0.055)
    # Sit above the caption safe zone (~bottom 30% on vertical).
    block_bottom = int(height * 0.68)
    bar_w = 6
    text_x = pad_x + bar_w + 18

    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    sub_bbox = draw.textbbox((0, 0), subtitle, font=sub_font) if subtitle else (0, 0, 0, 0)
    title_h = title_bbox[3] - title_bbox[1]
    sub_h = (sub_bbox[3] - sub_bbox[1]) if subtitle else 0
    gap = 6 if subtitle else 0
    block_h = title_h + gap + sub_h + 28
    block_top = block_bottom - block_h
    text_w = max(title_bbox[2] - title_bbox[0], sub_bbox[2] - sub_bbox[0])
    box_w = text_w + bar_w + 48
    _rounded_rect(draw, (pad_x - 10, block_top, pad_x + box_w, block_bottom), 8, bg_c)
    draw.rectangle((pad_x, block_top + 10, pad_x + bar_w, block_bottom - 10), fill=accent_c)
    y = block_top + 14
    draw.text((text_x, y), title, fill=fg_c, font=title_font)
    if subtitle:
        draw.text((text_x, y + title_h + gap), subtitle, fill=dim_c, font=sub_font)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")


def render_title(
    out: Path,
    title: str,
    subtitle: str,
    width: int,
    height: int,
    accent: str,
    fg: str,
    bg: str,
) -> None:
    img = Image.new("RGBA", (width, height), parse_hex(bg, alpha=220))
    draw = ImageDraw.Draw(img)
    title_font = load_font(max(40, height // 12))
    sub_font = load_font(max(20, height // 28))
    fg_c = parse_hex(fg)
    accent_c = parse_hex(accent)

    tb = draw.textbbox((0, 0), title, font=title_font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    tx = (width - tw) // 2
    ty = int(height * 0.42) - th // 2
    draw.text((tx, ty), title, fill=fg_c, font=title_font)
    bar_y = ty + th + 16
    bar_w = min(width // 5, max(80, tw // 3))
    draw.rectangle(((width - bar_w) // 2, bar_y, (width + bar_w) // 2, bar_y + 4), fill=accent_c)
    if subtitle:
        sb = draw.textbbox((0, 0), subtitle, font=sub_font)
        sw = sb[2] - sb[0]
        draw.text(((width - sw) // 2, bar_y + 18), subtitle, fill=parse_hex(fg, alpha=210), font=sub_font)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")


def render_card(
    out: Path,
    title: str,
    subtitle: str,
    width: int,
    height: int,
    accent: str,
    fg: str,
    bg: str,
) -> None:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title_font = load_font(max(48, height // 10))
    sub_font = load_font(max(20, height // 28))

    tb = draw.textbbox((0, 0), title, font=title_font)
    sb = draw.textbbox((0, 0), subtitle, font=sub_font) if subtitle else (0, 0, 0, 0)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    sw, sh = (sb[2] - sb[0], sb[3] - sb[1]) if subtitle else (0, 0)
    pad_x, pad_y = 48, 36
    box_w = max(tw, sw) + pad_x * 2
    box_h = th + (18 + sh if subtitle else 0) + pad_y * 2
    x0 = (width - box_w) // 2
    y0 = (height - box_h) // 2
    _rounded_rect(draw, (x0, y0, x0 + box_w, y0 + box_h), 16, parse_hex(bg, alpha=220))
    draw.rectangle((x0, y0, x0 + 8, y0 + box_h), fill=parse_hex(accent))
    draw.text((x0 + pad_x, y0 + pad_y), title, fill=parse_hex(fg), font=title_font)
    if subtitle:
        draw.text((x0 + pad_x, y0 + pad_y + th + 14), subtitle, fill=parse_hex(fg, alpha=210), font=sub_font)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a simple overlay graphic (PNG with alpha)")
    ap.add_argument("kind", choices=("lower-third", "title", "card"))
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--accent", default="FF5A00")
    ap.add_argument("--fg", default="FFFFFF")
    ap.add_argument("--bg", default="0A0A0A")
    args = ap.parse_args()

    try:
        if args.kind == "lower-third":
            render_lower_third(
                args.output, args.title, args.subtitle,
                args.width, args.height, args.accent, args.fg, args.bg,
            )
        elif args.kind == "title":
            render_title(
                args.output, args.title, args.subtitle,
                args.width, args.height, args.accent, args.fg, args.bg,
            )
        else:
            render_card(
                args.output, args.title, args.subtitle,
                args.width, args.height, args.accent, args.fg, args.bg,
            )
    except ValueError as e:
        sys.exit(str(e))
    print(f"graphic → {args.output}")


if __name__ == "__main__":
    main()
