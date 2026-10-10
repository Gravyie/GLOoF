"""Perennial-ice composite, baseline loss/gain, RGI reference and backward compatibility."""
import copy
import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from src.core.config import Settings
from src.main import create_app
from src.services.geospatial import WaterParams
from src.services.history import analyse_archive
from src.services.perennial_ice import SceneMask, baseline_change, build_perennial, classify, persistence_composite

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "archive" / "south_lhonak.json"
RGI = ROOT / "reference" / "rgi7_south_lhonak.geojson"


def test_transient_snow_in_one_scene_is_not_perennial():
    n, h, w = 5, 4, 4
    ice = np.zeros((n, h, w), bool)
    valid = np.ones((n, h, w), bool)
    ice[:, 0, 0] = True  # glacier: ice in every scene
    ice[2, 1, 1] = True  # fresh snow in one scene only
    perennial, frac, n_valid = persistence_composite(ice, valid, 0.7)
    assert perennial[0, 0] and not perennial[1, 1]
    assert frac[1, 1] == 0.2 and n_valid[0, 0] == 5


def test_cloud_masked_pixels_do_not_vote():
    ice = np.zeros((5, 1, 3), bool)
    valid = np.ones((5, 1, 3), bool)
    # pixel 0: clouded in 3 scenes (where the reading says "not ice"), ice in the 2 clear ones -> perennial
    ice[:, 0, 0] = [False, False, False, True, True]
    valid[:3, 0, 0] = False
    # pixel 1: ice under cloud must not count either: 3 clouded "ice" readings, 2 clear non-ice
    ice[:3, 0, 1] = True
    valid[:3, 0, 1] = False
    # pixel 2: never observed -> unknown, not ice
    valid[:, 0, 2] = False
    ice[:, 0, 2] = True
    perennial, frac, n_valid = persistence_composite(ice, valid, 0.7)
    assert perennial[0, 0] and frac[0, 0] == 1.0 and n_valid[0, 0] == 2
    assert not perennial[0, 1] and frac[0, 1] == 0.0
    assert not perennial[0, 2] and n_valid[0, 2] == 0 and np.isnan(frac[0, 2])


def test_classify_excludes_water_and_cloud():
    g = np.array([[0.6, 0.6, 0.1, 0.6]], np.float32)
    nir = np.array([[0.5, 0.5, 0.02, 0.5]], np.float32)
    sw = np.array([[0.05, 0.05, 0.01, 0.05]], np.float32)
    scl = np.array([[11, 9, 6, 0]], np.uint8)  # snow, cloud, water, nodata
    ice, valid = classify(g, nir, sw, scl, WaterParams())
    assert ice.tolist() == [[True, False, False, False]]
    assert valid.tolist() == [[True, False, True, False]]


def test_loss_and_gain_against_fixed_baseline_only_where_observed():
    base = np.zeros((10, 10), bool)
    base[2:8, 2:8] = True  # 36 px
    cur = np.zeros((10, 10), bool)
    cur[2:8, 2:6] = True  # retreated: lost 12 px
    cur[0, 0] = True  # artefact patch: gained 1 px
    obs = np.ones((10, 10), bool)
    cur_obs = obs.copy()
    cur_obs[7, :] = False  # one row unobserved this year
    lost, gained, both = baseline_change(base, cur, obs, cur_obs)
    assert lost.sum() == 12 - 2  # the 2 lost pixels in the unobserved row are not counted
    assert gained.sum() == 1 and not both[7].any()


def _mask(arr, scene, day):
    return SceneMask(scene, f"{day}T05:00:00+00:00", arr, np.ones_like(arr), from_origin(600000, 3090000, 10, 10), "EPSG:32645")


def test_build_perennial_years_compare_to_baseline_not_previous_year():
    big = np.zeros((60, 60), bool)
    big[5:55, 5:55] = True
    mid = np.zeros_like(big)
    mid[5:55, 5:40] = True
    small = np.zeros_like(big)
    small[5:55, 5:30] = True
    by_year = {
        2018: [_mask(big, f"a{i}", f"2018-10-0{i + 1}") for i in range(3)],
        2019: [_mask(mid, f"b{i}", f"2019-10-0{i + 1}") for i in range(3)],
        2020: [_mask(small, "c0", "2020-10-01")],  # one scene: low confidence
    }
    out = build_perennial(by_year, persistence=0.7, min_scenes=3, baseline_year=2018, params=WaterParams())["years"]
    y = {r["year"]: r for r in out}
    assert y[2018]["lost_km2"] == 0 and y[2018]["gained_km2"] == 0
    assert y[2019]["lost_km2"] == round(50 * 15 * 100 / 1e6, 3)
    assert y[2020]["lost_km2"] == round(50 * 25 * 100 / 1e6, 3)  # vs 2018, not vs 2019
    assert y[2020]["low_confidence"] and not y[2019]["low_confidence"]
    assert y[2020]["lost"]["features"] and y[2019]["outline"]["features"]


def test_rgi_reference_is_cited_and_dated():
    doc = json.loads(RGI.read_text())
    meta = doc["metadata"]
    assert meta["version"] == "7.0" and meta["doi"] == "10.5067/F6JMOVY5NAVZ" and "RGI 7.0 Consortium" in meta["citation"]
    assert doc["features"] and all(f["properties"]["src_date"] and f["properties"]["rgi_id"] for f in doc["features"])


def test_risk_timeline_unchanged_by_ice_keys():
    doc = json.loads(ARCHIVE.read_text())
    stripped = {k: v for k, v in copy.deepcopy(doc).items() if k not in ("perennial_ice", "ice_reference")}
    assert analyse_archive(doc) == analyse_archive(stripped)


def test_api_keeps_archive_shape_and_serves_ice_separately():
    client = TestClient(create_app(Settings(_env_file=None)))
    a = client.get("/api/archive?lake=south_lhonak").json()
    assert "perennial_ice" not in a and "ice_reference" not in a
    assert {"schema", "lake", "frames", "timeline"} <= set(a) and a["frames"]
    r = client.get("/api/archive/ice?lake=south_lhonak")
    if a["has_ice_layer"]:
        assert r.status_code == 200 and r.json()["perennial_ice"]["years"]
    else:
        assert r.status_code == 404
    assert client.get("/api/archive/ice?lake=imja_tsho").status_code == 404


def test_old_archive_without_ice_still_loads(tmp_path, monkeypatch):
    doc = json.loads(ARCHIVE.read_text())
    old = {k: v for k, v in doc.items() if k not in ("perennial_ice", "ice_reference")}
    (tmp_path / "south_lhonak.json").write_text(json.dumps(old))
    client = TestClient(create_app(Settings(_env_file=None, archive_dir=str(tmp_path))))
    assert client.get("/api/archive?lake=south_lhonak").json()["has_ice_layer"] is False
    assert client.get("/api/archive/ice?lake=south_lhonak").status_code == 404
