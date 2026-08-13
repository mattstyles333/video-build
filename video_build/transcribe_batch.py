"""Batch-transcribe every video in a directory with 4 parallel workers.

Walks <videos_dir> for common video extensions, runs Grok STT (default) or
ElevenLabs Scribe on each, writes transcripts to
<videos_dir>/edit/transcripts/<name>.json.

Cached per-file: any source that already has a transcript is skipped.

Usage:
    python helpers/transcribe_batch.py <videos_dir>
    python helpers/transcribe_batch.py <videos_dir> --provider grok
    python helpers/transcribe_batch.py <videos_dir> --workers 4
    python helpers/transcribe_batch.py <videos_dir> --num-speakers 2
    python helpers/transcribe_batch.py <videos_dir> --edit-dir /custom/edit
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from video_build.media import asset_id, iter_assets, probe
from video_build.transcribe import PROVIDERS, load_api_key, resolve_provider, transcribe_one


def find_videos(videos_dir: Path) -> list[Path]:
    return [p for p, kind in iter_assets(videos_dir) if kind == "video"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Parallel batch transcription of a videos directory")
    ap.add_argument("videos_dir", type=Path, help="Directory containing source videos")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <videos_dir>/edit)",
    )
    ap.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
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
        help="Optional ISO language code. Omit to auto-detect per file.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional speaker count (ElevenLabs only). Improves diarization when known.",
    )
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"not a directory: {videos_dir}")

    edit_dir = (args.edit_dir or (videos_dir / "edit")).resolve()
    (edit_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    videos = find_videos(videos_dir)
    if not videos:
        sys.exit(f"no videos found in {videos_dir}")

    silent = [v for v in videos if not probe(v).get("has_audio")]
    speech = [v for v in videos if v not in silent]
    for v in silent:
        print(f"  skip {v.relative_to(videos_dir)} (no audio)")
    if not speech:
        print("no videos with audio to transcribe")
        return

    def tr_path(v: Path) -> Path:
        return edit_dir / "transcripts" / f"{asset_id(v, videos_dir, edit_dir)}.json"

    already_cached = [v for v in speech if tr_path(v).exists()]
    pending = [v for v in speech if v not in already_cached]

    print(f"found {len(videos)} videos ({len(already_cached)} cached, {len(pending)} to transcribe)")
    if not pending:
        print("nothing to do")
        return

    provider = resolve_provider(args.provider)
    api_key = load_api_key(provider)

    print(f"transcribing {len(pending)} files with {args.workers} parallel workers [{provider}]")
    t0 = time.time()

    errors: list[tuple[Path, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                transcribe_one,
                video=v,
                edit_dir=edit_dir,
                api_key=api_key,
                language=args.language,
                num_speakers=args.num_speakers,
                verbose=False,
                provider=provider,
                name=asset_id(v, videos_dir, edit_dir),
            ): v
            for v in pending
        }
        for fut in as_completed(futures):
            v = futures[fut]
            try:
                out = fut.result()
                print(f"  + {v.stem}  →  {out.name}")
            except Exception as e:
                errors.append((v, str(e)))
                print(f"  x {v.stem}  FAILED: {e}")

    dt = time.time() - t0
    print(f"\ndone in {dt:.1f}s")
    if errors:
        print(f"{len(errors)} failures:")
        for v, msg in errors:
            print(f"  {v.name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
