"""Perennial ice: one glacier outline per year, robust to seasonal snow.

Per-frame NDSI "snow and ice" includes seasonal snow, so a snowy scene looks
like glacier growth. Instead, for every year:

  1. take only end-of-ablation scenes (default 15 Sep - 31 Oct), when seasonal
     snow cover is at its minimum,
  2. classify every pixel of every scene as ice / not ice / not observed
     (cloud, cloud shadow, cirrus or nodata -> not observed, NO vote),
  3. call a pixel perennial ice when it is ice in >= ``persistence`` of the
     scenes in which it was observed.

Every year is then compared with ONE fixed baseline year (never the previous
year), on the raster grid, only where both years were observed:
  lost   = ice in baseline, not ice now
  gained = ice now, not ice in baseline
Gain is kept and shown: real glacier gain over this period is unlikely, so it
is mostly a quality signal (residual snow, shadow).

Per-scene masks are cached as compressed .npz so composites can be rebuilt with
no network access (``--reuse-raw``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import Affine

from src.services.geospatial import (
    SCL_CLOUD_SHADOW,
    BandWindow,
    GeospatialError,
    WaterParams,
    compute_ndwi,
    read_band_window,
    to_reflectance,
    vectorize_mask,
)
from src.services.stac_client import SceneRef

log = logging.getLogger(__name__)
NODATA_SCL = 0


@dataclass
class SceneMask:
    scene: str
    acquired: str
    ice: np.ndarray  # bool
    valid: np.ndarray  # bool: observed (clear, finite)
    transform: Affine
    crs: str


# --------------------------------------------------------------------------- #
# Pure maths (tested on synthetic stacks)
# --------------------------------------------------------------------------- #
def classify(green: np.ndarray, nir: np.ndarray, swir: np.ndarray, scl: np.ndarray | None,
             p: WaterParams) -> tuple[np.ndarray, np.ndarray]:
    """Reflectance arrays -> (ice, valid). Water is never ice."""
    with np.errstate(invalid="ignore", divide="ignore"):
        ndsi = (green - swir) / (green + swir)
    ndwi = compute_ndwi(green, nir)
    valid = np.isfinite(green) & np.isfinite(nir) & np.isfinite(swir)
    if scl is not None:
        valid &= ~np.isin(scl, SCL_CLOUD_SHADOW) & (scl != NODATA_SCL)
    with np.errstate(invalid="ignore"):
        water = (ndwi > p.ndwi_threshold) & (nir <= p.max_nir_reflectance)
        ice = valid & ~water & (ndsi > p.ndsi_threshold) & (nir >= p.min_ice_nir_reflectance)
    return ice, valid


def persistence_composite(ice_stack: np.ndarray, valid_stack: np.ndarray, persistence: float
                          ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(n, h, w) stacks -> (perennial, vote_fraction, n_valid).

    Only observed pixels vote. A pixel never observed has vote_fraction NaN and
    is not perennial; callers treat it as unknown via ``n_valid == 0``.
    """
    valid = valid_stack.astype(bool)
    ice_votes = (ice_stack.astype(bool) & valid).sum(axis=0)
    n_valid = valid.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        frac = np.where(n_valid > 0, ice_votes / np.maximum(n_valid, 1), np.nan)
    perennial = (n_valid > 0) & (frac >= persistence - 1e-9)
    return perennial, frac.astype(np.float32), n_valid


def baseline_change(base: np.ndarray, cur: np.ndarray, base_obs: np.ndarray, cur_obs: np.ndarray
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(lost, gained, both_observed) on a shared grid; unobserved pixels never count."""
    both = base_obs & cur_obs
    return base & ~cur & both, cur & ~base & both, both


# --------------------------------------------------------------------------- #
# Scene reading + cache
# --------------------------------------------------------------------------- #
class MaskCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _p(self, scene: str) -> Path:
        return self.root / f"{scene}.npz"

    def get(self, scene: str) -> SceneMask | None:
        p = self._p(scene)
        if not p.exists():
            return None
        z = np.load(p, allow_pickle=False)
        shp = tuple(z["shape"])
        n = shp[0] * shp[1]
        return SceneMask(scene=scene, acquired=str(z["acquired"]),
                         ice=np.unpackbits(z["ice"])[:n].reshape(shp).astype(bool),
                         valid=np.unpackbits(z["valid"])[:n].reshape(shp).astype(bool),
                         transform=Affine(*z["transform"]), crs=str(z["crs"]))

    def put(self, m: SceneMask) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self._p(m.scene), ice=np.packbits(m.ice), valid=np.packbits(m.valid),
                            shape=np.array(m.ice.shape), transform=np.array(tuple(m.transform)[:6]),
                            crs=np.array(m.crs), acquired=np.array(m.acquired))

    def scenes(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.npz")) if self.root.exists() else []


def read_scene(scene: SceneRef, bbox: tuple[float, float, float, float], p: WaterParams,
               ref: BandWindow | None = None) -> tuple[SceneMask, BandWindow]:
    """Read one scene onto the reference grid (the first scene defines it)."""
    if not scene.swir or not scene.scl_href:
        raise GeospatialError("scene lacks SWIR or SCL")
    green = read_band_window(scene.green.href, bbox, like=ref) if ref else read_band_window(scene.green.href, bbox)
    grid = ref or green
    nir = read_band_window(scene.nir.href, bbox, like=grid)
    swir = read_band_window(scene.swir.href, bbox, like=grid, resampling=Resampling.bilinear)
    scl = read_band_window(scene.scl_href, bbox, like=grid)
    ice, valid = classify(to_reflectance(green.data, scene.green.scale, scene.green.offset),
                          to_reflectance(nir.data, scene.nir.scale, scene.nir.offset),
                          to_reflectance(swir.data, scene.swir.scale, scene.swir.offset), scl.data, p)
    return SceneMask(scene.item_id, scene.acquired.isoformat(), ice, valid, grid.transform, str(grid.crs)), grid


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #
def _vec(mask: np.ndarray, m: SceneMask, kind: str, p: WaterParams, min_area: float | None = None) -> dict[str, Any]:
    feats = vectorize_mask(mask, mask.astype(np.float32), m.transform, CRS.from_string(m.crs), kind=kind,
                           index_name="vote", min_area_m2=min_area if min_area is not None else p.min_ice_area_m2,
                           simplify_m=p.ice_simplify_tolerance_m)
    return {"type": "FeatureCollection", "features": feats}


def _area_km2(mask: np.ndarray, transform: Affine) -> float:
    return round(float(mask.sum()) * abs(transform.a * transform.e) / 1e6, 3)


def build_perennial(masks_by_year: dict[int, list[SceneMask]], *, persistence: float, min_scenes: int,
                    baseline_year: int, params: WaterParams) -> dict[str, Any]:
    years = sorted(y for y, ms in masks_by_year.items() if ms)
    if baseline_year not in years:
        raise GeospatialError(f"no usable scenes for baseline year {baseline_year}")
    comp: dict[int, tuple[np.ndarray, np.ndarray, SceneMask]] = {}
    for y in years:
        ms = masks_by_year[y]
        perennial, _, n_valid = persistence_composite(np.stack([m.ice for m in ms]), np.stack([m.valid for m in ms]),
                                                      persistence)
        comp[y] = (perennial, n_valid > 0, ms[0])
    base, base_obs, _ = comp[baseline_year]
    out_years = []
    for y in years:
        cur, obs, m0 = comp[y]
        lost, gained, both = baseline_change(base, cur, base_obs, obs)
        ms = masks_by_year[y]
        out_years.append({
            "year": y,
            "ice_area_km2": _area_km2(cur, m0.transform),
            "scenes_used": len(ms),
            "scene_ids": [m.scene for m in ms],
            "scene_dates": [m.acquired[:10] for m in ms],
            "low_confidence": len(ms) < min_scenes,
            "unobserved_fraction": round(float(1 - obs.mean()), 4),
            "lost_km2": _area_km2(lost, m0.transform),
            "gained_km2": _area_km2(gained, m0.transform),
            "outline": _vec(cur, m0, "perennial_ice", params),
            # small patches still matter here (they are the quality signal), so a lower area floor
            "lost": _vec(lost, m0, "ice_lost", params, min_area=2000.0),
            "gained": _vec(gained, m0, "ice_gained", params, min_area=2000.0),
        })
    return {"years": out_years, "_baseline_mask": base, "_grid": comp[baseline_year][2]}


def rgi_agreement(ours_fc: dict[str, Any], rgi_fc: dict[str, Any], ids: list[str], epsg: int) -> dict[str, Any]:
    """Overlap of our baseline outline with selected RGI glaciers (diagnostic only)."""
    from src.services.boundary import to_metric

    rgi = [f for f in rgi_fc["features"] if f["properties"]["rgi_id"] in ids]
    if not rgi or not ours_fc["features"]:
        return {"rgi_ids": ids, "available": False}
    r = shapely.union_all([shapely.make_valid(to_metric(f["geometry"], epsg)) for f in rgi])
    o_all = shapely.union_all([shapely.make_valid(to_metric(f["geometry"], epsg)) for f in ours_fc["features"]])
    o = o_all.intersection(r.buffer(300))  # our ice belonging to these glaciers (not neighbours)
    inter = o.intersection(r).area
    union = o.union(r).area
    only_rgi = r.difference(o)
    # where is the RGI-only ice? split into near-terminus (within 1.5 km of the terminus) vs elsewhere
    terms = [shapely.Point(*to_metric(shapely.Point(f["properties"]["termlon"], f["properties"]["termlat"]), epsg).coords[0])
             for f in rgi if f["properties"].get("termlon") is not None]
    near = only_rgi.intersection(shapely.union_all([t.buffer(1500) for t in terms])) if terms else shapely.Polygon()
    return {
        "rgi_ids": ids,
        "available": True,
        "rgi_area_km2": round(r.area / 1e6, 3),
        "ours_area_km2": round(o.area / 1e6, 3),
        "iou": round(inter / union, 3) if union else None,
        "only_rgi_km2": round(only_rgi.area / 1e6, 3),
        "only_rgi_near_terminus_km2": round(near.area / 1e6, 3),
        "only_ours_km2": round(o.difference(r).area / 1e6, 3),
        "note": "Diagnostic only. RGI includes debris-covered ice and dates from its source image; differences are "
                "partly method, not melt.",
    }


def season_bounds(year: int, season: tuple[str, str]) -> tuple[datetime, datetime]:
    a = datetime.fromisoformat(f"{year}-{season[0]}").replace(tzinfo=timezone.utc)
    b = datetime.fromisoformat(f"{year}-{season[1]}T23:59:59").replace(tzinfo=timezone.utc)
    return a, b


def year_of(acquired: str) -> int:
    return date.fromisoformat(acquired[:10]).year
