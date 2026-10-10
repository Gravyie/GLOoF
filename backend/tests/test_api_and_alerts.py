import pytest
from fastapi.testclient import TestClient

from src.core.config import LAKES, Settings
from src.main import create_app
from src.services.sns_service import (
    AlertCooldownError,
    SnsAlertService,
    build_message,
    estimate_segments,
)
from src.services.telemetry import NoUsableSceneError, assess_trend


class FakeSns:
    def __init__(self):
        self.calls = []

    def publish(self, **kw):
        self.calls.append(kw)
        return {"MessageId": f"id-{len(self.calls)}"}


def settings(**kw):
    base = dict(alert_api_key="secret", alert_dry_run=True, alert_phone_numbers="+919800000000")
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_message_is_bilingual_per_lake():
    msg = build_message(LAKES["south_lhonak"], "EVACUATE")
    assert "EVACUATE" in msg and "South Lhonak" in msg and "निकासी" in msg
    assert "तुरुन्त" in build_message(LAKES["imja_tsho"], "EVACUATE")
    assert estimate_segments(msg) >= 2


def test_dry_run_never_calls_sns():
    fake = FakeSns()
    res = SnsAlertService(settings(), client=fake).send_alert(LAKES["imja_tsho"], "WARNING")
    assert res.dry_run and fake.calls == []


def test_live_send_is_transactional_with_cooldown():
    fake = FakeSns()
    svc = SnsAlertService(settings(alert_dry_run=False), client=fake)
    res = svc.send_alert(LAKES["imja_tsho"], "EVACUATE")
    assert not res.dry_run and res.message_ids == ["id-1"]
    attrs = fake.calls[0]["MessageAttributes"]
    assert attrs["AWS.SNS.SMS.SMSType"]["StringValue"] == "Transactional"
    with pytest.raises(AlertCooldownError):
        svc.send_alert(LAKES["imja_tsho"], "EVACUATE")


def test_request_cannot_disable_server_dry_run():
    fake = FakeSns()
    res = SnsAlertService(settings(), client=fake).send_alert(LAKES["imja_tsho"], "WARNING", force_dry_run=False)
    assert res.dry_run and not fake.calls


def test_assess_trend():
    s = lambda v, d: {"water_area_m2": v, "acquired": f"2026-0{d}-01T00:00:00+00:00"}
    assert assess_trend([s(100, 1), s(105, 2), s(130, 3)], 10, 25)["level"] == "CRITICAL"
    assert assess_trend([s(100, 1), s(104, 2), s(112, 3)], 10, 25)["level"] == "ELEVATED"
    assert assess_trend([s(100, 1), s(100, 2), s(101, 3)], 10, 25)["level"] == "NORMAL"
    assert assess_trend([s(100, 1)], 10, 25)["level"] == "INSUFFICIENT_DATA"


class FakeTelemetry:
    def get_telemetry(self, lake, refresh=False):
        raise NoUsableSceneError("all candidate scenes cloudy")


@pytest.fixture
def client():
    app = create_app(settings())
    app.state.telemetry = FakeTelemetry()
    return TestClient(app)


def test_lakes_and_unknown_lake(client):
    assert {l["id"] for l in client.get("/api/lakes").json()} == set(LAKES)
    assert client.get("/api/telemetry?lake=nope").status_code == 404


def test_no_usable_scene_maps_to_503(client):
    r = client.get("/api/telemetry?lake=imja_tsho")
    assert r.status_code == 503 and "cloudy" in r.json()["detail"]


def test_alert_auth(client):
    body = {"lake": "imja_tsho", "level": "WARNING"}
    assert client.post("/api/alert", json=body).status_code == 401
    assert client.post("/api/alert", json=body, headers={"X-API-Key": "bad"}).status_code == 401
    ok = client.post("/api/alert", json=body, headers={"X-API-Key": "secret"})
    assert ok.status_code == 200 and ok.json()["dry_run"] is True


def test_alert_disabled_without_key():
    app = create_app(settings(alert_api_key=None))
    r = TestClient(app).post("/api/alert", json={"lake": "imja_tsho"}, headers={"X-API-Key": "x"})
    assert r.status_code == 503


def test_implausible_growth_is_unreliable_not_critical():
    """Regression: spring-ice -> summer-open-water must not read as CRITICAL."""
    mk = lambda v, d, ice=None: {"water_area_m2": v, "acquired": f"2026-0{d}-01T00:00:00+00:00", "ice_area_m2": ice}
    t = assess_trend([mk(2000, 4), mk(90000, 6), mk(125000, 9)], 10, 25, 100)
    assert t["level"] == "UNRELIABLE" and t["growth_pct"] > 1000
    # ice change is reported alongside
    t = assess_trend([mk(100, 6, 500), mk(101, 7, 480), mk(102, 9, 450)], 10, 25, 100)
    assert t["level"] == "NORMAL" and t["ice_change_pct"] == -10.0
