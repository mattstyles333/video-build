"""Grok Imagine client for edit-bin gaps.

Fills missing plates into <edit>/generated/ using the xAI Imagine API.
References resolve through bin.json so a look written at inventory time
is injected into the prompt automatically.

Usage:
    python helpers/imagine.py still  --prompt "..." --slug street --ref stills/alley
    python helpers/imagine.py video  --prompt "slow push-in" --slug street --image generated/street
    python helpers/imagine.py shot   --prompt "..." --slug street --ref stills/alley --duration 6
    python helpers/imagine.py fill   --edit-dir <edit> --videos-dir <folder>
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from inventory import build_bin, load_bin
from media import is_image, is_video, read_env_key
from session import SessionError, require_confirmed


XAI_BASE = "https://api.x.ai/v1"
IMAGE_MODEL = "grok-imagine-image-quality"
VIDEO_MODEL = "grok-imagine-video-1.5"
VIDEO_EDIT_MODEL = "grok-imagine-video"
MAX_VIDEO_BYTES = 40 * 1024 * 1024
VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
}
DEFAULT_STILL_DURATION = 0
DEFAULT_SHOT_DURATION = 6
VIDEO_POLL_S = 5
VIDEO_TIMEOUT_S = 600
MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class ImagineError(RuntimeError):
    pass


# -------- bin / refs ---------------------------------------------------------


def slugify(raw: str) -> str:
    s = raw.strip().replace("\\", "/").rsplit("/", 1)[-1]
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-").lower()
    if not s:
        raise ImagineError(f"empty slug from {raw!r}")
    return s


def load_assets(edit_dir: Path) -> dict[str, dict]:
    data = load_bin(edit_dir / "bin.json")
    return {a["id"]: a for a in data.get("assets") or [] if a.get("id")}


def resolve_ref(token: str, edit_dir: Path, assets: dict[str, dict]) -> tuple[Path, dict | None]:
    """Resolve a bin id, generated/ slug, or filesystem path to a local file."""
    token = token.strip()
    if token in assets:
        entry = assets[token]
        return Path(entry["path"]), entry
    p = Path(token)
    if not p.is_absolute():
        cand = (edit_dir / token).resolve()
        if cand.exists():
            return cand, None
        cand = (edit_dir.parent / token).resolve()
        if cand.exists():
            return cand, None
    p = p.resolve()
    if p.exists():
        entry = None
        for a in assets.values():
            try:
                if Path(a.get("path", "")).resolve() == p:
                    entry = a
                    break
            except OSError:
                continue
        return p, entry
    raise ImagineError(f"cannot resolve ref {token!r} (not a bin id or file)")


def file_to_data_uri(path: Path) -> str:
    mime = MIME.get(path.suffix.lower(), "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def file_to_video_uri(path: Path) -> str:
    mime = VIDEO_MIME.get(path.suffix.lower(), "video/mp4")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def prepare_video_source(path: Path, scratch: Path) -> Path:
    """Use the file as-is if small enough; otherwise a 720p proxy for the API."""
    if not is_video(path):
        raise ImagineError(f"{path} is not a video — edit/extend need a clip")
    if path.stat().st_size <= MAX_VIDEO_BYTES:
        return path
    dest = scratch / f"{path.stem}_proxy.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(path),
            "-vf", "scale=-2:720",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-an", "-movflags", "+faststart",
            str(dest),
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return dest


def extract_mid_frame(video: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dur = 0.0
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            text=True,
        ).strip()
        dur = float(out)
    except Exception:
        pass
    t = max(0.0, dur * 0.4)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "3", str(dest)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not dest.exists() or dest.stat().st_size == 0:
        raise ImagineError(f"failed to extract a still from {video}")
    return dest


def materialize_image(path: Path, scratch: Path) -> Path:
    if is_image(path):
        return path
    if is_video(path):
        return extract_mid_frame(path, scratch / f"{path.stem}_frame.jpg")
    raise ImagineError(f"{path} is not an image or video")


def aspect_from_image(path: Path) -> str:
    try:
        from PIL import Image
        w, h = Image.open(path).size
    except Exception:
        return "16:9"
    if h > w * 1.2:
        return "9:16"
    if w > h * 1.2:
        return "16:9"
    return "1:1"


def enrich_prompt(prompt: str, ref_entries: list[dict]) -> str:
    looks = [e.get("look", "").strip() for e in ref_entries if e.get("look")]
    looks = [x for x in looks if x]
    if not looks:
        return prompt.strip()
    return prompt.strip().rstrip(".") + ". Match this visual look: " + "; ".join(looks) + "."


# -------- HTTP ---------------------------------------------------------------


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def generate_image(
    api_key: str,
    prompt: str,
    *,
    model: str = IMAGE_MODEL,
    aspect_ratio: str | None = None,
    resolution: str = "1k",
    refs: list[Path] | None = None,
) -> bytes:
    body: dict = {
        "model": model,
        "prompt": prompt,
        "response_format": "b64_json",
        "resolution": resolution,
    }
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    if refs:
        images = [{"url": file_to_data_uri(p), "type": "image_url"} for p in refs]
        if len(images) == 1:
            body["image"] = images[0]
        else:
            body["images"] = images
        url = f"{XAI_BASE}/images/edits"
    else:
        url = f"{XAI_BASE}/images/generations"
    resp = requests.post(url, headers=_headers(api_key), json=body, timeout=180)
    if resp.status_code != 200:
        raise ImagineError(f"image API {resp.status_code}: {resp.text[:600]}")
    data = resp.json()
    if data.get("respect_moderation") is False:
        raise ImagineError("image blocked by moderation")
    items = data.get("data") or []
    if not items or not items[0].get("b64_json"):
        # URL fallback
        img_url = (items[0].get("url") if items else None) or data.get("url")
        if not img_url:
            raise ImagineError(f"image API returned no image: {json.dumps(data)[:400]}")
        got = requests.get(img_url, timeout=120)
        got.raise_for_status()
        return got.content
    return base64.b64decode(items[0]["b64_json"])


def tag_voices(prompt: str, voices: list[str]) -> str:
    if not voices:
        return prompt
    if "<AUDIO_0>" in prompt:
        return prompt
    return prompt.rstrip(".") + ". The speaker uses the voice from <AUDIO_0>."


def start_video(
    api_key: str,
    prompt: str,
    *,
    model: str = VIDEO_MODEL,
    duration: int = 6,
    aspect_ratio: str | None = None,
    resolution: str = "720p",
    first_frame: Path | None = None,
    ref_images: list[Path] | None = None,
    voices: list[str] | None = None,
) -> str:
    if first_frame and ref_images:
        raise ImagineError("cannot mix --image (first frame) with --ref (reference-to-video)")
    voices = [v for v in (voices or []) if v]
    if len(voices) > 3:
        raise ImagineError("at most 3 voices per video")
    body: dict = {
        "model": model,
        "prompt": tag_voices(prompt, voices),
        "duration": int(duration),
        "resolution": resolution,
    }
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    if first_frame:
        body["image"] = {"url": file_to_data_uri(first_frame)}
    elif ref_images:
        body["reference_images"] = [{"url": file_to_data_uri(p)} for p in ref_images]
    if voices:
        body["reference_audios"] = [{"voice_id": v} for v in voices]
    return post_video_job(api_key, "/videos/generations", body)


def post_video_job(api_key: str, path: str, body: dict) -> str:
    resp = requests.post(
        f"{XAI_BASE}{path}",
        headers=_headers(api_key),
        json=body,
        timeout=180,
    )
    if resp.status_code != 200:
        raise ImagineError(f"video API {path} {resp.status_code}: {resp.text[:600]}")
    rid = resp.json().get("request_id")
    if not rid:
        raise ImagineError(f"video API {path} returned no request_id: {resp.text[:400]}")
    return rid


def start_edit(api_key: str, prompt: str, video: Path, *, model: str = VIDEO_EDIT_MODEL) -> str:
    return post_video_job(api_key, "/videos/edits", {
        "model": model,
        "prompt": prompt,
        "video": {"url": file_to_video_uri(video)},
    })


def start_extend(
    api_key: str,
    prompt: str,
    video: Path,
    *,
    duration: int,
    model: str = VIDEO_EDIT_MODEL,
) -> str:
    return post_video_job(api_key, "/videos/extensions", {
        "model": model,
        "prompt": prompt,
        "duration": int(duration),
        "video": {"url": file_to_video_uri(video)},
    })


def poll_video(api_key: str, request_id: str, timeout_s: int = VIDEO_TIMEOUT_S) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = requests.get(
            f"{XAI_BASE}/videos/{request_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise ImagineError(f"video poll {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        status = data.get("status")
        if status == "done":
            video = data.get("video") or {}
            if video.get("respect_moderation") is False:
                raise ImagineError("video blocked by moderation")
            url = video.get("url")
            if not url:
                raise ImagineError(f"video done but no url: {json.dumps(data)[:400]}")
            return url
        if status in {"failed", "expired"}:
            err = data.get("error") or {}
            raise ImagineError(
                f"video {status}: {err.get('code', '')} {err.get('message', data)}"
            )
        time.sleep(VIDEO_POLL_S)
    raise ImagineError(f"video {request_id} timed out after {timeout_s}s")


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


# -------- high-level ops -----------------------------------------------------


def require_key() -> str:
    key = read_env_key("XAI_API_KEY")
    if not key:
        raise ImagineError("XAI_API_KEY not found in .env or environment")
    return key


def write_sidecar(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def run_still(
    edit_dir: Path,
    slug: str,
    prompt: str,
    ref_tokens: list[str],
    *,
    aspect_ratio: str | None,
    resolution: str,
    model: str,
    force: bool,
) -> dict:
    assets = load_assets(edit_dir)
    out = edit_dir / "generated" / f"{slug}.png"
    if out.exists() and not force:
        print(f"cached: {out}")
        return {"slug": slug, "image": str(out), "cached": True}

    refs: list[Path] = []
    entries: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        for token in ref_tokens:
            path, entry = resolve_ref(token, edit_dir, assets)
            refs.append(materialize_image(path, scratch))
            if entry:
                entries.append(entry)
        aspect = aspect_ratio or (aspect_from_image(refs[0]) if refs else "16:9")
        final_prompt = enrich_prompt(prompt, entries)
        print(f"imagine still [{model}] aspect={aspect} refs={len(refs)}")
        raw = generate_image(
            require_key(), final_prompt,
            model=model, aspect_ratio=aspect, resolution=resolution, refs=refs,
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    meta = {
        "slug": slug,
        "kind": "still",
        "prompt": prompt,
        "enriched_prompt": final_prompt,
        "refs": ref_tokens,
        "image": str(out),
        "model": model,
        "aspect_ratio": aspect,
    }
    write_sidecar(edit_dir / "generated" / f"{slug}.json", meta)
    print(f"still → {out}")
    return meta


def run_video(
    edit_dir: Path,
    slug: str,
    prompt: str,
    *,
    image_token: str | None,
    ref_tokens: list[str],
    duration: int,
    aspect_ratio: str | None,
    resolution: str,
    model: str,
    force: bool,
    voices: list[str] | None = None,
) -> dict:
    assets = load_assets(edit_dir)
    out = edit_dir / "generated" / f"{slug}.mp4"
    if out.exists() and not force:
        print(f"cached: {out}")
        return {"slug": slug, "video": str(out), "cached": True}

    first: Path | None = None
    refs: list[Path] = []
    entries: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        if image_token:
            path, entry = resolve_ref(image_token, edit_dir, assets)
            first = materialize_image(path, scratch)
            if entry:
                entries.append(entry)
        for token in ref_tokens:
            path, entry = resolve_ref(token, edit_dir, assets)
            refs.append(materialize_image(path, scratch))
            if entry:
                entries.append(entry)
        aspect = aspect_ratio
        if not aspect:
            seed = first or (refs[0] if refs else None)
            aspect = aspect_from_image(seed) if seed else "16:9"
        final_prompt = enrich_prompt(prompt, entries)
        print(f"imagine video [{model}] {duration}s {aspect} {resolution}")
        rid = start_video(
            require_key(), final_prompt,
            model=model, duration=duration, aspect_ratio=aspect,
            resolution=resolution, first_frame=first,
            ref_images=refs or None,
            voices=voices,
        )
        print(f"  request {rid} — polling")
        url = poll_video(require_key(), rid)
        download(url, out)
    meta = {
        "slug": slug,
        "kind": "video",
        "prompt": prompt,
        "enriched_prompt": final_prompt,
        "refs": ref_tokens,
        "image": image_token,
        "video": str(out),
        "model": model,
        "duration": duration,
        "aspect_ratio": aspect,
        "request_id": rid,
        "voices": voices or [],
    }
    write_sidecar(edit_dir / "generated" / f"{slug}.json", meta)
    print(f"video → {out}")
    return meta


def run_shot(
    edit_dir: Path,
    slug: str,
    prompt: str,
    ref_tokens: list[str],
    *,
    duration: int,
    aspect_ratio: str | None,
    image_resolution: str,
    video_resolution: str,
    image_model: str,
    video_model: str,
    force: bool,
    voices: list[str] | None = None,
) -> dict:
    """Still (optionally from refs) then image-to-video. The default gap fill."""
    still = run_still(
        edit_dir, slug, prompt, ref_tokens,
        aspect_ratio=aspect_ratio, resolution=image_resolution,
        model=image_model, force=force,
    )
    if duration <= 0:
        return still
    motion = prompt
    if "camera" not in prompt.lower() and "push" not in prompt.lower():
        motion = prompt.rstrip(".") + ". Slow camera push-in, single subject, no extra action."
    video = run_video(
        edit_dir, slug, motion,
        image_token=still["image"],
        ref_tokens=[],
        duration=duration,
        aspect_ratio=still.get("aspect_ratio") or aspect_ratio,
        resolution=video_resolution,
        model=video_model,
        force=force,
        voices=voices,
    )
    meta = {**still, **video, "kind": "shot", "image": still.get("image")}
    write_sidecar(edit_dir / "generated" / f"{slug}.json", meta)
    return meta


def run_revise(
    edit_dir: Path,
    slug: str,
    prompt: str,
    video_token: str,
    *,
    kind: str,
    duration: int,
    model: str,
    force: bool,
) -> dict:
    """kind is 'edit' (change in place) or 'extend' (add duration seconds)."""
    if kind not in {"edit", "extend"}:
        raise ImagineError(f"revise kind must be edit or extend, got {kind!r}")
    if kind == "extend" and not 2 <= duration <= 10:
        raise ImagineError("extend duration must be 2–10 (added seconds, not total)")
    assets = load_assets(edit_dir)
    out = edit_dir / "generated" / f"{slug}.mp4"
    if out.exists() and not force:
        print(f"cached: {out}")
        return {"slug": slug, "video": str(out), "kind": kind, "cached": True}
    if not video_token:
        raise ImagineError(f"{kind} needs --video (a bin id or path)")
    path, entry = resolve_ref(video_token, edit_dir, assets)
    with tempfile.TemporaryDirectory() as tmp:
        src = prepare_video_source(path, Path(tmp))
        print(f"imagine {kind} [{model}] src={path.name}" + (
            f" +{duration}s" if kind == "extend" else ""
        ))
        if kind == "edit":
            rid = start_edit(require_key(), prompt, src, model=model)
        else:
            rid = start_extend(require_key(), prompt, src, duration=duration, model=model)
        print(f"  request {rid} — polling")
        url = poll_video(require_key(), rid)
        download(url, out)
    meta = {
        "slug": slug,
        "kind": kind,
        "prompt": prompt,
        "source": video_token,
        "look": (entry or {}).get("look") or "",
        "video": str(out),
        "model": model,
        "request_id": rid,
    }
    if kind == "extend":
        meta["duration"] = duration
    write_sidecar(edit_dir / "generated" / f"{slug}.json", meta)
    print(f"{kind} → {out}")
    return meta


def load_gaps(edit_dir: Path) -> list[dict]:
    path = edit_dir / "gaps.json"
    if not path.exists():
        raise ImagineError(f"no {path} — write gaps.json from the confirmed strategy")
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        gaps = data.get("gaps") or data.get("generate") or []
    else:
        gaps = data
    if not isinstance(gaps, list) or not gaps:
        raise ImagineError(f"{path} has no gaps")
    return gaps


def run_fill(
    edit_dir: Path,
    videos_dir: Path,
    *,
    force: bool,
    image_model: str,
    video_model: str,
    image_resolution: str,
    video_resolution: str,
) -> list[dict]:
    require_confirmed(edit_dir, force=force, action="imagine fill")
    gaps = load_gaps(edit_dir)
    results = []
    for i, gap in enumerate(gaps, start=1):
        slug = slugify(str(gap.get("slug") or gap.get("id") or f"gap-{i}"))
        prompt = (gap.get("prompt") or "").strip()
        if not prompt:
            raise ImagineError(f"gap {slug} has no prompt")
        refs = gap.get("refs") or gap.get("ref") or []
        if isinstance(refs, str):
            refs = [refs]
        kind = (gap.get("kind") or "shot").lower()
        duration = int(gap.get("duration") if gap.get("duration") is not None else (
            0 if kind == "still" else DEFAULT_SHOT_DURATION
        ))
        aspect = gap.get("aspect_ratio") or gap.get("aspect")
        voices = gap.get("voices") or gap.get("voice") or []
        if isinstance(voices, str):
            voices = [voices]
        print(f"\n[{i}/{len(gaps)}] {kind} {slug}")
        if kind == "still":
            results.append(run_still(
                edit_dir, slug, prompt, list(refs),
                aspect_ratio=aspect, resolution=image_resolution,
                model=image_model, force=force,
            ))
        elif kind == "video":
            results.append(run_video(
                edit_dir, slug, prompt,
                image_token=gap.get("image"),
                ref_tokens=list(refs),
                duration=duration or DEFAULT_SHOT_DURATION,
                aspect_ratio=aspect, resolution=video_resolution,
                model=video_model, force=force, voices=list(voices)[:3],
            ))
        elif kind in {"edit", "extend"}:
            src = gap.get("video") or (refs[0] if refs else "")
            results.append(run_revise(
                edit_dir, slug, prompt, str(src),
                kind=kind,
                duration=duration or 5,
                model=VIDEO_EDIT_MODEL,
                force=force,
            ))
        else:
            results.append(run_shot(
                edit_dir, slug, prompt, list(refs),
                duration=duration, aspect_ratio=aspect,
                image_resolution=image_resolution,
                video_resolution=video_resolution,
                image_model=image_model, video_model=video_model,
                force=force, voices=list(voices)[:3],
            ))
    print("\nre-inventory generated/")
    build_bin(videos_dir, edit_dir)
    return results


# -------- CLI ----------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Fill edit/generated/ via Grok Imagine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--edit-dir", type=Path, required=True)
        p.add_argument("--slug", required=True)
        p.add_argument("--prompt", required=True)
        p.add_argument("--ref", action="append", default=[], help="Bin id or path. Repeatable, max 3.")
        p.add_argument("--aspect", default=None, help="16:9, 9:16, 1:1, auto, …")
        p.add_argument("--force", action="store_true")
        p.add_argument("--voice", action="append", default=[], help="Imagine voice_id (repeatable, max 3).")

    p_still = sub.add_parser("still", help="Generate or edit a still")
    add_common(p_still)
    p_still.add_argument("--resolution", default="1k", choices=("1k", "2k"))
    p_still.add_argument("--model", default=IMAGE_MODEL)

    p_video = sub.add_parser("video", help="Text-to-video, image-to-video, or reference-to-video")
    add_common(p_video)
    p_video.add_argument("--image", default=None, help="Bin id or path used as frame 1")
    p_video.add_argument("--duration", type=int, default=DEFAULT_SHOT_DURATION)
    p_video.add_argument("--resolution", default="720p", choices=("480p", "720p", "1080p"))
    p_video.add_argument("--model", default=VIDEO_MODEL)

    p_shot = sub.add_parser("shot", help="Still then image-to-video (default generate-gap)")
    add_common(p_shot)
    p_shot.add_argument("--duration", type=int, default=DEFAULT_SHOT_DURATION)
    p_shot.add_argument("--image-resolution", default="1k", choices=("1k", "2k"))
    p_shot.add_argument("--video-resolution", default="720p", choices=("480p", "720p", "1080p"))
    p_shot.add_argument("--image-model", default=IMAGE_MODEL)
    p_shot.add_argument("--video-model", default=VIDEO_MODEL)

    p_edit = sub.add_parser("edit", help="Change an existing clip; keep the rest of the shot")
    add_common(p_edit)
    p_edit.add_argument("--video", required=True, help="Bin id or path of the source clip")
    p_edit.add_argument("--model", default=VIDEO_EDIT_MODEL)

    p_ext = sub.add_parser("extend", help="Continue a clip from its last frame (duration = added seconds)")
    add_common(p_ext)
    p_ext.add_argument("--video", required=True, help="Bin id or path of the source clip")
    p_ext.add_argument("--duration", type=int, default=5, help="Seconds to add, not total length")
    p_ext.add_argument("--model", default=VIDEO_EDIT_MODEL)

    p_fill = sub.add_parser("fill", help="Run every entry in edit/gaps.json, then re-inventory")
    p_fill.add_argument("--edit-dir", type=Path, required=True)
    p_fill.add_argument("--videos-dir", type=Path, default=None)
    p_fill.add_argument("--force", action="store_true")
    p_fill.add_argument("--image-model", default=IMAGE_MODEL)
    p_fill.add_argument("--video-model", default=VIDEO_MODEL)
    p_fill.add_argument("--image-resolution", default="1k", choices=("1k", "2k"))
    p_fill.add_argument("--video-resolution", default="720p", choices=("480p", "720p", "1080p"))

    args = ap.parse_args()
    try:
        if args.cmd == "still":
            run_still(
                args.edit_dir.resolve(), slugify(args.slug), args.prompt, args.ref[:3],
                aspect_ratio=args.aspect, resolution=args.resolution,
                model=args.model, force=args.force,
            )
        elif args.cmd == "video":
            if not 1 <= args.duration <= 15:
                raise ImagineError("duration must be 1–15")
            run_video(
                args.edit_dir.resolve(), slugify(args.slug), args.prompt,
                image_token=args.image, ref_tokens=args.ref[:3],
                duration=args.duration, aspect_ratio=args.aspect,
                resolution=args.resolution, model=args.model, force=args.force,
                voices=args.voice[:3],
            )
        elif args.cmd == "shot":
            if args.duration < 0 or args.duration > 15:
                raise ImagineError("duration must be 0–15 (0 = still only)")
            run_shot(
                args.edit_dir.resolve(), slugify(args.slug), args.prompt, args.ref[:3],
                duration=args.duration, aspect_ratio=args.aspect,
                image_resolution=args.image_resolution,
                video_resolution=args.video_resolution,
                image_model=args.image_model, video_model=args.video_model,
                force=args.force, voices=args.voice[:3],
            )
        elif args.cmd in {"edit", "extend"}:
            run_revise(
                args.edit_dir.resolve(), slugify(args.slug), args.prompt, args.video,
                kind=args.cmd, duration=getattr(args, "duration", 5),
                model=args.model, force=args.force,
            )
        else:
            edit_dir = args.edit_dir.resolve()
            videos_dir = (args.videos_dir or edit_dir.parent).resolve()
            run_fill(
                edit_dir, videos_dir, force=args.force,
                image_model=args.image_model, video_model=args.video_model,
                image_resolution=args.image_resolution,
                video_resolution=args.video_resolution,
            )
    except (ImagineError, SessionError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
