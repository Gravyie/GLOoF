"""Cross-account SNS drill: region parsing, message bodies, the three gates and the latch."""
import json

from fastapi.testclient import TestClient

from src.core.config import LAKES, Settings
from src.main import create_app
from src.services.alert_log import DynamoAlertLog
from src.services.demo_latch import DynamoLatch
from src.services.sns_service import (
    DRILL_PREFIX,
    build_drill_email,
    build_drill_sms,
    is_gsm7,
    topic_account,
    topic_region,
)

TOPIC = "arn:aws:sns:us-east-1:222233334444:glof-alerts"
KEY = {"X-API-Key": "k"}


class FakeSns:
    def __init__(self):
        self.calls = []

    def publish(self, **kw):
        self.calls.append(kw)
        return {"MessageId": f"msg-{len(self.calls)}"}


def live_settings(tmp_path, **kw):
    base = dict(alert_api_key="k", alert_dry_run=False, sim_live_sms=True, demo_sms_live=True,
                sns_topic_arn=TOPIC, local_persistence=True, data_dir=str(tmp_path))
    base.update(kw)
    return Settings(_env_file=None, **base)


def app_with_fake(settings):
    app = create_app(settings)
    fake = FakeSns()
    app.state.alerts._client = fake  # noqa: SLF001 - inject the SNS client
    return app, fake


def pending_evacuate(app):
    """What the Outburst night sim records at EVACUATE with auto-approve off."""
    recs = app.state.dispatcher.dispatch(LAKES["south_lhonak"], "EVACUATE", [{"level": "EVACUATE", "text": "test"}],
                                         source="simulation", sim_h=0.0, autopilot=False)
    return next(r["id"] for r in recs if r["audience"] == "public")


# --------------------------------------------------------------------------- messages
def test_region_and_account_parsed_from_topic_arn():
    assert topic_region(TOPIC) == "us-east-1" and topic_account(TOPIC) == "222233334444"
    assert topic_region("not-an-arn") is None


def test_drill_sms_is_short_gsm7_and_prefixed():
    for level in ("WATCH", "ADVISORY", "WARNING", "EVACUATE", "TEST"):
        sms = build_drill_sms(LAKES["south_lhonak"], level)
        assert len(sms) <= 160 and is_gsm7(sms) and sms.startswith(f"{DRILL_PREFIX}:")
    assert not is_gsm7("DRILL — em dash")  # the reason the prompt's em dash was replaced


def test_drill_email_is_prefixed_and_bilingual():
    email = build_drill_email(LAKES["south_lhonak"], "EVACUATE")
    assert email.startswith(DRILL_PREFIX) and "South Lhonak" in email and "निकासी" in email


# --------------------------------------------------------------------------- gates + latch
def test_gates_off_sends_nothing(tmp_path):
    app, fake = app_with_fake(live_settings(tmp_path, demo_sms_live=False))
    client = TestClient(app)
    rec = client.post(f"/api/alerts/{pending_evacuate(app)}/approve", headers=KEY).json()
    assert rec["status"] == "simulated" and fake.calls == []
    assert client.post("/api/notify/test", headers=KEY).status_code == 409 and fake.calls == []
    assert client.get("/api/system").json()["sns"]["mode"] == "simulated"


def test_latch_sends_once_across_refresh_seek_reset_and_restart(tmp_path):
    s = live_settings(tmp_path)
    app, fake = app_with_fake(s)
    client = TestClient(app)

    first = client.post(f"/api/alerts/{pending_evacuate(app)}/approve", headers=KEY).json()
    assert first["status"] == "sent" and first["message_ids"] == ["msg-1"] and first["accepted_at"]
    call = fake.calls[0]
    assert call["TopicArn"] == TOPIC and call["MessageStructure"] == "json"
    assert call["MessageAttributes"]["AWS.SNS.SMS.SMSType"]["StringValue"] == "Transactional"
    body = json.loads(call["Message"])
    assert set(body) == {"default", "sms", "email"} and body["sms"].startswith(f"{DRILL_PREFIX}:")

    # refresh + seek + reset: the sim records a new pending EVACUATE; approving it must not resend
    client.get("/api/sim/state")
    client.post("/api/sim/reset")
    again = client.post(f"/api/alerts/{pending_evacuate(app)}/approve", headers=KEY).json()
    assert again["status"] == "simulated" and "already sent" in again["note"] and len(fake.calls) == 1
    assert client.get("/api/system").json()["demo_sms"]["armed"] is False

    # restart: a new process on the same data dir
    app2, fake2 = app_with_fake(s)
    c2 = TestClient(app2)
    r = c2.post(f"/api/alerts/{pending_evacuate(app2)}/approve", headers=KEY).json()
    assert r["status"] == "simulated" and fake2.calls == []

    # only an operator re-arm allows another send
    assert c2.post("/api/demo-sms/rearm").status_code == 401
    assert c2.post("/api/demo-sms/rearm", headers=KEY).json()["armed"] is True
    r = c2.post(f"/api/alerts/{pending_evacuate(app2)}/approve", headers=KEY).json()
    assert r["status"] == "sent" and len(fake2.calls) == 1


def test_only_evacuate_drill_is_real(tmp_path):
    app, fake = app_with_fake(live_settings(tmp_path))
    client = TestClient(app)
    recs = app.state.dispatcher.dispatch(LAKES["south_lhonak"], "WARNING", [], source="simulation", autopilot=False)
    pid = next(r["id"] for r in recs if r["audience"] == "public")
    assert client.post(f"/api/alerts/{pid}/approve", headers=KEY).json()["status"] == "simulated"
    # history replay with every gate on: still simulated
    client.post("/api/archive/replay/seek?lake=south_lhonak&progress=1")
    alerts = client.get("/api/archive/replay/state?lake=south_lhonak").json()["alerts"]
    assert alerts and all(a["status"] == "simulated" for a in alerts)
    assert fake.calls == []


def test_send_test_and_system_report(tmp_path):
    app, fake = app_with_fake(live_settings(tmp_path))
    client = TestClient(app)
    r = client.post("/api/notify/test", headers=KEY).json()
    assert r["status"] == "accepted by SNS" and r["message_id"] == "msg-1"
    assert json.loads(fake.calls[0]["Message"])["sms"].startswith("DRILL - TEST ONLY: TEST")
    sysinfo = client.get("/api/system").json()
    assert sysinfo["sns"]["mode"] == "live" and sysinfo["sns"]["topic_account"] == "...4444"
    assert sysinfo["sns"]["region"] == "us-east-1" and "us-east-1" in sysinfo["sns"]["label"]


def test_failed_publish_gives_the_arming_back(tmp_path):
    app, _ = app_with_fake(live_settings(tmp_path))

    class Broken:
        def publish(self, **kw):
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "AuthorizationError", "Message": "no"}}, "Publish")

    app.state.alerts._client = Broken()  # noqa: SLF001
    r = TestClient(app).post(f"/api/alerts/{pending_evacuate(app)}/approve", headers=KEY).json()
    assert r["status"] == "failed" and app.state.latch.state()["armed"] is True


# --------------------------------------------------------------------------- DynamoDB backends
class FakeTable:
    """Just enough of a boto3 Table for the log and the latch."""

    def __init__(self):
        self.items = {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None, ReturnValues=None):
        from botocore.exceptions import ClientError

        cur = self.items.setdefault(Key["pk"], dict(Key))
        v = ExpressionAttributeValues
        if ConditionExpression and not ("armed" not in cur or cur["armed"] == v[":tr"]):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if UpdateExpression.startswith("ADD n"):
            cur["n"] = cur.get("n", 0) + v[":one"]
        elif "armed = :f" in UpdateExpression:
            cur.update(armed=v[":f"], sent_at=v[":t"])
            cur.pop("message_id", None)
        elif "message_id = :m" in UpdateExpression:
            cur["message_id"] = v[":m"]
        return {"Attributes": dict(cur)}

    def put_item(self, Item):
        self.items[Item["pk"]] = dict(Item)

    def get_item(self, Key):
        return {"Item": dict(self.items[Key["pk"]])} if Key["pk"] in self.items else {}

    def delete_item(self, Key):
        self.items.pop(Key["pk"], None)

    def scan(self, **kw):
        return {"Items": [dict(i) for i in self.items.values()]}


def test_dynamo_alert_log_and_latch():
    t = FakeTable()
    log = DynamoAlertLog(t)
    a = log.add(lake="south_lhonak", level="WATCH", source="simulation", status="simulated")
    b = log.add(lake="south_lhonak", level="EVACUATE", source="archive", status="pending_approval")
    assert (a["id"], b["id"]) == (1, 2)
    assert log.update(2, status="sent")["status"] == "sent"
    assert [r["id"] for r in log.list(lake="south_lhonak")] == [1, 2]
    log.clear(source="simulation")
    assert [r["id"] for r in log.list()] == [2]

    latch = DynamoLatch(t)
    assert latch.state()["armed"] is True
    assert latch.consume() is True and latch.consume() is False
    latch.record("msg-9")
    assert latch.state()["message_id"] == "msg-9"
    latch.rearm()
    assert latch.consume() is True
