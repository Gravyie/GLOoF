"""Demo SMS latch: the real drill SMS goes out at most once per History replay run.

Starting the History replay from the beginning arms it; reaching the trigger
level (DRILL_TRIGGER_LEVEL, default WARNING) consumes it. Page refreshes,
seeking back and forth, and a server restart mid-run never resend. A cooldown
(DRILL_COOLDOWN_SECONDS) also blocks a second SMS shortly after the last one,
even if a new run starts. State is persisted (local JSON file, or a DynamoDB
item on AWS) and ``consume()`` is atomic, so two triggers cannot both send.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.alert_log import BACKEND_DIR, dynamo_table

ARMED = {"armed": True, "sent_at": None, "sent_epoch": None, "message_id": None}


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

    def consume(self, cooldown_s: float = 0) -> tuple[bool, str]:
        """(True, "") exactly once per arming and outside the cooldown: the caller may send."""
        with self._lock:
            st = self.state()
            if not st["armed"]:
                return False, "already sent in this run"
            if st.get("sent_epoch") and time.time() - st["sent_epoch"] < cooldown_s:
                return False, f"cooldown: last drill SMS {int(time.time() - st['sent_epoch'])} s ago"
            self._write({"armed": False, "sent_at": _now(), "sent_epoch": time.time(), "message_id": None})
            return True, ""

    def record(self, message_id: str) -> None:
        with self._lock:
            self._write({**self.state(), "message_id": message_id})

    def rearm(self) -> dict[str, Any]:
        """Arm for the next send; keeps the last send time so the cooldown still applies."""
        with self._lock:
            self._write({**self.state(), "armed": True})
            return self.state()

    def give_back(self) -> None:
        """A publish failed, so nothing went out: re-arm and drop the cooldown stamp."""
        with self._lock:
            self._write({**self.state(), "armed": True, "sent_at": None, "sent_epoch": None})


class DynamoLatch:
    KEY = {"pk": "latch#demo_sms"}

    def __init__(self, table: Any) -> None:
        self._t = table

    def state(self) -> dict[str, Any]:
        item = self._t.get_item(Key=self.KEY).get("Item")
        if not item:
            return dict(ARMED)
        ep = item.get("sent_epoch")
        return {"armed": bool(item.get("armed", True)), "sent_at": item.get("sent_at"),
                "sent_epoch": float(ep) if ep is not None else None, "message_id": item.get("message_id")}

    def consume(self, cooldown_s: float = 0) -> tuple[bool, str]:
        from decimal import Decimal

        from botocore.exceptions import ClientError

        now = time.time()
        try:  # conditional: only one caller can flip armed -> false, and only outside the cooldown
            self._t.update_item(
                Key=self.KEY,
                UpdateExpression="SET armed = :f, sent_at = :t, sent_epoch = :e REMOVE message_id",
                ConditionExpression="(attribute_not_exists(armed) OR armed = :tr) AND "
                                    "(attribute_not_exists(sent_epoch) OR sent_epoch < :cut)",
                ExpressionAttributeValues={":f": False, ":tr": True, ":t": _now(),
                                           ":e": Decimal(str(now)), ":cut": Decimal(str(now - cooldown_s))})
            return True, ""
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
        st = self.state()
        if not st["armed"]:
            return False, "already sent in this run"
        return False, f"cooldown: last drill SMS {int(now - (st['sent_epoch'] or now))} s ago"

    def record(self, message_id: str) -> None:
        self._t.update_item(Key=self.KEY, UpdateExpression="SET message_id = :m",
                            ExpressionAttributeValues={":m": message_id})

    def rearm(self) -> dict[str, Any]:
        self._t.update_item(Key=self.KEY, UpdateExpression="SET armed = :tr", ExpressionAttributeValues={":tr": True})
        return self.state()

    def give_back(self) -> None:
        self._t.update_item(Key=self.KEY, UpdateExpression="SET armed = :tr REMOVE sent_at, sent_epoch",
                            ExpressionAttributeValues={":tr": True})


def make_latch(settings: Any) -> FileLatch | DynamoLatch:
    if settings.alerts_table:
        return DynamoLatch(dynamo_table(settings.alerts_table, settings.aws_region))
    if not settings.local_persistence:
        return FileLatch(None)
    data = Path(settings.data_dir)
    return FileLatch((data if data.is_absolute() else BACKEND_DIR / data) / "demo_sms_latch.json")
