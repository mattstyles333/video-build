"""Per-segment extraction and lossless concat."""

from __future__ import annotations

import subprocess
from pathlib import Path

from video_build.media import is_image
from video_build.render.common import (
    apply_transition_sugar,
    parse_picture,
    resolve_grade_filter,
    resolve_path,
    video_fade_filter,
)
from video_build.render.probe import TONEMAP_CHAIN, is_hdr_source, is_portrait_source

try:
    from video_build.grade import auto_grade_for_clip
except ImportError:
    def auto_grade_for_clip(video, start=0.0, duration=None, verbose=False):  # type: ignore
        return "eq=contrast=1.03:saturation=0.98", {}


def kenburns_filter(source: Path, duration: float, draft: bool) -> str:
    portrait = is_portrait_source(source)
    if draft:
        tw, th = (720, 1280) if portrait else (1280, 720)
    else:
        tw, th = (1080, 1920) if portrait else (1920, 1080)
    frames = max(2, int(round(duration * 24)))
    return (
        f"scale=8000:-1,"
        f"zoompan=z='min(zoom+0.0012,1.12)':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={tw}x{th}:fps=24"
    )


def extract_segment(
    source: Path,
    seg_start: float,
    duration: float,
    grade_filter: str,
    out_path: Path,
    preview: bool = False,
    draft: bool = False,
    kenburns: bool = False,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
    picture: Path | None = None,
    picture_start: float = 0.0,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pic = picture or source
    pic_start = picture_start if picture is not None else seg_start
    split = picture is not None
    pic_still = is_image(pic)
    aud_still = is_image(source)

    portrait = is_portrait_source(pic)
    if draft:
        scale = "scale=-2:1280" if portrait else "scale=1280:-2"
    else:
        scale = "scale=-2:1920" if portrait else "scale=1920:-2"

    vf_parts: list[str] = []
    if not pic_still and is_hdr_source(pic):
        vf_parts.append(TONEMAP_CHAIN)
    if pic_still and kenburns:
        vf_parts.append(kenburns_filter(pic, duration, draft))
    else:
        vf_parts.append(scale)
    if grade_filter:
        vf_parts.append(grade_filter)
    if not pic_still:
        vf_parts.append("tpad=stop_mode=clone:stop_duration=30")
    vfade = video_fade_filter(duration, fade_in, fade_out)
    if vfade:
        vf_parts.append(vfade)
    vf = ",".join(vf_parts)

    fade_out_start = max(0.0, duration - 0.03)
    af = f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_start:.3f}:d=0.03"

    if draft:
        preset, crf = "ultrafast", "28"
    elif preview:
        preset, crf = "medium", "22"
    else:
        preset, crf = "fast", "20"

    cmd = ["ffmpeg", "-y"]
    if pic_still:
        cmd += [
            "-loop", "1", "-framerate", "24", "-t", f"{duration:.3f}",
            "-i", str(pic),
        ]
    else:
        cmd += [
            "-ss", f"{pic_start:.3f}",
            "-i", str(pic),
            "-t", f"{duration:.3f}",
        ]

    if split or pic_still:
        if aud_still:
            cmd += [
                "-f", "lavfi", "-t", f"{duration:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        else:
            cmd += [
                "-ss", f"{seg_start:.3f}",
                "-i", str(source),
                "-t", f"{duration:.3f}",
            ]
        cmd += ["-vf", vf, "-af", af, "-map", "0:v", "-map", "1:a", "-shortest"]
    else:
        cmd += ["-vf", vf, "-af", af]

    cmd += [
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        "-t", f"{duration:.3f}",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def extract_all_segments(
    edl: dict,
    edit_dir: Path,
    preview: bool,
    draft: bool = False,
) -> list[Path]:
    resolved = resolve_grade_filter(edl.get("grade"))
    is_auto = resolved == "__AUTO__"
    clips_dir = edit_dir / (
        "clips_draft" if draft else ("clips_preview" if preview else "clips_graded")
    )
    clips_dir.mkdir(parents=True, exist_ok=True)

    ranges = apply_transition_sugar(edl["ranges"])
    sources = edl["sources"]

    seg_paths: list[Path] = []
    print(f"extracting {len(ranges)} segment(s) → {clips_dir.name}/")
    if is_auto:
        print("  (auto-grade per segment: analyzing each range)")
    for i, r in enumerate(ranges):
        src_name = r["source"]
        src_path = resolve_path(sources[src_name], edit_dir)
        start = float(r.get("start") or 0)
        end = float(r["end"])
        duration = end - start
        still = is_image(src_path)
        extract_start = 0.0 if still else start
        pic = parse_picture(r)
        pic_path: Path | None = None
        pic_start = 0.0
        pic_kb = bool(r.get("kenburns"))
        if pic:
            pic_path = resolve_path(sources[pic["source"]], edit_dir)
            pic_start = 0.0 if is_image(pic_path) else float(pic["start"])
            pic_kb = bool(pic["kenburns"])
        grade_src = pic_path or src_path
        grade_at = pic_start if pic_path else start
        safe_name = Path(str(src_name)).name.replace("/", "_")
        out_path = clips_dir / f"seg_{i:02d}_{safe_name}.mp4"

        if is_auto and not is_image(grade_src):
            seg_filter, _stats = auto_grade_for_clip(
                grade_src, start=grade_at, duration=duration, verbose=False
            )
        else:
            seg_filter = "" if is_image(grade_src) and is_auto else resolved

        note = r.get("beat") or r.get("note") or ""
        pic_note = f"  pic={pic['source']}" if pic else ""
        print(f"  [{i:02d}] {src_name}  {start:7.2f}-{end:7.2f}  ({duration:5.2f}s)  {note}{pic_note}")
        if is_auto:
            print(f"        grade: {seg_filter or '(none)'}")
        extract_segment(
            src_path, extract_start, duration, seg_filter, out_path,
            preview=preview, draft=draft,
            kenburns=pic_kb,
            fade_in=float(r.get("fade_in") or 0),
            fade_out=float(r.get("fade_out") or 0),
            picture=pic_path,
            picture_start=pic_start,
        )
        seg_paths.append(out_path)

    return seg_paths


def concat_segments(segment_paths: list[Path], out_path: Path, edit_dir: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = edit_dir / "_concat.txt"
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in segment_paths))

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"concat → {out_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    concat_list.unlink(missing_ok=True)
