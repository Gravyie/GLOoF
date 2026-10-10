"""Boundary maths, archive risk timeline and the archive replay's alert firing."""
import time

from src.core.config import LAKES, Settings
from src.services.alert_log import AlertLog
from src.services.archive import drop_outliers, thin_frames
from src.services.boundary import advance_retreat, frozen_fraction, lake_body, to_metric
from src.services.dispatch import AlertDispatcher
from src.services.history import analyse_archive
from src.services.history_replay import HistoryReplay
from src.services.sns_service import SnsAlertService

LON, LAT = 88.196, 27.911
DEG_M = 1 / 98_000  # ~1 m in degrees at this latitude (x); good enough for tests


def square(half_m, cx=LON, cy=LAT, stretch_east_m=0.0):
    h = half_m * DEG_M
    e = (half_m + stretch_east_m) * DEG_M
    return {"type": "Polygon", "coordinates": [[[cx - h, cy - h], [cx + e, cy - h], [cx + e, cy + h], [cx - h, cy + h], [cx - h, cy - h]]]}


def frame(date, area_ha, geom=None, frozen=0.0):
    geom = geom or square(500)
    return {"date": date, "scene": f"S2_{date}", "stats": {"lake_area_m2": area_ha * 1e4, "ice_area_m2": 0.0,
                                                           "frozen_fraction": frozen, "cloud_fraction": 0.0},
            "lake": {"type": "Feature", "properties": {}, "geometry": geom},
            "water": {"type": "FeatureCollection", "features": []}, "ice": None}


def doc(frames):
    return {"lake": {"id": "south_lhonak", "utm_epsg": 32645}, "frames": frames}


def test_advance_measures_how_far_the_shoreline_moved():
    base = to_metric(square(500), 32645)
    grown = to_metric(square(500, stretch_east_m=200), 32645)
    adv, ret = advance_retreat(grown, base)
    assert 180 < adv < 230 and ret == 0.0


def test_lake_body_strips_inflow_stream():
    lake = to_metric(square(500), 32645)
    stream = to_metric(square(5, cx=LON + 900 * DEG_M), 32645).buffer(0)
    from shapely.geometry import LineString
    thin = LineString([(lake.bounds[2], lake.centroid.y), (lake.bounds[2] + 1500, lake.centroid.y)]).buffer(4)
    assert lake_body(lake.union(thin).union(stream)).bounds[2] < lake.bounds[2] + 5


def test_frozen_lake_is_not_drainage():
    ice = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": square(500)}]}
    assert frozen_fraction(square(500), ice, 32645) > 0.95


def test_outliers_and_thinning():
    fr = [frame(f"2020-10-{d:02d}", a) for d, a in [(1, 150), (3, 151), (5, 20), (12, 152), (14, 153), (30, 154)]]
    kept = drop_outliers(fr)
    assert all(f["stats"]["lake_area_m2"] > 100e4 for f in kept)
    assert [f["date"] for f in thin_frames(kept, 8)] == ["2020-10-01", "2020-10-12", "2020-10-30"]


def test_timeline_escalates_on_sustained_growth_without_lookahead():
    frames = [frame(f"2018-10-{d:02d}", 150) for d in (1, 10, 20)]
    frames += [frame(f"{y}-10-{d:02d}", 150 * g, square(500, stretch_east_m=60 * (y - 2018)))
               for y, g in [(2019, 1.04), (2020, 1.12), (2021, 1.2), (2022, 1.3)] for d in (5, 15, 25)]
    tl = analyse_archive(doc(frames))
    levels = [f["level"] for f in tl["frames"]]
    assert levels[:3] == ["NORMAL"] * 3
    assert "WARNING" in levels and levels.index("WARNING") >= 12  # only once growth is critical
    ups = [e for e in tl["events"] if e["escalation"]]
    assert ups and ups[0]["to"] == "WATCH"
    # adding a future frame never changes the past
    tl2 = analyse_archive(doc(frames + [frame("2023-10-05", 400)]))
    assert [f["level"] for f in tl2["frames"][:-1]] == levels


def test_replay_fires_alerts_as_it_passes_and_rearms_on_rewind():
    frames = [frame(f"2018-10-{d:02d}", 150) for d in (1, 10, 20)]
    frames += [frame(f"{y}-10-{d:02d}", 150 * g) for y, g in [(2020, 1.3), (2021, 1.32)] for d in (5, 15, 25)]
    tl = analyse_archive(doc(frames))
    s = Settings(_env_file=None)
    log = AlertLog()
    rp = HistoryReplay(LAKES["south_lhonak"], tl, AlertDispatcher(s, SnsAlertService(s), log), duration_s=1.0)
    assert rp.state()["alerts"] == []
    rp.seek(progress=1.0)
    fired = rp.state()["alerts"]
    assert fired and all(a["status"] == "simulated" and a["source"] == "archive" for a in fired)
    assert any(a["level"] == "WARNING" and a["audience"] == "public" for a in fired)
    rp.seek(progress=0.0)
    assert rp.state()["alerts"] == []  # re-armed
    rp.start(speed=16)
    time.sleep(0.15)
    rp.tick()
    assert rp.state()["finished"] and rp.state()["alerts"]
