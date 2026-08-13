"""Subtitle style coercion and master SRT generation."""

from __future__ import annotations

import json
import re
from pathlib import Path

from video_build.media import is_image
from video_build.render.common import SUB_STYLE_DEFAULTS, apply_caption_case, resolve_path

PUNCT_BREAK = set(".,!?;:")


def coerce_subtitle_style(edl: dict) -> dict:
    raw = edl.get("subtitle_style")
    style = dict(SUB_STYLE_DEFAULTS)
    if raw is None:
        return style
    if isinstance(raw, str):
        style["force_style"] = raw
        return style
    if not isinstance(raw, dict):
        return style
    key_map = {
        "font": "font", "fontname": "font", "FontName": "font",
        "size": "size", "fontsize": "size", "FontSize": "size",
        "bold": "bold", "margin_v": "margin_v", "MarginV": "margin_v",
        "alignment": "alignment", "Alignment": "alignment",
        "chunk_words": "chunk_words", "case": "case",
        "primary": "primary", "PrimaryColour": "primary",
        "outline": "outline", "OutlineColour": "outline",
        "force_style": "force_style",
    }
    for k, v in raw.items():
        dest = key_map.get(k, k)
        if dest in style or dest == "force_style":
            style[dest] = v
    return style


def force_style_from_edl(edl: dict) -> str:
    style = coerce_subtitle_style(edl)
    if style.get("force_style"):
        return str(style["force_style"])
    bold = 1 if style.get("bold") in (True, 1, "1", "true", "True") else 0
    return (
        f"FontName={style['font']},FontSize={int(style['size'])},Bold={bold},"
        f"PrimaryColour={style['primary']},OutlineColour={style['outline']},"
        f"BackColour={style['back']},BorderStyle={int(style['border_style'])},"
        f"Outline={int(style['outline_width'])},Shadow={int(style['shadow'])},"
        f"Alignment={int(style['alignment'])},MarginV={int(style['margin_v'])}"
    )


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _words_in_range(transcript: dict, t_start: float, t_end: float) -> list[dict]:
    out: list[dict] = []
    for w in transcript.get("words", []):
        if w.get("type") != "word":
            continue
        ws = w.get("start")
        we = w.get("end")
        if ws is None or we is None:
            continue
        if we <= t_start or ws >= t_end:
            continue
        out.append(w)
    return out


def build_master_srt(edl: dict, edit_dir: Path, out_path: Path) -> None:
    transcripts_dir = edit_dir / "transcripts"
    sources = edl["sources"]
    style = coerce_subtitle_style(edl)
    chunk_words = max(1, int(style.get("chunk_words") or 2))
    case = str(style.get("case") or "upper")

    entries: list[tuple[float, float, str]] = []
    seg_offset = 0.0

    for r in edl["ranges"]:
        src_name = r["source"]
        src_path = resolve_path(sources[src_name], edit_dir) if src_name in sources else None
        seg_start = float(r.get("start") or 0)
        seg_end = float(r["end"])
        seg_duration = seg_end - seg_start

        if src_path is not None and is_image(src_path):
            seg_offset += seg_duration
            continue

        tr_path = transcripts_dir / f"{src_name}.json"
        if not tr_path.exists():
            tr_path = transcripts_dir / f"{Path(str(src_name)).name}.json"
        if not tr_path.exists():
            print(f"  no transcript for {src_name}, skipping captions for this segment")
            seg_offset += seg_duration
            continue

        transcript = json.loads(tr_path.read_text())
        words_in_seg = _words_in_range(transcript, seg_start, seg_end)

        chunks: list[list[dict]] = []
        current: list[dict] = []
        for w in words_in_seg:
            text = (w.get("text") or "").strip()
            if not text:
                continue
            current.append(w)
            ends_in_punct = bool(text) and text[-1] in PUNCT_BREAK
            if len(current) >= chunk_words or ends_in_punct:
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)

        for chunk in chunks:
            local_start = max(seg_start, chunk[0].get("start", seg_start))
            local_end = min(seg_end, chunk[-1].get("end", seg_end))
            out_start = max(0.0, local_start - seg_start) + seg_offset
            out_end = max(0.0, local_end - seg_start) + seg_offset
            if out_end <= out_start:
                out_end = out_start + 0.4
            text = " ".join((w.get("text") or "").strip() for w in chunk)
            text = re.sub(r"\s+", " ", text).strip()
            text = text.rstrip(",;:")
            text = apply_caption_case(text, case)
            entries.append((out_start, out_end, text))

        seg_offset += seg_duration

    entries.sort(key=lambda e: e[0])
    lines: list[str] = []
    for i, (a, b, t) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(a)} --> {_srt_timestamp(b)}")
        lines.append(t)
        lines.append("")
    out_path.write_text("\n".join(lines))
    print(f"master SRT → {out_path.name} ({len(entries)} cues)")
