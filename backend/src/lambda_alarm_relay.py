"""AWS Lambda: relay CloudWatch alarm state changes to the cross-account SNS topic.

Alarms live in the main account's region; the alert topic lives in the SMS account
in us-east-1. Rather than rely on a cross-region alarm action, an EventBridge rule
(main region) forwards "CloudWatch Alarm State Change" events here, and this
function publishes a short operational message to the topic (region parsed from
the ARN). Same container image as the ingest Lambda, different handler.
"""
from __future__ import annotations

import json
import logging
import os

import boto3

from src.services.sns_service import topic_region

log = logging.getLogger()
log.setLevel(logging.INFO)


def message_for(event: dict) -> tuple[str, str]:
    d = event.get("detail", {})
    name = d.get("alarmName", "unknown alarm")
    state = d.get("state", {}).get("value", "UNKNOWN")
    reason = (d.get("state", {}).get("reason") or "")[:300]
    sms = f"[GLOF MONITORING] {name} is {state}. Check the dashboard."[:160]
    email = f"[GLOF MONITORING] {name} is {state}\n\n{reason}\n\nRegion: {event.get('region')}\nTime: {event.get('time')}"
    return sms, email


def handler(event, context):  # noqa: ARG001 - Lambda signature
    topic = os.environ["SNS_TOPIC_ARN"]
    sms, email = message_for(event)
    sns = boto3.client("sns", region_name=topic_region(topic))
    resp = sns.publish(
        TopicArn=topic, MessageStructure="json", Subject=sms[:100],
        Message=json.dumps({"default": sms, "sms": sms, "email": email}),
        MessageAttributes={"AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"},
                           "audience": {"DataType": "String", "StringValue": "operators"}})
    log.info("relayed %s -> %s", event.get("detail", {}).get("alarmName"), resp["MessageId"])
    return {"message_id": resp["MessageId"]}
