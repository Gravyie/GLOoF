"""AWS Lambda: scheduled ingest of new Sentinel-2 scenes + automatic SNS alerts.

Triggered by EventBridge (daily). For every monitored lake:
  1. load the boundary archive from S3
  2. analyse Sentinel-2 L2A scenes newer than the last frame (AWS Open Data COGs)
  3. append the new outlines and save the archive back to S3
  4. re-run the risk timeline; if the latest level is HIGHER than the level we
     last notified about, dispatch through the same AlertDispatcher -> SNS topic
  5. persist the notified level in S3 (state/<lake>.json) so a level is
     announced once, not every day

Event overrides (all optional):
  {"lakes": ["south_lhonak"], "dry_run": true, "force_level": "WARNING"}
``force_level`` exists to rehearse the notification path without waiting for a
real escalation; it is written into the message as a drill.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.core.config import LAKES, get_settings
from src.services.alert_log import AlertLog
from src.services.archive import ArchiveBuilder, make_store
from src.services.dispatch import AlertDispatcher
from src.services.history import analyse_archive
from src.services.risk import RANK
from src.services.sns_service import SnsAlertService
from src.services.stac_client import StacSceneFinder

log = logging.getLogger()
log.setLevel(logging.INFO)


def _state_key(lake_id: str) -> str:
    return f"state/{lake_id}"


def run(event: dict[str, Any] | None = None) -> dict[str, Any]:
    event = event or {}
    s = get_settings()
    store = make_store(s)
    builder = ArchiveBuilder(s, StacSceneFinder(s))
    dispatcher = AlertDispatcher(s, SnsAlertService(s), AlertLog())
    # Real notifications only when explicitly enabled for the Lambda (off by default, so
    # a fresh deployment cannot text anyone on its first run).
    live = (s.lambda_live_alerts and not event.get("dry_run", False) and not s.alert_dry_run
            and bool(s.sns_topic_arn))
    report: dict[str, Any] = {"ran_at": datetime.now(timezone.utc).isoformat(), "live_sns": live, "lakes": {}}

    for lake_id in event.get("lakes") or [k for k in LAKES if store.load(k) is not None] or ["south_lhonak"]:
        lake = LAKES[lake_id]
        doc, fresh = builder.update(lake, store.load(lake.id))
        if fresh:
            store.save(lake.id, doc)
        tl = analyse_archive(doc)
        latest = tl["frames"][-1] if tl["frames"] else None
        state = store.load(_state_key(lake.id)) or {"notified_level": "NORMAL"}
        level = event.get("force_level") or (latest["level"] if latest else "NORMAL")
        reasons = latest["reasons"] if latest else []
        if event.get("force_level"):
            reasons = [{"level": level, "text": "DRILL: forced level to test the notification path"}] + reasons

        sent: list[dict[str, Any]] = []
        if RANK[level] > RANK[state["notified_level"]]:
            sent = dispatcher.dispatch(lake, level, reasons, source="lambda", autopilot=True, live=live,
                                       meta={"date": latest["date"] if latest else None})
            if not event.get("force_level"):
                state = {"notified_level": level, "at": report["ran_at"], "frame_date": latest["date"] if latest else None}
                store.save(_state_key(lake.id), state)
        elif RANK[level] < RANK[state["notified_level"]] and RANK[state["notified_level"]] < RANK["WARNING"]:
            # step down silently below WARNING (WARNING/EVACUATE need an operator to clear)
            store.save(_state_key(lake.id), {**state, "notified_level": level})

        report["lakes"][lake.id] = {
            "new_frames": [f["date"] for f in fresh],
            "frames_total": len(doc["frames"]),
            "latest_date": latest["date"] if latest else None,
            "level": level,
            "notified_level": state["notified_level"],
            "alerts": [{"audience": a["audience"], "status": a["status"], "message_ids": a["message_ids"]} for a in sent],
        }
    log.info(json.dumps(report))
    emit_success_metric()
    return report


def emit_success_metric() -> None:
    """CloudWatch Embedded Metric Format: one IngestSuccess datapoint per completed run.
    The 'no successful ingest in 48 h' alarm watches for its absence. Printing EMF to
    stdout needs no extra IAM permission (it goes through the function's log group)."""
    import time

    print(json.dumps({
        "_aws": {"Timestamp": int(time.time() * 1000),
                 "CloudWatchMetrics": [{"Namespace": "GLOF", "Dimensions": [[]],
                                        "Metrics": [{"Name": "IngestSuccess", "Unit": "Count"}]}]},
        "IngestSuccess": 1,
    }))


def handler(event, context):  # noqa: ARG001 - Lambda signature
    return run(event if isinstance(event, dict) else {})
