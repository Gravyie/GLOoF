"""Risk timeline over the boundary archive: satellite-only indicators per frame.

Every frame of the archive is evaluated in date order, using ONLY frames up to
and including it (no look-ahead), exactly as a monitor running on that date
would have. The level then goes through the same hysteresis tracker as the live
system, and each level change becomes an alert event on the timeline.

Indicators (all derived from the real outlines):
  growth_pct          smoothed lake extent vs the first-season baseline
  growth_rate_pct_yr  robust (Theil-Sen) trend over the trailing two years
  advance_m           how far the shoreline has pushed beyond the baseline outline
  drop_pct            fall in lake extent vs the recent pre-frame median
  frozen_fraction     share of the last lake footprint now read as snow/ice

Thresholds are PLACEHOLDERS for a screening tool, chosen as round numbers before
calibration; they must be set with the disaster-management authority.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from src.services.boundary import advance_retreat, lake_body, to_metric
from src.services.risk import LEVELS, RANK, RiskTracker, Thresholds


@dataclass(frozen=True)
class HistoryThresholds:
    growth_watch_pct: float = 10.0
    growth_advisory_pct: float = 15.0
    growth_warning_pct: float = 25.0
    rate_advisory_pct_yr: float = 2.0  # sustained expansion
    advance_watch_m: float = 150.0
    advance_warning_m: float = 300.0
    drainage_drop_pct: float = 12.0  # vs the median of the preceding frames
    drainage_window_days: int = 45
    max_frozen_fraction: float = 0.15  # above this a "drop" is ice/snow, not drainage
    smooth_n: int = 3  # rolling median length (frames)
    baseline_min_frames: int = 3


def _median_recent(values: list[float], n: int) -> float:
    return statistics.median(values[-n:])


def _theil_sen(xs: list[float], ys: list[float]) -> float | None:
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i]) for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[j] != xs[i]]
    return statistics.median(slopes) if slopes else None


def _d(s: str) -> date:
    return date.fromisoformat(s)


def evaluate_frame(ind: dict[str, Any], th: HistoryThresholds) -> tuple[str, list[dict], list[str]]:
    reasons: list[dict] = []
    flags: list[str] = []

    def add(level: str, text: str) -> None:
        reasons.append({"level": level, "text": text})

    g, rate, adv = ind["growth_pct"], ind["growth_rate_pct_yr"], ind["advance_m"]
    if g is not None and g >= th.growth_watch_pct:
        add("WATCH", f"Lake {g:.0f}% larger than its {ind['baseline_label']} extent")
    if adv is not None and adv >= th.advance_watch_m:
        add("WATCH", f"Shoreline has advanced {adv:.0f} m beyond the baseline outline")
    if g is not None and g >= th.growth_advisory_pct and rate is not None and rate >= th.rate_advisory_pct_yr:
        add("ADVISORY", f"Sustained expansion: {rate:+.1f}% per year over the last two years")
    if g is not None and g >= th.growth_warning_pct:
        add("WARNING", f"Critical growth: lake {g:.0f}% above baseline")
    if g is not None and g >= th.growth_advisory_pct and adv is not None and adv >= th.advance_warning_m:
        add("WARNING", f"Large boundary shift ({adv:.0f} m) together with {g:.0f}% growth")
    if ind["drainage"]:
        add("EVACUATE", f"Sudden drainage: lake extent fell {ind['drop_pct']:.0f}% within "
                        f"{ind['drop_days']} days. Outburst flood likely downstream.")
    elif ind["drop_pct"] is not None and ind["drop_pct"] >= th.drainage_drop_pct and ind["frozen_fraction"] > th.max_frozen_fraction:
        flags.append(f"Area fell {ind['drop_pct']:.0f}% but {ind['frozen_fraction']:.0%} of the lake now reads as "
                     "snow/ice: treated as freezing, not drainage")
    if ind["gap_days"] is not None and ind["gap_days"] > 60:
        flags.append(f"Data gap: previous clear scene was {ind['gap_days']} days earlier")
    level = max((r["level"] for r in reasons), key=RANK.get, default="NORMAL")
    return level, reasons, flags


def analyse_archive(doc: dict[str, Any], th: HistoryThresholds | None = None) -> dict[str, Any]:
    """Per-frame indicators + tracked level + level-change events."""
    th = th or HistoryThresholds()
    frames = doc["frames"]
    epsg = doc["lake"].get("utm_epsg", 32645)
    if not frames:
        return {"frames": [], "events": [], "baseline": None, "thresholds": asdict(th)}

    first_year = _d(frames[0]["date"]).year
    base_frames = [f for f in frames if _d(f["date"]).year == first_year]
    if len(base_frames) < th.baseline_min_frames:
        base_frames = frames[: th.baseline_min_frames]
    baseline_area = statistics.median(f["stats"]["lake_area_m2"] for f in base_frames)
    # Baseline outline = the base frame whose area is closest to the median (a real outline, not a blend).
    base_ref = min(base_frames, key=lambda f: abs(f["stats"]["lake_area_m2"] - baseline_area))
    base_geom = lake_body(to_metric(base_ref["lake"]["geometry"], epsg))
    y0, y1 = _d(base_frames[0]["date"]).year, _d(base_frames[-1]["date"]).year
    baseline_label = f"{y0}" if y0 == y1 else f"{y0}-{str(y1)[2:]}"

    tracker = RiskTracker(Thresholds(escalate_persistence=2, deescalate_persistence=3))
    out: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    areas: list[float] = []
    advances: list[float] = []
    for i, f in enumerate(frames):
        a = f["stats"]["lake_area_m2"]
        areas.append(a)
        smoothed = _median_recent(areas, th.smooth_n)
        geom = lake_body(to_metric(f["lake"]["geometry"], epsg))
        adv, ret = advance_retreat(geom, base_geom)
        advances.append(adv)
        d = _d(f["date"])

        # trend over the trailing two years (needs at least a year of spread)
        win = [(_d(x["date"]), x["stats"]["lake_area_m2"]) for x in frames[: i + 1] if (d - _d(x["date"])).days <= 730]
        rate = None
        if len(win) >= 6 and (win[-1][0] - win[0][0]).days >= 365:
            slope = _theil_sen([(w[0] - d).days / 365.25 for w in win], [w[1] for w in win])
            rate = round(slope / baseline_area * 100, 2) if slope is not None else None

        # drainage: this frame AND the previous one both well below the median of
        # the frames before them, all within the window, and not explained by ice
        drop_pct, drop_days, drainage = None, None, False
        prior = [x for x in frames[: max(i - 1, 0)] if (d - _d(x["date"])).days <= th.drainage_window_days + 30]
        if i >= 1 and len(prior) >= 2:
            ref = statistics.median(x["stats"]["lake_area_m2"] for x in prior[-3:])
            pair = [frames[i - 1]["stats"]["lake_area_m2"], a]
            drop_pct = round((ref - max(pair)) / ref * 100, 1) if ref else None
            drop_days = (d - _d(prior[-1]["date"])).days
            frozen = max(frames[i - 1]["stats"].get("frozen_fraction", 0), f["stats"].get("frozen_fraction", 0))
            drainage = (drop_pct is not None and drop_pct >= th.drainage_drop_pct
                        and drop_days <= th.drainage_window_days and frozen <= th.max_frozen_fraction)

        ind = {
            "lake_area_m2": a,
            "smoothed_area_m2": round(smoothed, 1),
            "growth_pct": round((smoothed - baseline_area) / baseline_area * 100, 1),
            "growth_rate_pct_yr": rate,
            "advance_m": round(_median_recent(advances, th.smooth_n), 1),
            "advance_raw_m": adv,
            "retreat_m": ret,
            "drop_pct": drop_pct,
            "drop_days": drop_days,
            "drainage": drainage,
            "frozen_fraction": f["stats"].get("frozen_fraction", 0.0),
            "ice_area_m2": f["stats"].get("ice_area_m2"),
            "gap_days": (d - _d(frames[i - 1]["date"])).days if i else None,
            "baseline_label": baseline_label,
        }
        raw, reasons, flags = evaluate_frame(ind, th)
        level, prev = tracker.step(raw)
        if prev is not None:
            events.append({"frame": i, "date": f["date"], "from": prev, "to": level,
                           "escalation": RANK[level] > RANK[prev], "reasons": reasons})
        out.append({"i": i, "date": f["date"], "level": level, "raw_level": raw,
                    "indicators": ind, "reasons": reasons, "flags": flags})

    return {
        "baseline": {"area_m2": round(baseline_area, 1), "label": baseline_label, "frames": len(base_frames),
                     "outline_date": base_ref["date"]},
        "thresholds": asdict(th),
        "levels": LEVELS,
        "frames": out,
        "events": events,
    }
