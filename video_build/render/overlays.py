"""Overlay filter construction and final compositing."""

from __future__ import annotations

import subprocess
from pathlib import Path

from video_build.media import is_image
from video_build.render.common import SUB_FORCE_STYLE, resolve_path, run


def overlay_input_args(ov: dict, edit_dir: Path) -> list[str]:
    ov_path = resolve_path(ov["file"], edit_dir)
    if is_image(ov_path):
        dur = float(ov["duration"])
        return ["-loop", "1", "-framerate", "24", "-t", f"{dur:.3f}", "-i", str(ov_path)]
    return ["-i", str(ov_path)]


def build_overlay_filters(overlays: list[dict]) -> tuple[list[str], str]:
    parts: list[str] = []
    current = "[0:v]"
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        dur = float(ov["duration"])
        end = t + dur
        chain = [f"[{idx}:v]setpts=PTS-STARTPTS+{t}/TB"]
        w, h = ov.get("w"), ov.get("h")
        if w and h:
            chain.append(f"scale={int(w)}:{int(h)}")
        elif w:
            chain.append(f"scale={int(w)}:-2")
        elif h:
            chain.append(f"scale=-2:{int(h)}")
        opacity = ov.get("opacity")
        fade_in = float(ov.get("fade_in") or 0)
        fade_out = float(ov.get("fade_out") or 0)
        need_alpha = fade_in > 0 or fade_out > 0 or (
            opacity is not None and float(opacity) < 1.0
        )
        if need_alpha:
            chain.append("format=rgba")
        if opacity is not None and float(opacity) < 1.0:
            chain.append(f"colorchannelmixer=aa={float(opacity):.3f}")
        if fade_in > 0:
            chain.append(f"fade=t=in:st={t:.3f}:d={fade_in:.3f}:alpha=1")
        if fade_out > 0:
            fo_at = max(t, end - fade_out)
            chain.append(f"fade=t=out:st={fo_at:.3f}:d={fade_out:.3f}:alpha=1")
        parts.append(",".join(chain) + f"[a{idx}]")
        x = ov.get("x", 0)
        y = ov.get("y", 0)
        next_label = f"[v{idx}]"
        parts.append(
            f"{current}[a{idx}]overlay=x={x}:y={y}:enable='between(t,{t:.3f},{end:.3f})'{next_label}"
        )
        current = next_label
    return parts, current


def build_final_composite(
    base_path: Path,
    overlays: list[dict],
    subtitles_path: Path | None,
    out_path: Path,
    edit_dir: Path,
    force_style: str | None = None,
) -> None:
    has_overlays = bool(overlays)
    has_subs = subtitles_path is not None and subtitles_path.exists()
    style = force_style or SUB_FORCE_STYLE

    if not has_overlays and not has_subs:
        run(["ffmpeg", "-y", "-i", str(base_path), "-c", "copy", str(out_path)], quiet=True)
        return

    inputs: list[str] = ["-i", str(base_path)]
    for ov in overlays:
        inputs += overlay_input_args(ov, edit_dir)

    filter_parts: list[str] = []
    current = "[0:v]"
    if has_overlays:
        ov_parts, current = build_overlay_filters(overlays)
        filter_parts.extend(ov_parts)

    if has_subs:
        subs_abs = str(subtitles_path.resolve()).replace(":", r"\:").replace("'", r"\'")
        filter_parts.append(
            f"{current}subtitles='{subs_abs}':force_style='{style}'[outv]"
        )
        out_label = "[outv]"
    else:
        if has_overlays:
            filter_parts.append(f"{current}null[outv]")
            out_label = "[outv]"
        else:
            out_label = "[0:v]"

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", out_label,
        "-map", "0:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"compositing → {out_path.name}")
    print(f"  overlays: {len(overlays)}, subtitles: {'yes' if has_subs else 'no'}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
