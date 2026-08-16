"""Validate EDL and bin artifacts before render."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from video_build.media import is_image
from video_build.render.common import resolve_path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "edl.schema.json"
EDL_SCHEMA = json.loads(SCHEMA_PATH.read_text())


class ValidationError(ValueError):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValidationError(msg)


def validate_edl_schema(edl: dict) -> None:
    """Validate EDL against schemas/edl.schema.json."""
    jsonschema.validate(edl, EDL_SCHEMA)


def validate_edl(
    edl: dict,
    edit_dir: Path,
    *,
    check_files: bool = True,
    skip_subtitle_file: bool = False,
    check_word_boundaries: bool = True,
) -> list[str]:
    """Validate EDL structure, source references, and time ranges.

    Returns non-fatal warnings (duration mismatch, word-boundary drift).
    Raises ValidationError on hard failures.

    Set skip_subtitle_file when render will build master.srt (--build-subtitles)
    or skip subtitles entirely (--no-subtitles).
    """
    warnings: list[str] = []
    _require(isinstance(edl, dict), "EDL must be a JSON object")

    try:
        validate_edl_schema(edl)
    except jsonschema.ValidationError as e:
        raise ValidationError(f"EDL schema: {e.message}") from e

    version = edl.get("version")
    if version is not None:
        _require(isinstance(version, int) and version >= 1, "EDL version must be an integer >= 1")

    sources = edl.get("sources")
    _require(isinstance(sources, dict) and sources, "EDL must have a non-empty 'sources' object")

    ranges = edl.get("ranges")
    _require(isinstance(ranges, list) and ranges, "EDL must have a non-empty 'ranges' array")

    for i, src_path in sources.items():
        _require(isinstance(i, str) and i.strip(), f"sources key {i!r} must be a non-empty string")
        _require(isinstance(src_path, str) and src_path.strip(), f"sources[{i!r}] must be a path string")
        if check_files:
            resolved = resolve_path(src_path, edit_dir)
            _require(resolved.exists(), f"sources[{i!r}] path does not exist: {resolved}")

    for i, r in enumerate(ranges):
        _require(isinstance(r, dict), f"ranges[{i}] must be an object")
        src = r.get("source")
        _require(isinstance(src, str) and src.strip(), f"ranges[{i}] missing 'source'")
        _require(src in sources, f"ranges[{i}] source {src!r} not in sources")

        end = r.get("end")
        _require(end is not None, f"ranges[{i}] missing 'end'")
        start = float(r.get("start") or 0)
        end_f = float(end)
        _require(end_f > start, f"ranges[{i}] end ({end_f}) must be > start ({start})")

        pic = r.get("picture")
        if pic is not None:
            if isinstance(pic, str):
                pic_src = pic
            elif isinstance(pic, dict):
                pic_src = pic.get("source")
                _require(isinstance(pic_src, str) and pic_src.strip(), f"ranges[{i}] picture.source required")
            else:
                raise ValidationError(f"ranges[{i}] picture must be a string or object")
            _require(pic_src in sources, f"ranges[{i}] picture source {pic_src!r} not in sources")

    overlays = edl.get("overlays") or []
    _require(isinstance(overlays, list), "overlays must be an array if present")
    for i, ov in enumerate(overlays):
        _require(isinstance(ov, dict), f"overlays[{i}] must be an object")
        for key in ("file", "start_in_output", "duration"):
            _require(key in ov, f"overlays[{i}] missing '{key}'")
        if check_files:
            ov_path = resolve_path(ov["file"], edit_dir)
            _require(ov_path.exists(), f"overlays[{i}] file does not exist: {ov_path}")

    tracks = edl.get("audio_tracks") or []
    _require(isinstance(tracks, list), "audio_tracks must be an array if present")
    for i, tr in enumerate(tracks):
        _require(isinstance(tr, dict), f"audio_tracks[{i}] must be an object")
        _require("file" in tr, f"audio_tracks[{i}] missing 'file'")
        if check_files:
            tr_path = resolve_path(tr["file"], edit_dir)
            _require(tr_path.exists(), f"audio_tracks[{i}] file does not exist: {tr_path}")

    subs = edl.get("subtitles")
    if subs is not None:
        _require(isinstance(subs, str) and subs.strip(), "subtitles must be a non-empty path string")
        if check_files and not skip_subtitle_file:
            subs_path = resolve_path(subs, edit_dir)
            _require(subs_path.exists(), f"subtitles path does not exist: {subs_path}")

    style = edl.get("subtitle_style")
    if style is not None:
        _require(
            isinstance(style, (str, dict)),
            "subtitle_style must be a string or object",
        )

    total = edl.get("total_duration_s")
    if total is not None:
        _require(isinstance(total, (int, float)) and total > 0, "total_duration_s must be a positive number")

    computed = sum(float(r["end"]) - float(r.get("start") or 0) for r in ranges)
    if total is not None and abs(computed - float(total)) > 0.5:
        warnings.append(
            f"total_duration_s ({total}) differs from sum of ranges ({computed:.2f}s) by > 0.5s "
            f"(expected {computed:.2f}s)"
        )

    if check_word_boundaries:
        warnings.extend(validate_range_word_boundaries(edl, edit_dir))

    return warnings


def validate_range_word_boundaries(edl: dict, edit_dir: Path) -> list[str]:
    """Warn if range edges don't align with word boundaries."""
    warnings: list[str] = []
    transcripts_dir = edit_dir / "transcripts"
    sources = edl.get("sources") or {}

    for i, r in enumerate(edl.get("ranges") or []):
        src = r.get("source")
        if not src or src not in sources:
            continue
        if is_image(resolve_path(sources[src], edit_dir)):
            continue
        tr_path = transcripts_dir / f"{src}.json"
        if not tr_path.exists():
            tr_path = transcripts_dir / f"{Path(str(src)).name}.json"
        if not tr_path.exists():
            continue
        transcript = json.loads(tr_path.read_text())
        words = [w for w in transcript.get("words", []) if w.get("type") == "word"]
        if not words:
            continue
        starts = {round(float(w["start"]), 3) for w in words if w.get("start") is not None}
        ends = {round(float(w["end"]), 3) for w in words if w.get("end") is not None}
        seg_start = round(float(r.get("start") or 0), 3)
        seg_end = round(float(r["end"]), 3)
        if seg_start not in starts and seg_start > 0:
            warnings.append(f"ranges[{i}] start {seg_start} not on a word boundary")
        if seg_end not in ends:
            warnings.append(f"ranges[{i}] end {seg_end} not on a word boundary")
    return warnings
