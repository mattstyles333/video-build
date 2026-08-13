"""Render a video from an EDL.

  1. Per-segment extract with color grade + 30ms audio fades baked in
     (stills become held/Ken-Burns clips with silent audio)
  2. Lossless -c copy concat into base.mp4
  3. Overlays (PTS-shifted, optional x/y/scale/opacity/fades) then
     subtitles LAST → final.mp4

Usage:
    python helpers/render.py <edl.json> -o final.mp4
    python helpers/render.py <edl.json> -o preview.mp4 --preview
    python helpers/render.py <edl.json> -o final.mp4 --build-subtitles
    python helpers/render.py <edl.json> -o final.mp4 --no-subtitles
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from media import is_image

try:
    from grade import get_preset, auto_grade_for_clip  # same directory
except Exception:
    def get_preset(name: str) -> str:
        return ""

    def auto_grade_for_clip(video, start=0.0, duration=None, verbose=False):  # type: ignore
        return "eq=contrast=1.03:saturation=0.98", {}


# -------- Subtitle style (bold-overlay, proven at 1920×1080 and 1080×1920) --
#
# MarginV is NOT taste — it is a platform safe-zone rule.
# TikTok / IG Reels / Shorts UI (caption, username, music, right-rail actions)
# covers roughly the bottom ~25–30% of a 1080×1920 frame. Captions placed near
# the bottom edge get clipped or obscured by the UI. libass auto-scales the
# render canvas relative to PlayResY=288, so MarginV=90 lands the caption
# baseline roughly 30% up from the bottom on any aspect — clear of the UI on
# every major vertical-video platform. Do not drop this below ~75 without a
# specific reason.
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

# -------- Helpers ------------------------------------------------------------


def run(cmd: list[str], quiet: bool = False) -> None:
    if not quiet:
        print(f"  $ {' '.join(str(c) for c in cmd[:6])}{' …' if len(cmd) > 6 else ''}")
    subprocess.run(cmd, check=True)


def resolve_grade_filter(grade_field: str | None) -> str:
    """The EDL's 'grade' field can be a preset name, a raw ffmpeg filter, or 'auto'.

    Returns the filter string to embed into the per-segment -vf chain.
    For 'auto', returns the sentinel "__AUTO__" which is resolved per-segment.
    """
    if not grade_field:
        return ""
    if grade_field == "auto":
        return "__AUTO__"
    # Preset names are short identifiers, filter strings contain '=' or ','.
    if re.fullmatch(r"[a-zA-Z0-9_\-]+", grade_field):
        try:
            return get_preset(grade_field)
        except KeyError:
            print(f"warning: unknown preset '{grade_field}', using as raw filter")
            return grade_field
    return grade_field


def resolve_path(maybe_path: str, base: Path) -> Path:
    """Resolve a path that may be absolute or relative to `base`."""
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def apply_transition_sugar(ranges: list[dict]) -> list[dict]:
    """`transition_out: fade` on a range becomes fade_out here + fade_in on the next."""
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
        "font": "font",
        "fontname": "font",
        "FontName": "font",
        "size": "size",
        "fontsize": "size",
        "FontSize": "size",
        "bold": "bold",
        "margin_v": "margin_v",
        "MarginV": "margin_v",
        "alignment": "alignment",
        "Alignment": "alignment",
        "chunk_words": "chunk_words",
        "case": "case",
        "primary": "primary",
        "PrimaryColour": "primary",
        "outline": "outline",
        "OutlineColour": "outline",
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


def apply_caption_case(text: str, case: str) -> str:
    mode = (case or "upper").lower()
    if mode == "upper":
        return text.upper()
    if mode == "title":
        return text.title()
    return text


def overlay_input_args(ov: dict, edit_dir: Path) -> list[str]:
    ov_path = resolve_path(ov["file"], edit_dir)
    if is_image(ov_path):
        dur = float(ov["duration"])
        return ["-loop", "1", "-framerate", "24", "-t", f"{dur:.3f}", "-i", str(ov_path)]
    return ["-i", str(ov_path)]


def build_overlay_filters(overlays: list[dict]) -> tuple[list[str], str]:
    """PTS-shift + optional geometry/fade/opacity. Returns (filter parts, last label)."""
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


# -------- HDR → SDR tone mapping (HLG / PQ sources) --------------------------
#
# iPhone defaults to HLG HDR in Rec.2020 (and many mirrorless cameras ship PQ).
# If the source is HDR and we only downconvert bit depth (yuv420p10le → yuv420p)
# without tone-mapping, the output is 8-bit but still carries HLG/PQ transfer
# metadata. Players that honor the metadata (screen recorders, most social
# upload re-encodes) interpret 8-bit values in an HDR container and the result
# looks oversaturated / blown out. QuickTime on macOS can hide this locally —
# screen recording and uploaded renders cannot.
#
# Fix: detect HDR via color_transfer and prepend a zscale+tonemap chain to the
# vf graph so the output is clean Rec.709 SDR.

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) and HLG

TONEMAP_CHAIN = (
    "zscale=t=linear:npl=100,"
    "format=gbrpf32le,"
    "zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,"
    "format=yuv420p"
)


def is_hdr_source(video: Path) -> bool:
    """Return True if the source uses a PQ or HLG transfer function."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() in HDR_TRANSFERS
    except subprocess.CalledProcessError:
        return False


def is_portrait_source(video: Path) -> bool:
    """Return True if the video's height > width (portrait / vertical)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, check=True,
        )
        w, h = map(int, out.stdout.strip().split(","))
        return h > w
    except Exception:
        return False


# -------- Per-segment extraction (Rule 2 + Rule 3) --------------------------


def parse_picture(range_row: dict) -> dict | None:
    """Normalize range['picture'] to {source, start, kenburns} or None."""
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
    """Extract a cut range as its own MP4 with grade + 30ms audio fades baked in.

    Audio comes from `source` at `seg_start`. Picture defaults to the same file.
    Pass `picture` to put B-roll / a still on screen while the A-roll talks.

    Images become a held clip (optional Ken Burns). Silent stills (no separate
    audio source) get anullsrc so they concat with video segments.
    """
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
        # If the B-roll is shorter than the VO, hold the last frame.
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
    """Extract every EDL range into edit_dir/clips_graded/seg_NN.mp4.
    Returns the ordered list of segment paths.

    If the EDL `grade` is "auto", analyze each segment range with
    `auto_grade_for_clip` and apply a per-segment subtle correction.
    Otherwise, apply the same preset/raw filter to every segment.
    """
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


# -------- Lossless concat ----------------------------------------------------


def concat_segments(segment_paths: list[Path], out_path: Path, edit_dir: Path) -> None:
    """Lossless concat via the concat demuxer. No re-encode."""
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


# -------- Master SRT (Rule 5) ------------------------------------------------


PUNCT_BREAK = set(".,!?;:")


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
    """Build an output-timeline SRT from per-source transcripts.

    Chunking and case come from EDL `subtitle_style` (defaults: 2-word UPPERCASE).
    Output times: word.start - segment_start + segment_offset.
    """
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

        # Stills / generated inserts have no speech.
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

        # Group into N-word chunks, break on punctuation
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

    # Sort and write as SRT
    entries.sort(key=lambda e: e[0])
    lines: list[str] = []
    for i, (a, b, t) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(a)} --> {_srt_timestamp(b)}")
        lines.append(t)
        lines.append("")
    out_path.write_text("\n".join(lines))
    print(f"master SRT → {out_path.name} ({len(entries)} cues)")


# -------- Loudness normalization (social-ready audio) -----------------------


# Social-media standard: -14 LUFS integrated, -1 dBTP peak, LRA 11 LU.
# Matches YouTube / Instagram / TikTok / X / LinkedIn normalization targets.
LOUDNORM_I = -14.0
LOUDNORM_TP = -1.0
LOUDNORM_LRA = 11.0


def measure_loudness(video_path: Path) -> dict[str, str] | None:
    """Run ffmpeg loudnorm first pass and parse the JSON measurement.

    Returns a dict with measured_i, measured_tp, measured_lra, measured_thresh,
    target_offset, or None if measurement failed.
    """
    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}:print_format=json"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(video_path),
        "-af", filter_str,
        "-vn", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # loudnorm prints the JSON to stderr at the end of the run
    stderr = proc.stderr

    # Find the JSON block — loudnorm output contains a `{ ... }` block
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(stderr[start : end + 1])
    except json.JSONDecodeError:
        return None
    needed = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    if not needed.issubset(data.keys()):
        return None
    return data


def apply_loudnorm_two_pass(
    input_path: Path,
    output_path: Path,
    preview: bool = False,
) -> bool:
    """Run two-pass loudnorm on input_path, write normalized copy to output_path.

    Returns True on success, False if measurement failed (caller should fall
    back to copying the input unchanged).

    In preview mode, skips the measurement pass and uses a one-pass approximation
    for speed. Final mode always does the proper two-pass.
    """
    if preview:
        # One-pass approximation — faster, slightly less accurate.
        filter_str = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-i", str(input_path),
            "-c:v", "copy",
            "-af", filter_str,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(output_path),
        ]
        print(f"  loudnorm (1-pass preview) → {output_path.name}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True

    # Full two-pass
    print(f"  loudnorm pass 1: measuring {input_path.name}")
    measurement = measure_loudness(input_path)
    if measurement is None:
        print("  loudnorm measurement failed — falling back to 1-pass")
        return apply_loudnorm_two_pass(input_path, output_path, preview=True)

    print(f"    measured: I={measurement['input_i']} LUFS  "
          f"TP={measurement['input_tp']}  LRA={measurement['input_lra']}")

    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        f":measured_I={measurement['input_i']}"
        f":measured_TP={measurement['input_tp']}"
        f":measured_LRA={measurement['input_lra']}"
        f":measured_thresh={measurement['input_thresh']}"
        f":offset={measurement['target_offset']}"
        f":linear=true"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(input_path),
        "-c:v", "copy",
        "-af", filter_str,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(output_path),
    ]
    print(f"  loudnorm pass 2: normalizing → {output_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return True


# -------- Extra audio beds (VO / music) -------------------------------------


def is_music_bed(tr: dict) -> bool:
    role = str(tr.get("role") or "").lower()
    if role in {"music", "bed"}:
        return True
    if tr.get("loop") or tr.get("duck") or tr.get("duck_db") is not None:
        return True
    return False


def wants_duck(tr: dict) -> bool:
    return bool(tr.get("duck")) or tr.get("duck_db") is not None


def duck_linear_gain(tr: dict) -> float:
    db = float(tr["duck_db"]) if tr.get("duck_db") is not None else 8.0
    return 10 ** (-abs(db) / 20.0)


def duck_volume_expr(base_vol: float, gain: float, windows: list[tuple[float, float]]) -> str:
    """ffmpeg volume expression: full level, then *gain inside speech windows."""
    if not windows or gain >= 0.999:
        return f"{base_vol:.6f}"
    cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in windows)
    return f"{base_vol:.6f}*if({cond},{gain:.4f},1)"


def speech_windows_from_edl(edl: dict, edit_dir: Path) -> list[tuple[float, float]]:
    """Output-timeline spans where A-roll (or forced) speech should duck music."""
    sources = edl.get("sources") or {}
    windows: list[tuple[float, float]] = []
    t = 0.0
    for r in edl.get("ranges") or []:
        dur = float(r["end"]) - float(r.get("start") or 0)
        flag = r.get("duck")
        speak = False
        if flag is False:
            speak = False
        elif flag is True:
            speak = True
        else:
            name = r.get("source")
            if name in sources:
                speak = not is_image(resolve_path(sources[name], edit_dir))
        if speak and dur > 0:
            windows.append((t, t + dur))
        t += dur
    return windows


def extra_speech_windows(tracks: list[dict], edit_dir: Path) -> list[tuple[float, float]]:
    """VO beds (non-music tracks) also count as speech for ducking."""
    from media import probe

    windows: list[tuple[float, float]] = []
    for tr in tracks:
        if is_music_bed(tr):
            continue
        start = float(tr.get("start_in_output") or 0)
        dur = tr.get("duration")
        if dur is None:
            try:
                dur = probe(resolve_path(tr["file"], edit_dir)).get("duration_s") or 0
            except Exception:
                dur = 0
        if float(dur) > 0:
            windows.append((start, start + float(dur)))
    return windows


def audio_mix_filter_parts(
    tracks: list[dict],
    speech_windows: list[tuple[float, float]] | None = None,
) -> list[str]:
    windows = list(speech_windows or [])
    parts: list[str] = []
    voice_labels = ["[0:a]"]
    bed_labels: list[str] = []

    for i, tr in enumerate(tracks, start=1):
        delay_ms = max(0, int(round(float(tr.get("start_in_output") or 0) * 1000)))
        vol = float(tr.get("volume") if tr.get("volume") is not None else 1.0)
        chain = [f"[{i}:a]aresample=48000"]
        if delay_ms:
            chain.append(f"adelay={delay_ms}:all=1")
        if is_music_bed(tr) and wants_duck(tr):
            expr = duck_volume_expr(vol, duck_linear_gain(tr), windows)
            chain.append("volume=" + expr.replace(",", r"\,"))
        elif abs(vol - 1.0) > 0.001:
            chain.append(f"volume={vol:.3f}")
        label = f"[a{i}]"
        parts.append(",".join(chain) + label)
        if is_music_bed(tr):
            bed_labels.append(label)
        else:
            voice_labels.append(label)

    if not bed_labels:
        mix_in = voice_labels
        parts.append(
            "".join(mix_in)
            + f"amix=inputs={len(mix_in)}:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        return parts

    if len(voice_labels) == 1:
        speech = voice_labels[0]
    else:
        parts.append(
            "".join(voice_labels)
            + f"amix=inputs={len(voice_labels)}:duration=first:dropout_transition=0:normalize=0[speech]"
        )
        speech = "[speech]"
    final = [speech] + bed_labels
    parts.append(
        "".join(final)
        + f"amix=inputs={len(final)}:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    return parts


def mix_audio_tracks(
    base_path: Path,
    tracks: list[dict],
    edit_dir: Path,
    out_path: Path,
    edl: dict | None = None,
) -> None:
    """Mix EDL audio_tracks onto the concat base. Video is stream-copied."""
    from media import probe

    base_dur = float((probe(base_path).get("duration_s") or 0) or 0)
    inputs: list[str] = ["-i", str(base_path)]
    for tr in tracks:
        src = resolve_path(tr["file"], edit_dir)
        if tr.get("loop") and base_dur > 0:
            inputs += ["-stream_loop", "-1", "-t", f"{base_dur:.3f}", "-i", str(src)]
        else:
            inputs += ["-i", str(src)]
    windows = []
    if edl:
        windows.extend(speech_windows_from_edl(edl, edit_dir))
    windows.extend(extra_speech_windows(tracks, edit_dir))
    parts = audio_mix_filter_parts(tracks, speech_windows=windows)
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"audio mix → {out_path.name}  ({len(tracks)} extra track(s))")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# -------- Final compositing (Rule 1 + Rule 4) -------------------------------


def build_final_composite(
    base_path: Path,
    overlays: list[dict],
    subtitles_path: Path | None,
    out_path: Path,
    edit_dir: Path,
    force_style: str | None = None,
) -> None:
    """Final pass: base → overlays (PTS-shifted) → subtitles LAST → out.

    If there are no overlays and no subtitles, just copy base to out.
    """
    has_overlays = bool(overlays)
    has_subs = subtitles_path is not None and subtitles_path.exists()
    style = force_style or SUB_FORCE_STYLE

    if not has_overlays and not has_subs:
        # Nothing to do — just rename/copy base to final name
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

    # Subtitles LAST — Rule 1
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


# -------- Main ---------------------------------------------------------------


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
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")

    edl = json.loads(edl_path.read_text())
    edit_dir = edl_path.parent
    out_path = args.output.resolve()

    try:
        from session import SessionError, require_confirmed
        require_confirmed(edit_dir, force=args.force, action="render")
    except SessionError as e:
        sys.exit(str(e))

    # 1. Extract per-segment (auto-grade per range if EDL grade is "auto")
    segment_paths = extract_all_segments(
        edl, edit_dir, preview=args.preview, draft=args.draft
    )

    # 2. Concat → base
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

    # 3. Subtitles: build if requested, resolve final path
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

    # 4. Composite (overlays + subtitles LAST) → intermediate (pre-loudnorm) path
    overlays = edl.get("overlays") or []
    force_style = force_style_from_edl(edl)
    if args.no_loudnorm:
        # Composite directly to final output
        build_final_composite(
            base_path, overlays, subs_path, out_path, edit_dir, force_style=force_style
        )
    else:
        # Composite to a temp file, then run loudnorm → final output
        tmp_composite = out_path.with_suffix(".prenorm.mp4")
        build_final_composite(
            base_path, overlays, subs_path, tmp_composite, edit_dir, force_style=force_style
        )
        print("loudness normalization → social-ready (-14 LUFS / -1 dBTP / LRA 11)")
        apply_loudnorm_two_pass(tmp_composite, out_path, preview=args.draft)
        tmp_composite.unlink(missing_ok=True)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\ndone: {out_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
