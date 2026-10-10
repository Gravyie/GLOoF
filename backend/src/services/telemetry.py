"""Orchestration: STAC search -> raster read -> water extraction -> telemetry.

Routes stay thin and call this; geospatial.py stays pure maths; stac_client.py
stays pure catalog access.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from rasterio.enums import Resampling

from src.core.cache import TTLStore
from src.core.config import Lake, Settings
from src.services.geospatial import (
    BandWindow,
    GeospatialError,
    WaterExtraction,
    WaterParams,
    extract_water,
    read_band_window,
)
from src.services.stac_client import SceneRef, StacSceneFinder

log = logging.getLogger(__name__)


class NoUsableSceneError(RuntimeError):
    pass


def _pct(first: float, last: float) -> float | None:
    return None if first <= 0 else round((last - first) / first * 100.0, 1)


def assess_trend(
    series: list[dict[str, Any]],
    elevated_pct: float,
    critical_pct: float,
    max_plausible_pct: float = 100.0,
) -> dict[str, Any]:
    """Heuristic lake-growth flag from a same-season water-area series.

    A screening signal (first vs latest observation), not a calibrated GLOF
    model. Growth beyond ``max_plausible_pct`` is physically implausible for a
    glacial lake over a year or two, so it is reported as UNRELIABLE (almost
    certainly ice, snow, shadow or cloud) instead of CRITICAL.
    """
    n = len(series)
    if n < 3:
        return {"level": "INSUFFICIENT_DATA", "growth_pct": None, "ice_change_pct": None,
                "observations": n, "basis": "need >= 3 clear same-season observations"}
    first, last = series[0], series[-1]
    growth = _pct(first["water_area_m2"], last["water_area_m2"])
    ice_a, ice_b = first.get("ice_area_m2"), last.get("ice_area_m2")
    ice_change = _pct(ice_a, ice_b) if ice_a is not None and ice_b is not None else None
    basis = f"main-lake area, {first['acquired'][:10]} -> {last['acquired'][:10]} (melt season only)"
    if growth is None:
        level = "INSUFFICIENT_DATA"
    elif abs(growth) > max_plausible_pct:
        level, basis = "UNRELIABLE", f"{growth:+.0f}% is physically implausible - likely ice/snow/shadow, not growth. " + basis
    elif growth >= critical_pct:
        level = "CRITICAL"
    elif growth >= elevated_pct:
        level = "ELEVATED"
    else:
        level = "NORMAL"
    return {"level": level, "growth_pct": growth, "ice_change_pct": ice_change,
            "observations": n, "basis": basis}


class TelemetryService:
    def __init__(self, settings: Settings, finder: StacSceneFinder, store: TTLStore) -> None:
        self._s = settings
        self._finder = finder
        self._store = store

    # -- single scene ------------------------------------------------------- #
    def _params(self) -> WaterParams:
        s = self._s
        return WaterParams(
            ndwi_threshold=s.ndwi_threshold,
            max_nir_reflectance=s.max_nir_reflectance,
            min_polygon_area_m2=s.min_polygon_area_m2,
            simplify_tolerance_m=s.simplify_tolerance_m,
            exclude_snow=s.exclude_snow_class,
        )  # ice thresholds use WaterParams defaults

    def _analyse_uncached(self, lake: Lake, scene: SceneRef) -> WaterExtraction:
        green = read_band_window(scene.green.href, lake.bbox)
        nir = read_band_window(scene.nir.href, lake.bbox, like=green)
        scl: BandWindow | None = None
        if scene.scl_href:
            scl = read_band_window(scene.scl_href, lake.bbox, like=green)
        swir: BandWindow | None = None
        swir_cal = None
        if scene.swir:  # bilinear: 20 m SWIR -> 10 m grid
            swir = read_band_window(scene.swir.href, lake.bbox, like=green, resampling=Resampling.bilinear)
            swir_cal = (scene.swir.scale, scene.swir.offset)
        return extract_water(
            green,
            nir,
            scl,
            green_cal=(scene.green.scale, scene.green.offset),
            nir_cal=(scene.nir.scale, scene.nir.offset),
            params=self._params(),
            swir=swir,
            swir_cal=swir_cal,
        )

    def analyse_scene(self, lake: Lake, scene: SceneRef) -> WaterExtraction:
        return self._store.get_or_compute(
            ("scene", lake.id, scene.item_id), lambda: self._analyse_uncached(lake, scene)
        )

    def _usable(self, ex: WaterExtraction) -> str | None:
        """Return a rejection reason, or None if the scene is usable."""
        st = ex.stats
        if st["cloud_fraction"] > self._s.max_local_cloud_fraction:
            return f"cloud/shadow {st['cloud_fraction']:.0%} of window"
        if st["valid_fraction"] < self._s.min_valid_fraction:
            return f"only {st['valid_fraction']:.0%} valid pixels"
        return None

    # -- latest telemetry --------------------------------------------------- #
    def _compute_telemetry(self, lake: Lake) -> dict[str, Any]:
        scenes = self._finder.find_scenes(lake.bbox, limit=self._s.max_candidate_scenes)
        rejected: list[dict[str, str]] = []
        for scene in scenes:
            try:
                ex = self.analyse_scene(lake, scene)
            except GeospatialError as exc:
                rejected.append({"scene": scene.item_id, "reason": str(exc)})
                continue
            reason = self._usable(ex)
            if reason:
                rejected.append({"scene": scene.item_id, "reason": reason})
                continue
            return {
                "lake": _lake_dict(lake),
                "scene": {
                    "id": scene.item_id,
                    "acquired": scene.acquired.isoformat(),
                    "tile_cloud_cover": scene.tile_cloud_cover,
                    "source": "Sentinel-2 L2A via AWS Earth Search",
                },
                "stats": ex.stats,
                "parameters": {
                    "ndwi_threshold": self._s.ndwi_threshold,
                    "max_nir_reflectance": self._s.max_nir_reflectance,
                    "min_polygon_area_m2": self._s.min_polygon_area_m2,
                    "ndsi_threshold": self._params().ndsi_threshold,
                },
                "rejected_scenes": rejected,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "water": ex.feature_collection,
                "ice": ex.ice_collection,
            }
        raise NoUsableSceneError(
            f"no usable scene for {lake.name} in the last {self._s.search_window_days} days "
            f"({len(scenes)} candidates, {len(rejected)} rejected)"
        )

    def get_telemetry(self, lake: Lake, *, refresh: bool = False) -> dict[str, Any]:
        return self._store.get_or_compute(
            ("telemetry", lake.id), lambda: self._compute_telemetry(lake), force=refresh
        )

    # -- time series -------------------------------------------------------- #
    def _compute_timeseries(self, lake: Lake, n: int) -> dict[str, Any]:
        scenes = self._finder.find_scenes(
            lake.bbox, limit=n * 12, days=self._s.timeseries_days
        )
        # one scene per acquisition date (overlapping MGRS tiles duplicate dates),
        # melt-season months only, newest first, capped to keep latency sane
        seen: set[str] = set()
        unique = []
        for sc in scenes:
            d = sc.acquired.date().isoformat()
            if sc.acquired.month not in self._s.melt_months or d in seen:
                continue
            seen.add(d)
            unique.append(sc)
        unique = unique[: n * 2]

        def work(sc: SceneRef) -> dict[str, Any] | None:
            try:
                ex = self.analyse_scene(lake, sc)
            except Exception as exc:  # one bad scene must not sink the series
                log.warning("timeseries: %s failed: %s", sc.item_id, exc)
                return None
            if self._usable(ex):
                return None
            return {
                "scene": sc.item_id,
                "acquired": sc.acquired.isoformat(),
                "water_area_m2": ex.stats["primary_area_m2"],
                "ice_area_m2": ex.stats["ice_area_m2"],
                "cloud_fraction": ex.stats["cloud_fraction"],
            }

        with ThreadPoolExecutor(max_workers=self._s.timeseries_workers) as pool:
            results = [r for r in pool.map(work, unique) if r]
        results.sort(key=lambda r: r["acquired"])
        results = results[-n:]
        return {
            "lake": lake.id,
            "metric": "main water body area (m2) + snow/ice area (m2), melt season only",
            "series": results,
            "trend": assess_trend(
                results, self._s.growth_elevated_pct, self._s.growth_critical_pct,
                self._s.max_plausible_growth_pct,
            ),
        }

    def get_timeseries(self, lake: Lake, n: int, *, refresh: bool = False) -> dict[str, Any]:
        n = max(2, min(n, self._s.timeseries_max_scenes))
        return self._store.get_or_compute(
            ("series", lake.id, n), lambda: self._compute_timeseries(lake, n), force=refresh
        )


def _lake_dict(lake: Lake) -> dict[str, Any]:
    w, s, e, n = lake.bbox
    return {
        "id": lake.id,
        "name": lake.name,
        "country": lake.country,
        "center": list(lake.center),
        "bbox": [w, s, e, n],
        "lake_elevation_m": lake.lake_elevation_m,
    }


def lake_summary(lake: Lake) -> dict[str, Any]:
    return _lake_dict(lake)
