"""Alert audit log: every alert decision (no phone numbers).

Two backends with one interface:
  * ``AlertLog`` - in memory, optionally mirrored to a JSON file (local ``./run.sh``)
  * ``DynamoAlertLog`` - DynamoDB table (AWS); ids come from an atomic counter
``make_alert_log(settings)`` picks DynamoDB when ``ALERTS_TABLE`` is set.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AlertLog:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._items: list[dict[str, Any]] = []
        self._next = 1
        self._lock = threading.Lock()
        if self._path and self._path.exists():
            try:
                self._items = json.loads(self._path.read_text())
                self._next = max((r["id"] for r in self._items), default=0) + 1
            except (OSError, ValueError, KeyError):
                self._items = []

    def _persist(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._items))
        tmp.replace(self._path)

    def add(self, **fields: Any) -> dict[str, Any]:
        with self._lock:
            rec = {"id": self._next, "ts": _now(), **fields}
            self._next += 1
            self._items.append(rec)
            self._persist()
            return dict(rec)

    def update(self, alert_id: int, **fields: Any) -> dict[str, Any] | None:
        with self._lock:
            for rec in self._items:
                if rec["id"] == alert_id:
                    rec.update(fields)
                    self._persist()
                    return dict(rec)
        return None

    def get(self, alert_id: int) -> dict[str, Any] | None:
        with self._lock:
            return next((dict(r) for r in self._items if r["id"] == alert_id), None)

    def list(self, *, lake: str | None = None, source: str | None = None, after_id: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._items
                    if r["id"] > after_id and (lake is None or r["lake"] == lake)
                    and (source is None or r.get("source") == source)]

    def discard(self, pred: Callable[[dict[str, Any]], bool]) -> int:
        """Remove every record for which ``pred(record)`` is true. Returns the count."""
        with self._lock:
            before = len(self._items)
            self._items = [r for r in self._items if not pred(r)]
            self._persist()
            return before - len(self._items)

    def clear(self, *, source: str | None = None) -> None:
        with self._lock:
            self._items = [r for r in self._items if source is not None and r.get("source") != source]
            self._persist()


def _plain(v: Any) -> Any:
    """DynamoDB returns Decimals; give callers plain ints/floats."""
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, Decimal):
        return int(v) if v == int(v) else float(v)
    return v


def _dyn(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, list):
        return [_dyn(x) for x in v]
    if isinstance(v, dict):
        return {k: _dyn(x) for k, x in v.items() if x is not None}
    return v


class DynamoAlertLog:
    """Same interface as AlertLog, stored in one DynamoDB table (pk = "alert#<id>").

    Volumes are tiny (a handful of alerts per replay), so list/clear use a scan.
    """

    COUNTER = {"pk": "counter#alerts"}

    def __init__(self, table: Any) -> None:
        self._t = table

    def _next_id(self) -> int:
        r = self._t.update_item(Key=self.COUNTER, UpdateExpression="ADD n :one",
                                ExpressionAttributeValues={":one": 1}, ReturnValues="UPDATED_NEW")
        return int(r["Attributes"]["n"])

    def _all(self) -> list[dict[str, Any]]:
        items, kw = [], {}
        while True:
            r = self._t.scan(**kw)
            items += [_plain(i) for i in r.get("Items", []) if str(i.get("pk", "")).startswith("alert#")]
            if "LastEvaluatedKey" not in r:
                return items
            kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    @staticmethod
    def _out(item: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in item.items() if k != "pk"}

    def add(self, **fields: Any) -> dict[str, Any]:
        rec = {"id": self._next_id(), "ts": _now(), **fields}
        self._t.put_item(Item=_dyn({"pk": f"alert#{rec['id']}", **rec}))
        return rec

    def update(self, alert_id: int, **fields: Any) -> dict[str, Any] | None:
        cur = self.get(alert_id)
        if cur is None:
            return None
        cur.update(fields)
        self._t.put_item(Item=_dyn({"pk": f"alert#{alert_id}", **cur}))
        return cur

    def get(self, alert_id: int) -> dict[str, Any] | None:
        r = self._t.get_item(Key={"pk": f"alert#{alert_id}"})
        return self._out(_plain(r["Item"])) if "Item" in r else None

    def list(self, *, lake: str | None = None, source: str | None = None, after_id: int = 0) -> list[dict[str, Any]]:
        out = [self._out(i) for i in self._all()
               if i["id"] > after_id and (lake is None or i.get("lake") == lake)
               and (source is None or i.get("source") == source)]
        return sorted(out, key=lambda r: r["id"])

    def discard(self, pred: Callable[[dict[str, Any]], bool]) -> int:
        gone = [i for i in self._all() if pred(self._out(i))]
        for i in gone:
            self._t.delete_item(Key={"pk": i["pk"]})
        return len(gone)

    def clear(self, *, source: str | None = None) -> None:
        self.discard(lambda r: source is None or r.get("source") == source)


def dynamo_table(name: str, region: str | None = None) -> Any:
    import boto3

    return boto3.resource("dynamodb", region_name=region).Table(name)


def make_alert_log(settings: Any) -> AlertLog | DynamoAlertLog:
    if settings.alerts_table:
        return DynamoAlertLog(dynamo_table(settings.alerts_table, settings.aws_region))
    if not settings.local_persistence:
        return AlertLog()
    data = Path(settings.data_dir)
    return AlertLog((data if data.is_absolute() else BACKEND_DIR / data) / "alert_log.json")
