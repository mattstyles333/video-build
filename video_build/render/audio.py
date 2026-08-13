"""Loudness normalization and audio track mixing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from video_build.media import is_image, probe
from video_build.render.common import resolve_path

LOUDNORM_I = -14.0
LOUDNORM_TP = -1.0
LOUDNORM_LRA = 11.0


def measure_loudness(video_path: Path) -> dict[str, str] | None:
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
    stderr = proc.stderr
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
    if preview:
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
    if not windows or gain >= 0.999:
        return f"{base_vol:.6f}"
    cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in windows)
    return f"{base_vol:.6f}*if({cond},{gain:.4f},1)"


def speech_windows_from_edl(edl: dict, edit_dir: Path) -> list[tuple[float, float]]:
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
    windows: list[tuple[float, float]] = []
    for tr in tracks:
        if is_music_bed(tr):
            continue
        start = float(tr.get("start_in_output") or 0)
        dur = tr.get("duration")
        if dur is None:
            try:
                dur = probe(resolve_path(tr["file"], edit_dir)).get("duration_s") or 0
            except (OSError, KeyError):
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
