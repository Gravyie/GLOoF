"""FastAPI entry point.  Run from backend/:  uvicorn src.main:app --reload --port 8000"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.core.cache import TTLStore
from src.core.config import Settings, get_settings
from src.core.config import LAKES
from src.services.alert_log import AlertLog
from src.services.dispatch import AlertDispatcher
from src.services.simulation import SimulationRunner
from src.services.sns_service import SnsAlertService
from src.services.stac_client import StacSceneFinder
from src.services.telemetry import TelemetryService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = FastAPI(title="Glacial Threat Monitor", version="1.0.0")
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
    app.state.alert_log = AlertLog()
    app.state.dispatcher = AlertDispatcher(settings, app.state.alerts, app.state.alert_log)
    # Simulation is dry-run unless SIM_LIVE_SMS=true AND ALERT_DRY_RUN=false.
    live = settings.sim_live_sms and not settings.alert_dry_run
    app.state.sim = SimulationRunner(LAKES["south_lhonak"], app.state.dispatcher, live_sms=live)

    app.include_router(router)
    return app


app = create_app()
