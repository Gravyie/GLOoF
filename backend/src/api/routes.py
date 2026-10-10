"""REST endpoints. Handlers are sync `def` on purpose: rasterio/boto3 block,
so FastAPI runs them in its threadpool instead of stalling the event loop."""
from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException, Query, Request

from src.api.schemas import AlertRequest, AlertResponse
from src.core.config import LAKES, UnknownLakeError, get_lake
from src.services.geospatial import GeospatialError
from src.services.sns_service import AlertCooldownError, AlertError
from src.services.stac_client import StacError
from src.services.telemetry import NoUsableSceneError, lake_summary

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _cache_path(lake_id: str) -> Path:
    return DATA_DIR / f"telemetry_{lake_id}.json"


def _require_operator(request: Request, key: str | None) -> None:
    expected = request.app.state.settings.alert_api_key
    if not expected:
        raise HTTPException(503, "operator actions disabled: ALERT_API_KEY is not configured")
    if not key or not secrets.compare_digest(key, expected):
        raise HTTPException(401, "invalid API key")


def _lake_or_404(lake_id: str):
    try:
        return get_lake(lake_id)
    except UnknownLakeError:
        raise HTTPException(404, f"unknown lake '{lake_id}'. Options: {', '.join(LAKES)}")


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/lakes")
def lakes():
    return [lake_summary(lake) for lake in LAKES.values()]


@router.get("/telemetry")
def telemetry(
    request: Request,
    lake: str = Query("south_lhonak"),
    refresh: bool = Query(False, description="bypass the cache"),
):
    """Latest clear-sky water polygons (GeoJSON) + scene metadata, computed live."""
    lk = _lake_or_404(lake)
    cache = _cache_path(lk.id)
    try:
        data = request.app.state.telemetry.get_telemetry(lk, refresh=refresh)
        try:  # keep a copy so the dashboard still works offline (e.g. on stage Wi-Fi)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data))
        except OSError:
            log.warning("could not write telemetry cache")
        return data
    except (NoUsableSceneError, StacError, GeospatialError) as exc:
        if cache.exists():
            data = json.loads(cache.read_text())
            data["stale"] = True
            data["stale_reason"] = str(exc)
            return data
        if isinstance(exc, NoUsableSceneError):
            raise HTTPException(503, str(exc))
        log.exception("telemetry failed")
        raise HTTPException(502, f"upstream data error: {exc}")


@router.get("/timeseries")
def timeseries(
    request: Request,
    lake: str = Query("south_lhonak"),
    n: int = Query(6, ge=2, le=8),
    refresh: bool = Query(False),
):
    """Water-area history over recent clear scenes + a heuristic growth flag."""
    lk = _lake_or_404(lake)
    try:
        return request.app.state.telemetry.get_timeseries(lk, n, refresh=refresh)
    except (StacError, GeospatialError) as exc:
        log.exception("timeseries failed")
        raise HTTPException(502, f"upstream data error: {exc}")


@router.post("/alert", response_model=AlertResponse)
def alert(
    body: AlertRequest,
    request: Request,
    x_api_key: str | None = Header(default=None),
):
    """Send (or dry-run) the bilingual evacuation SMS. Requires X-API-Key."""
    settings = request.app.state.settings
    if not settings.alert_api_key:
        raise HTTPException(503, "alerting disabled: ALERT_API_KEY is not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.alert_api_key):
        raise HTTPException(401, "invalid API key")
    lk = _lake_or_404(body.lake)
    try:
        result = request.app.state.alerts.send_alert(lk, body.level, force_dry_run=body.dry_run)
    except AlertCooldownError as exc:
        raise HTTPException(429, str(exc))
    except AlertError as exc:
        raise HTTPException(502, str(exc))
    return AlertResponse(**asdict(result))


# --------------------------------------------------------------------------- #
# Alerts log + approval, and the outburst replay simulation
# --------------------------------------------------------------------------- #
@router.get("/alerts")
def alerts(request: Request, lake: str | None = None, after_id: int = 0):
    """Audit log of every alert decision (no phone numbers)."""
    return request.app.state.alert_log.list(lake=lake, after_id=after_id)


@router.post("/alerts/{alert_id}/approve")
def approve_alert(alert_id: int, request: Request, x_api_key: str | None = Header(default=None)):
    _require_operator(request, x_api_key)
    st = request.app.state
    live = st.settings.sim_live_sms and not st.settings.alert_dry_run
    rec = st.dispatcher.approve(alert_id, "operator", live)
    if rec is None:
        raise HTTPException(404, "no pending alert with that id")
    return rec


@router.post("/alerts/{alert_id}/reject")
def reject_alert(alert_id: int, request: Request, x_api_key: str | None = Header(default=None)):
    _require_operator(request, x_api_key)
    rec = request.app.state.dispatcher.reject(alert_id, "operator")
    if rec is None:
        raise HTTPException(404, "no pending alert with that id")
    return rec


@router.get("/sim/state")
def sim_state(request: Request, after_id: int = 0):
    return request.app.state.sim.state(after_id)


@router.post("/sim/start")
def sim_start(request: Request, speed: float = 1.0, autopilot: bool = True):
    request.app.state.sim.start(speed, autopilot)
    return request.app.state.sim.state()


@router.post("/sim/pause")
def sim_pause(request: Request):
    request.app.state.sim.pause()
    return request.app.state.sim.state()


@router.post("/sim/speed")
def sim_speed(request: Request, speed: float):
    request.app.state.sim.set_speed(speed)
    return request.app.state.sim.state()


@router.post("/sim/reset")
def sim_reset(request: Request):
    request.app.state.sim.reset()
    return request.app.state.sim.state()


# --------------------------------------------------------------------------- #
# Boundary archive (2017 -> today) + its replay
# --------------------------------------------------------------------------- #
def _replay_or_404(request: Request, lake: str):
    rp = request.app.state.replays.get(_lake_or_404(lake).id)
    if rp is None:
        raise HTTPException(404, f"no boundary archive for '{lake}'. Build it: python -m scripts.build_archive --lake {lake}")
    return rp


@router.get("/archive")
def archive(request: Request, lake: str = Query("south_lhonak")):
    """Every archived outline + the per-frame risk timeline, in one response
    (the view renders as one verdict, so it is never assembled from N calls)."""
    st = request.app.state
    lk = _lake_or_404(lake)
    doc = st.archives.get(lk.id)
    if not doc:
        raise HTTPException(404, f"no boundary archive for '{lake}'. Build it: python -m scripts.build_archive --lake {lake}")
    # The yearly ice layer is large and optional: served separately by /api/archive/ice,
    # so this response keeps exactly its original shape for existing consumers.
    base = {k: v for k, v in doc.items() if k not in ICE_KEYS}
    base["lake"] = {**doc["lake"], "ice_bbox": list(lk.ice_bbox) if lk.ice_bbox else None}  # additive
    base["has_ice_layer"] = bool(doc.get("perennial_ice"))
    return {**base, "timeline": st.timelines[lk.id], "store": st.archive_store.describe}


ICE_KEYS = ("perennial_ice", "ice_reference")


@router.get("/archive/ice")
def archive_ice(request: Request, lake: str = Query("south_lhonak")):
    """Yearly perennial-ice composites, loss/gain vs the baseline year, and the RGI reference."""
    lk = _lake_or_404(lake)
    doc = request.app.state.archives.get(lk.id)
    if not doc or not doc.get("perennial_ice"):
        raise HTTPException(404, f"no perennial-ice layer for '{lake}'. Build it: "
                                 f"python -m scripts.build_archive --lake {lake} --ice-only")
    lake_meta = {**doc["lake"], "ice_bbox": list(lk.ice_bbox) if lk.ice_bbox else None}
    return {"lake": lake_meta, **{k: doc.get(k) for k in ICE_KEYS}}


@router.get("/archive/replay/state")
def replay_state(request: Request, lake: str = Query("south_lhonak")):
    return _replay_or_404(request, lake).state()


@router.post("/archive/replay/start")
def replay_start(request: Request, lake: str = Query("south_lhonak"), speed: float = 1.0, autopilot: bool = True):
    rp = _replay_or_404(request, lake)
    rp.start(speed, autopilot)
    return rp.state()


@router.post("/archive/replay/pause")
def replay_pause(request: Request, lake: str = Query("south_lhonak")):
    rp = _replay_or_404(request, lake)
    rp.pause()
    return rp.state()


@router.post("/archive/replay/speed")
def replay_speed(request: Request, speed: float, lake: str = Query("south_lhonak")):
    rp = _replay_or_404(request, lake)
    rp.set_speed(speed)
    return rp.state()


@router.post("/archive/replay/seek")
def replay_seek(request: Request, lake: str = Query("south_lhonak"),
                progress: float | None = Query(None, ge=0, le=1), date: str | None = None):
    rp = _replay_or_404(request, lake)
    if progress is None and date is None:
        raise HTTPException(422, "give progress (0-1) or date (YYYY-MM-DD)")
    try:
        rp.seek(progress=progress, day=date)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")
    return rp.state()


@router.post("/archive/replay/reset")
def replay_reset(request: Request, lake: str = Query("south_lhonak")):
    rp = _replay_or_404(request, lake)
    rp.reset()
    return rp.state()


@router.post("/archive/reload")
def archive_reload(request: Request, x_api_key: str | None = Header(default=None)):
    """Re-read archives from the store (e.g. after the ingest Lambda appended a scene)."""
    _require_operator(request, x_api_key)
    from src.main import load_archives

    return {"loaded": load_archives(request.app)}


# --------------------------------------------------------------------------- #
# System status: every integration states its real mode (no unlabeled stand-ins)
# --------------------------------------------------------------------------- #
@router.get("/system")
def system(request: Request):
    st = request.app.state
    s = st.settings
    if s.sns_live and s.sns_topic_arn:
        sns = {"mode": "live", "label": f"live: SNS topic {s.sns_topic_arn.rsplit(':', 1)[-1]}", "topic": s.sns_topic_arn}
    elif s.sns_live and (s.phone_list or s.operator_phones or s.official_phones):
        sns = {"mode": "live", "label": "live: SNS direct SMS", "topic": None}
    else:
        why = "ALERT_DRY_RUN=true" if s.alert_dry_run else "SIM_LIVE_SMS=false" if not s.sim_live_sms else "no topic/recipients"
        sns = {"mode": "simulated", "label": f"simulated delivery ({why})", "topic": s.sns_topic_arn}
    return {
        "sns": sns,
        "archive": {"store": st.archive_store.describe, "lakes": {k: len(v["frames"]) for k, v in st.archives.items()}},
        "imagery": "Sentinel-2 L2A via AWS Earth Search (AWS Open Data)",
        "terrain": "Terrarium elevation tiles (AWS Open Data)",
        "operator_actions": bool(s.alert_api_key),
    }


@router.post("/notify/test")
def notify_test(request: Request, x_api_key: str | None = Header(default=None)):
    """Prove the SNS path end to end: verify the topic, then publish one test
    message to operators. Refuses (409) rather than pretending when not live."""
    _require_operator(request, x_api_key)
    st = request.app.state
    if not st.settings.sns_live or not st.settings.sns_topic_arn:
        raise HTTPException(409, "SNS is in simulated mode: set SNS_TOPIC_ARN, SIM_LIVE_SMS=true, ALERT_DRY_RUN=false")
    try:
        info = st.alerts.verify_topic()
        ids = st.alerts.publish_text(
            "[GLOF TEST] Glacial Lake Monitor: notification path check. No action needed.",
            topic=st.settings.sns_topic_arn, subject="[GLOF TEST] notification path check",
            attributes={"audience": "operators", "level": "TEST", "lake": "all"})
    except AlertError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:  # botocore credential errors etc.
        raise HTTPException(502, f"SNS publish failed: {exc}")
    return {**info, "message_ids": ids}
