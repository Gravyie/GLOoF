"""HTML alert email sent through Amazon SES.

SNS email is plain text only, so the designed alert (red danger banner, clear
English / Hindi / Nepali sections) goes through SES. Table layout with inline
styles: the only thing that renders consistently in Gmail, Outlook and phone
mail apps. A plain-text part is always included for clients that block HTML.

Translations are written for this demo and must be reviewed by native speakers
and the district disaster management authority before real use; the footer
says so.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from src.core.config import Lake, Settings
from src.services.sns_service import DRILL_PREFIX, TEMPLATES, AlertError

LEVEL_COLOR = {"WATCH": "#b7791f", "ADVISORY": "#c05621", "WARNING": "#c53030", "EVACUATE": "#9b2c2c"}
LANGS = [("en", "English", "en"), ("hi", "हिन्दी (Hindi)", "hi"), ("ne", "नेपाली (Nepali)", "ne")]
ACTIONS = {
    "en": ["Keep away from the river banks downstream.", "Keep phones charged and listen for official updates.",
           "Be ready to move to high ground if told to."],
    "hi": ["नदी के किनारों से दूर रहें।", "फ़ोन चार्ज रखें और सरकारी सूचनाओं पर ध्यान दें।",
           "निर्देश मिलने पर ऊँचे स्थान पर जाने के लिए तैयार रहें।"],
    "ne": ["नदी किनारबाट टाढा रहनुहोस्।", "फोन चार्ज राख्नुहोस् र सरकारी सूचना सुन्नुहोस्।",
           "निर्देशन आएमा उच्च स्थानमा जान तयार रहनुहोस्।"],
}


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _localised(lake: Lake, level: str, lang: str) -> str:
    t = TEMPLATES.get(lang, {}).get(level)
    return t.format(lake=lake.name) if t else ""


def _nice_date(d: str | None) -> str:
    try:
        return datetime.fromisoformat(d).strftime("%d %b %Y") if d else "-"
    except ValueError:
        return d or "-"


def subject(lake: Lake, level: str) -> str:
    return f"{DRILL_PREFIX}: GLOF {level} - {lake.name}"


def render_text(lake: Lake, level: str, reasons: list[dict], replay_date: str | None, sent_at: str) -> str:
    bar = "=" * 60
    lines = [bar, f"  {DRILL_PREFIX}  |  GLOF {level}  |  {lake.name}", bar, "",
             f"Lake: {lake.name}, {lake.country}",
             f"Satellite record: {_nice_date(replay_date)}",
             f"Sent: {sent_at}", "", "Why this alert:"]
    lines += [f"  - {r['text']}" for r in reasons] or ["  - risk level reached"]
    for code, label, _ in LANGS:
        msg = _localised(lake, level, code)
        if not msg:
            continue
        lines += ["", "-" * 60, f"  {label.upper() if code == 'en' else label}", "-" * 60, msg]
        lines += [f"  * {a}" for a in ACTIONS[code]]
    lines += ["", bar, "This is a drill of the Glacial Lake Monitor alert system. No real emergency.",
              "Translations to be reviewed by native speakers before real use.", bar]
    return "\n".join(lines)


def render_html(lake: Lake, level: str, reasons: list[dict], replay_date: str | None, sent_at: str) -> str:
    red = LEVEL_COLOR.get(level, "#c53030")
    reason_rows = "".join(
        f'<tr><td style="padding:6px 0;color:#2d3748;font-size:14px;line-height:20px;">'
        f'<span style="color:{red};font-weight:700;">&#9679;</span>&nbsp; {_esc(r["text"])}</td></tr>'
        for r in reasons) or '<tr><td style="padding:6px 0;font-size:14px;">Risk level reached.</td></tr>'

    sections = []
    for code, label, lang_attr in LANGS:
        msg = _localised(lake, level, code)
        if not msg:
            continue
        acts = "".join(f'<li style="margin:0 0 4px 0;">{_esc(a)}</li>' for a in ACTIONS[code])
        sections.append(f"""
        <tr><td style="padding:0 28px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:0 0 16px 0;border:1px solid #e2e8f0;border-left:4px solid {red};border-radius:6px;">
            <tr><td style="padding:10px 16px;background:#fff5f5;border-bottom:1px solid #fed7d7;font-size:12px;font-weight:700;letter-spacing:0.5px;color:{red};">{_esc(label)}</td></tr>
            <tr><td lang="{lang_attr}" style="padding:14px 16px;font-size:15px;line-height:24px;color:#1a202c;">
              {_esc(msg)}
              <ul style="margin:10px 0 0 18px;padding:0;font-size:14px;line-height:22px;color:#2d3748;">{acts}</ul>
            </td></tr>
          </table>
        </td></tr>""")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(subject(lake, level))}</title></head>
<body style="margin:0;padding:0;background:#edf2f7;font-family:-apple-system,'Segoe UI',Roboto,'Noto Sans','Noto Sans Devanagari',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#edf2f7;padding:24px 12px;">
<tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #e2e8f0;">

    <tr><td style="background:#1a202c;color:#ffffff;padding:8px 28px;font-size:12px;font-weight:700;letter-spacing:1px;">
      {_esc(DRILL_PREFIX)} &nbsp;&middot;&nbsp; NOT A REAL EMERGENCY
    </td></tr>

    <tr><td style="background:{red};padding:22px 28px;color:#ffffff;">
      <div style="font-size:13px;font-weight:600;opacity:0.9;letter-spacing:0.5px;">GLACIAL LAKE OUTBURST FLOOD ALERT</div>
      <div style="font-size:28px;font-weight:800;line-height:34px;margin-top:4px;">&#9888; {_esc(level)}</div>
      <div style="font-size:16px;margin-top:2px;">{_esc(lake.name)}, {_esc(lake.country)}</div>
    </td></tr>

    <tr><td style="padding:20px 28px 8px 28px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:13px;color:#4a5568;">
        <tr>
          <td style="padding:0 12px 0 0;"><div style="color:#718096;">Satellite record</div><div style="font-size:15px;color:#1a202c;font-weight:600;">{_esc(_nice_date(replay_date))}</div></td>
          <td style="padding:0 12px;"><div style="color:#718096;">Risk level</div><div style="display:inline-block;margin-top:2px;padding:2px 10px;border-radius:12px;background:#fff5f5;color:{red};font-weight:700;border:1px solid #feb2b2;">{_esc(level)}</div></td>
          <td style="padding:0 0 0 12px;"><div style="color:#718096;">Sent</div><div style="font-size:15px;color:#1a202c;font-weight:600;">{_esc(sent_at)}</div></td>
        </tr>
      </table>
    </td></tr>

    <tr><td style="padding:12px 28px 4px 28px;font-size:13px;font-weight:700;color:#718096;letter-spacing:0.5px;">WHY THIS ALERT</td></tr>
    <tr><td style="padding:0 28px 16px 28px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{reason_rows}</table></td></tr>

    <tr><td style="padding:4px 28px 10px 28px;font-size:13px;font-weight:700;color:#718096;letter-spacing:0.5px;">MESSAGE FOR RESIDENTS</td></tr>
    {''.join(sections)}

    <tr><td style="padding:8px 28px 22px 28px;font-size:12px;line-height:18px;color:#718096;border-top:1px solid #edf2f7;">
      This is a drill of the Glacial Lake Monitor alert system, sent by Amazon SES when the satellite history replay reached {_esc(level)}.
      Translations are drafts and must be reviewed by native speakers and the district disaster management authority before real use.
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


class SesEmailSender:
    """Sends the HTML alert through SES v2. Recipients come from ALERT_EMAILS; in the SES
    sandbox each recipient (and the sender) must be a verified identity."""

    def __init__(self, settings: Settings, client: Any = None) -> None:
        self._s = settings
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self._s.ses_sender and self._s.alert_email_list)

    def _ses(self):
        if self._client is None:
            import boto3

            session = boto3.Session(profile_name=self._s.ses_profile) if self._s.ses_profile else boto3.Session()
            self._client = session.client("sesv2", region_name=self._s.ses_region or self._s.aws_region)
        return self._client

    def send(self, lake: Lake, level: str, reasons: list[dict], replay_date: str | None) -> str:
        sent_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
        try:
            r = self._ses().send_email(
                FromEmailAddress=f"Glacial Lake Monitor <{self._s.ses_sender}>",
                Destination={"ToAddresses": self._s.alert_email_list},
                Content={"Simple": {
                    "Subject": {"Data": subject(lake, level), "Charset": "UTF-8"},
                    "Body": {"Html": {"Data": render_html(lake, level, reasons, replay_date, sent_at), "Charset": "UTF-8"},
                             "Text": {"Data": render_text(lake, level, reasons, replay_date, sent_at), "Charset": "UTF-8"}},
                }},
            )
        except Exception as exc:  # botocore ClientError / credential errors
            raise AlertError(f"SES send failed: {exc}") from exc
        return r["MessageId"]
