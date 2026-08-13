"""Session gate: is this edit ready to generate / render?

The program is blocked until the user confirms strategy.md. Drafting
clears confirmation. Editing strategy.md after confirm invalidates it.

Usage:
    python helpers/session.py status --edit-dir <edit>
    python helpers/session.py confirm --edit-dir <edit>
    python helpers/session.py check --edit-dir <edit>   # exit 1 if not confirmed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from inventory import load_bin


STAMP = "strategy.confirmed"
DRAFT_MARK = "UNCONFIRMED DRAFT"


class SessionError(RuntimeError):
    pass


def file_hash(path: Path) -> str:
    data = path.read_bytes() if path.exists() else b""
    return hashlib.sha256(data).hexdigest()[:16]


def strategy_state(edit_dir: Path) -> str:
    md = edit_dir / "strategy.md"
    if not md.exists():
        return "missing"
    text = md.read_text(encoding="utf-8")
    if DRAFT_MARK in text:
        return "draft"
    stamp = edit_dir / STAMP
    if not stamp.exists():
        return "unconfirmed"
    try:
        meta = json.loads(stamp.read_text())
    except json.JSONDecodeError:
        return "unconfirmed"
    if meta.get("hash") != file_hash(md):
        return "stale"
    return "confirmed"


def clear_confirmation(edit_dir: Path) -> None:
    stamp = edit_dir / STAMP
    stamp.unlink(missing_ok=True)


def confirm(edit_dir: Path) -> Path:
    md = edit_dir / "strategy.md"
    if not md.exists():
        raise SessionError(f"no {md} — draft a strategy first")
    text = md.read_text(encoding="utf-8")
    if DRAFT_MARK in text:
        lines = [ln for ln in text.splitlines() if DRAFT_MARK not in ln]
        # drop a leftover empty blockquote line next to the banner
        cleaned: list[str] = []
        for ln in lines:
            if ln.strip() == ">" and (not cleaned or cleaned[-1].strip() == ""):
                continue
            cleaned.append(ln)
        md.write_text("\n".join(cleaned).strip() + "\n", encoding="utf-8")
    stamp = edit_dir / STAMP
    stamp.write_text(json.dumps({
        "hash": file_hash(md),
        "confirmed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2) + "\n")
    return stamp


def require_confirmed(edit_dir: Path, *, force: bool = False, action: str = "this") -> None:
    if force:
        return
    state = strategy_state(edit_dir)
    if state == "confirmed":
        return
    raise SessionError(
        f"cannot {action}: strategy is {state}. "
        f"Confirm with: python helpers/session.py confirm --edit-dir {edit_dir}"
    )


def gaps_progress(edit_dir: Path) -> tuple[int, int, list[str]]:
    path = edit_dir / "gaps.json"
    if not path.exists():
        return 0, 0, []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return 0, 0, []
    gaps = data.get("gaps") if isinstance(data, dict) else data
    if not isinstance(gaps, list):
        return 0, 0, []
    missing: list[str] = []
    filled = 0
    for g in gaps:
        slug = str((g or {}).get("slug") or "")
        if not slug:
            continue
        gen = edit_dir / "generated"
        if any((gen / f"{slug}{ext}").exists() for ext in (".mp4", ".png", ".wav", ".webm")):
            filled += 1
        else:
            missing.append(slug)
    return filled, len(gaps), missing


def inspect(edit_dir: Path) -> dict:
    edit_dir = edit_dir.resolve()
    bin_data = load_bin(edit_dir / "bin.json")
    assets = list(bin_data.get("assets") or [])
    looks_missing = sum(1 for a in assets if not (a.get("look") or "").strip())
    filled, n_gaps, gap_missing = gaps_progress(edit_dir)
    edl_ranges = 0
    edl_path = edit_dir / "edl.json"
    if edl_path.exists():
        try:
            edl_ranges = len(json.loads(edl_path.read_text()).get("ranges") or [])
        except json.JSONDecodeError:
            edl_ranges = -1
    latest = ""
    hist = edit_dir / "history"
    if hist.is_dir():
        snaps = sorted(p.name for p in hist.iterdir() if p.is_dir())
        if snaps:
            latest = snaps[-1]
    return {
        "edit_dir": str(edit_dir),
        "bin_assets": len(assets),
        "looks_missing": looks_missing,
        "strategy": strategy_state(edit_dir),
        "gaps_filled": filled,
        "gaps_total": n_gaps,
        "gaps_missing": gap_missing,
        "edl_ranges": edl_ranges,
        "latest_snapshot": latest,
        "ready": strategy_state(edit_dir) == "confirmed",
    }


def format_status(info: dict) -> str:
    lines = [
        f"edit:      {info['edit_dir']}",
        f"bin:       {info['bin_assets']} assets, {info['looks_missing']} look(s) empty",
        f"strategy:  {info['strategy']}",
        f"gaps:      {info['gaps_filled']}/{info['gaps_total']} filled"
        + (f"  missing: {', '.join(info['gaps_missing'])}" if info["gaps_missing"] else ""),
        f"edl:       {info['edl_ranges']} range(s)" if info["edl_ranges"] >= 0 else "edl:       unreadable",
        f"snapshot:  {info['latest_snapshot'] or '(none)'}",
        f"ready:     {'yes' if info['ready'] else 'no — confirm strategy before fill/render'}",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Session status and strategy confirmation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (
        ("status", "Print bin / strategy / gaps / edl / snapshot"),
        ("confirm", "Mark strategy.md confirmed (required before fill/render)"),
        ("check", "Exit 1 unless strategy is confirmed"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--edit-dir", type=Path, required=True)
    args = ap.parse_args()
    edit_dir = args.edit_dir.resolve()
    try:
        if args.cmd == "status":
            print(format_status(inspect(edit_dir)))
            return
        if args.cmd == "confirm":
            confirm(edit_dir)
            print(f"strategy confirmed → {edit_dir / STAMP}")
            return
        require_confirmed(edit_dir, action="proceed")
        print("strategy confirmed")
    except SessionError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
