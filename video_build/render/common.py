"""Shared render helpers: paths, grades, transitions, subtitle style defaults."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

try:
    from video_build.grade import auto_grade_for_clip, get_preset
except ImportError:
    def get_preset(name: str) -> str:
        return ""

    def auto_grade_for_clip(video, start=0.0, duration=None, verbose=False):  # type: ignore
        return "eq=contrast=1.03:saturation=0.98", {}


SUB_STYLE_DEFAULTS = {
    "font": "Helvetica",
    "size": 18,
    "bold": True,
    "primary": "&H00FFFFFF",
    "outline": "&H00000000",
    "back": "&H00000000",
    "border_style": 1,
    "outline_width": 2,
    "shadow": 0,
    "alignment": 2,
    "margin_v": 90,
    "chunk_words": 2,
    "case": "upper",
}

SUB_FORCE_STYLE = (
    "FontName=Helvetica,FontSize=18,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
    "BorderStyle=1,Outline=2,Shadow=0,"
    "Alignment=2,MarginV=90"
)


def run(cmd: list[str], quiet: bool = False) -> None:
    if not quiet:
        print(f"  $ {' '.join(str(c) for c in cmd[:6])}{' …' if len(cmd) > 6 else ''}")
    subprocess.run(cmd, check=True)


def resolve_grade_filter(grade_field: str | None) -> str:
    if not grade_field:
        return ""
    if grade_field == "auto":
        return "__AUTO__"
    if re.fullmatch(r"[a-zA-Z0-9_\-]+", grade_field):
        try:
            return get_preset(grade_field)
        except KeyError:
            print(f"warning: unknown preset '{grade_field}', using as raw filter")
            return grade_field
    return grade_field


def resolve_path(maybe_path: str, base: Path) -> Path:
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def apply_transition_sugar(ranges: list[dict]) -> list[dict]:
    out = [dict(r) for r in ranges]
    for i, r in enumerate(out):
        kind = str(r.get("transition_out") or r.get("transition") or "cut").lower()
        if kind in {"fade", "fadeblack", "black", "fade-to-black"}:
            dur = float(r.get("transition_duration") or 0.4)
            r["fade_out"] = max(float(r.get("fade_out") or 0), dur)
            if i + 1 < len(out):
                nxt = out[i + 1]
                nxt["fade_in"] = max(float(nxt.get("fade_in") or 0), dur)
    return out


def video_fade_filter(duration: float, fade_in: float, fade_out: float) -> str:
    parts: list[str] = []
    cap = max(0.0, duration / 2.0 - 0.001)
    if fade_in > 0 and duration > 0:
        d = min(float(fade_in), cap) if cap > 0 else float(fade_in)
        parts.append(f"fade=t=in:st=0:d={d:.3f}")
    if fade_out > 0 and duration > 0:
        d = min(float(fade_out), cap) if cap > 0 else float(fade_out)
        start = max(0.0, duration - d)
        parts.append(f"fade=t=out:st={start:.3f}:d={d:.3f}")
    return ",".join(parts)


def apply_caption_case(text: str, case: str) -> str:
    mode = (case or "upper").lower()
    if mode == "upper":
        return text.upper()
    if mode == "title":
        return text.title()
    return text


def parse_picture(range_row: dict) -> dict | None:
    raw = range_row.get("picture")
    if raw is None:
        return None
    if isinstance(raw, str):
        return {"source": raw, "start": 0.0, "kenburns": bool(range_row.get("kenburns"))}
    if not isinstance(raw, dict) or not raw.get("source"):
        return None
    return {
        "source": raw["source"],
        "start": float(raw["start"]) if raw.get("start") is not None else 0.0,
        "kenburns": bool(raw["kenburns"]) if "kenburns" in raw else bool(range_row.get("kenburns")),
    }
