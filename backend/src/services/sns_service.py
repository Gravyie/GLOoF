"""AWS SNS evacuation SMS: bilingual (English + local language), transactional.

Safety rails, because this can text real people:
  * dry-run is ON by default (message is built and returned, not sent)
  * the server setting wins: a request can force dry-run, never disable it
  * a per-lake cooldown blocks repeat sends
  * message text comes only from vetted templates - no free text
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from src.core.config import Lake, Settings

log = logging.getLogger(__name__)

E164 = re.compile(r"^\+[1-9]\d{7,14}$")

# Translations MUST be reviewed by native speakers / the district disaster
# management authority before real-world use.
TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "WARNING": "[GLOF WARNING] {lake}: abnormal lake growth detected. Stay alert, keep away from the river banks downstream and await official instructions.",
        "EVACUATE": "[GLOF EVACUATE NOW] {lake}: flood risk is critical. Move immediately to high ground away from the river. Follow official instructions.",
    },
    "hi": {
        "WARNING": "[GLOF चेतावनी] {lake}: झील के असामान्य विस्तार का पता चला है। सतर्क रहें, नदी किनारे से दूर रहें और सरकारी निर्देशों की प्रतीक्षा करें।",
        "EVACUATE": "[GLOF तुरंत निकासी] {lake}: बाढ़ का खतरा गंभीर है। तुरंत नदी से दूर ऊँचे स्थान पर जाएँ। सरकारी निर्देशों का पालन करें।",
    },
    "ne": {
        "WARNING": "[GLOF चेतावनी] {lake}: तालको असामान्य विस्तार पत्ता लागेको छ। सतर्क रहनुहोस्, नदी किनारबाट टाढा रहनुहोस् र सरकारी निर्देशनको प्रतीक्षा गर्नुहोस्।",
        "EVACUATE": "[GLOF तुरुन्त निकासी] {lake}: बाढीको जोखिम गम्भीर छ। तुरुन्तै नदीबाट टाढा उच्च स्थानमा जानुहोस्। सरकारी निर्देशनको पालना गर्नुहोस्।",
    },
}


# --------------------------------------------------------------------------- #
# Drill messages (cross-account topic, MessageStructure=json)
# --------------------------------------------------------------------------- #
# GSM 03.38 basic character set (+ the common extension chars). Anything outside
# it forces UCS-2 and drops one SMS from 160 to 70 characters.
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
           "¿abcdefghijklmnopqrstuvwxyzäöñüà")
SMS_MAX = 160
DRILL_PREFIX = "DRILL - TEST ONLY"
DRILL_ACTION = {
    "WATCH": "Stay informed. Follow official guidance.",
    "ADVISORY": "Be ready to move. Follow official guidance.",
    "WARNING": "Keep away from the river. Follow official guidance.",
    "EVACUATE": "Move to high ground away from the river. Follow official guidance.",
    "TEST": "Notification path check. No action needed.",
}


def is_gsm7(text: str) -> bool:
    return all(ch in GSM7 for ch in text)


def build_drill_sms(lake: Lake | None, level: str) -> str:
    name = lake.name if lake else "Glacial Lake Monitor"
    body = f"{DRILL_PREFIX}: {level} - {name}\n{DRILL_ACTION[level]}"
    if len(body) > SMS_MAX or not is_gsm7(body):
        raise AlertError("drill SMS must be <= 160 GSM-7 characters")
    return body


def build_drill_email(lake: Lake | None, level: str) -> str:
    head = f"{DRILL_PREFIX}\n\nThis is a drill of the Glacial Lake Monitor alert path. No real emergency.\n\n"
    if lake and level in TEMPLATES["en"]:
        return head + build_message(lake, level)
    return head + build_drill_sms(lake, level)


def topic_region(arn: str | None) -> str | None:
    """arn:aws:sns:<region>:<account>:<name> -> region (None if not an SNS ARN)."""
    parts = (arn or "").split(":")
    return parts[3] if len(parts) >= 6 and parts[2] == "sns" and parts[3] else None


def topic_account(arn: str | None) -> str | None:
    parts = (arn or "").split(":")
    return parts[4] if len(parts) >= 6 and parts[2] == "sns" else None


class AlertError(RuntimeError):
    pass


class AlertCooldownError(AlertError):
    pass


def build_message(lake: Lake, level: str) -> str:
    if level not in TEMPLATES["en"]:
        raise AlertError(f"unknown alert level: {level}")
    local = TEMPLATES[lake.alert_language][level].format(lake=lake.name)
    english = TEMPLATES["en"][level].format(lake=lake.name)
    return f"{english}\n{local}"


def estimate_segments(text: str) -> int:
    """Approximate SMS segment count. Any non-ASCII char forces UCS-2 (70/67)."""
    if text.isascii():
        single, multi = 160, 153
    else:
        single, multi = 70, 67
    n = len(text)
    return 1 if n <= single else -(-n // multi)


@dataclass
class AlertResult:
    dry_run: bool
    lake: str
    level: str
    message: str
    segments_estimate: int
    recipients: int
    message_ids: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


class SnsAlertService:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._s = settings
        self._client = client
        self._last_sent: dict[str, float] = {}
        self._lock = threading.Lock()

    def _sns(self):
        if self._client is None:
            # The topic may live in another account and region (the SMS account is
            # us-east-1): publish from that region, not the app's own.
            region = topic_region(self._s.sns_topic_arn) or self._s.aws_region
            self._client = boto3.client("sns", region_name=region)
        return self._client

    def publish_email(self, lake: Lake, level: str, reasons: list[dict], *, replay_date: str | None = None) -> str:
        """Email-only alert: audience=email, which the SMS subscriptions' filter policy
        (audience=public) excludes, so it costs no SMS budget. Returns the MessageId."""
        import json
        from datetime import datetime, timezone

        if not self._s.sns_topic_arn:
            raise AlertError("SNS_TOPIC_ARN is not configured")
        from src.services.email_alert import render_text

        when = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
        body = render_text(lake, level, reasons, replay_date, when)
        payload = {"default": body, "email": body}
        attrs = {"audience": {"DataType": "String", "StringValue": "email"},
                 "level": {"DataType": "String", "StringValue": level},
                 "lake": {"DataType": "String", "StringValue": lake.id}}
        subject = f"{DRILL_PREFIX}: GLOF {level} - {lake.name}"
        try:
            resp = self._sns().publish(TopicArn=self._s.sns_topic_arn, Message=json.dumps(payload),
                                       MessageStructure="json", Subject=subject[:100], MessageAttributes=attrs)
        except (ClientError, BotoCoreError) as exc:
            raise AlertError(f"SNS publish failed: {exc}") from exc
        return resp["MessageId"]

    def publish_drill(self, lake: Lake | None, level: str, *, audience: str = "public") -> str:
        """One drill message to the topic: short GSM-7 SMS, bilingual email. Returns the MessageId."""
        import json

        if not self._s.sns_topic_arn:
            raise AlertError("SNS_TOPIC_ARN is not configured")
        sms = build_drill_sms(lake, level)
        payload = {"default": sms, "sms": sms, "email": build_drill_email(lake, level)}
        attrs = {
            "AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"},
            "audience": {"DataType": "String", "StringValue": audience},
            "level": {"DataType": "String", "StringValue": level},
            "lake": {"DataType": "String", "StringValue": lake.id if lake else "all"},
        }
        subject = f"{DRILL_PREFIX}: GLOF {level}" + (f" - {lake.name}" if lake else "")
        try:
            resp = self._sns().publish(TopicArn=self._s.sns_topic_arn, Message=json.dumps(payload),
                                       MessageStructure="json", Subject=subject[:100], MessageAttributes=attrs)
        except (ClientError, BotoCoreError) as exc:
            raise AlertError(f"SNS publish failed: {exc}") from exc
        return resp["MessageId"]

    def _attributes(self) -> dict[str, dict[str, str]]:
        attrs = {"AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}}
        if self._s.sns_sender_id:
            attrs["AWS.SNS.SMS.SenderID"] = {"DataType": "String", "StringValue": self._s.sns_sender_id}
        if self._s.sns_entity_id:  # India DLT
            attrs["AWS.MM.SMS.EntityId"] = {"DataType": "String", "StringValue": self._s.sns_entity_id}
        if self._s.sns_template_id:
            attrs["AWS.MM.SMS.TemplateId"] = {"DataType": "String", "StringValue": self._s.sns_template_id}
        return attrs

    def publish_text(self, message: str, *, phones: list[str] | None = None, topic: str | None = None,
                     subject: str | None = None, attributes: dict[str, str] | None = None) -> list[str]:
        """Low-level send used by the dispatcher. Returns SNS message ids.

        ``attributes`` become String message attributes on the topic publish, so
        subscriptions can filter (e.g. officials only get audience=officials).
        ``subject`` is used by email subscribers (ASCII, <= 100 chars).
        """
        sns, attrs, ids = self._sns(), self._attributes(), []
        if topic:
            topic_attrs = dict(attrs)
            for k, v in (attributes or {}).items():
                if v:
                    topic_attrs[k] = {"DataType": "String", "StringValue": v}
            kw = {"Subject": subject.encode("ascii", "ignore").decode()[:100]} if subject else {}
            ids.append(sns.publish(TopicArn=topic, Message=message, MessageAttributes=topic_attrs, **kw)["MessageId"])
        for phone in phones or []:
            if not E164.match(phone):
                raise AlertError(f"invalid E.164 number: ...{phone[-4:]}")
            ids.append(sns.publish(PhoneNumber=phone, Message=message, MessageAttributes=attrs)["MessageId"])
        return ids

    def verify_topic(self) -> dict[str, Any]:
        """Read the topic's attributes: proves credentials + topic exist without sending."""
        if not self._s.sns_topic_arn:
            raise AlertError("SNS_TOPIC_ARN is not configured")
        try:
            a = self._sns().get_topic_attributes(TopicArn=self._s.sns_topic_arn)["Attributes"]
        except (ClientError, BotoCoreError) as exc:
            raise AlertError(f"cannot read SNS topic: {exc}") from exc
        return {"topic": self._s.sns_topic_arn,
                "subscriptions_confirmed": int(a.get("SubscriptionsConfirmed", 0)),
                "subscriptions_pending": int(a.get("SubscriptionsPending", 0))}

    def _check_cooldown(self, lake_id: str) -> None:
        with self._lock:
            last = self._last_sent.get(lake_id)
            wait = self._s.alert_cooldown_seconds - (time.monotonic() - last) if last else 0
            if wait > 0:
                raise AlertCooldownError(f"alert cooldown active, retry in {int(wait)}s")

    def send_alert(self, lake: Lake, level: str, *, force_dry_run: bool = False) -> AlertResult:
        message = build_message(lake, level)
        dry = self._s.alert_dry_run or force_dry_run
        topic = self._s.sns_topic_arn
        phones = self._s.phone_list

        bad = [p for p in phones if not E164.match(p)]
        if bad:
            raise AlertError(f"invalid E.164 number(s) in config: {', '.join(bad)}")
        if not topic and not phones:
            raise AlertError("no recipients: set SNS_TOPIC_ARN or ALERT_PHONE_NUMBERS")

        recipients = 1 if topic else len(phones)
        result = AlertResult(
            dry_run=dry, lake=lake.id, level=level, message=message,
            segments_estimate=estimate_segments(message), recipients=recipients,
        )
        if dry:
            log.info("DRY RUN alert for %s (%s)", lake.id, level)
            return result

        self._check_cooldown(lake.id)
        sns = self._sns()
        attrs = self._attributes()
        try:
            if topic:
                resp = sns.publish(TopicArn=topic, Message=message, MessageAttributes=attrs)
                result.message_ids.append(resp["MessageId"])
            else:
                for phone in phones:
                    try:
                        resp = sns.publish(PhoneNumber=phone, Message=message, MessageAttributes=attrs)
                        result.message_ids.append(resp["MessageId"])
                    except (ClientError, BotoCoreError) as exc:
                        log.error("SNS publish to %s failed: %s", phone[-4:], exc)
                        result.failed.append(f"...{phone[-4:]}")
        except (ClientError, BotoCoreError) as exc:
            raise AlertError(f"SNS publish failed: {exc}") from exc

        if not result.message_ids:
            raise AlertError("SNS rejected every recipient")
        with self._lock:
            self._last_sent[lake.id] = time.monotonic()
        return result
