"""FastAPI entry point.  Run from backend/:  uvicorn src.main:app --reload --port 8000"""
from __future__ import annotations

import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from src.api.routes import router
from src.core.cache import TTLStore
from src.core.config import Settings, get_settings
from src.core.config import LAKES
from src.services.alert_log import make_alert_log
from src.services.demo_latch import make_latch
from src.services.archive import make_store
from src.services.history import analyse_archive
from src.services.history_replay import HistoryReplay, Ticker
from src.services.dispatch import AlertDispatcher
from src.services.simulation import SimulationRunner
from src.services.sns_service import SnsAlertService
from src.services.stac_client import StacSceneFinder
from src.services.telemetry import TelemetryService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ticker = Ticker(lambda: list(app.state.replays.values()))
        ticker.start()
        yield
        ticker.stop()

    app = FastAPI(title="Glacial Threat Monitor", version="2.0.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=2048)  # the archive is ~2.5 MB of GeoJSON
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    # Composition root: services are built once and shared via app.state.
    app.state.settings = settings
    app.state.telemetry = TelemetryService(
        settings, StacSceneFinder(settings), TTLStore(settings.cache_ttl_seconds)
    )
    app.state.alerts = SnsAlertService(settings)
    app.state.alert_log = make_alert_log(settings)
    app.state.latch = make_latch(settings)
    from src.services.email_alert import SesEmailSender

    app.state.ses = SesEmailSender(settings)
    app.state.dispatcher = AlertDispatcher(settings, app.state.alerts, app.state.alert_log, app.state.latch,
                                           app.state.ses)
    # Replays deliver through the dispatcher as "simulated". The one exception is the
    # demo drill: the History replay reaching DRILL_TRIGGER_LEVEL sends one real SMS
    # (three gates, latched once per run, cooldown).
    app.state.sim = SimulationRunner(LAKES["south_lhonak"], app.state.dispatcher, live_sms=False)

    # Boundary archive (local JSON or S3) -> risk timeline -> server-side replay.
    app.state.archive_store = make_store(settings)
    app.state.archives, app.state.timelines, app.state.replays = {}, {}, {}
    load_archives(app)

    app.include_router(router)

    # Serve the built frontend from the same origin when it is bundled into the image
    # (deployments without CloudFront). /api/* routes are registered first, so they win.
    static = Path(__file__).resolve().parents[1] / "static"
    if (static / "index.html").exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=static, html=True), name="site")
    return app


def load_archives(app: FastAPI) -> list[str]:
    """(Re)load every lake's archive from the store. Safe to call at runtime."""
    st = app.state
    loaded = []
    for lake in LAKES.values():
        try:
            doc = st.archive_store.load(lake.id)
        except Exception:  # S3 unreachable: keep whatever is already loaded
            logging.getLogger(__name__).exception("archive load failed for %s", lake.id)
            continue
        if not doc or not doc.get("frames"):
            continue
        timeline = analyse_archive(doc)
        st.archives[lake.id], st.timelines[lake.id] = doc, timeline
        if lake.id in st.replays:
            st.replays[lake.id].load(timeline)
        else:
            st.replays[lake.id] = HistoryReplay(lake, timeline, st.dispatcher, live=False,
                                                duration_s=st.settings.replay_duration_s,
                                                on_new_run=st.latch.rearm)
        loaded.append(lake.id)
    return loaded


app = create_app()
