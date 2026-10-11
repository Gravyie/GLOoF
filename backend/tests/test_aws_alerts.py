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


R = "/api/archive/replay"
Q = "?lake=south_lhonak"


def run_history(client):
    """Start a fresh History run and play it through to the end (passes the 2023 Warning)."""
    client.post(f"{R}/reset{Q}")
    client.post(f"{R}/start{Q}&speed=1")
    client.post(f"{R}/seek{Q}&progress=1")
    return client.get(f"{R}/state{Q}").json()["alerts"]


def public_warning(alerts):
    return next(a for a in alerts if a["audience"] == "public" and a["level"] == "WARNING")


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
    w = public_warning(run_history(client))
    assert w["status"] == "simulated" and fake.calls == []
    assert client.post("/api/notify/test", headers=KEY).status_code == 409 and fake.calls == []
    assert client.get("/api/system").json()["sns"]["mode"] == "simulated"


def test_history_warning_sends_one_sms_automatically(tmp_path):
    app, fake = app_with_fake(live_settings(tmp_path))
    client = TestClient(app)
    alerts = run_history(client)
    w = public_warning(alerts)
    assert w["status"] == "sent" and w["message_ids"] == ["msg-1"] and w["accepted_at"] and "us-east-1" in w["channel"]
    assert len(fake.calls) == 1  # only the Warning public alert is real; Watch/Advisory/officials stay simulated
    assert all(a["status"] == "simulated" for a in alerts if a is not w and a["id"] != w["id"])
    call = fake.calls[0]
    assert call["TopicArn"] == TOPIC and call["MessageStructure"] == "json"
    assert call["MessageAttributes"]["AWS.SNS.SMS.SMSType"]["StringValue"] == "Transactional"
    body = json.loads(call["Message"])
    assert set(body) == {"default", "sms", "email"} and body["sms"].startswith("DRILL - TEST ONLY: WARNING")


def test_no_resend_on_refresh_seek_or_restart(tmp_path):
    s = live_settings(tmp_path)
    app, fake = app_with_fake(s)
    client = TestClient(app)
    run_history(client)
    assert len(fake.calls) == 1
    # refresh (polling) and seeking back then forward again: same run, no resend
    client.get(f"{R}/state{Q}")
    client.post(f"{R}/seek{Q}&progress=0")
    client.post(f"{R}/seek{Q}&progress=1")
    w = public_warning(client.get(f"{R}/state{Q}").json()["alerts"])
    assert w["status"] == "simulated" and "already sent" in w["note"] and len(fake.calls) == 1
    # server restart: new process, same persisted latch; replaying without a new run sends nothing
    app2, fake2 = app_with_fake(s)
    c2 = TestClient(app2)
    c2.post(f"{R}/seek{Q}&progress=1")
    assert fake2.calls == []


def test_new_run_sends_again_only_after_cooldown(tmp_path):
    app, fake = app_with_fake(live_settings(tmp_path, drill_cooldown_seconds=300))
    client = TestClient(app)
    run_history(client)
    w = public_warning(run_history(client))  # a new run straight away
    assert w["status"] == "simulated" and "cooldown" in w["note"] and len(fake.calls) == 1

    app2, fake2 = app_with_fake(live_settings(tmp_path / "b", drill_cooldown_seconds=0))
    c2 = TestClient(app2)
    run_history(c2)
    run_history(c2)
    assert len(fake2.calls) == 2  # no cooldown: one SMS per run


def test_flood_night_never_sends_for_real(tmp_path):
    app, fake = app_with_fake(live_settings(tmp_path))
    client = TestClient(app)
    rec = client.post(f"/api/alerts/{pending_evacuate(app)}/approve", headers=KEY).json()
    assert rec["status"] == "simulated" and fake.calls == []


def test_send_test_and_system_report(tmp_path):
    app, fake = app_with_fake(live_settings(tmp_path))
    client = TestClient(app)
    r = client.post("/api/notify/test", headers=KEY).json()
    assert r["status"] == "accepted by SNS" and r["message_id"] == "msg-1"
    assert json.loads(fake.calls[0]["Message"])["sms"].startswith("DRILL - TEST ONLY: TEST")
    sysinfo = client.get("/api/system").json()
    assert sysinfo["sns"]["mode"] == "live" and sysinfo["sns"]["topic_account"] == "...4444"
    assert sysinfo["sns"]["region"] == "us-east-1" and "WARNING" in sysinfo["sns"]["trigger"]


def test_failed_publish_gives_the_arming_back(tmp_path):
    app, _ = app_with_fake(live_settings(tmp_path))

    class Broken:
        def publish(self, **kw):
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "AuthorizationError", "Message": "no"}}, "Publish")

    app.state.alerts._client = Broken()  # noqa: SLF001
    w = public_warning(run_history(TestClient(app)))
    assert w["status"] == "failed" and app.state.latch.state()["armed"] is True


# --------------------------------------------------------------------------- DynamoDB backends
class FakeTable:
    """Just enough of a boto3 Table for the log and the latch."""

    def __init__(self):
        self.items = {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None, ReturnValues=None):
        from botocore.exceptions import ClientError

        cur = self.items.setdefault(Key["pk"], dict(Key))
        v = ExpressionAttributeValues
        if ConditionExpression:
            armed_ok = "armed" not in cur or cur["armed"] == v[":tr"]
            cool_ok = "sent_epoch" not in cur or cur["sent_epoch"] < v[":cut"]
            if not (armed_ok and cool_ok):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if UpdateExpression.startswith("ADD n"):
            cur["n"] = cur.get("n", 0) + v[":one"]
        elif "armed = :f" in UpdateExpression:
            cur.update(armed=v[":f"], sent_at=v[":t"], sent_epoch=v[":e"])
            cur.pop("message_id", None)
        elif "message_id = :m" in UpdateExpression:
            cur["message_id"] = v[":m"]
        elif UpdateExpression.startswith("SET armed = :tr"):
            cur["armed"] = True
            if "REMOVE" in UpdateExpression:
                cur.pop("sent_at", None)
                cur.pop("sent_epoch", None)
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
    assert latch.consume(300)[0] is True
    ok, why = latch.consume(300)
    assert not ok and "already sent" in why
    latch.record("msg-9")
    assert latch.state()["message_id"] == "msg-9"
    latch.rearm()
    ok, why = latch.consume(300)
    assert not ok and "cooldown" in why  # armed again, but inside the cooldown
    assert latch.consume(0)[0] is True
    latch.give_back()
    assert latch.state()["armed"] is True and latch.state()["sent_epoch"] is None
