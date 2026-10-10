import time

import pytest
from fastapi.testclient import TestClient

from src.core.config import Settings
from src.main import create_app
from src.services import simulation as sim
from src.services.risk import Indicators, RiskTracker, Thresholds, evaluate


def lv(**kw):
    return evaluate(Indicators(**kw)).level


def test_rules():
    assert lv() == "NORMAL"
    assert lv(growth_pct=12) == "WATCH"
    assert lv(rain_24h_mm=50) == "WATCH"
    assert lv(growth_pct=12, rain_24h_mm=50) == "ADVISORY"
    assert lv(incidents_unverified=1) == "ADVISORY"
    assert lv(lake_rise_m_per_h=0.7) == "WARNING"            # one sensor
    assert lv(incidents_verified=1, lake_rise_m_per_h=0.7) == "EVACUATE"  # two confirmations
    assert lv(lake_rise_m_per_h=2.0) == "EVACUATE"           # extreme rate
    a = evaluate(Indicators(growth_pct=12, data_age_days=30))
    assert a.flags and "Data gap" in a.flags[0] and all(r["text"] for r in a.reasons)


def test_hysteresis():
    t = RiskTracker(Thresholds())
    assert t.step("WATCH") == ("NORMAL", None)              # needs 2 in a row
    assert t.step("WATCH") == ("WATCH", "NORMAL")
    assert t.step("WARNING")[0] == "WARNING"                # immediate
    for _ in range(10):
        t.step("NORMAL")
    assert t.level == "WARNING"                             # never auto-clears
    t.clear()
    assert t.level == "NORMAL"


def test_scenario_escalates_in_order_before_the_outburst():
    t, order, first = RiskTracker(), [], {}
    h = sim.SIM_H[0]
    while h <= 0.0:
        lvl, prev = t.step(evaluate(sim.indicators_at(h)[0]).level)
        if prev:
            order.append(lvl); first[lvl] = h
        h += sim.STEP_H
    assert order == ["WATCH", "ADVISORY", "WARNING", "EVACUATE"]
    assert first["EVACUATE"] < 0  # public evacuation alert BEFORE the outburst
    assert first["WATCH"] < first["ADVISORY"] < first["WARNING"] <= first["EVACUATE"]


@pytest.fixture
def client():
    return TestClient(create_app(Settings(_env_file=None, alert_api_key="k", alert_dry_run=True)))


def test_sim_api_fires_alerts_once_and_never_sends_real_sms(client):
    st = client.post("/api/sim/reset").json()
    assert st["level"] == "NORMAL" and st["alerts"] == []
    client.post("/api/sim/start?speed=8")
    time.sleep(0.1)
    # jump the clock to the end by folding elapsed time
    client.app.state.sim._elapsed = sim.DURATION_S  # noqa: SLF001
    st = client.get("/api/sim/state").json()
    assert st["finished"] and st["level"] == "EVACUATE"
    levels = [h["to"] for h in st["level_history"]]
    assert levels == ["WATCH", "ADVISORY", "WARNING", "EVACUATE"]
    alerts = st["alerts"]
    assert {a["status"] for a in alerts} == {"simulated"} and not any(a["live"] for a in alerts)
    public = [a for a in alerts if a["audience"] == "public"]
    assert [a["level"] for a in public] == ["WARNING", "EVACUATE"]
    assert "निकासी" in public[-1]["message"] and "EVACUATE" in public[-1]["message"]
    # polling again must not duplicate alerts
    again = client.get(f"/api/sim/state?after_id={alerts[-1]['id']}").json()
    assert again["alerts"] == [] and len(client.get("/api/alerts").json()) == len(alerts)


def test_approval_flow_and_auth(client):
    client.post("/api/sim/reset")
    client.post("/api/sim/start?speed=8&autopilot=false")
    client.app.state.sim._elapsed = sim.DURATION_S  # noqa: SLF001
    st = client.get("/api/sim/state").json()
    pend = [a for a in st["alerts"] if a["status"] == "pending_approval"]
    assert pend and all(a["audience"] == "public" for a in pend)
    pid = pend[0]["id"]
    assert client.post(f"/api/alerts/{pid}/approve").status_code == 401
    ok = client.post(f"/api/alerts/{pid}/approve", headers={"X-API-Key": "k"})
    assert ok.status_code == 200 and ok.json()["status"] == "simulated"
    assert client.post(f"/api/alerts/{pid}/approve", headers={"X-API-Key": "k"}).status_code == 404
