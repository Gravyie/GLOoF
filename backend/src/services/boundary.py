"""Boundary maths on lake outlines: how far did the shoreline move, and where?

Pure functions over GeoJSON geometries (EPSG:4326), done in a metric UTM CRS.
No I/O, so they are cheap to test and to run inside a Lambda.

Definitions (all in metres / m2):
  extent_m2     area enclosed by the outer shoreline (holes from icebergs filled)
  advance_m     how far the shoreline has pushed OUT beyond a reference outline:
                the largest distance from any part of the current lake that lies
                outside the reference, back to the reference shoreline
  retreat_m     the same in reverse (reference area no longer water)
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import shapely
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon, mapping, shape


@lru_cache(maxsize=8)
def _to_utm(epsg: int) -> Transformer:
    return Transformer.from_crs(4326, epsg, always_xy=True)


def to_metric(geom: dict[str, Any] | Any, epsg: int):
    g = shape(geom) if isinstance(geom, dict) else geom
    t = _to_utm(epsg)
    return shapely.transform(g, lambda x, y: t.transform(x, y), interleaved=False)


def fill_holes(g):
    if isinstance(g, Polygon):
        return Polygon(g.exterior)
    if isinstance(g, MultiPolygon):
        return MultiPolygon([Polygon(p.exterior) for p in g.geoms])
    return g


def lake_body(g, width_m: float = 15.0):
    """Morphological opening: removes inflow streams and slivers narrower than
    ~2 x ``width_m`` that hang off the lake, keeps the largest part. Without it
    the far end of a stream counts as kilometres of 'shoreline advance'."""
    g = shapely.make_valid(fill_holes(g))
    opened = g.buffer(-width_m).buffer(width_m)
    if opened.is_empty:
        return g
    if isinstance(opened, MultiPolygon):
        opened = max(opened.geoms, key=lambda p: p.area)
    return opened


def advance_retreat(current, reference) -> tuple[float, float]:
    """(advance_m, retreat_m) of ``current`` relative to ``reference`` (metric geoms)."""
    if current is None or reference is None or current.is_empty or reference.is_empty:
        return 0.0, 0.0
    current, reference = shapely.make_valid(current), shapely.make_valid(reference)
    a = _max_outside_distance(current, reference)
    r = _max_outside_distance(reference, current)
    return a, r


def _max_outside_distance(a, b) -> float:
    """Largest distance from a point of ``a`` lying outside ``b`` to ``b``."""
    out = a.difference(b)
    if out.is_empty or out.area < 400:
        return 0.0
    pts = shapely.get_coordinates(out.boundary)
    if not len(pts):
        return 0.0
    dists = shapely.distance(shapely.points(pts), b)
    return round(float(dists.max()), 1)


def lake_shape_stats(geom_wgs84: dict[str, Any], epsg: int) -> dict[str, float]:
    g = fill_holes(to_metric(geom_wgs84, epsg))
    rect = g.minimum_rotated_rectangle
    xs = shapely.get_coordinates(rect.exterior if isinstance(rect, Polygon) else rect)
    sides = [((xs[i + 1][0] - xs[i][0]) ** 2 + (xs[i + 1][1] - xs[i][1]) ** 2) ** 0.5 for i in range(len(xs) - 1)]
    return {
        "extent_m2": round(g.area, 1),
        "perimeter_m": round(g.length, 1),
        "length_m": round(max(sides) if sides else 0.0, 1),
    }


def frozen_fraction(prev_lake_wgs84: dict[str, Any] | None, ice_fc: dict[str, Any] | None, epsg: int) -> float:
    """Share of the previous lake footprint now classified as snow/ice.

    A lake that freezes or gets snowed on reads as 'less water' to NDWI. Without
    this check that looks exactly like a drainage (outburst) event.
    """
    if not prev_lake_wgs84 or not ice_fc or not ice_fc.get("features"):
        return 0.0
    lake = shapely.make_valid(fill_holes(to_metric(prev_lake_wgs84, epsg)))
    if lake.area <= 0:
        return 0.0
    try:
        ice = shapely.union_all([shapely.make_valid(to_metric(f["geometry"], epsg)) for f in ice_fc["features"]])
        return round(float(lake.intersection(ice).area / lake.area), 3)
    except shapely.errors.GEOSException:  # degenerate outline: no evidence of freezing
        return 0.0


def to_geojson(g_metric, epsg: int) -> dict[str, Any]:
    back = Transformer.from_crs(epsg, 4326, always_xy=True)
    return mapping(shapely.set_precision(shapely.transform(g_metric, lambda x, y: back.transform(x, y), interleaved=False), 1e-6))
