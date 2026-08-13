"""Build a compact asset bin from a working directory of video, images, and audio.

Walks the folder once, probes every media file, and writes:

  <edit>/bin.json     structured catalog (hash-cached)
  <edit>/bin.md       LLM-readable view
  <edit>/bin/thumbs/  contact sheets (video), stills (image), waveforms (audio)

Looks (one-line visual descriptions) are preserved across re-runs. The agent
fills them after looking at the thumbs — this helper does not call a vision API.

Usage:
    python helpers/inventory.py <videos_dir>
    python helpers/inventory.py <videos_dir> --edit-dir /custom/edit
    python helpers/inventory.py <videos_dir> --set-look C0103="talking head, warm interior"
    python helpers/inventory.py <videos_dir> --force
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from media import asset_id, fingerprint, iter_assets, probe, thumb_name
from timeline_view import compute_envelope, extract_frames, load_font

from PIL import Image, ImageDraw


THUMB_DIRNAME = "bin/thumbs"
BIN_JSON = "bin.json"
BIN_MD = "bin.md"


# -------- thumbs -------------------------------------------------------------


def _run_ffmpeg(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


def render_image_thumb(src: Path, dest: Path, max_w: int = 640) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        img = Image.open(src).convert("RGB")
    except Exception:
        return _run_ffmpeg(
            ["ffmpeg", "-y", "-i", str(src), "-frames:v", "1", "-vf", f"scale={max_w}:-2", "-q:v", "4", str(dest)]
        )
    if img.width > max_w:
        h = max(1, int(img.height * max_w / img.width))
        img = img.resize((max_w, h), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=85)
    return True


def render_video_contact_sheet(src: Path, duration: float, dest: Path, n: int = 4) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if duration <= 0:
        start, end = 0.0, 0.1
    else:
        start = duration * 0.02
        end = max(start + 0.01, duration * 0.98)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        try:
            frames = extract_frames(src, start, end, n, tmp_dir)
        except Exception:
            return False
        imgs: list[tuple[Image.Image, float]] = []
        if n <= 1:
            times = [(start + end) / 2.0]
        else:
            step = (end - start) / (n - 1)
            times = [start + i * step for i in range(n)]
        for fp, t in zip(frames, times):
            try:
                imgs.append((Image.open(fp).convert("RGB"), t))
            except Exception:
                continue
        if not imgs:
            return False

        frame_h = 180
        gap = 6
        pad = 8
        label_h = 18
        resized: list[tuple[Image.Image, float]] = []
        for img, t in imgs:
            new_w = max(1, int(frame_h * img.width / img.height))
            resized.append((img.resize((new_w, frame_h), Image.LANCZOS), t))

        width = pad * 2 + sum(im.width for im, _ in resized) + gap * (len(resized) - 1)
        height = pad * 2 + frame_h + label_h
        canvas = Image.new("RGB", (width, height), (18, 18, 22))
        draw = ImageDraw.Draw(canvas)
        font = load_font(12)
        x = pad
        for im, t in resized:
            canvas.paste(im, (x, pad))
            draw.text((x + 2, pad + frame_h + 2), f"{t:.1f}s", fill=(180, 180, 186), font=font)
            x += im.width + gap
        canvas.save(dest, "JPEG", quality=85)
        return True


def render_audio_thumb(src: Path, duration: float, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    end = max(duration, 0.2)
    try:
        env = compute_envelope(src, 0.0, end, samples=800)
    except Exception:
        return False
    width, height = 640, 160
    canvas = Image.new("RGB", (width, height), (18, 18, 22))
    draw = ImageDraw.Draw(canvas)
    mid = height // 2
    max_amp = height // 2 - 8
    wave = (140, 180, 255)
    pts_top: list[tuple[int, int]] = []
    pts_bot: list[tuple[int, int]] = []
    n = max(1, len(env) - 1)
    for i, v in enumerate(env):
        xi = int(i * (width - 1) / n)
        a = int(float(v) * max_amp)
        pts_top.append((xi, mid - a))
        pts_bot.append((xi, mid + a))
    if pts_top:
        poly = pts_top + list(reversed(pts_bot))
        draw.polygon(poly, fill=(40, 60, 90))
        draw.line(pts_top, fill=wave, width=1)
        draw.line(pts_bot, fill=wave, width=1)
    canvas.save(dest, "JPEG", quality=85)
    return True


# -------- catalog ------------------------------------------------------------


def parse_set_look(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in items:
        if "=" not in raw:
            raise ValueError(f"--set-look needs ID=text, got {raw!r}")
        key, val = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--set-look needs ID=text, got {raw!r}")
        out[key] = val.strip()
    return out


def load_bin(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "assets": []}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"version": 1, "assets": []}
    if not isinstance(data, dict):
        return {"version": 1, "assets": []}
    data.setdefault("version", 1)
    data.setdefault("assets", [])
    return data


def looks_from_bin(data: dict) -> dict[str, str]:
    looks: dict[str, str] = {}
    for asset in data.get("assets") or []:
        look = (asset.get("look") or "").strip()
        aid = asset.get("id")
        if aid and look:
            looks[aid] = look
    return looks


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}m {s:04.1f}s"


def render_bin_md(data: dict) -> str:
    assets = list(data.get("assets") or [])
    counts = {"video": 0, "image": 0, "audio": 0}
    for a in assets:
        k = a.get("kind")
        if k in counts:
            counts[k] += 1
    lines = [
        "# Asset bin",
        "",
        f"Scanned: {data.get('scanned_at', '')}",
        f"{len(assets)} assets ({counts['video']} video, {counts['image']} image, {counts['audio']} audio)",
        "",
        "Looks are one-line visual descriptions. Fill missing ones after viewing `bin/thumbs/`.",
        "",
    ]

    sections = [
        ("video", "Videos"),
        ("image", "Images"),
        ("audio", "Audio"),
    ]
    for kind, title in sections:
        group = [a for a in assets if a.get("kind") == kind]
        if not group:
            continue
        lines.append(f"## {title}")
        lines.append("")
        for a in group:
            aid = a.get("id", "?")
            name = Path(a.get("path") or aid).name
            lines.append(f"### {aid}")
            lines.append(f"- file: `{name}`")
            lines.append(f"- path: `{a.get('path', '')}`")
            if kind != "image":
                lines.append(f"- duration: {format_duration(float(a.get('duration_s') or 0))}")
            w, h = a.get("width") or 0, a.get("height") or 0
            if w and h:
                fps = a.get("fps") or 0
                fps_s = f" @ {fps:.2f}fps" if fps and kind == "video" else ""
                lines.append(f"- size: {w}×{h}{fps_s}")
            if kind == "video":
                lines.append(f"- audio: {'yes' if a.get('has_audio') else 'no'}")
            if a.get("thumb"):
                lines.append(f"- thumb: `{a['thumb']}`")
            look = (a.get("look") or "").strip()
            lines.append(f"- look: {look if look else '_(not yet described)_'}")
            lines.append("")
    return "\n".join(lines)


def build_bin(
    videos_dir: Path,
    edit_dir: Path,
    set_looks: dict[str, str] | None = None,
    force: bool = False,
) -> dict:
    edit_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir = edit_dir / "bin" / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    bin_path = edit_dir / BIN_JSON
    old = load_bin(bin_path)
    old_by_id = {a["id"]: a for a in old.get("assets") or [] if a.get("id")}
    looks = looks_from_bin(old)
    if set_looks:
        looks.update(set_looks)

    generated = edit_dir / "generated"
    extra = [generated] if generated.is_dir() else []
    discovered = iter_assets(videos_dir, extra_roots=extra)

    assets: list[dict] = []
    for path, kind in discovered:
        aid = asset_id(path, videos_dir, edit_dir)
        fp = fingerprint(path)
        prev = old_by_id.get(aid)
        cached = (
            not force
            and prev is not None
            and prev.get("fingerprint") == fp
            and prev.get("kind") == kind
        )

        if cached:
            entry = dict(prev)
            entry["path"] = str(path)
            entry["look"] = looks.get(aid, entry.get("look") or "")
            thumb_rel = entry.get("thumb")
            if thumb_rel and not (edit_dir / thumb_rel).exists():
                cached = False

        if not cached:
            meta = probe(path)
            thumb_rel = f"{THUMB_DIRNAME}/{thumb_name(aid)}"
            thumb_path = edit_dir / thumb_rel
            ok = False
            if kind == "video":
                ok = render_video_contact_sheet(path, float(meta["duration_s"] or 0), thumb_path)
            elif kind == "image":
                ok = render_image_thumb(path, thumb_path)
            else:
                ok = render_audio_thumb(path, float(meta["duration_s"] or 0), thumb_path)
            entry = {
                "id": aid,
                "kind": kind,
                "path": str(path),
                "fingerprint": fp,
                "duration_s": round(float(meta["duration_s"] or 0), 3),
                "width": int(meta["width"] or 0),
                "height": int(meta["height"] or 0),
                "fps": round(float(meta["fps"] or 0), 3),
                "has_audio": bool(meta["has_audio"]),
                "codec_v": meta.get("codec_v") or "",
                "codec_a": meta.get("codec_a") or "",
                "thumb": thumb_rel if ok else "",
                "look": looks.get(aid, ""),
            }
        assets.append(entry)

    data = {
        "version": 1,
        "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": str(videos_dir.resolve()),
        "assets": assets,
    }
    bin_path.write_text(json.dumps(data, indent=2) + "\n")
    (edit_dir / BIN_MD).write_text(render_bin_md(data), encoding="utf-8")
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description="Inventory a folder of video, images, and audio into edit/bin")
    ap.add_argument("videos_dir", type=Path, help="Working directory of source assets")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <videos_dir>/edit)",
    )
    ap.add_argument(
        "--set-look",
        action="append",
        default=[],
        metavar="ID=TEXT",
        help="Set a one-line look for an asset id. Repeatable. Preserved on later runs.",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Rebuild thumbs even when the source fingerprint is unchanged.",
    )
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"not a directory: {videos_dir}")

    try:
        set_looks = parse_set_look(args.set_look)
    except ValueError as e:
        sys.exit(str(e))

    edit_dir = (args.edit_dir or (videos_dir / "edit")).resolve()
    data = build_bin(videos_dir, edit_dir, set_looks=set_looks, force=args.force)

    assets = data["assets"]
    kinds = {}
    for a in assets:
        kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
    missing_looks = sum(1 for a in assets if not (a.get("look") or "").strip())
    print(f"bin → {edit_dir / BIN_MD}")
    print(
        f"  {len(assets)} assets "
        f"({kinds.get('video', 0)} video, {kinds.get('image', 0)} image, {kinds.get('audio', 0)} audio)"
    )
    if missing_looks:
        print(f"  {missing_looks} look(s) still empty — view bin/thumbs/ then --set-look ID=\"...\"")


if __name__ == "__main__":
    main()
