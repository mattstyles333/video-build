"""Draft strategy.md + gaps.json from the bin and packed speech via Grok.

This writes a DRAFT. Hard rule 11 still applies: the user must confirm
strategy.md before any cut, Imagine fill, or TTS.

Usage:
    python helpers/strategy.py draft --edit-dir <edit>
    python helpers/strategy.py draft --edit-dir <edit> --brief "45s vertical launch"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

from media import read_env_key
from session import clear_confirmation


XAI_CHAT = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL = "grok-4.6"


class StrategyError(RuntimeError):
    pass


SYSTEM = """You are the editor of a text-driven video project.
Read the asset bin and packed speech. Draft a cut strategy the human will confirm.

Return ONLY a JSON object:
{
  "strategy_md": "<markdown>",
  "gaps": [ { "slug", "kind", "prompt", "refs", "duration", "aspect_ratio", "voices"? } ]
}

strategy_md MUST use this shape:

# Strategy

**Target:** <seconds> · <WxH> · <delivery>
**Grade:** auto | none | <preset>
**Subtitles:** <style>
**Voice:** <none | voice_id + one line why>
**Palette:** <few concrete values>

## Beats

| beat | spoken | visual | asset | notes |
# visual=a-roll (face+voice), b-roll (picture: asset, sound stays on spoken take), still, generate, graphic
|------|--------|--------|-------|-------|
| ... | ... | a-roll or b-roll or still or generate or graphic | <bin id or generated/slug> | ... |

## Generate

- `generated/<slug>`: one line

## Voiceover

- none  OR  the VO script as short lines mapped to beats (only if there is no usable A-roll speech, or the brief asks for VO)

Rules:
- Prefer bin coverage over generate. Do not invent footage the bin already has.
- generate beats need a matching gaps[] entry. kind is shot (default), still, video, edit, or extend.
- Prefer edit/extend when the bin already has the right shot but it needs a change or more duration. Set video to that bin id.
- refs are bin ids. Seed looks from the bin.
- Exact text, numbers, UI, diagrams → visual=graphic, never generate.
- Speech cuts snap to words; do not invent timestamps you cannot see in the packed transcript.
- If the brief is silent on length/aspect, infer from the material and say so in Target.
- gaps[].voices is optional (Imagine speaking shots). Do not put TTS script in gaps; put it under Voiceover.
"""


def require_key() -> str:
    key = read_env_key("XAI_API_KEY")
    if not key:
        raise StrategyError("XAI_API_KEY not found in .env or environment")
    return key


def _read(path: Path, limit: int = 80_000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) > limit:
        return text[:limit] + "\n\n[truncated]\n"
    return text


def parse_model_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict) or "strategy_md" not in data:
        raise StrategyError("model did not return strategy_md")
    data.setdefault("gaps", [])
    return data


def draft(
    edit_dir: Path,
    brief: str,
    *,
    model: str,
    force: bool,
) -> dict:
    edit_dir = edit_dir.resolve()
    dest_s = edit_dir / "strategy.md"
    dest_g = edit_dir / "gaps.json"
    if dest_s.exists() and not force:
        raise StrategyError(f"{dest_s} exists — pass --force to replace the draft")

    user = "\n\n".join([
        f"BRIEF:\n{brief.strip() or '(none — infer from the material)'}",
        "BIN:\n" + (_read(edit_dir / "bin.md") or "(no bin.md — run inventory.py)"),
        "SPEECH:\n" + (_read(edit_dir / "takes_packed.md") or "(no packed speech)"),
        "MEMORY:\n" + (_read(edit_dir / "project.md", 8_000) or "(none)"),
    ])
    resp = requests.post(
        XAI_CHAT,
        headers={"Authorization": f"Bearer {require_key()}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
        },
        timeout=180,
    )
    if resp.status_code != 200:
        raise StrategyError(f"chat API {resp.status_code}: {resp.text[:600]}")
    body = resp.json()
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise StrategyError(f"unexpected chat response: {json.dumps(body)[:400]}") from e
    data = parse_model_json(content)

    md = str(data["strategy_md"]).strip() + "\n"
    if not md.lstrip().startswith("#"):
        md = "# Strategy\n\n" + md
    if "**UNCONFIRMED DRAFT**" not in md.split("\n", 2)[0]:
        lines = md.splitlines()
        insert_at = 1 if lines and lines[0].startswith("#") else 0
        lines.insert(insert_at, "")
        lines.insert(insert_at + 1, "> **UNCONFIRMED DRAFT** — do not cut, generate, or speak until the user confirms this file.")
        md = "\n".join(lines) + "\n"

    gaps = data.get("gaps") or []
    if not isinstance(gaps, list):
        gaps = []

    dest_s.write_text(md, encoding="utf-8")
    dest_g.write_text(json.dumps({"gaps": gaps}, indent=2) + "\n")
    clear_confirmation(edit_dir)
    print(f"draft strategy → {dest_s}")
    print(f"draft gaps     → {dest_g}  ({len(gaps)} generate beat(s))")
    print("wait for the user to confirm strategy.md before executing")
    return {"strategy": str(dest_s), "gaps": str(dest_g), "n_gaps": len(gaps)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Draft strategy.md + gaps.json with Grok")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("draft")
    p.add_argument("--edit-dir", type=Path, required=True)
    p.add_argument("--brief", default="", help="Length, aspect, audience, must-keep / must-cut")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--force", action="store_true")
    args = ap.parse_args()
    try:
        draft(args.edit_dir.resolve(), args.brief, model=args.model, force=args.force)
    except StrategyError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
