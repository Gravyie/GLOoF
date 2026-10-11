"""Server-side replay of the boundary archive (Dec 2017 -> today).

The server owns the clock, like the outburst replay. A background ticker walks
it forward; whenever the playhead passes a frame where the risk timeline
changed level upwards, the alert is dispatched through the same
AlertDispatcher (and so the same SNS path) the live monitor uses. Nothing
needs a browser to be open for the notifications to fire.

Seeking backwards re-arms the alerts after the new position: they are removed
from the log and fire again when the playhead passes them.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any, Callable

from src.core.config import Lake
from src.services.dispatch import AlertDispatcher

log = logging.getLogger(__name__)
SOURCE = "archive"
LEAD_DAYS = 25  # timeline starts a little before the first frame
TAIL_DAYS = 10


def _ord(s: str) -> int:
    return date.fromisoformat(s).toordinal()


class HistoryReplay:
    def __init__(self, lake: Lake, timeline: dict[str, Any], dispatcher: AlertDispatcher, *,
                 live: bool = False, duration_s: float = 90.0, on_new_run: Any = None) -> None:
        self._lake, self._disp, self._live = lake, dispatcher, live
        self._on_new_run = on_new_run  # called when a run starts from the beginning (arms the drill SMS)
        self._lock = threading.Lock()
        self.load(timeline, duration_s)

    # -- setup -------------------------------------------------------------
    def load(self, timeline: dict[str, Any], duration_s: float | None = None) -> None:
        with self._lock:
            self._tl = timeline
            self._days = [_ord(f["date"]) for f in timeline["frames"]]
            self._events = {e["frame"]: e for e in timeline["events"]}
            if self._days:
                self._d0, self._d1 = self._days[0] - LEAD_DAYS, self._days[-1] + TAIL_DAYS
            else:
                self._d0 = self._d1 = date.today().toordinal()
            if duration_s:
                self._duration = float(duration_s)
            self._reset_locked()

    def _reset_locked(self) -> None:
        self._running = False
        self._elapsed = 0.0
        self._t0 = 0.0
        self._speed = 1.0
        self._autopilot = True
        self._passed = 0  # number of frames whose alerts have been processed
        self._disp._log.clear(source=SOURCE)  # noqa: SLF001

    # -- control -----------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def start(self, speed: float = 1.0, autopilot: bool = True) -> None:
        with self._lock:
            self._speed = max(0.25, min(float(speed), 16.0))
            self._autopilot = autopilot
            if self._wall() >= self._duration:  # restart from the top when finished
                self._elapsed, self._passed = 0.0, 0
                self._disp._log.clear(source=SOURCE)  # noqa: SLF001
            if not self._running:
                if self._elapsed == 0 and self._on_new_run:
                    self._on_new_run()
                self._running, self._t0 = True, time.monotonic()

    def pause(self) -> None:
        with self._lock:
            self._fold()
            self._running = False

    def set_speed(self, speed: float) -> None:
        with self._lock:
            self._fold()
            self._speed = max(0.25, min(float(speed), 16.0))

    def seek(self, *, progress: float | None = None, day: str | None = None) -> None:
        with self._lock:
            self._fold()
            if day is not None:
                progress = (_ord(day) - self._d0) / max(self._d1 - self._d0, 1)
            p = max(0.0, min(float(progress or 0.0), 1.0))
            self._elapsed = p * self._duration
            n = self._frames_upto(self._day_at(self._elapsed))
            if n < self._passed:  # rewind: re-arm alerts after the playhead
                cutoff = n
                self._disp._log.discard(lambda r: r.get("source") == SOURCE and r.get("frame", -1) >= cutoff)  # noqa: SLF001
                self._passed = n
            self._advance_locked()

    def _fold(self) -> None:
        if self._running:
            now = time.monotonic()
            self._elapsed += (now - self._t0) * self._speed
            self._t0 = now

    # -- clock -------------------------------------------------------------
    def _wall(self) -> float:
        extra = (time.monotonic() - self._t0) * self._speed if self._running else 0.0
        return min(self._elapsed + extra, self._duration)

    def _day_at(self, wall: float) -> float:
        return self._d0 + (wall / self._duration) * (self._d1 - self._d0)

    def _frames_upto(self, day: float) -> int:
        return sum(1 for d in self._days if d <= day + 1e-9)

    def tick(self) -> None:
        """Called by the background ticker and by every state() poll."""
        with self._lock:
            self._advance_locked()

    def _advance_locked(self) -> None:
        wall = self._wall()
        n = self._frames_upto(self._day_at(wall))
        frames = self._tl["frames"]
        while self._passed < n:
            i = self._passed
            ev = self._events.get(i)
            if ev and ev["escalation"]:
                self._disp.dispatch(
                    self._lake, ev["to"], ev["reasons"], source=SOURCE, autopilot=self._autopilot,
                    live=self._live, meta={"frame": i, "date": frames[i]["date"], "from_level": ev["from"]})
            self._passed += 1
        if wall >= self._duration and self._running:
            self._fold()
            self._running = False

    # -- state -------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        with self._lock:
            self._advance_locked()
            wall = self._wall()
            day = self._day_at(wall)
            frames = self._tl["frames"]
            idx = self._frames_upto(day) - 1
            cur = frames[idx] if idx >= 0 else None
            return {
                "lake": self._lake.id,
                "running": self._running,
                "finished": wall >= self._duration,
                "speed": self._speed,
                "autopilot": self._autopilot,
                "live_sns": self._live,
                "duration_s": self._duration,
                "progress": wall / self._duration,
                "day": day,  # proleptic ordinal (float); client extrapolates while running
                "date": date.fromordinal(int(day)).isoformat(),
                "span": {"start": date.fromordinal(self._d0).isoformat(), "end": date.fromordinal(self._d1).isoformat(),
                         "start_day": self._d0, "end_day": self._d1},
                "days_per_second": (self._d1 - self._d0) / self._duration * self._speed if self._running else 0.0,
                "frame": idx,
                "level": cur["level"] if cur else "NORMAL",
                "reasons": cur["reasons"] if cur else [],
                "flags": cur["flags"] if cur else [],
                "alerts": self._disp._log.list(lake=self._lake.id, source=SOURCE),  # noqa: SLF001
            }


class Ticker:
    """Background thread that advances every replay so alerts fire on time
    even when no browser is polling."""

    def __init__(self, targets: Callable[[], list[Any]], interval_s: float = 0.2) -> None:
        self._targets, self._interval = targets, interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="replay-ticker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            for t in self._targets():
                try:
                    t.tick()
                except Exception:  # a bad tick must never kill the ticker
                    log.exception("replay tick failed")
