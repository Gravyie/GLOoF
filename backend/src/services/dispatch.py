"""Alert dispatch: maps a risk level to audiences + messages, records every
decision in the AlertLog, and (only when explicitly live) sends via AWS SNS.

Policy:
  WATCH     -> operators (internal)
  ADVISORY  -> district officials (internal)
  WARNING   -> officials + public (bilingual), public needs operator approval
  EVACUATE  -> officials + public (bilingual), public needs operator approval
``autopilot=True`` (demo) skips the approval step. Nothing is ever sent for real
unless ``live`` is True, which callers derive from server-side settings only.
"""
from __future__ import annotations

import logging
from typing import Any

from src.core.config import Lake, Settings
from src.services.alert_log import AlertLog
from src.services.sns_service import AlertError, SnsAlertService, build_message

log = logging.getLogger(__name__)


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


DEMO_RECIPIENTS = {"operators": 3, "officials": 5, "public": 120}
AUDIENCE_LABEL = {"operators": "Control-room operators", "officials": "District officials",
                  "public": "Downstream residents"}


def internal_message(lake: Lake, level: str, reasons: list[dict]) -> str:
    top = "; ".join(r["text"] for r in reasons if r["level"] in (level, "WATCH", "ADVISORY", "WARNING"))[:110]
    action = {"WATCH": "Monitor closely.", "ADVISORY": "Verify and prepare.",
              "WARNING": "Confirm and alert public.", "EVACUATE": "Evacuation recommended."}[level]
    return f"[GLOF {level}] {lake.name}: {top}. {action}"


class AlertDispatcher:
    def __init__(self, settings: Settings, sns: SnsAlertService, alert_log: AlertLog, latch: Any = None) -> None:
        self._s, self._sns, self._log, self._latch = settings, sns, alert_log, latch

    def _phones(self, audience: str) -> list[str]:
        s = self._s
        return {"operators": s.operator_phones, "officials": s.official_phones, "public": s.phone_list}[audience]

    def _deliver(self, audience: str, message: str, live: bool, *, level: str = "", lake: Lake | None = None
                 ) -> tuple[str, list[str], int]:
        phones = self._phones(audience)
        # One topic for every audience; subscriptions pick theirs with an SNS
        # filter policy on the "audience" attribute (see infra/terraform).
        topic = self._s.sns_topic_arn
        if live and (phones or topic):
            try:
                subject = f"[GLOF {level}] {lake.name}" if lake and level else "GLOF alert"
                ids = self._sns.publish_text(message, phones=phones, topic=topic, subject=subject,
                                             attributes={"audience": audience, "level": level,
                                                         "lake": lake.id if lake else ""})
                return "sent", ids, len(phones) + (1 if topic else 0)
            except (AlertError, Exception) as exc:  # never let delivery crash the monitor
                log.error("delivery to %s failed: %s", audience, exc)
                return "failed", [], len(phones)
        return "simulated", [], len(phones) or DEMO_RECIPIENTS[audience]

    def _plan(self, lake: Lake, level: str, reasons: list[dict]) -> list[tuple[str, str]]:
        internal = internal_message(lake, level, reasons) if level != "NORMAL" else ""
        if level == "WATCH":
            return [("operators", internal)]
        if level == "ADVISORY":
            return [("officials", internal)]
        if level in ("WARNING", "EVACUATE"):
            return [("officials", internal), ("public", build_message(lake, level))]
        return []

    def dispatch(self, lake: Lake, level: str, reasons: list[dict], *, source: str,
                 sim_h: float | None = None, autopilot: bool = True, live: bool = False,
                 meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        out = []
        for audience, message in self._plan(lake, level, reasons):
            needs_approval = audience == "public" and not autopilot
            if needs_approval:
                status, ids, n = "pending_approval", [], len(self._phones(audience)) or DEMO_RECIPIENTS[audience]
            else:
                status, ids, n = self._deliver(audience, message, live, level=level, lake=lake)
            out.append(self._log.add(
                lake=lake.id, level=level, audience=audience, audience_label=AUDIENCE_LABEL[audience],
                recipients=n, message=message, status=status, message_ids=ids,
                source=source, sim_h=sim_h, channel=self.channel(), live=live, **(meta or {})))
        return out

    def channel(self) -> str:
        if self._s.sns_topic_arn:
            return f"AWS SNS topic {self._s.sns_topic_arn.rsplit(':', 1)[-1]}"
        return "SMS via AWS SNS"

    def approve(self, alert_id: int, approver: str, live: bool = False) -> dict[str, Any] | None:
        """Operator approval of a pending public alert.

        The only route from a simulation to a real phone is the demo drill: an
        EVACUATE public alert from the Outburst night sim, with all three gates
        set, consuming the persisted latch. Everything else stays simulated.
        """
        rec = self._log.get(alert_id)
        if not rec or rec["status"] != "pending_approval":
            return None
        from src.core.config import LAKES

        lake = LAKES.get(rec["lake"])
        if rec.get("source") == "simulation" and rec["level"] == "EVACUATE" and rec["audience"] == "public":
            return self._approve_drill(rec, approver, lake)
        status, ids, n = self._deliver(rec["audience"], rec["message"], live, level=rec["level"], lake=lake)
        return self._log.update(alert_id, status=status, message_ids=ids, recipients=n, approved_by=approver)

    def _approve_drill(self, rec: dict[str, Any], approver: str, lake: Lake | None) -> dict[str, Any] | None:
        base = {"approved_by": approver}
        if not self._s.drill_live or self._latch is None:
            return self._log.update(rec["id"], status="simulated", note="practice mode: real SMS needs all three gates", **base)
        if not self._latch.consume():
            return self._log.update(rec["id"], status="simulated", note="drill already sent; re-arm to send again", **base)
        try:
            mid = self.send_drill(lake, "EVACUATE")
        except Exception as exc:  # nothing went out: give the arming back so the operator can retry
            log.error("drill publish failed: %s", exc)
            self._latch.rearm()
            return self._log.update(rec["id"], status="failed", note=str(exc)[:200], **base)
        self._latch.record(mid)
        return self._log.update(rec["id"], status="sent", message_ids=[mid], accepted_at=_utcnow(),
                                channel=self.drill_channel(), live=True, recipients=1, **base)

    def send_drill(self, lake: Lake | None, level: str) -> str:
        """One real drill publish (callers check the gates). Returns the SNS MessageId."""
        return self._sns.publish_drill(lake, level)

    def drill_channel(self) -> str:
        from src.services.sns_service import topic_account, topic_region

        acct = topic_account(self._s.sns_topic_arn) or ""
        return f"Amazon SNS (account ...{acct[-4:]}, {topic_region(self._s.sns_topic_arn)})"

    def reject(self, alert_id: int, approver: str) -> dict[str, Any] | None:
        rec = self._log.get(alert_id)
        if not rec or rec["status"] != "pending_approval":
            return None
        return self._log.update(alert_id, status="rejected", approved_by=approver)
