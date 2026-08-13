"""Transcribe a video with Grok STT or ElevenLabs Scribe.

Accepts either XAI_API_KEY or ELEVENLABS_API_KEY. An existing ElevenLabs-only
.env keeps working with no flags. If both keys are set, Grok STT is used
unless you pass --provider elevenlabs.

Extracts mono 16kHz audio via ffmpeg, uploads with diarization + word-level
timestamps + filler-word retention, writes a Scribe-shaped transcript to
<edit_dir>/transcripts/<video_stem>.json so pack/render/timeline_view stay
provider-agnostic.

Cached: if the output file already exists, the upload is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --provider grok
    python helpers/transcribe.py <video_path> --provider elevenlabs
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --num-speakers 2
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from video_build.media import read_env_key

GROK_STT_URL = "https://api.x.ai/v1/stt"
SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"
PROVIDERS = ("grok", "elevenlabs")
KEY_FOR_PROVIDER = {
    "grok": "XAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
}


def _read_key(name: str) -> str:
    return read_env_key(name)


def resolve_provider(explicit: str | None) -> str:
    if explicit:
        if explicit not in PROVIDERS:
            sys.exit(f"unknown --provider {explicit!r} (want {'|'.join(PROVIDERS)})")
        return explicit
    if _read_key("XAI_API_KEY"):
        return "grok"
    if _read_key("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    sys.exit(
        "need XAI_API_KEY or ELEVENLABS_API_KEY in .env or the environment "
        "(existing ElevenLabs-only setups keep working; pass --provider to force one)"
    )


def load_api_key(provider: str | None = None) -> str:
    provider = resolve_provider(provider)
    name = KEY_FOR_PROVIDER[provider]
    v = _read_key(name)
    if not v:
        sys.exit(f"{name} not found in .env or environment")
    return v


def extract_audio(video_path: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def grok_to_scribe(payload: dict) -> dict:
    """Normalize Grok STT words into the Scribe-shaped schema pack/render expect.

    Grok returns {text, start, end, speaker?} with no type / spacing / audio_event
    entries. We add type=word, map speaker→speaker_id, and synthesize spacing
    tokens from inter-word gaps so silence-aware packing still works.
    """
    words_out: list[dict] = []
    prev_end: float | None = None
    for w in payload.get("words") or []:
        start = w.get("start")
        if start is None:
            continue
        end = w.get("end", start)
        if prev_end is not None and start > prev_end:
            words_out.append({
                "type": "spacing",
                "text": " ",
                "start": prev_end,
                "end": start,
            })
        entry: dict = {
            "type": "word",
            "text": w.get("text") or "",
            "start": start,
            "end": end,
        }
        speaker = w.get("speaker")
        if speaker is not None:
            entry["speaker_id"] = f"speaker_{speaker}"
        words_out.append(entry)
        prev_end = end
    return {
        "text": payload.get("text", ""),
        "language": payload.get("language"),
        "duration": payload.get("duration"),
        "provider": "grok",
        "words": words_out,
    }


def call_grok_stt(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
) -> dict:
    # Verbatim + fillers: do not set format=true (that runs ITN).
    form: list[tuple[str, str]] = [
        ("diarize", "true"),
        ("filler_words", "true"),
    ]
    if language:
        form.append(("language", language))

    with open(audio_path, "rb") as f:
        resp = requests.post(
            GROK_STT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data=form,
            files={"file": (audio_path.name, f, "audio/wav")},
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Grok STT returned {resp.status_code}: {resp.text[:500]}")

    return grok_to_scribe(resp.json())


def call_scribe(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    data: dict[str, str] = {
        "model_id": "scribe_v1",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    if language:
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)

    with open(audio_path, "rb") as f:
        resp = requests.post(
            SCRIBE_URL,
            headers={"xi-api-key": api_key},
            files={"file": (audio_path.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Scribe returned {resp.status_code}: {resp.text[:500]}")

    payload = resp.json()
    if isinstance(payload, dict):
        payload.setdefault("provider", "elevenlabs")
    return payload


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
    provider: str | None = None,
    name: str | None = None,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns existing path immediately if the transcript already exists.
    `name` is the cache key (bin asset id). Defaults to the file stem.
    """
    provider = resolve_provider(provider)
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_name = name or video.stem
    out_path = transcripts_dir / f"{out_name}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    if verbose:
        print(f"  extracting audio from {video.name} [{provider}]", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio)
        size_mb = audio.stat().st_size / (1024 * 1024)
        if verbose:
            print(f"  uploading {video.stem}.wav ({size_mb:.1f} MB)", flush=True)
        if provider == "grok":
            payload = call_grok_stt(audio, api_key, language)
        else:
            payload = call_scribe(audio, api_key, language, num_speakers)

    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            print(f"    words: {len(payload['words'])}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with Grok STT or ElevenLabs Scribe")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=None,
        help="STT backend. Default: grok if XAI_API_KEY is set, else elevenlabs.",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code (e.g., 'en'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional speaker count (ElevenLabs only). Improves diarization accuracy.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    provider = resolve_provider(args.provider)
    api_key = load_api_key(provider)

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        api_key=api_key,
        language=args.language,
        num_speakers=args.num_speakers,
        provider=provider,
    )


if __name__ == "__main__":
    main()
