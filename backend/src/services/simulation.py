"""Replay simulation of a glacial-lake outburst, driving the real risk engine
and alert dispatcher.

IMPORTANT: the timeline below is an ILLUSTRATIVE RECONSTRUCTION written for a
demonstration, not measured data. It is loosely modelled on the night of 3-4
October 2023 at South Lhonak Lake (Sikkim). The lake outline shown on the map
is the live Sentinel-2 polygon, scaled by ``area_factor`` for visualisation.

The server owns the clock. Every poll of ``state()`` evaluates all risk steps
up to the current simulated time, in order, and dispatches alerts exactly once
when the level changes - the same code path a live deployment would use.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from src.core.config import Lake
from src.services.dispatch import AlertDispatcher
from src.services.risk import Indicators, RiskTracker, Thresholds, evaluate

SCENARIO_ID = "south_lhonak_2023"
SCENARIO_LAKE = "south_lhonak"
STEP_H = 0.25

# wall-clock seconds (at 1x) -> simulated hours relative to the outburst (t = 0)
_CLOCK = [(0, -720), (20, -120), (35, -36), (55, -6), (75, -1.0), (90, 0.0), (105, 3.0), (120, 12.0)]
WALL_S, SIM_H = zip(*_CLOCK)
DURATION_S = WALL_S[-1]

_KF = {
    "area": ([-720, -360, -120, -24, 0, 1, 3, 6, 12], [1.00, 1.05, 1.10, 1.14, 1.16, 0.90, 0.55, 0.40, 0.38]),
    "rain": ([-720, -120, -60, -36, -24, -12, -4, 0, 12], [2, 5, 12, 35, 55, 82, 95, 60, 20]),
    "lake_rise": ([-720, -3, -2, -1, -0.5, 0, 1], [0, 0, 0.3, 0.8, 1.3, 2.0, -3.0]),
    "gauge_rise": ([-720, 0, 0.25, 1, 3, 6, 12], [0, 0, 0.4, 2.5, 3.0, 1.0, 0.0]),
}
EVENTS = [
    {"h": -120, "label": "Lake slowly enlarging"},
    {"h": -36, "label": "Rainfall intensifies"},
    {"h": -1.5, "label": "Field report: seepage at moraine wall"},
    {"h": -0.8, "label": "Report verified by operator"},
    {"h": 0, "label": "Moraine failure - outburst begins"},
    {"h": 0.25, "label": "Downstream gauge rising"},
]
INCIDENT_REPORT_H, INCIDENT_VERIFIED_H = -1.5, -0.8
T0_LABEL = "night of 3-4 Oct 2023 (approx.)"


def _at(name: str, h: float) -> float:
    xs, ys = _KF[name]
    return float(np.interp(h, xs, ys))


def indicators_at(h: float) -> tuple[Indicators, float]:
    area = _at("area", h)
    pre = h < 0.5
    ind = Indicators(
        growth_pct=(area - 1.0) * 100.0 if area > 1.0 else 0.0,
        rain_24h_mm=_at("rain", h),
        lake_rise_m_per_h=_at("lake_rise", h) if h > -3.5 else 0.0,
        gauge_rise_m_per_h=_at("gauge_rise", h),
        incidents_unverified=1 if INCIDENT_REPORT_H <= h < INCIDENT_VERIFIED_H else 0,
        incidents_verified=1 if h >= INCIDENT_VERIFIED_H else 0,
        data_age_days=3.0 if pre else None,
    )
    return ind, area


def clock_label(h: float) -> str:
    if abs(h) < 0.05:
        return "T = outburst"
    sign = "-" if h < 0 else "+"
    a = abs(h)
    if a >= 48:
        return f"T {sign} {a / 24:.0f} days"
    if a >= 1:
        return f"T {sign} {a:.0f} h {int(round((a % 1) * 60)):02d} min" if a % 1 else f"T {sign} {a:.0f} h"
    return f"T {sign} {int(round(a * 60))} min"


class SimulationRunner:
    def __init__(self, lake: Lake, dispatcher: AlertDispatcher, *, live_sms: bool = False) -> None:
        self._lake, self._disp, self._live = lake, dispatcher, live_sms
        self._lock = threading.Lock()
        self.reset()

    # -- control ------------------------------------------------------------
    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self._running = False
            self._elapsed = 0.0
            self._t0 = 0.0
            self._speed = 1.0
            self._autopilot = True
            self._tracker = RiskTracker(Thresholds())
            self._next_eval = SIM_H[0]
            self._assessment = evaluate(indicators_at(SIM_H[0])[0])
            self._level_history: list[dict] = []
            self._disp._log.clear(source="simulation")  # noqa: SLF001

    def start(self, speed: float = 1.0, autopilot: bool = True) -> None:
        with self._lock:
            self._speed = max(0.25, min(float(speed), 8.0))
            self._autopilot = autopilot
            if not self._running:
                self._running, self._t0 = True, time.monotonic()

    def pause(self) -> None:
        with self._lock:
            self._fold()
            self._running = False

    def set_speed(self, speed: float) -> None:
        with self._lock:
            self._fold()
            self._speed = max(0.25, min(float(speed), 8.0))
            if self._running:
                self._t0 = time.monotonic()

    def _fold(self) -> None:
        if self._running:
            self._elapsed += (time.monotonic() - self._t0) * self._speed
            self._t0 = time.monotonic()

    # -- state --------------------------------------------------------------
    def _wall(self) -> float:
        extra = (time.monotonic() - self._t0) * self._speed if self._running else 0.0
        return min(self._elapsed + extra, DURATION_S)

    def state(self, after_id: int = 0) -> dict[str, Any]:
        with self._lock:
            wall = self._wall()
            h = float(np.interp(wall, WALL_S, SIM_H))
            guard = 0
            while self._next_eval <= h + 1e-9 and guard < 6000:
                guard += 1
                ind, _ = indicators_at(self._next_eval)
                self._assessment = evaluate(ind)
                level, prev = self._tracker.step(self._assessment.level)
                if prev is not None:
                    self._level_history.append({"h": self._next_eval, "from": prev, "to": level,
                                                "label": clock_label(self._next_eval)})
                    self._disp.dispatch(
                        self._lake, level, self._assessment.reasons, source="simulation",
                        sim_h=self._next_eval, autopilot=self._autopilot, live=self._live)
                self._next_eval += STEP_H
            ind, area = indicators_at(h)
            finished = wall >= DURATION_S
            if finished and self._running:
                self._fold()
                self._running = False
            return {
                "scenario": {"id": SCENARIO_ID, "lake": SCENARIO_LAKE,
                             "title": "South Lhonak Lake outburst - replay",
                             "disclaimer": "Illustrative reconstruction for demonstration. Timeline values are "
                                           "scripted, not measured. Lake outline = live Sentinel-2 data, scaled.",
                             "t0": T0_LABEL, "clock": [list(c) for c in _CLOCK], "events": EVENTS, "duration_s": DURATION_S,
                             "sim_start_h": SIM_H[0], "sim_end_h": SIM_H[-1]},
                "running": self._running, "finished": finished, "speed": self._speed,
                "autopilot": self._autopilot, "live_sms": self._live,
                "progress": wall / DURATION_S, "sim_h": h, "clock": clock_label(h),
                "level": self._tracker.level, "raw_level": self._assessment.level,
                "reasons": self._assessment.reasons, "flags": self._assessment.flags,
                "indicators": {"growth_pct": ind.growth_pct, "rain_24h_mm": ind.rain_24h_mm,
                               "lake_rise_m_per_h": ind.lake_rise_m_per_h,
                               "gauge_rise_m_per_h": ind.gauge_rise_m_per_h,
                               "incidents_unverified": ind.incidents_unverified,
                               "incidents_verified": ind.incidents_verified},
                "area_factor": area, "level_history": list(self._level_history),
                "alerts": self._disp._log.list(lake=SCENARIO_LAKE, source="simulation", after_id=after_id),  # noqa: SLF001
            }
