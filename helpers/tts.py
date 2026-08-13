"""Grok text-to-speech for voiceover beds.

Writes <edit>/generated/<slug>.wav (48 kHz, mix-ready) plus a sidecar JSON.
Optional character timestamps become a word-level transcript so captions
can follow generated VO without a second STT call.

Usage:
    python helpers/tts.py voices
    python helpers/tts.py say --edit-dir <edit> --slug vo --text "We fixed this."
    python helpers/tts.py say --edit-dir <edit> --slug vo --file script.txt --voice eve
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import requests

from imagine import slugify, write_sidecar
from inventory import build_bin
from media import read_env_key


XAI_TTS = "https://api.x.ai/v1/tts"
XAI_VOICES = "https://api.x.ai/v1/tts/voices"
DEFAULT_VOICE = "eve"


class TtsError(RuntimeError):
    pass


def require_key() -> str:
    key = read_env_key("XAI_API_KEY")
    if not key:
        raise TtsError("XAI_API_KEY not found in .env or environment")
    return key


def list_voices(api_key: str) -> list[dict]:
    resp = requests.get(
        XAI_VOICES,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise TtsError(f"voices API {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    return list(data.get("voices") or [])


def synthesize(
    api_key: str,
    text: str,
    *,
    voice_id: str = DEFAULT_VOICE,
    language: str = "en",
    speed: float = 1.0,
    timestamps: bool = True,
) -> tuple[bytes, dict]:
    if not text.strip():
        raise TtsError("empty text")
    if len(text) > 15_000:
        raise TtsError("text exceeds 15,000 characters — split the script")
    body = {
        "text": text,
        "voice_id": voice_id,
        "language": language,
        "speed": speed,
        "output_format": {"codec": "wav", "sample_rate": 48000},
        "with_timestamps": timestamps,
        "text_normalization": True,
    }
    resp = requests.post(
        XAI_TTS,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=180,
    )
    if resp.status_code != 200:
        raise TtsError(f"tts API {resp.status_code}: {resp.text[:600]}")
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
    if timestamps or "json" in ctype:
        payload = resp.json()
        audio_b64 = payload.get("audio")
        if not audio_b64:
            raise TtsError("tts JSON response had no audio")
        return base64.b64decode(audio_b64), payload
    return resp.content, {"duration": None}


def words_from_timestamps(payload: dict) -> list[dict]:
    ts = payload.get("audio_timestamps") or {}
    chars = ts.get("graph_chars") or []
    times = ts.get("graph_times") or []
    words: list[dict] = []
    buf = ""
    start: float | None = None
    end = 0.0
    for ch, span in zip(chars, times):
        if not span or len(span) < 2:
            continue
        a, b = float(span[0]), float(span[1])
        if ch.isspace():
            if buf.strip():
                words.append({
                    "type": "word",
                    "text": buf,
                    "start": a if start is None else start,
                    "end": end,
                })
            buf, start = "", None
            continue
        if start is None:
            start = a
        buf += ch
        end = b
    if buf.strip():
        words.append({
            "type": "word",
            "text": buf,
            "start": 0.0 if start is None else start,
            "end": end,
        })
    return words


def write_transcript(edit_dir: Path, slug: str, words: list[dict], duration: float | None) -> Path:
    out = edit_dir / "transcripts" / "generated" / f"{slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = " ".join(w["text"] for w in words)
    out.write_text(json.dumps({
        "text": text,
        "duration": duration,
        "provider": "grok-tts",
        "words": words,
    }, indent=2) + "\n")
    return out


def say(
    edit_dir: Path,
    slug: str,
    text: str,
    *,
    voice_id: str,
    language: str,
    speed: float,
    force: bool,
    inventory: bool,
    videos_dir: Path | None,
) -> dict:
    dest = edit_dir / "generated" / f"{slug}.wav"
    if dest.exists() and not force:
        print(f"cached: {dest}")
        return {"slug": slug, "audio": str(dest), "cached": True}
    audio, payload = synthesize(
        require_key(), text, voice_id=voice_id, language=language, speed=speed,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(audio)
    words = words_from_timestamps(payload)
    duration = payload.get("duration")
    tr_path = None
    if words:
        tr_path = write_transcript(edit_dir, slug, words, duration)
    meta = {
        "slug": slug,
        "kind": "voice",
        "text": text,
        "voice_id": voice_id,
        "language": language,
        "speed": speed,
        "duration": duration,
        "audio": str(dest),
        "transcript": str(tr_path) if tr_path else "",
    }
    write_sidecar(edit_dir / "generated" / f"{slug}.json", meta)
    print(f"tts → {dest}" + (f"  ({duration:.2f}s)" if duration else ""))
    if inventory:
        root = videos_dir or edit_dir.parent
        build_bin(root, edit_dir)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="Grok TTS for edit/generated voiceover")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("voices", help="List available voice_ids")

    p_say = sub.add_parser("say", help="Synthesize a voiceover bed")
    p_say.add_argument("--edit-dir", type=Path, required=True)
    p_say.add_argument("--slug", required=True)
    src = p_say.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", default=None)
    src.add_argument("--file", type=Path, default=None)
    p_say.add_argument("--voice", default=DEFAULT_VOICE)
    p_say.add_argument("--language", default="en")
    p_say.add_argument("--speed", type=float, default=1.0)
    p_say.add_argument("--force", action="store_true")
    p_say.add_argument("--inventory", action="store_true")
    p_say.add_argument("--videos-dir", type=Path, default=None)

    args = ap.parse_args()
    try:
        if args.cmd == "voices":
            for v in list_voices(require_key()):
                vid = v.get("voice_id") or v.get("id") or ""
                name = v.get("name") or ""
                print(f"{vid:12}  {name}".rstrip())
            return
        text = args.text if args.text is not None else args.file.read_text()
        say(
            args.edit_dir.resolve(), slugify(args.slug), text,
            voice_id=args.voice, language=args.language, speed=args.speed,
            force=args.force, inventory=args.inventory,
            videos_dir=args.videos_dir.resolve() if args.videos_dir else None,
        )
    except TtsError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
