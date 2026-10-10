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


def build_ice(builder: ArchiveBuilder, lake, s, reuse_raw: bool) -> dict:
    root = Path(__file__).resolve().parents[1]
    rgi_path = root / "reference" / f"rgi7_{lake.id}.geojson"
    rgi = json.loads(rgi_path.read_text()) if rgi_path.exists() else None
    if rgi is None:
        print(f"note: {rgi_path.name} missing (run scripts.fetch_rgi); building ice without the RGI reference")
    cache = (Path(s.archive_dir) if Path(s.archive_dir).is_absolute() else root / s.archive_dir) / ".raw_ice" / lake.id
    out = builder.build_ice(lake, cache_dir=cache, reuse_raw=reuse_raw, rgi=rgi, progress=lambda m: print("  ice", m, flush=True))
    for y in out["perennial_ice"]["years"]:
        print(f"  {y['year']}: {y['ice_area_km2']:.2f} km2 from {y['scenes_used']} scenes"
              f"{' (LOW CONFIDENCE)' if y['low_confidence'] else ''}; lost {y['lost_km2']:.2f}, gained {y['gained_km2']:.2f}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="south_lhonak", choices=sorted(LAKES))
    ap.add_argument("--start", help="ISO date (default ARCHIVE_START)")
    ap.add_argument("--end", help="ISO date (default now)")
    ap.add_argument("--update", action="store_true", help="append scenes newer than the stored archive")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--reuse-raw", action="store_true", help="re-finalise the last downloaded frames (no network)")
    ap.add_argument("--ice", action="store_true", help="also build the yearly perennial-ice composites")
    ap.add_argument("--ice-only", action="store_true", help="only (re)build the ice composites into the stored archive")
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

    if a.ice_only:
        doc = store.load(lake.id)
        if not doc:
            print("no archive to add ice to; build it first")
            return 1
        doc = {**doc, **build_ice(builder, lake, s, a.reuse_raw)}
        print(f"ice composites -> {store.save(lake.id, doc)} in {time.time() - t0:.0f}s")
        return 0
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
        if a.ice:
            doc = {**doc, **build_ice(builder, lake, s, a.reuse_raw)}
        else:  # keep any ice layer already stored
            old = store.load(lake.id) or {}
            doc = {**doc, **{k: old[k] for k in builder.ICE_KEYS if k in old}}
    where = store.save(lake.id, doc)
    print(f"{len(doc['frames'])} frames -> {where} in {time.time() - t0:.0f}s")
    return 0 if doc["frames"] else 1


if __name__ == "__main__":
    sys.exit(main())
