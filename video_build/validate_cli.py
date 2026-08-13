"""CLI for EDL validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from video_build.validate import ValidationError, validate_edl


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate an EDL before render")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument(
        "--no-file-check",
        action="store_true",
        help="Validate structure only; do not require source files to exist",
    )
    args = ap.parse_args()
    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")
    edl = json.loads(edl_path.read_text())
    try:
        validate_edl(edl, edl_path.parent, check_files=not args.no_file_check)
    except ValidationError as e:
        sys.exit(str(e))
    print("EDL valid")


if __name__ == "__main__":
    main()
