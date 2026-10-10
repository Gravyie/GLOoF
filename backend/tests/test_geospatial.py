"""Pipeline tests. The rasters below are TEST FIXTURES written to /vsimem and read
back through the exact same code path as a real COG (read_band_window). The app
itself never contains mock geometry."""
import math

import numpy as np
import pytest
import rasterio
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from rasterio.transform import Affine
from rasterio.warp import transform as warp_transform

from src.services.geospatial import (
    BandWindow,
    WaterParams,
    compute_ndwi,
    extract_water,
    read_band_window,
    to_reflectance,
)

LON, LAT = 88.183, 27.913
CRS = "EPSG:32645"
SCALE, OFFSET = 1e-4, -0.1
BBOX = (LON - 0.025, LAT - 0.025, LON + 0.025, LAT + 0.025)


def dn(refl):
    return np.round((np.asarray(refl) - OFFSET) / SCALE).astype(np.uint16)


def write_tif(mf, arr, transform):
    with mf.open(driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                 dtype=arr.dtype, crs=CRS, transform=transform, tiled=True,
                 blockxsize=256, blockysize=256) as dst:
        dst.write(arr, 1)


@pytest.fixture
def scene():
    # 10 m grid, 1000 x 1000 px, centred on the lake
    (cx,), (cy,) = warp_transform("EPSG:4326", CRS, [LON], [LAT])
    x0, y0 = math.floor(cx / 10) * 10 - 5000, math.floor(cy / 10) * 10 + 5000
    t10 = Affine(10, 0, x0, 0, -10, y0)
    t20 = Affine(20, 0, x0, 0, -20, y0)
    n = 1000
    yy, xx = np.mgrid[0:n, 0:n]

    green = np.full((n, n), 0.15, np.float32)  # land
    nir = np.full((n, n), 0.35, np.float32)
    scl10 = np.full((n, n), 4, np.uint8)

    lake = (xx - 500) ** 2 + (yy - 500) ** 2 <= 25**2  # r = 25 px = 250 m
    green[lake], nir[lake] = 0.10, 0.02

    snow = (xx - 300) ** 2 + (yy - 300) ** 2 <= 20**2  # NDWI ~0.14 but NIR high
    green[snow], nir[snow] = 0.80, 0.60

    cloudy = (xx - 700) ** 2 + (yy - 500) ** 2 <= 20**2  # water-like but cloud
    green[cloudy], nir[cloudy] = 0.10, 0.02
    scl10[cloudy] = 9

    green[200:202, 200:202], nir[200:202, 200:202] = 0.10, 0.02  # 2x2 speckle

    # SWIR (20 m): land bright, water very dark, snow dark (high NDSI)
    swir = np.full((n, n), 0.30, np.float32)
    swir[lake] = 0.01
    swir[snow] = 0.10
    swir20 = swir[::2, ::2]

    scl20 = scl10[::2, ::2].copy()
    files = {}
    mfs = []
    for name, arr, tr in (("green", dn(green), t10), ("nir", dn(nir), t10), ("scl", scl20, t20), ("swir", dn(swir20), t20)):
        mf = MemoryFile()
        write_tif(mf, arr, tr)
        mfs.append(mf)
        files[name] = mf.name
    yield files
    for mf in mfs:
        mf.close()


def test_ndwi_and_reflectance():
    g = to_reflectance(np.array([0, 1100, 2100], np.uint16), SCALE, OFFSET)
    assert np.isnan(g[0]) and g[1] == pytest.approx(0.01) and g[2] == pytest.approx(0.11)
    nd = compute_ndwi(np.array([0.3, 0.1, 0.0], np.float32), np.array([0.1, 0.3, 0.0], np.float32))
    assert nd[0] == pytest.approx(0.5) and nd[1] == pytest.approx(-0.5) and np.isnan(nd[2])


def test_window_read_is_small_and_aligned(scene):
    green = read_band_window(scene["green"], BBOX)
    nir = read_band_window(scene["nir"], BBOX, like=green)
    scl = read_band_window(scene["scl"], BBOX, like=green)
    assert green.data.shape == nir.data.shape == scl.data.shape
    assert 400 < green.data.shape[0] < 600  # ~5 km window, not the whole raster
    assert green.crs == rasterio.crs.CRS.from_string(CRS)


def test_pipeline_extracts_only_the_real_lake(scene):
    green = read_band_window(scene["green"], BBOX)
    nir = read_band_window(scene["nir"], BBOX, like=green)
    scl = read_band_window(scene["scl"], BBOX, like=green)
    ex = extract_water(green, nir, scl, green_cal=(SCALE, OFFSET), nir_cal=(SCALE, OFFSET),
                       params=WaterParams())

    feats = ex.feature_collection["features"]
    # snow (NIR guard), cloud (SCL) and the 2x2 speckle (area filter) are all rejected
    assert len(feats) == 1
    props = feats[0]["properties"]
    assert props["primary"] is True
    expected = math.pi * (25 * 10) ** 2
    assert props["area_m2"] == pytest.approx(expected, rel=0.06)
    assert props["mean_ndwi"] > 0.5

    # geometry is real WGS84 and sits on the lake centre
    ring = feats[0]["geometry"]["coordinates"][0]
    lons, lats = zip(*[(p[0], p[1]) for p in ring])
    assert abs(np.mean(lons) - LON) < 0.001 and abs(np.mean(lats) - LAT) < 0.001
    assert ex.stats["cloud_fraction"] > 0 and ex.stats["valid_fraction"] == 1.0


def test_snow_guard_matters(scene):
    """With the NIR guard off, the snow patch is (wrongly) classed as water."""
    green = read_band_window(scene["green"], BBOX)
    nir = read_band_window(scene["nir"], BBOX, like=green)
    scl = read_band_window(scene["scl"], BBOX, like=green)
    ex = extract_water(green, nir, scl, green_cal=(SCALE, OFFSET), nir_cal=(SCALE, OFFSET),
                       params=WaterParams(max_nir_reflectance=1.0))
    assert len(ex.feature_collection["features"]) == 2


def test_grid_mismatch_is_rejected():
    a = BandWindow(np.zeros((4, 4), np.uint16), Affine(10, 0, 0, 0, -10, 0), rasterio.crs.CRS.from_epsg(32645))
    b = BandWindow(np.zeros((4, 5), np.uint16), Affine(10, 0, 0, 0, -10, 0), a.crs)
    with pytest.raises(Exception):
        extract_water(a, b, None, green_cal=(1, 0), nir_cal=(1, 0), params=WaterParams())


def test_ice_layer_finds_snow_not_water_or_land(scene):
    green = read_band_window(scene["green"], BBOX)
    nir = read_band_window(scene["nir"], BBOX, like=green)
    scl = read_band_window(scene["scl"], BBOX, like=green)
    swir = read_band_window(scene["swir"], BBOX, like=green, resampling=Resampling.bilinear)
    ex = extract_water(green, nir, scl, green_cal=(SCALE, OFFSET), nir_cal=(SCALE, OFFSET),
                       params=WaterParams(), swir=swir, swir_cal=(SCALE, OFFSET))

    # water result is unchanged by adding SWIR
    assert len(ex.feature_collection["features"]) == 1

    ice = ex.ice_collection["features"]
    assert len(ice) == 1  # only the snow patch: not the lake (NIR floor), land or cloud
    assert ice[0]["properties"]["kind"] == "ice"
    assert ice[0]["properties"]["area_m2"] == pytest.approx(math.pi * 200**2, rel=0.12)
    assert ice[0]["properties"]["mean_ndsi"] > 0.4
    assert ex.stats["ice_area_m2"] == pytest.approx(ice[0]["properties"]["area_m2"])
    # snow patch sits NW of the lake
    ring = ice[0]["geometry"]["coordinates"][0]
    assert np.mean([p[0] for p in ring]) < LON and np.mean([p[1] for p in ring]) > LAT


def test_no_swir_means_no_ice_layer(scene):
    green = read_band_window(scene["green"], BBOX)
    nir = read_band_window(scene["nir"], BBOX, like=green)
    ex = extract_water(green, nir, None, green_cal=(SCALE, OFFSET), nir_cal=(SCALE, OFFSET),
                       params=WaterParams())
    assert ex.ice_collection is None and ex.stats["ice_area_m2"] is None
