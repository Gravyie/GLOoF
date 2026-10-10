"""Build (or refresh) the multi-year boundary archive for a lake.

    cd backend && .venv/bin/python -m scripts.build_archive --lake south_lhonak
    .venv/bin/python -m scripts.build_archive --lake south_lhonak --update      # only new scenes
    ARCHIVE_BUCKET=my-bucket .venv/bin/python -m scripts.build_archive ...    # write to S3

Reads Sentinel-2 L2A COGs anonymously from AWS Open Data. A full 2017-2026 build
analyses a few hundred scenes and takes ~5-15 minutes depending on bandwidth.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import LAKES, get_settings
from src.services.archive import ArchiveBuilder, make_store
from src.services.stac_client import StacSceneFinder


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="south_lhonak", choices=sorted(LAKES))
    ap.add_argument("--start", help="ISO date (default ARCHIVE_START)")
    ap.add_argument("--end", help="ISO date (default now)")
    ap.add_argument("--update", action="store_true", help="append scenes newer than the stored archive")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--reuse-raw", action="store_true", help="re-finalise the last downloaded frames (no network)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    s = get_settings()
    lake = LAKES[a.lake]
    store = make_store(s)
    builder = ArchiveBuilder(s, StacSceneFinder(s))
    t0 = time.time()

    def progress(i: int, n: int, fr: dict | None) -> None:
        mark = f"{fr['date']} lake {fr['stats']['lake_area_m2'] / 1e4:6.1f} ha" if fr else "skipped (cloud / coverage)"
        print(f"[{i:4d}/{n}] {mark}", flush=True)

    if a.update:
        doc, fresh = builder.update(lake, store.load(lake.id))
        print(f"{len(fresh)} new frame(s)")
    else:
        start = datetime.fromisoformat(a.start or s.archive_start).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc) if a.end else None
        raw_path = Path(s.archive_dir if Path(s.archive_dir).is_absolute() else Path(__file__).parents[1] / s.archive_dir) / f".raw_{lake.id}.json"
        if a.reuse_raw and raw_path.exists():
            frames = json.loads(raw_path.read_text())
            print(f"reusing {len(frames)} raw frames from {raw_path.name}")
        else:
            frames = builder.build_frames(lake, start, end or datetime.now(timezone.utc), workers=a.workers, progress=progress)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps(frames))  # checkpoint: finalise bugs never cost a re-download
        doc = builder.document(lake, builder.finalise(lake, frames))
    where = store.save(lake.id, doc)
    print(f"{len(doc['frames'])} frames -> {where} in {time.time() - t0:.0f}s")
    return 0 if doc["frames"] else 1


if __name__ == "__main__":
    sys.exit(main())
