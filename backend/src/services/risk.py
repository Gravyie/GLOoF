"""Rule-based risk engine + hysteresis state machine.

Deliberately transparent: every level comes with the human-readable reasons that
produced it. Thresholds are PLACEHOLDERS and must be calibrated per lake with the
disaster-management authority before operational use.

Levels: NORMAL < WATCH < ADVISORY < WARNING < EVACUATE
  WATCH     slow indicator or a trigger condition on its own
  ADVISORY  slow indicator + trigger, or an unverified field report
  WARNING   one confirmation (verified report, or one sensor showing rapid rise)
  EVACUATE  two independent confirmations, or an extreme rate of rise
"""
from __future__ import annotations

from dataclasses import dataclass, field

LEVELS = ["NORMAL", "WATCH", "ADVISORY", "WARNING", "EVACUATE"]
RANK = {name: i for i, name in enumerate(LEVELS)}


@dataclass(frozen=True)
class Thresholds:
    growth_elevated_pct: float = 10.0
    growth_critical_pct: float = 25.0
    rain_trigger_mm_24h: float = 40.0
    rain_extreme_mm_24h: float = 80.0
    lake_rise_rapid_m_h: float = 0.5
    lake_rise_extreme_m_h: float = 1.5
    gauge_rise_rapid_m_h: float = 1.0
    stale_data_days: float = 15.0
    escalate_persistence: int = 2  # consecutive evaluations before WATCH/ADVISORY
    deescalate_persistence: int = 3  # consecutive lower evaluations to step down


@dataclass
class Indicators:
    growth_pct: float | None = None  # same-season lake area change
    rain_24h_mm: float | None = None
    quake_trigger: bool = False
    lake_rise_m_per_h: float | None = None  # lake-level sensor
    gauge_rise_m_per_h: float | None = None  # downstream river gauge
    incidents_unverified: int = 0
    incidents_verified: int = 0
    data_age_days: float | None = None


@dataclass
class Assessment:
    level: str
    reasons: list[dict] = field(default_factory=list)  # {"level": str, "text": str}
    flags: list[str] = field(default_factory=list)


def evaluate(ind: Indicators, th: Thresholds | None = None) -> Assessment:
    th = th or Thresholds()
    reasons: list[dict] = []
    flags: list[str] = []

    def add(level: str, text: str) -> None:
        reasons.append({"level": level, "text": text})

    growth_el = ind.growth_pct is not None and ind.growth_pct >= th.growth_elevated_pct
    growth_cr = ind.growth_pct is not None and ind.growth_pct >= th.growth_critical_pct
    rain = ind.rain_24h_mm is not None and ind.rain_24h_mm >= th.rain_trigger_mm_24h
    rain_x = ind.rain_24h_mm is not None and ind.rain_24h_mm >= th.rain_extreme_mm_24h
    lake_rapid = ind.lake_rise_m_per_h is not None and ind.lake_rise_m_per_h >= th.lake_rise_rapid_m_h
    lake_x = ind.lake_rise_m_per_h is not None and ind.lake_rise_m_per_h >= th.lake_rise_extreme_m_h
    gauge_rapid = ind.gauge_rise_m_per_h is not None and ind.gauge_rise_m_per_h >= th.gauge_rise_rapid_m_h

    if growth_el:
        add("WATCH", f"Lake area up {ind.growth_pct:.0f}% (same-season)")
    if rain:
        add("WATCH", f"Heavy rainfall: {ind.rain_24h_mm:.0f} mm in 24 h")
    if ind.quake_trigger:
        add("WATCH", "Earthquake near the lake")
    if (growth_el and (rain or ind.quake_trigger)) or growth_cr:
        add("ADVISORY", "Lake growth combined with a trigger condition" if not growth_cr
            else f"Critical lake growth ({ind.growth_pct:.0f}%)")
    if rain_x:
        add("ADVISORY", f"Extreme rainfall ({ind.rain_24h_mm:.0f} mm / 24 h)")
    if ind.incidents_unverified:
        add("ADVISORY", f"{ind.incidents_unverified} unverified field report(s)")

    confirmations = 0
    if ind.incidents_verified:
        confirmations += 1
        add("WARNING", "Field report verified by an operator")
    if lake_rapid:
        confirmations += 1
        add("WARNING", f"Lake level rising {ind.lake_rise_m_per_h:.1f} m/h")
    if gauge_rapid:
        confirmations += 1
        add("WARNING", f"Downstream gauge rising {ind.gauge_rise_m_per_h:.1f} m/h")
    if confirmations >= 2:
        add("EVACUATE", f"{confirmations} independent confirmations of an outburst in progress")
    if lake_x:
        add("EVACUATE", f"Extreme lake-level rise ({ind.lake_rise_m_per_h:.1f} m/h)")

    if ind.data_age_days is not None and ind.data_age_days > th.stale_data_days:
        flags.append(f"Data gap: newest clear satellite scene is {ind.data_age_days:.0f} days old")

    level = max((r["level"] for r in reasons), key=RANK.get, default="NORMAL")
    return Assessment(level=level, reasons=reasons, flags=flags)


class RiskTracker:
    """Hysteresis so a noisy indicator doesn't flap the public alert level.

    * WARNING/EVACUATE: escalate immediately; never step down automatically
      (an operator must call ``clear()``).
    * WATCH/ADVISORY: need ``escalate_persistence`` consecutive evaluations.
    * Step down one level at a time after ``deescalate_persistence`` lower ones.
    """

    def __init__(self, th: Thresholds | None = None) -> None:
        self.th = th or Thresholds()
        self.level = "NORMAL"
        self._up = 0
        self._down = 0

    def step(self, raw_level: str) -> tuple[str, str | None]:
        """Returns (current_level, previous_level_if_changed)."""
        prev, raw = self.level, RANK[raw_level]
        cur = RANK[self.level]
        if raw > cur:
            self._down = 0
            if raw >= RANK["WARNING"]:
                self.level = raw_level
            else:
                self._up += 1
                if self._up >= self.th.escalate_persistence:
                    self.level, self._up = raw_level, 0
        elif raw < cur and cur < RANK["WARNING"]:
            self._up = 0
            self._down += 1
            if self._down >= self.th.deescalate_persistence:
                self.level, self._down = LEVELS[cur - 1], 0
        else:
            self._up = self._down = 0
        return self.level, (prev if prev != self.level else None)

    def clear(self) -> None:
        self.level, self._up, self._down = "NORMAL", 0, 0
