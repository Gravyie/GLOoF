"""Screening-level GLOF flood-extent model (LAHARZ-style) on a DEM.

This is NOT a hydraulic simulation. It answers "if V cubic metres left the lake,
which valley cells would plausibly be reached, and how deep?" using three
well-documented, deliberately simple steps:

1. FLOW PATH   - fill depressions, D8 flow directions (pyflwdir), then trace
                 downstream from the lake.
2. CROSS-SECTION - for every point along the path, find the water stage whose
                 valley cross-section area equals  A = a1 * V^(2/3)
                 (Iverson et al. 1998; Griswold & Iverson 2007: a1 = 0.1 for
                 non-volcanic debris flows). Cells are linked to the nearest
                 path cell and measured as height-above-nearest-drainage (HAND).
3. RUN-OUT     - the "core" zone ends where the inundated planimetric area
                 reaches  B = a2 * V^(2/3)  (a2 = 20 for debris flows).
                 Beyond that, out to ``max_runout_m``, the same stage is kept
                 and tagged "extended": an upper bound, because water-dominated
                 outburst floods travel much further than debris flows but also
                 attenuate, and attenuation is not modelled here.

Limitations (surfaced to the user in the API response): 30 m surface model
(DSM, not bare earth), no peak-discharge attenuation, no sediment bulking,
no dam/bridge/channel-works effects, volume is an empirical estimate.
All maths is in a projected (metre) CRS.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyflwdir
import shapely
from pyflwdir.dem import fill_depressions
from rasterio import features
from rasterio.crs import CRS
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import LineString, mapping, shape
from shapely.ops import unary_union

import geopandas as gpd

NODATA = -9999.0
DEPTH_BINS_M = (1.0, 3.0, 10.0)  # classes: <1, 1-3, 3-10, >10
DEPTH_LABELS = ("<1 m", "1-3 m", "3-10 m", ">10 m")


class HydrologyError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Empirical relations
# --------------------------------------------------------------------------- #
def lake_volume_m3(area_m2: float) -> float:
    """Huggel et al. (2002): V = 0.104 * A^1.42  (m^3, m^2). Order-of-magnitude only."""
    if area_m2 <= 0:
        raise HydrologyError("lake area must be positive")
    return 0.104 * area_m2**1.42


@dataclass(frozen=True)
class FloodParams:
    alpha1: float = 0.1  # cross-section coefficient  A = alpha1 * V^(2/3)
    alpha2: float = 20.0  # planimetric coefficient    B = alpha2 * V^(2/3)
    corridor_m: float = 2500.0  # max lateral distance from path considered
    max_runout_m: float = 60000.0
    max_stage_m: float = 80.0  # sanity cap on flow depth above the channel
    min_cells_per_link: int = 3
    smooth_window: int = 9  # median filter over path stages (odd)


@dataclass(frozen=True)
class DemGrid:
    data: np.ndarray  # float32, metres
    transform: Affine  # north-up
    crs: CRS
    nodata: float = NODATA

    @property
    def res_x(self) -> float:
        return abs(self.transform.a)

    @property
    def res_y(self) -> float:
        return abs(self.transform.e)


@dataclass
class FlowContext:
    """Everything that depends on the DEM + start point but NOT on the volume."""

    dem: DemGrid
    path_rc: np.ndarray  # (n, 2) rows, cols along the path
    path_dist_m: np.ndarray  # (n,) cumulative distance from start
    path_z: np.ndarray  # (n,) filled-DEM elevation along the path
    link: np.ndarray  # int32 grid: nearest path index, -1 outside corridor
    hand: np.ndarray  # float32 grid: height above linked path cell (>= 0)
    cell_area_m2: float
    params: FloodParams
    warnings: list[str] = field(default_factory=list)


@dataclass
class Inundation:
    volume_m3: float
    cross_section_m2: float
    planimetric_limit_m2: float
    stage_m: np.ndarray  # (n,) smoothed stage per path point
    depth: np.ndarray  # float32 grid (0 = dry)
    zone: np.ndarray  # uint8 grid: 0 dry, 1 core, 2 extended
    core_end_index: int
    core_runout_m: float
    extended_runout_m: float
    area_core_m2: float
    area_extended_m2: float
    max_depth_m: float


# --------------------------------------------------------------------------- #
# 1. Flow path
# --------------------------------------------------------------------------- #
def _xy_to_rc(dem: DemGrid, x: float, y: float) -> tuple[int, int]:
    col, row = ~dem.transform * (x, y)
    r, c = int(math.floor(row)), int(math.floor(col))
    h, w = dem.data.shape
    if not (0 <= r < h and 0 <= c < w):
        raise HydrologyError("start point is outside the DEM window")
    return r, c


def build_flow_context(
    dem: DemGrid, start_xy: tuple[float, float], params: FloodParams | None = None
) -> FlowContext:
    """Route flow over ``dem`` and trace the downstream path from ``start_xy``
    (coordinates in the DEM's projected CRS)."""
    p = params or FloodParams()
    if not dem.crs.is_projected:
        raise HydrologyError("DEM must be in a projected CRS (metres)")
    data = np.where(np.isfinite(dem.data), dem.data, dem.nodata).astype(np.float32)
    h, w = data.shape
    warnings: list[str] = []

    flw = pyflwdir.from_dem(data, nodata=dem.nodata, transform=dem.transform, latlon=False)
    filled, _ = fill_depressions(data, nodata=dem.nodata, max_depth=-1.0, elv_min=None)

    r0, c0 = _xy_to_rc(dem, *start_xy)
    start_idx = r0 * w + c0
    paths, _ = flw.path(idxs=[start_idx], unit="cell")
    path = np.asarray(paths[0], dtype=np.int64)
    if path.size < 5:
        raise HydrologyError("flow path from the lake is too short (start in a pit or at the edge?)")

    rows, cols = path // w, path % w
    step = np.hypot(np.diff(cols) * dem.res_x, np.diff(rows) * dem.res_y)
    dist = np.concatenate([[0.0], np.cumsum(step)])

    keep = dist <= p.max_runout_m
    if not keep.all():
        path, rows, cols, dist = path[keep], rows[keep], cols[keep], dist[keep]
    if dist[-1] < p.max_runout_m * 0.98:
        warnings.append(
            f"flow path leaves the DEM window after {dist[-1] / 1000:.1f} km "
            f"(requested {p.max_runout_m / 1000:.0f} km); impact beyond that is not assessed"
        )

    n = path.size
    path_z = filled.reshape(-1)[path].astype(np.float32)

    # nearest-path-cell linkage (a HAND variant): Euclidean distance transform
    k_of_cell = np.full((h, w), -1, dtype=np.int32)
    k_of_cell[rows, cols] = np.arange(n, dtype=np.int32)
    dist_grid, (ir, ic) = ndimage.distance_transform_edt(
        k_of_cell < 0, sampling=(dem.res_y, dem.res_x), return_indices=True
    )
    link = k_of_cell[ir, ic]
    link = np.where(dist_grid <= p.corridor_m, link, -1).astype(np.int32)
    valid = data != dem.nodata
    link[~valid] = -1

    z_ref = np.where(link >= 0, path_z[np.clip(link, 0, n - 1)], 0.0)
    hand = np.where(link >= 0, np.maximum(data - z_ref, 0.0), 0.0).astype(np.float32)

    return FlowContext(
        dem=dem,
        path_rc=np.stack([rows, cols], axis=1),
        path_dist_m=dist,
        path_z=path_z,
        link=link,
        hand=hand,
        cell_area_m2=dem.res_x * dem.res_y,
        params=p,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# 2-3. Stage per cross-section + run-out
# --------------------------------------------------------------------------- #
def _stage_for_area(hand_sorted: np.ndarray, width_per_cell: float, target_area: float, cap: float) -> float:
    """Smallest stage h with  sum(max(h - hand_i, 0)) * width_per_cell == target_area.

    ``hand_sorted`` ascending. Piecewise-linear and convex in h -> closed form
    per segment.
    """
    n = hand_sorted.size
    csum = np.cumsum(hand_sorted)
    # area at stage = hand_sorted[j] using the first j+1 cells
    area_at = width_per_cell * (np.arange(1, n + 1) * hand_sorted - csum)
    j = int(np.searchsorted(area_at, target_area, side="right")) - 1  # last j with area_at[j] <= target
    j = max(j, 0)
    m = j + 1  # number of cells under water at this stage
    h = (target_area / width_per_cell + csum[j]) / m
    return float(min(h, cap))


def solve_inundation(ctx: FlowContext, volume_m3: float) -> Inundation:
    p = ctx.params
    if volume_m3 <= 0:
        raise HydrologyError("volume must be positive")
    v23 = volume_m3 ** (2.0 / 3.0)
    A = p.alpha1 * v23
    B = p.alpha2 * v23

    n = ctx.path_z.size
    link, hand = ctx.link.reshape(-1), ctx.hand.reshape(-1)
    sel = np.flatnonzero(link >= 0)
    order = sel[np.argsort(link[sel], kind="stable")]
    sorted_links = link[order]
    bounds = np.searchsorted(sorted_links, np.arange(n + 1))

    stage = np.zeros(n, dtype=np.float32)
    seg = np.gradient(ctx.path_dist_m) if n > 1 else np.array([ctx.dem.res_x])
    seg = np.clip(seg, min(ctx.dem.res_x, ctx.dem.res_y), None)
    for k in range(n):
        idx = order[bounds[k] : bounds[k + 1]]
        if idx.size < p.min_cells_per_link:
            stage[k] = np.nan
            continue
        hs = np.sort(hand[idx])
        width_per_cell = ctx.cell_area_m2 / seg[k]
        stage[k] = _stage_for_area(hs, width_per_cell, A, p.max_stage_m)

    # fill gaps, then smooth so single noisy cross-sections do not spike
    good = np.isfinite(stage)
    if not good.any():
        raise HydrologyError("could not determine a flow stage along the path")
    stage = np.interp(np.arange(n), np.flatnonzero(good), stage[good]).astype(np.float32)
    win = max(1, p.smooth_window | 1)
    stage = ndimage.median_filter(stage, size=win, mode="nearest")

    # wet cells: HAND below the local stage, inside the corridor, connected to the path
    link2d = ctx.link
    stage_grid = np.where(link2d >= 0, stage[np.clip(link2d, 0, n - 1)], -1.0)
    wet = (link2d >= 0) & (ctx.hand < stage_grid)
    lab, _ = ndimage.label(wet, structure=np.ones((3, 3), dtype=int))
    path_labels = np.unique(lab[ctx.path_rc[:, 0], ctx.path_rc[:, 1]])
    path_labels = path_labels[path_labels > 0]
    wet &= np.isin(lab, path_labels)
    depth = np.where(wet, stage_grid - ctx.hand, 0.0).astype(np.float32)

    # run-out: cumulative inundated planimetric area per path point
    per_k = np.bincount(link2d[wet], minlength=n).astype(np.float64) * ctx.cell_area_m2
    cum = np.cumsum(per_k)
    hit = np.flatnonzero(cum >= B)
    core_end = int(hit[0]) if hit.size else n - 1

    zone = np.zeros(depth.shape, dtype=np.uint8)
    core_cells = wet & (link2d <= core_end)
    zone[core_cells] = 1
    zone[wet & ~core_cells] = 2

    return Inundation(
        volume_m3=float(volume_m3),
        cross_section_m2=float(A),
        planimetric_limit_m2=float(B),
        stage_m=stage,
        depth=depth,
        zone=zone,
        core_end_index=core_end,
        core_runout_m=float(ctx.path_dist_m[core_end]),
        extended_runout_m=float(ctx.path_dist_m[-1]),
        area_core_m2=float(core_cells.sum() * ctx.cell_area_m2),
        area_extended_m2=float((wet & ~core_cells).sum() * ctx.cell_area_m2),
        max_depth_m=float(depth.max()),
    )


# --------------------------------------------------------------------------- #
# 4. Vectorise to GeoJSON (EPSG:4326)
# --------------------------------------------------------------------------- #
def _depth_class(depth: np.ndarray) -> np.ndarray:
    return np.digitize(depth, DEPTH_BINS_M).astype(np.uint8)  # 0..3


def inundation_features(ctx: FlowContext, inu: Inundation, *, min_cells: int = 3,
                        simplify_m: float = 20.0) -> list[dict[str, Any]]:
    """One polygon (multi) per zone x depth-class, in WGS84."""
    cls = _depth_class(inu.depth)
    codes = (inu.zone.astype(np.uint16) * 10 + cls).astype(np.uint16)
    mask = inu.zone > 0
    by_code: dict[int, list] = {}
    for geom, val in features.shapes(codes, mask=mask, transform=ctx.dem.transform, connectivity=8):
        by_code.setdefault(int(val), []).append(shape(geom))

    rows = []
    for code, geoms in sorted(by_code.items()):
        union = unary_union(geoms)
        if simplify_m > 0:
            union = union.simplify(simplify_m, preserve_topology=True)
        if union.is_empty or union.area < min_cells * ctx.cell_area_m2:
            continue
        zone, klass = divmod(code, 10)
        rows.append({"geometry": union, "zone": "core" if zone == 1 else "extended",
                     "depth_class": klass, "depth_label": DEPTH_LABELS[klass],
                     "area_m2": float(union.area)})
    if not rows:
        return []
    gdf = gpd.GeoDataFrame(rows, crs=ctx.dem.crs).to_crs(4326)
    out = []
    for i, r in gdf.iterrows():
        out.append({
            "type": "Feature", "id": int(i),
            "properties": {k: r[k] for k in ("zone", "depth_class", "depth_label")}
            | {"area_km2": round(r["area_m2"] / 1e6, 3)},
            "geometry": mapping(shapely.set_precision(r.geometry, 1e-5)),
        })
    return out


def path_feature(ctx: FlowContext, inu: Inundation | None = None) -> dict[str, Any]:
    """Flow path as a WGS84 LineString with distances."""
    t = ctx.dem.transform
    xs = t.c + (ctx.path_rc[:, 1] + 0.5) * t.a
    ys = t.f + (ctx.path_rc[:, 0] + 0.5) * t.e
    line = gpd.GeoSeries([LineString(np.column_stack([xs, ys])).simplify(ctx.dem.res_x * 0.5)],
                         crs=ctx.dem.crs).to_crs(4326).iloc[0]
    props = {"length_km": round(float(ctx.path_dist_m[-1]) / 1000, 2)}
    if inu is not None:
        props["core_runout_km"] = round(inu.core_runout_m / 1000, 2)
    return {"type": "Feature", "properties": props, "geometry": mapping(line)}


def path_points_wgs84(ctx: FlowContext) -> tuple[np.ndarray, np.ndarray]:
    """(lon, lat) of every path cell, for settlement snapping."""
    t = ctx.dem.transform
    xs = t.c + (ctx.path_rc[:, 1] + 0.5) * t.a
    ys = t.f + (ctx.path_rc[:, 0] + 0.5) * t.e
    pts = gpd.GeoSeries(gpd.points_from_xy(xs, ys), crs=ctx.dem.crs).to_crs(4326)
    return pts.x.to_numpy(), pts.y.to_numpy()
