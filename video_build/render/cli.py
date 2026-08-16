"""Render CLI orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from video_build.render import (
    apply_loudnorm_two_pass,
    build_final_composite,
    build_master_srt,
    concat_segments,
    extract_all_segments,
    force_style_from_edl,
    mix_audio_tracks,
)
from video_build.render.common import resolve_path
from video_build.session import SessionError, require_confirmed
from video_build.validate import ValidationError, validate_edl


def _report_warnings(warnings: list[str], *, strict: bool) -> None:
    for w in warnings:
        print(f"warning: {w}")
    if strict and warnings:
        sys.exit("validation warnings (--strict)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a video from an EDL")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Output video path")
    ap.add_argument(
        "--preview",
        action="store_true",
        help="Preview mode: 1080p, medium, CRF 22 — evaluable for QC, faster than final.",
    )
    ap.add_argument(
        "--draft",
        action="store_true",
        help="Draft mode: 720p, ultrafast, CRF 28 — cut-point verification only.",
    )
    ap.add_argument(
        "--build-subtitles",
        action="store_true",
        help="Build master.srt from transcripts + EDL offsets before compositing",
    )
    ap.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Skip subtitles even if the EDL references one",
    )
    ap.add_argument(
        "--no-loudnorm",
        action="store_true",
        help="Skip audio loudness normalization. Default is on (-14 LUFS, -1 dBTP, LRA 11).",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Render even if strategy.md is not confirmed.",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Exit if validation warnings (word boundaries, duration mismatch)",
    )
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")

    edl = json.loads(edl_path.read_text())
    edit_dir = edl_path.parent
    out_path = args.output.resolve()

    try:
        warnings = validate_edl(
            edl,
            edit_dir,
            skip_subtitle_file=args.build_subtitles or args.no_subtitles,
        )
        _report_warnings(warnings, strict=args.strict)
    except ValidationError as e:
        sys.exit(str(e))

    try:
        require_confirmed(edit_dir, force=args.force, action="render")
    except SessionError as e:
        sys.exit(str(e))

    segment_paths = extract_all_segments(
        edl, edit_dir, preview=args.preview, draft=args.draft
    )

    if args.draft:
        base_name = "base_draft.mp4"
    elif args.preview:
        base_name = "base_preview.mp4"
    else:
        base_name = "base.mp4"
    base_path = edit_dir / base_name
    concat_segments(segment_paths, base_path, edit_dir)

    audio_tracks = edl.get("audio_tracks") or []
    if audio_tracks:
        mixed = edit_dir / (base_path.stem + "_mix" + base_path.suffix)
        mix_audio_tracks(base_path, audio_tracks, edit_dir, mixed, edl=edl)
        base_path = mixed

    subs_path: Path | None = None
    if not args.no_subtitles:
        if args.build_subtitles:
            subs_path = edit_dir / "master.srt"
            build_master_srt(edl, edit_dir, subs_path)
        elif edl.get("subtitles"):
            subs_path = resolve_path(edl["subtitles"], edit_dir)
            if not subs_path.exists():
                print(f"warning: subtitles path in EDL does not exist: {subs_path}")
                subs_path = None

    overlays = edl.get("overlays") or []
    force_style = force_style_from_edl(edl)
    if args.no_loudnorm:
        build_final_composite(
            base_path, overlays, subs_path, out_path, edit_dir, force_style=force_style
        )
    else:
        tmp_composite = out_path.with_suffix(".prenorm.mp4")
        build_final_composite(
            base_path, overlays, subs_path, tmp_composite, edit_dir, force_style=force_style
        )
        print("loudness normalization → social-ready (-14 LUFS / -1 dBTP / LRA 11)")
        apply_loudnorm_two_pass(tmp_composite, out_path, preview=args.draft)
        tmp_composite.unlink(missing_ok=True)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\ndone: {out_path} ({size_mb:.1f} MB)")
