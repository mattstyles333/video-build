"""Self-eval helper: timeline PNGs at cut boundaries + duration check.

Runs the SKILL.md self-eval loop as a single command before showing preview
to the user.

Usage:
    video-build-self-eval --edl edit/edl.json --video edit/preview.mp4
    video-build-self-eval --edl edit/edl.json   # auto-resolves preview/final
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from video_build.media import probe
from video_build.timeline_view import edl_segments, render_timeline, resolve_rendered_video
from video_build.validate import ValidationError, validate_edl


def run_self_eval(
    edl_path: Path,
    *,
    video: Path | None = None,
    window: float = 1.5,
    n_frames: int = 8,
    strict: bool = False,
) -> dict:
    edl_path = edl_path.resolve()
    edit_dir = edl_path.parent
    edl = json.loads(edl_path.read_text())

    warnings = validate_edl(
        edl, edit_dir, skip_subtitle_file=True, check_word_boundaries=True,
    )
    if strict and warnings:
        raise ValidationError("validation warnings:\n  " + "\n  ".join(warnings))

    rendered = resolve_rendered_video(edl_path, video)
    if rendered is None:
        raise FileNotFoundError(
            "no rendered video found — pass --video or render preview.mp4 first"
        )

    segs = edl_segments(edl)
    if not segs:
        raise ValidationError("EDL has no ranges")

    expected_dur = segs[-1]["output_end"]
    actual_dur = float(probe(rendered).get("duration_s") or 0)
    dur_delta = abs(actual_dur - expected_dur)

    verify_dir = edit_dir / "verify"
    verify_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []

    # Overview
    overview = verify_dir / "self_eval_overview.png"
    from video_build.timeline_view import render_edl_timeline
    render_edl_timeline(edl_path, overview, video=rendered, n_frames=n_frames)
    saved.append(str(overview))

    total = expected_dur
    # Cut boundaries (skip t=0)
    for i in range(1, len(segs)):
        t = segs[i]["output_start"]
        start = max(0.0, t - window)
        end = min(total, t + window)
        out = verify_dir / f"self_eval_cut_{i:02d}_{t:.2f}.png"
        render_timeline(rendered, start, end, out, n_frames, transcript=None)
        saved.append(str(out))

    # Sample points: first 2s, last 2s, mid
    samples = [
        ("start", 0.0, min(2.0, total)),
        ("end", max(0.0, total - 2.0), total),
    ]
    if total > 6:
        mid = total / 2.0
        samples.append(("mid", max(0.0, mid - 1.0), min(total, mid + 1.0)))

    for name, a, b in samples:
        if b <= a:
            continue
        out = verify_dir / f"self_eval_{name}.png"
        render_timeline(rendered, a, b, out, n_frames, transcript=None)
        saved.append(str(out))

    issues: list[str] = list(warnings)
    if dur_delta > 0.5:
        issues.append(
            f"duration mismatch: video {actual_dur:.2f}s vs EDL {expected_dur:.2f}s "
            f"(delta {dur_delta:.2f}s)"
        )

    return {
        "video": str(rendered),
        "expected_duration_s": expected_dur,
        "actual_duration_s": actual_dur,
        "duration_delta_s": dur_delta,
        "warnings": warnings,
        "issues": issues,
        "pngs": saved,
        "passed": not issues,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Self-eval rendered output at EDL cut boundaries")
    ap.add_argument("--edl", type=Path, required=True, help="Path to edl.json")
    ap.add_argument("--video", type=Path, default=None, help="Rendered preview/final (auto-resolved)")
    ap.add_argument("--window", type=float, default=1.5, help="Seconds before/after each cut (default 1.5)")
    ap.add_argument("--n-frames", type=int, default=8, help="Frames per boundary PNG")
    ap.add_argument("--strict", action="store_true", help="Exit 1 on any validation warning")
    args = ap.parse_args()

    try:
        report = run_self_eval(
            args.edl,
            video=args.video,
            window=args.window,
            n_frames=args.n_frames,
            strict=args.strict,
        )
    except (ValidationError, FileNotFoundError) as e:
        sys.exit(str(e))

    print(f"self-eval: {report['video']}")
    print(f"  duration: {report['actual_duration_s']:.2f}s "
          f"(expected {report['expected_duration_s']:.2f}s, delta {report['duration_delta_s']:.2f}s)")
    print(f"  PNGs → {len(report['pngs'])} file(s) in {Path(args.edl).parent / 'verify'}")
    for p in report["pngs"]:
        print(f"    {p}")
    for w in report["warnings"]:
        print(f"  warning: {w}")
    for issue in report["issues"]:
        if issue not in report["warnings"]:
            print(f"  issue: {issue}")

    if report["passed"]:
        print("  passed: yes")
    else:
        print("  passed: no — review PNGs before showing the user")
        sys.exit(1)


if __name__ == "__main__":
    main()
