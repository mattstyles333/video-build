"""Snapshot and restore edit decisions (not pixels).

The program is strategy.md + gaps.json + edl.json. Pixels are a rebuild.
Each confirmed preview gets a numbered snapshot so you can restore a whole
cut or one beat.

Usage:
    python helpers/history.py init <videos_dir> [--git]
    python helpers/history.py snapshot --edit-dir <edit> -m "tighter hook"
    python helpers/history.py list --edit-dir <edit>
    python helpers/history.py restore 3 --edit-dir <edit>
    python helpers/history.py restore 3 --beat CITY --edit-dir <edit>
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_FILES = ("edl.json", "strategy.md", "gaps.json")
SIDECAR_GLOBS = ("generated/*.json",)


FOOTAGE_GITIGNORE = """# video-build: version the program, not the pixels.
# Sources and renders stay on disk. Decisions live in git.

# Media (working-directory footage)
*.mp4
*.mov
*.mkv
*.avi
*.m4v
*.webm
*.wav
*.mp3
*.aac
*.flac
*.ogg
*.aiff
*.aif

# Render / cache
edit/clips_graded/
edit/clips_preview/
edit/clips_draft/
edit/bin/thumbs/
edit/verify/
edit/downloads/
edit/preview.mp4
edit/final.mp4
edit/base.mp4
edit/base_preview.mp4
edit/base_draft.mp4

# Generated plates + animation renders (keep *.json sidecars)
edit/generated/*
!edit/generated/*.json
edit/animations/**/render.mp4
edit/animations/**/render.webm
edit/animations/**/node_modules/
edit/animations/**/media/
edit/graphics/*.png

# Keep the program
!edit/edl.json
!edit/strategy.md
!edit/gaps.json
!edit/bin.json
!edit/bin.md
!edit/project.md
!edit/takes_packed.md
!edit/master.srt
!edit/history/
!edit/history/**
"""


def slugify(message: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", message.strip().lower()).strip("-")
    return (s[:40] or "snapshot").rstrip("-")


def history_dir(edit_dir: Path) -> Path:
    return edit_dir / "history"


def parse_snapshot_name(name: str) -> tuple[int, str] | None:
    m = re.match(r"^(\d{3})-(.+)$", name)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def list_snapshots(edit_dir: Path) -> list[Path]:
    root = history_dir(edit_dir)
    if not root.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    for p in root.iterdir():
        if not p.is_dir():
            continue
        parsed = parse_snapshot_name(p.name)
        if parsed:
            found.append((parsed[0], p))
    found.sort(key=lambda item: item[0])
    return [p for _, p in found]


def resolve_snapshot(edit_dir: Path, spec: str) -> Path:
    snaps = list_snapshots(edit_dir)
    if not snaps:
        raise FileNotFoundError(f"no snapshots in {history_dir(edit_dir)}")
    if spec.isdigit():
        n = int(spec)
        for p in snaps:
            parsed = parse_snapshot_name(p.name)
            if parsed and parsed[0] == n:
                return p
        raise FileNotFoundError(f"no snapshot {n:03d}")
    # allow "003" or "003-tighter-hook" or unique slug substring
    for p in snaps:
        if p.name == spec or p.name.startswith(spec) or spec in p.name:
            return p
    raise FileNotFoundError(f"no snapshot matching {spec!r}")


def next_index(edit_dir: Path) -> int:
    snaps = list_snapshots(edit_dir)
    if not snaps:
        return 1
    parsed = parse_snapshot_name(snaps[-1].name)
    return (parsed[0] if parsed else 0) + 1


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def range_duration(r: dict) -> float:
    return float(r["end"]) - float(r.get("start") or 0)


def beat_key(r: dict) -> str:
    return str(r.get("beat") or "").strip().lower()


def ranges_window(ranges: list[dict], beat: str) -> tuple[float, float] | None:
    """Output-timeline [start, end) covering all ranges with this beat."""
    want = beat.strip().lower()
    start = 0.0
    hit_start: float | None = None
    hit_end: float | None = None
    for r in ranges:
        dur = range_duration(r)
        if beat_key(r) == want:
            if hit_start is None:
                hit_start = start
            hit_end = start + dur
        start += dur
    if hit_start is None or hit_end is None:
        return None
    return hit_start, hit_end


def overlay_in_window(ov: dict, window: tuple[float, float], beat: str) -> bool:
    if beat_key(ov) == beat.strip().lower():
        return True
    t0 = float(ov.get("start_in_output") or 0)
    t1 = t0 + float(ov.get("duration") or 0)
    a, b = window
    return t0 < b and t1 > a


def restore_beat(current: dict, snapshot: dict, beat: str) -> dict:
    """Replace ranges (and overlapping overlays) for one beat. Shift later overlays."""
    want = beat.strip().lower()
    cur_ranges = list(current.get("ranges") or [])
    snap_ranges = list(snapshot.get("ranges") or [])
    snap_beat = [r for r in snap_ranges if beat_key(r) == want]
    if not snap_beat:
        raise ValueError(f"snapshot has no beat {beat!r}")

    old_window = ranges_window(cur_ranges, want)
    old_dur = (old_window[1] - old_window[0]) if old_window else 0.0

    if any(beat_key(r) == want for r in cur_ranges):
        new_ranges: list[dict] = []
        inserted = False
        for r in cur_ranges:
            if beat_key(r) != want:
                new_ranges.append(r)
                continue
            if not inserted:
                new_ranges.extend(dict(x) for x in snap_beat)
                inserted = True
        ranges = new_ranges
    else:
        ranges = cur_ranges + [dict(x) for x in snap_beat]

    new_window = ranges_window(ranges, want)
    if new_window is None:
        raise ValueError(f"beat {beat!r} missing after splice")
    new_start, new_end = new_window
    new_dur = new_end - new_start
    delta = new_dur - old_dur
    old_end = old_window[1] if old_window else new_start

    snap_window = ranges_window(snap_ranges, want)
    if snap_window is None:
        raise ValueError(f"snapshot window missing for {beat!r}")
    snap_start, snap_end = snap_window
    shift = new_start - snap_start

    kept: list[dict] = []
    for ov in current.get("overlays") or []:
        if old_window and overlay_in_window(ov, old_window, want):
            continue
        ov = dict(ov)
        t0 = float(ov.get("start_in_output") or 0)
        if old_window and t0 >= old_end - 1e-9 and delta:
            ov["start_in_output"] = round(t0 + delta, 3)
        kept.append(ov)

    for ov in snapshot.get("overlays") or []:
        if overlay_in_window(ov, snap_window, want):
            imported = dict(ov)
            imported["start_in_output"] = round(
                float(imported.get("start_in_output") or 0) + shift, 3
            )
            imported.setdefault("beat", snap_beat[0].get("beat") or beat)
            kept.append(imported)

    kept.sort(key=lambda o: float(o.get("start_in_output") or 0))
    out = dict(current)
    out["ranges"] = ranges
    out["overlays"] = kept
    out["total_duration_s"] = round(sum(range_duration(r) for r in ranges), 3)
    return out


def snapshot(edit_dir: Path, message: str) -> Path:
    edit_dir = edit_dir.resolve()
    if not (edit_dir / "edl.json").exists():
        raise FileNotFoundError(f"no edl.json in {edit_dir} — nothing to snapshot")
    n = next_index(edit_dir)
    dest = history_dir(edit_dir) / f"{n:03d}-{slugify(message)}"
    dest.mkdir(parents=True, exist_ok=False)
    for name in SNAPSHOT_FILES:
        src = edit_dir / name
        if src.exists():
            shutil.copy2(src, dest / name)
    sidecar_dir = dest / "generated"
    for pattern in SIDECAR_GLOBS:
        for src in edit_dir.glob(pattern):
            sidecar_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, sidecar_dir / src.name)
    meta = {
        "n": n,
        "message": message.strip(),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (dest / "message.txt").write_text(message.strip() + "\n")
    write_json(dest / "meta.json", meta)
    return dest


def restore_full(edit_dir: Path, snap: Path) -> None:
    for name in SNAPSHOT_FILES:
        src = snap / name
        if src.exists():
            shutil.copy2(src, edit_dir / name)


def maybe_git_commit(root: Path, message: str) -> bool:
    git_dir = root / ".git"
    if not git_dir.exists():
        return False
    try:
        subprocess.run(
            ["git", "-C", str(root), "add",
             "edit/edl.json", "edit/strategy.md", "edit/gaps.json",
             "edit/history", "edit/bin.json", "edit/bin.md",
             "edit/project.md", "edit/generated"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        staged = subprocess.run(
            ["git", "-C", str(root), "diff", "--cached", "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if staged.returncode == 0:
            return False
        subprocess.run(
            ["git", "-C", str(root), "commit", "-m", message],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def init_footage(videos_dir: Path, use_git: bool) -> None:
    videos_dir = videos_dir.resolve()
    gi = videos_dir / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    if "video-build: version the program" not in existing:
        block = FOOTAGE_GITIGNORE
        if existing and not existing.endswith("\n"):
            existing += "\n"
        gi.write_text(existing + ("\n" if existing else "") + block)
        print(f"gitignore → {gi}")
    else:
        print(f"gitignore already present: {gi}")
    (videos_dir / "edit").mkdir(parents=True, exist_ok=True)
    if use_git:
        if not (videos_dir / ".git").exists():
            subprocess.run(["git", "init"], cwd=videos_dir, check=True)
            print(f"git init → {videos_dir}")
        maybe_git_commit(videos_dir, "video-build: track edit decisions")


def snapshot_summary(path: Path) -> str:
    parsed = parse_snapshot_name(path.name)
    n = f"{parsed[0]:03d}" if parsed else "???"
    msg = ""
    meta_p = path / "meta.json"
    if meta_p.exists():
        try:
            meta = read_json(meta_p)
            msg = meta.get("message") or ""
            when = meta.get("created_at", "")
        except json.JSONDecodeError:
            when = ""
    else:
        when = ""
        msg_p = path / "message.txt"
        if msg_p.exists():
            msg = msg_p.read_text().strip()
    extra = ""
    edl_p = path / "edl.json"
    if edl_p.exists():
        try:
            edl = read_json(edl_p)
            extra = f"{len(edl.get('ranges') or [])} ranges, {len(edl.get('overlays') or [])} overlays"
        except json.JSONDecodeError:
            extra = "edl unreadable"
    return f"{n}  {msg or path.name}  {when}  {extra}".rstrip()


def main() -> None:
    ap = argparse.ArgumentParser(description="Snapshot / restore edit decisions")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Write a media-safe .gitignore (optional git init)")
    p_init.add_argument("videos_dir", type=Path)
    p_init.add_argument("--git", action="store_true", help="git init + commit text artifacts if possible")

    p_snap = sub.add_parser("snapshot", help="Record the current program")
    p_snap.add_argument("--edit-dir", type=Path, required=True)
    p_snap.add_argument("-m", "--message", required=True)
    p_snap.add_argument("--git", action="store_true", help="commit the snapshot if the footage dir is a git repo")

    p_list = sub.add_parser("list", help="List snapshots")
    p_list.add_argument("--edit-dir", type=Path, required=True)

    p_rest = sub.add_parser("restore", help="Restore a snapshot (full or one beat)")
    p_rest.add_argument("spec", help="Index (3), padded (003), or snapshot name")
    p_rest.add_argument("--edit-dir", type=Path, required=True)
    p_rest.add_argument("--beat", default=None, help="Restore only this EDL beat")
    p_rest.add_argument("--git", action="store_true")

    args = ap.parse_args()
    try:
        if args.cmd == "init":
            init_footage(args.videos_dir.resolve(), use_git=args.git)
            return
        edit_dir = args.edit_dir.resolve()
        if args.cmd == "snapshot":
            dest = snapshot(edit_dir, args.message)
            print(f"snapshot → {dest.relative_to(edit_dir)}")
            if args.git:
                root = edit_dir.parent
                if maybe_git_commit(root, f"edit: {args.message.strip()}"):
                    print(f"git commit in {root}")
            return
        if args.cmd == "list":
            snaps = list_snapshots(edit_dir)
            if not snaps:
                print("no snapshots")
                return
            for p in snaps:
                print(snapshot_summary(p))
            return
        snap = resolve_snapshot(edit_dir, args.spec)
        if args.beat:
            current_p = edit_dir / "edl.json"
            snap_edl_p = snap / "edl.json"
            if not current_p.exists() or not snap_edl_p.exists():
                raise FileNotFoundError("need edl.json in both edit/ and the snapshot")
            merged = restore_beat(read_json(current_p), read_json(snap_edl_p), args.beat)
            write_json(current_p, merged)
            print(f"restored beat {args.beat!r} from {snap.name} → edl.json")
        else:
            restore_full(edit_dir, snap)
            print(f"restored {snap.name} → {edit_dir}")
        if args.git:
            root = edit_dir.parent
            if maybe_git_commit(root, f"edit: restore {snap.name}" + (f" beat {args.beat}" if args.beat else "")):
                print(f"git commit in {root}")
    except (FileNotFoundError, ValueError, OSError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
