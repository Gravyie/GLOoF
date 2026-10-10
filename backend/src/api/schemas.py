"""Request/response models for the REST API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AlertRequest(BaseModel):
    lake: str
    level: Literal["WARNING", "EVACUATE"] = "WARNING"
    # Can only force a dry run. The server's ALERT_DRY_RUN can't be overridden.
    dry_run: bool = False


class AlertResponse(BaseModel):
    dry_run: bool
    lake: str
    level: str
    message: str
    segments_estimate: int
    recipients: int
    message_ids: list[str]
    failed: list[str]
