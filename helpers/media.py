"""Shared media discovery and ffprobe for inventory / transcribe / render."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal

Kind = Literal["video", "image", "audio"]

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".gif", ".bmp"}
AUDIO_EXTS = {".wav", ".mp3", ".aac", ".m4a", ".flac", ".ogg", ".opus", ".aiff", ".aif"}

SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "animations",
    "bin",
    "clips_draft",
    "clips_graded",
    "clips_preview",
    "downloads",
    "edit",
    "media",
    "node_modules",
    "transcripts",
    "venv",
    "verify",
}


def read_env_key(name: str) -> str:
    """Read a key from video-build/.env, then cwd .env, then the environment."""
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() != name:
                continue
            val = v.strip().strip('"').strip("'")
            if val:
                return val
    return os.environ.get(name, "").strip()


def kind_of(path: Path) -> Kind | None:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def asset_id(path: Path, videos_dir: Path, edit_dir: Path) -> str:
    """Stable id: path relative to the videos dir, or to edit/ for generated files."""
    path = path.resolve()
    videos_dir = videos_dir.resolve()
    edit_dir = edit_dir.resolve()
    if path == edit_dir or edit_dir in path.parents:
        rel = path.relative_to(edit_dir)
        return str(rel.with_suffix("")).replace("\\", "/")
    try:
        rel = path.relative_to(videos_dir)
        return str(rel.with_suffix("")).replace("\\", "/")
    except ValueError:
        return path.stem


def thumb_name(asset_id: str) -> str:
    return asset_id.replace("/", "__") + ".jpg"


def _should_skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def iter_assets(
    root: Path,
    extra_roots: list[Path] | None = None,
) -> list[tuple[Path, Kind]]:
    """Walk root (+ optional extra roots) for video/image/audio files.

    Skips `edit/` and other output/cache directories. Does not follow symlinks.
    """
    found: list[tuple[Path, Kind]] = []
    seen: set[Path] = set()

    def walk(base: Path) -> None:
        if not base.is_dir():
            return
        for dirpath, dirnames, filenames in base.walk(follow_symlinks=False) if hasattr(base, "walk") else _os_walk(base):
            dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
            for name in filenames:
                if name.startswith("."):
                    continue
                p = (dirpath / name).resolve()
                if p in seen:
                    continue
                kind = kind_of(p)
                if kind is None:
                    continue
                seen.add(p)
                found.append((p, kind))

    walk(root.resolve())
    for extra in extra_roots or []:
        extra = extra.resolve()
        if extra.is_file():
            kind = kind_of(extra)
            if kind and extra not in seen:
                found.append((extra, kind))
            continue
        walk(extra)

    found.sort(key=lambda item: item[0].as_posix().lower())
    return found


def _os_walk(base: Path):
    """Fallback matching Path.walk's (dirpath, dirnames, filenames) shape."""
    import os

    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        yield Path(dirpath), dirnames, filenames


def _parse_fps(rate: str | None) -> float:
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            d = float(den)
            return float(num) / d if d else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def _rotation_degrees(stream: dict) -> int:
    tags = stream.get("tags") or {}
    raw = tags.get("rotate")
    if raw is not None:
        try:
            return int(float(raw)) % 360
        except ValueError:
            pass
    for entry in stream.get("side_data_list") or []:
        if "rotation" in entry:
            try:
                return int(float(entry["rotation"])) % 360
            except (TypeError, ValueError):
                continue
    return 0


def probe(path: Path) -> dict:
    """ffprobe a file. Always returns a complete dict; zeros on failure."""
    info = {
        "duration_s": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "has_video": False,
        "has_audio": False,
        "codec_v": "",
        "codec_a": "",
    }
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(out.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return info

    fmt = data.get("format") or {}
    try:
        info["duration_s"] = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        pass

    for stream in data.get("streams") or []:
        ctype = stream.get("codec_type")
        if ctype == "video":
            if (stream.get("disposition") or {}).get("attached_pic"):
                continue
            if info["has_video"]:
                continue
            info["has_video"] = True
            info["width"] = int(stream.get("width") or 0)
            info["height"] = int(stream.get("height") or 0)
            info["codec_v"] = stream.get("codec_name") or ""
            info["fps"] = _parse_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
            rot = _rotation_degrees(stream)
            if rot in (90, 270, -90, -270):
                info["width"], info["height"] = info["height"], info["width"]
        elif ctype == "audio" and not info["has_audio"]:
            info["has_audio"] = True
            info["codec_a"] = stream.get("codec_name") or ""

    return info
