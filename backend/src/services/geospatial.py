"""Raster -> vector pipeline: Sentinel-2 COG windows -> NDWI -> GeoJSON polygons.

Pipeline (no coordinates are ever typed in by hand):

    COG windows (B03, B08, SCL) read straight into memory
      -> DN to surface reflectance (scale + offset from STAC metadata)
      -> NDWI = (Green - NIR) / (Green + NIR)
      -> water mask = NDWI > threshold, clear sky, NIR below snow level
      -> rasterio.features.shapes  (pixel mask -> polygons in the raster CRS)
      -> area filter + simplify in metres (UTM), then reproject to EPSG:4326
      -> GeoJSON FeatureCollection

Only the analysis window around the lake is read (HTTP range requests on the
COG), not the whole 110 x 110 km tile.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
import shapely
from rasterio import features
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import Affine, array_bounds
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from shapely.geometry import mapping, shape

# GDAL settings for anonymous, efficient COG access on public S3.
GDAL_ENV = {
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
}

# Sentinel-2 SCL classes: 3 cloud shadow, 8/9 cloud (medium/high), 10 thin cirrus, 11 snow/ice
SCL_CLOUD_SHADOW = (3, 8, 9, 10)
SCL_SNOW = 11


class GeospatialError(RuntimeError):
    pass


@dataclass(frozen=True)
class BandWindow:
    data: np.ndarray
    transform: Affine
    crs: CRS


@dataclass(frozen=True)
class WaterParams:
    ndwi_threshold: float = 0.1
    max_nir_reflectance: float = 0.20
    min_polygon_area_m2: float = 2000.0
    simplify_tolerance_m: float = 5.0
    exclude_snow: bool = False
    # Snow/ice (NDSI = (Green - SWIR) / (Green + SWIR)). The NIR floor is the
    # standard guard that stops dark water being classed as snow.
    ndsi_threshold: float = 0.4
    min_ice_nir_reflectance: float = 0.11
    min_ice_area_m2: float = 20000.0
    ice_simplify_tolerance_m: float = 15.0


@dataclass(frozen=True)
class WaterExtraction:
    feature_collection: dict[str, Any]  # water polygons
    stats: dict[str, Any]
    ice_collection: dict[str, Any] | None = None  # snow/ice polygons (needs SWIR)


# --------------------------------------------------------------------------- #
# 1. Reading COG windows into memory
# --------------------------------------------------------------------------- #
def read_band_window(
    href: str,
    bbox_wgs84: tuple[float, float, float, float],
    *,
    like: BandWindow | None = None,
    resampling: Resampling = Resampling.nearest,
) -> BandWindow:
    """Read the part of a COG covering ``bbox_wgs84`` into a numpy array.

    * ``like=None``: pixel-aligned window in the band's native grid.
    * ``like=<BandWindow>``: read exactly the same ground footprint, resampled
      onto ``like``'s grid (used to align the 20 m SCL to the 10 m bands, and
      to guarantee B03/B08 share one grid).
    """
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(href) as src:
            if like is None:
                native = transform_bounds("EPSG:4326", src.crs, *bbox_wgs84, densify_pts=21)
                win = from_bounds(*native, transform=src.transform)
                col0, row0 = math.floor(win.col_off), math.floor(win.row_off)
                col1 = math.ceil(win.col_off + win.width)
                row1 = math.ceil(win.row_off + win.height)
                win = Window(col0, row0, col1 - col0, row1 - row0)
                try:
                    win = win.intersection(Window(0, 0, src.width, src.height))
                except Exception as exc:  # rasterio.errors.WindowError
                    raise GeospatialError("window does not intersect raster") from exc
                data = src.read(1, window=win)
                return BandWindow(data, src.window_transform(win), src.crs)

            if src.crs != like.crs:
                raise GeospatialError(f"CRS mismatch: {src.crs} vs {like.crs}")
            h, w = like.data.shape
            west, south, east, north = array_bounds(h, w, like.transform)
            win = from_bounds(west, south, east, north, transform=src.transform)
            data = src.read(1, window=win, out_shape=(h, w), resampling=resampling)
            return BandWindow(data, like.transform, like.crs)


# --------------------------------------------------------------------------- #
# 2. Radiometry + NDWI
# --------------------------------------------------------------------------- #
def to_reflectance(dn: np.ndarray, scale: float, offset: float) -> np.ndarray:
    """DN -> surface reflectance. DN == 0 is Sentinel-2 nodata -> NaN."""
    refl = dn.astype(np.float32) * np.float32(scale) + np.float32(offset)
    refl = np.clip(refl, 0.0, None)
    refl[dn == 0] = np.nan
    return refl


def compute_ndwi(green: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """NDWI = (Green - NIR) / (Green + NIR); NaN where undefined."""
    with np.errstate(invalid="ignore", divide="ignore"):
        denom = green + nir
        ndwi = (green - nir) / denom
    ndwi[~np.isfinite(ndwi)] = np.nan
    return ndwi.astype(np.float32)


# --------------------------------------------------------------------------- #
# 3. Mask -> polygons
# --------------------------------------------------------------------------- #
def vectorize_mask(
    mask: np.ndarray,
    index: np.ndarray,
    transform: Affine,
    crs: CRS,
    *,
    kind: str,
    index_name: str,
    min_area_m2: float,
    simplify_m: float,
) -> list[dict[str, Any]]:
    """Polygonize a boolean mask and return GeoJSON features in EPSG:4326."""
    if not crs.is_projected:
        raise GeospatialError("raster CRS must be projected (metres) for area maths")
    if not mask.any():
        return []

    geoms = [
        shape(geom)
        for geom, value in features.shapes(
            mask.astype(np.uint8), mask=mask, transform=transform, connectivity=8
        )
        if value == 1
    ]
    if not geoms:
        return []

    gdf = gpd.GeoDataFrame({"geometry": geoms}, crs=crs)
    gdf["area_m2"] = gdf.geometry.area  # native CRS is UTM -> metres
    gdf = gdf[gdf["area_m2"] >= min_area_m2].copy()
    if gdf.empty:
        return []
    gdf = gdf.sort_values("area_m2", ascending=False).reset_index(drop=True)

    # Zonal mean of the index per polygon (only a handful of polygons survive).
    means: list[float] = []
    for geom in gdf.geometry:
        inside = features.geometry_mask(
            [mapping(geom)], out_shape=mask.shape, transform=transform, invert=True
        )
        vals = index[inside & mask]
        means.append(float(np.nanmean(vals)) if vals.size else float("nan"))
    gdf["mean_idx"] = means

    if simplify_m > 0:
        gdf["geometry"] = gdf.geometry.simplify(simplify_m, preserve_topology=True)

    gdf = gdf.to_crs(4326)
    out: list[dict[str, Any]] = []
    for idx, row in gdf.iterrows():
        geom = shapely.set_precision(row.geometry, 1e-6)  # ~0.1 m, keeps JSON small
        out.append(
            {
                "type": "Feature",
                "id": int(idx),
                "properties": {
                    "kind": kind,
                    "area_m2": round(float(row["area_m2"]), 1),
                    "area_ha": round(float(row["area_m2"]) / 1e4, 3),
                    f"mean_{index_name}": None if math.isnan(row["mean_idx"]) else round(row["mean_idx"], 3),
                    "primary": bool(idx == 0),  # largest body
                },
                "geometry": mapping(geom),
            }
        )
    return out


def _fc(feats: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": feats}


# --------------------------------------------------------------------------- #
# 4. End-to-end extraction
# --------------------------------------------------------------------------- #
def extract_water(
    green: BandWindow,
    nir: BandWindow,
    scl: BandWindow | None,
    *,
    green_cal: tuple[float, float],
    nir_cal: tuple[float, float],
    params: WaterParams,
    swir: BandWindow | None = None,
    swir_cal: tuple[float, float] | None = None,
) -> WaterExtraction:
    """Water (NDWI) and, when a SWIR band is given, snow/ice (NDSI) polygons."""
    if green.data.shape != nir.data.shape or green.transform != nir.transform:
        raise GeospatialError("green and NIR windows are not on the same grid")

    g = to_reflectance(green.data, *green_cal)
    n = to_reflectance(nir.data, *nir_cal)
    ndwi = compute_ndwi(g, n)
    valid = np.isfinite(ndwi)

    # Cloud / cloud shadow / cirrus (and snow, only if the caller asks).
    bad = np.zeros(valid.shape, dtype=bool)
    if scl is not None:
        classes = SCL_CLOUD_SHADOW + ((SCL_SNOW,) if params.exclude_snow else ())
        bad = np.isin(scl.data, classes)
    clear = valid & ~bad

    with np.errstate(invalid="ignore"):
        water = clear & (ndwi > params.ndwi_threshold) & (n <= params.max_nir_reflectance)

    water_feats = vectorize_mask(
        water, ndwi, green.transform, green.crs,
        kind="water", index_name="ndwi",
        min_area_m2=params.min_polygon_area_m2, simplify_m=params.simplify_tolerance_m,
    )

    ice_feats: list[dict[str, Any]] | None = None
    ice_total = 0.0
    if swir is not None and swir_cal is not None:
        if swir.data.shape != green.data.shape:
            raise GeospatialError("SWIR window is not on the green/NIR grid")
        sw = to_reflectance(swir.data, *swir_cal)
        with np.errstate(invalid="ignore", divide="ignore"):
            ndsi = (g - sw) / (g + sw)
        ndsi[~np.isfinite(ndsi)] = np.nan
        with np.errstate(invalid="ignore"):
            ice = clear & ~water & (ndsi > params.ndsi_threshold) & (n >= params.min_ice_nir_reflectance)
        ice_feats = vectorize_mask(
            ice, ndsi, green.transform, green.crs,
            kind="ice", index_name="ndsi",
            min_area_m2=params.min_ice_area_m2, simplify_m=params.ice_simplify_tolerance_m,
        )
        ice_total = float(sum(f["properties"]["area_m2"] for f in ice_feats))

    px = abs(green.transform.a)
    total_area = float(sum(f["properties"]["area_m2"] for f in water_feats))
    primary = water_feats[0]["properties"]["area_m2"] if water_feats else 0.0

    stats = {
        "water_area_m2": round(total_area, 1),
        "water_area_ha": round(total_area / 1e4, 3),
        "primary_area_m2": primary,
        "polygon_count": len(water_feats),
        "ice_area_m2": round(ice_total, 1) if ice_feats is not None else None,
        "ice_area_ha": round(ice_total / 1e4, 2) if ice_feats is not None else None,
        "ice_polygon_count": len(ice_feats) if ice_feats is not None else None,
        "pixel_size_m": px,
        "window_shape": list(valid.shape),
        "valid_fraction": round(float(valid.mean()), 4),
        "cloud_fraction": round(float(bad.mean()), 4),
        "crs": str(green.crs),
    }
    return WaterExtraction(
        feature_collection=_fc(water_feats),
        stats=stats,
        ice_collection=_fc(ice_feats) if ice_feats is not None else None,
    )
