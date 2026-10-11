"""Demo SMS latch: the real drill alert goes out at most once per arming.

Page refreshes, seeks, sim resets and server restarts must never resend; only
an operator re-arm (POST /api/demo-sms/rearm) allows another send. The latch is
persisted (local JSON file, or a DynamoDB item on AWS) and ``consume()`` is
atomic, so two concurrent approvals cannot both send.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.alert_log import BACKEND_DIR, dynamo_table

ARMED = {"armed": True, "sent_at": None, "message_id": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FileLatch:
    _lock = threading.Lock()  # one process; the rename keeps the file itself consistent

    def __init__(self, path: str | Path | None) -> None:
        self._p = Path(path) if path else None  # None: in memory (tests, throwaway processes)
        self._mem: dict[str, Any] | None = None

    def state(self) -> dict[str, Any]:
        if self._p is None:
            return dict(self._mem or ARMED)
        try:
            return {**ARMED, **json.loads(self._p.read_text())}
        except (OSError, ValueError):
            return dict(ARMED)  # never used: starts armed

    def _write(self, st: dict[str, Any]) -> None:
        if self._p is None:
            self._mem = dict(st)
            return
        self._p.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        tmp.replace(self._p)

    def consume(self) -> bool:
        """True exactly once per arming: the caller may send."""
        with self._lock:
            if not self.state()["armed"]:
                return False
            self._write({"armed": False, "sent_at": _now(), "message_id": None})
            return True

    def record(self, message_id: str) -> None:
        with self._lock:
            self._write({**self.state(), "message_id": message_id})

    def rearm(self) -> dict[str, Any]:
        with self._lock:
            self._write(dict(ARMED))
            return self.state()


class DynamoLatch:
    KEY = {"pk": "latch#demo_sms"}

    def __init__(self, table: Any) -> None:
        self._t = table

    def state(self) -> dict[str, Any]:
        item = self._t.get_item(Key=self.KEY).get("Item")
        if not item:
            return dict(ARMED)
        return {"armed": bool(item.get("armed", True)), "sent_at": item.get("sent_at"), "message_id": item.get("message_id")}

    def consume(self) -> bool:
        from botocore.exceptions import ClientError

        try:  # conditional: only one caller can flip armed -> false
            self._t.update_item(
                Key=self.KEY,
                UpdateExpression="SET armed = :f, sent_at = :t REMOVE message_id",
                ConditionExpression="attribute_not_exists(armed) OR armed = :tr",
                ExpressionAttributeValues={":f": False, ":tr": True, ":t": _now()})
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def record(self, message_id: str) -> None:
        self._t.update_item(Key=self.KEY, UpdateExpression="SET message_id = :m",
                            ExpressionAttributeValues={":m": message_id})

    def rearm(self) -> dict[str, Any]:
        self._t.put_item(Item={**self.KEY, "armed": True})
        return self.state()


def make_latch(settings: Any) -> FileLatch | DynamoLatch:
    if settings.alerts_table:
        return DynamoLatch(dynamo_table(settings.alerts_table, settings.aws_region))
    if not settings.local_persistence:
        return FileLatch(None)
    data = Path(settings.data_dir)
    return FileLatch((data if data.is_absolute() else BACKEND_DIR / data) / "demo_sms_latch.json")
