"""Multi-year boundary archive: one water + ice outline per clear acquisition.

Builder pipeline (per lake):
    STAC search over [start, end], open-water season only, footprint covers the window
      -> one scene per date (least cloudy tile)
      -> cheap SCL-only cloud screen (one 20 m band) before reading the 10 m bands
      -> NDWI water + NDSI ice extraction (geospatial.extract_water)
      -> "the lake" = water polygon at the lake's seed point
      -> thinning: keep a frame every ``min_gap_days`` or whenever the area jumps
The result is a plain JSON document. It is written to a local directory (and
committed, so the demo needs no network) or to S3 by the ingest Lambda.

Every frame is a real Sentinel-2 L2A observation; nothing is interpolated here.
(The frontend morphs between frames for display and says so.)
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from rasterio.enums import Resampling

from src.core.config import Lake, Settings
from src.services.boundary import frozen_fraction
from src.services.geospatial import (
    SCL_CLOUD_SHADOW,
    GeospatialError,
    WaterParams,
    extract_water,
    read_band_window,
    select_lake,
)
from src.services.stac_client import SceneRef, StacSceneFinder

log = logging.getLogger(__name__)
SCHEMA = 2
BACKEND_DIR = Path(__file__).resolve().parents[2]

# Display-only extras are trimmed so a 9-year archive stays a few MB.
MIN_CONTEXT_WATER_M2 = 10_000  # other lakes nearby (>= 1 ha)


# --------------------------------------------------------------------------- #
# Storage: local directory or S3, same interface
# --------------------------------------------------------------------------- #
class ArchiveStore:
    def load(self, key: str) -> dict[str, Any] | None: ...
    def save(self, key: str, doc: dict[str, Any]) -> str: ...
    describe: str = ""


class LocalArchiveStore(ArchiveStore):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.is_absolute():
            self.root = BACKEND_DIR / self.root
        self.describe = f"local:{self.root.relative_to(BACKEND_DIR) if self.root.is_relative_to(BACKEND_DIR) else self.root}"

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load(self, key: str) -> dict[str, Any] | None:
        p = self._path(key)
        return json.loads(p.read_text()) if p.exists() else None

    def save(self, key: str, doc: dict[str, Any]) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")))
        tmp.replace(p)
        return str(p)


class S3ArchiveStore(ArchiveStore):
    def __init__(self, bucket: str, prefix: str = "archive/", region: str | None = None, client: Any = None) -> None:
        import boto3

        self.bucket, self.prefix = bucket, prefix
        self._s3 = client or boto3.client("s3", region_name=region)
        self.describe = f"s3://{bucket}/{prefix}"

    def load(self, key: str) -> dict[str, Any] | None:
        try:
            obj = self._s3.get_object(Bucket=self.bucket, Key=f"{self.prefix}{key}.json")
        except self._s3.exceptions.NoSuchKey:
            return None
        return json.loads(obj["Body"].read())

    def save(self, key: str, doc: dict[str, Any]) -> str:
        k = f"{self.prefix}{key}.json"
        self._s3.put_object(Bucket=self.bucket, Key=k, Body=json.dumps(doc, separators=(",", ":")).encode(),
                            ContentType="application/json")
        return f"s3://{self.bucket}/{k}"


def make_store(settings: Settings) -> ArchiveStore:
    if settings.archive_bucket:
        return S3ArchiveStore(settings.archive_bucket, settings.archive_prefix, settings.aws_region)
    return LocalArchiveStore(settings.archive_dir)


# --------------------------------------------------------------------------- #
# Frame selection
# --------------------------------------------------------------------------- #
def in_season(d: date, season: tuple[str, str]) -> bool:
    md = d.strftime("%m-%d")
    return season[0] <= md <= season[1]


def thin_frames(frames: list[dict[str, Any]], min_gap_days: int, jump_pct: float = 8.0) -> list[dict[str, Any]]:
    """Keep a frame when ``min_gap_days`` have passed or the lake area jumped.

    Frames must be date-sorted. Sudden changes (a drainage) are never thinned away.
    """
    kept: list[dict[str, Any]] = []
    for f in frames:
        if not kept:
            kept.append(f)
            continue
        last = kept[-1]
        gap = (date.fromisoformat(f["date"]) - date.fromisoformat(last["date"])).days
        a0, a1 = last["stats"]["lake_area_m2"], f["stats"]["lake_area_m2"]
        jump = abs(a1 - a0) / a0 * 100 if a0 else 100.0
        if gap >= min_gap_days or jump >= jump_pct:
            kept.append(f)
    return kept


def drop_outliers(frames: list[dict[str, Any]], low: float = 0.6, high: float = 1.6) -> list[dict[str, Any]]:
    """Drop scenes whose lake extent is wildly off the archive median.

    A glacial lake does not lose 40% of its area and get it back a week later;
    such frames are shadow, fresh snow or lake ice misread by NDWI. A real
    drainage that persists over several scenes moves the median-relative
    level only partly, and the risk engine's own drop test handles it.
    """
    if len(frames) < 5:
        return frames
    import statistics

    med = statistics.median(f["stats"]["lake_area_m2"] for f in frames)
    return [f for f in frames if low * med <= f["stats"]["lake_area_m2"] <= high * med]


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #
class ArchiveBuilder:
    def __init__(self, settings: Settings, finder: StacSceneFinder) -> None:
        self._s = settings
        self._finder = finder

    def _params(self) -> WaterParams:
        s = self._s
        return WaterParams(
            ndwi_threshold=s.ndwi_threshold,
            max_nir_reflectance=s.max_nir_reflectance,
            min_polygon_area_m2=s.min_polygon_area_m2,
            simplify_tolerance_m=s.simplify_tolerance_m,
        )

    def candidates(self, lake: Lake, start: datetime, end: datetime, per_month: int = 4) -> list[SceneRef]:
        """One search per season (a single 9-year search pages slowly and was
        observed to drop later years). Keeps the ``per_month`` least-cloudy scenes."""
        season = self._s.season_window
        out: list[SceneRef] = []
        for year in range(start.year, end.year + 1):
            a = max(start, datetime.fromisoformat(f"{year}-{season[0]}").replace(tzinfo=timezone.utc))
            b = min(end, datetime.fromisoformat(f"{year}-{season[1]}T23:59:59").replace(tzinfo=timezone.utc))
            if a >= b:
                continue
            scenes = self._finder.find_scenes_between(lake.bbox, a, b, max_tile_cloud=40.0, limit=300)
            # best few per month: spreads frames through the season so short
            # windows (e.g. the clear days just before a monsoon-season event) survive
            by_month: dict[int, list[SceneRef]] = {}
            for sc in sorted(scenes, key=lambda sc: sc.tile_cloud_cover or 0):
                by_month.setdefault(sc.acquired.month, []).append(sc)
            out.extend(sc for lst in by_month.values() for sc in lst[:per_month])
        return sorted(out, key=lambda sc: sc.acquired)

    def analyse(self, lake: Lake, scene: SceneRef) -> dict[str, Any] | None:
        """One frame, or None if the scene is cloudy / unusable over the lake window."""
        if not scene.scl_href or not scene.swir:
            return None
        green = read_band_window(scene.green.href, lake.bbox)
        scl = read_band_window(scene.scl_href, lake.bbox, like=green)
        cloud = float(np.isin(scl.data, SCL_CLOUD_SHADOW).mean())
        if cloud > self._s.archive_max_cloud_fraction:
            return None
        nir = read_band_window(scene.nir.href, lake.bbox, like=green)
        swir = read_band_window(scene.swir.href, lake.bbox, like=green, resampling=Resampling.bilinear)
        ex = extract_water(
            green, nir, scl,
            green_cal=(scene.green.scale, scene.green.offset),
            nir_cal=(scene.nir.scale, scene.nir.offset),
            params=self._params(),
            swir=swir, swir_cal=(scene.swir.scale, scene.swir.offset),
        )
        if ex.stats["valid_fraction"] < self._s.min_valid_fraction:
            return None
        lake_feat = select_lake(ex.feature_collection, lake.seed_point)
        if lake_feat is None:
            return None
        others = [f for f in ex.feature_collection["features"]
                  if not f["properties"]["primary"] and f["properties"]["area_m2"] >= MIN_CONTEXT_WATER_M2]
        return {
            "date": scene.acquired.date().isoformat(),
            "acquired": scene.acquired.isoformat(),
            "scene": scene.item_id,
            "stats": {
                "lake_area_m2": lake_feat["properties"]["area_m2"],
                "water_area_m2": ex.stats["water_area_m2"],
                "ice_area_m2": ex.stats["ice_area_m2"],
                "cloud_fraction": round(cloud, 4),
                "mean_ndwi": lake_feat["properties"].get("mean_ndwi"),
            },
            "lake": {"type": "Feature", "properties": {"kind": "lake"}, "geometry": lake_feat["geometry"]},
            "water": {"type": "FeatureCollection", "features": others},
            "ice": ex.ice_collection,
        }

    def _analyse_safe(self, lake: Lake, scene: SceneRef) -> dict[str, Any] | None:
        try:
            return self.analyse(lake, scene)
        except GeospatialError as exc:
            log.info("skip %s: %s", scene.item_id, exc)
        except Exception as exc:  # network blips on one COG must not sink a 9-year build
            log.warning("skip %s: %s", scene.item_id, exc)
        return None

    def build_frames(
        self, lake: Lake, start: datetime, end: datetime, *,
        workers: int = 8, progress: Callable[[int, int, dict | None], None] | None = None,
    ) -> list[dict[str, Any]]:
        scenes = self.candidates(lake, start, end)
        out: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, fr in enumerate(pool.map(lambda sc: self._analyse_safe(lake, sc), scenes)):
                if fr:
                    out.append(fr)
                if progress:
                    progress(i + 1, len(scenes), fr)
        out.sort(key=lambda f: f["date"])
        return out

    def finalise(self, lake: Lake, frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
        frames = drop_outliers(sorted(frames, key=lambda f: f["date"]))
        frames = thin_frames(frames, self._s.archive_min_gap_days)
        prev = None
        for f in frames:  # how much of the last lake footprint is now ice/snow (freeze guard)
            f["stats"]["frozen_fraction"] = frozen_fraction(prev["lake"]["geometry"] if prev else None,
                                                            f["ice"], lake.utm_epsg)
            prev = f
        return frames

    def document(self, lake: Lake, frames: list[dict[str, Any]]) -> dict[str, Any]:
        from src.services.telemetry import lake_summary

        return {
            "schema": SCHEMA,
            "lake": {**lake_summary(lake), "seed": list(lake.seed_point), "events": list(lake.events)},
            "source": "Sentinel-2 L2A (ESA Copernicus) via AWS Earth Search STAC, COGs on AWS Open Data",
            "collection": self._s.stac_collection,
            "method": {
                "water": f"NDWI > {self._s.ndwi_threshold} and NIR <= {self._s.max_nir_reflectance}, SCL cloud mask",
                "ice": "NDSI > 0.4 and NIR >= 0.11 (snow and clean ice; debris-covered ice not detected)",
                "season": self._s.archive_season,
                "max_cloud_fraction": self._s.archive_max_cloud_fraction,
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "frames": frames,
        }

    def build(self, lake: Lake, start: datetime | None = None, end: datetime | None = None, **kw) -> dict[str, Any]:
        start = start or datetime.fromisoformat(self._s.archive_start).replace(tzinfo=timezone.utc)
        end = end or datetime.now(timezone.utc)
        return self.document(lake, self.finalise(lake, self.build_frames(lake, start, end, **kw)))

    def update(self, lake: Lake, doc: dict[str, Any] | None, *, now: datetime | None = None) -> tuple[dict[str, Any], list[dict]]:
        """Incremental refresh (what the scheduled Lambda runs): scenes newer than
        the last frame are analysed and appended. Returns (doc, new_frames)."""
        now = now or datetime.now(timezone.utc)
        if not doc or not doc.get("frames"):
            doc = self.build(lake, end=now)
            return doc, doc["frames"]
        last = date.fromisoformat(doc["frames"][-1]["date"])
        start = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        fresh = self.build_frames(lake, start, now) if start < now else []
        known = {f["date"] for f in doc["frames"]}
        fresh = [f for f in fresh if f["date"] not in known]
        if fresh:
            frames = self.finalise(lake, doc["frames"] + fresh)
            new_dates = {f["date"] for f in fresh}
            fresh = [f for f in frames if f["date"] in new_dates]
            doc = {**self.document(lake, frames)}
        return doc, fresh
