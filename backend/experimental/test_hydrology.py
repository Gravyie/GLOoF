"""Hydrology tests on a synthetic parabolic valley where the right answer is
known analytically (this is a test fixture, not app data).

Valley: z = base - slope*y + k*(x-xc)^2, so a cross-section filled to depth h
has area (4/3) h^1.5 / sqrt(k)."""
import math

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import Affine

from src.services.hydrology import (
    DemGrid, FloodParams, HydrologyError, build_flow_context, inundation_features,
    lake_volume_m3, path_feature, solve_inundation,
)

RES, N, K, SLOPE = 60.0, 400, 2.5e-4, 0.03


@pytest.fixture(scope="module")
def ctx():
    rr, cc = np.mgrid[0:N, 0:N]
    xm, ym = cc * RES, rr * RES
    z = 4500 - SLOPE * ym + K * (xm - N * RES / 2) ** 2
    z[20:30, N // 2 - 3 : N // 2 + 3] -= 25  # a lake basin (depression) at the top
    dem = DemGrid(z.astype(np.float32), Affine(RES, 0, 500000, 0, -RES, 3100000), CRS.from_epsg(32645))
    start = (500000 + (N // 2 + 0.5) * RES, 3100000 - 25 * RES)  # inside the basin
    return build_flow_context(dem, start, FloodParams(max_runout_m=20000))


def test_path_leaves_the_lake_and_runs_downvalley(ctx):
    r = ctx.path_rc[:, 0]
    assert r[-1] > r[0] + 100 and np.all(np.diff(ctx.path_dist_m) > 0)
    assert abs(ctx.path_rc[-1, 1] - N // 2) <= 4  # stays in the valley axis
    assert ctx.path_dist_m[-1] <= 20000 + RES * 2


def test_stage_matches_analytic_cross_section(ctx):
    V = 1e7
    inu = solve_inundation(ctx, V)
    A = 0.1 * V ** (2 / 3)
    h_expected = (A * math.sqrt(K) * 3 / 4) ** (2 / 3)
    mid = inu.stage_m[len(inu.stage_m) // 4 : -len(inu.stage_m) // 4]
    assert np.median(mid) == pytest.approx(h_expected, rel=0.2)
    # wet width ~ 2*sqrt(h/K)
    row = 200
    wet_cells = (inu.zone[row] > 0).sum()
    assert wet_cells * RES == pytest.approx(2 * math.sqrt(h_expected / K), rel=0.25)


def test_core_runout_follows_planimetric_limit(ctx):
    V = 1e7
    inu = solve_inundation(ctx, V)
    B = 20 * V ** (2 / 3)
    width = 2 * math.sqrt(np.median(inu.stage_m) / K)
    assert inu.core_runout_m == pytest.approx(B / width, rel=0.3)
    assert inu.area_core_m2 == pytest.approx(B, rel=0.15)
    assert inu.extended_runout_m > inu.core_runout_m and inu.area_extended_m2 > 0


def test_bigger_release_means_deeper_and_longer(ctx):
    a, b = solve_inundation(ctx, 3e6), solve_inundation(ctx, 3e7)
    assert b.max_depth_m > a.max_depth_m and b.core_runout_m > a.core_runout_m


def test_geojson_is_wgs84_and_has_zones(ctx):
    inu = solve_inundation(ctx, 1e7)
    feats = inundation_features(ctx, inu)
    assert {f["properties"]["zone"] for f in feats} == {"core", "extended"}
    lon, lat = feats[0]["geometry"]["coordinates"][0][0][:2] if feats[0]["geometry"]["type"] == "Polygon" \
        else feats[0]["geometry"]["coordinates"][0][0][0][:2]
    assert 86 < lon < 90 and 26 < lat < 30  # UTM 45N around Sikkim -> sensible lon/lat
    assert path_feature(ctx, inu)["geometry"]["type"] == "LineString"


def test_volume_relation_and_bad_inputs(ctx):
    assert lake_volume_m3(1.3e6) == pytest.approx(5.0e7, rel=0.05)  # Huggel 2002
    with pytest.raises(HydrologyError):
        lake_volume_m3(0)
    with pytest.raises(HydrologyError):
        solve_inundation(ctx, -1)
