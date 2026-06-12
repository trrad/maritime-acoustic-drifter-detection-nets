"""Stub event-detection scaffold for testing event-triggered surfacing.

This is NOT a real acoustic detection sensor — it's a TEST scaffold
that simulates "the fleet just told us an event needs triangulation."
A deterministic injected schedule of (lat, lon, t) tuples is the v1
representation; a Poisson-process generator is the second option.

The driver consults the detector each tick. When the detector reports
an event AND the node is not already in surface dwell, the experiment
loop starts a short out-of-schedule surface burst (separate counter)
that does NOT close a bias-Kalman leg — so leg accumulators continue
across the burst, preserving the `x_start` re-anchor contract for the
next mesh-slot surfacing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np  # type: ignore[import-not-found]


@dataclass(frozen=True)
class EventInfo:
    """Triangulation event reported by the (stub) detector at a tick.
    Carries the event's truth (lat, lon) for downstream analysis; the
    actual triangulation is computed across the fleet outside the
    single-node sim.

    `src` identifies the emitting source so downstream surfacing
    policies can reason about per-source track continuity (e.g., a
    stream of pings from "boat:3" is one acoustic-event sequence; a
    "boat:7" ping is a different one). Defaults to "unknown" for
    callers that don't carry source identity.
    """
    lat: float
    lon: float
    t_sec: float
    src: str = "unknown"


@dataclass
class EventScheduleDetector:
    """Deterministic injection of events at known (lat, lon, t) tuples.

    Fires each event at the first `event_at_tick(t)` call with `t` at
    or after `event.t_sec`. This guarantees no event is missed even when
    ticks are widely spaced relative to event times — the previous
    ±match_window_sec design dropped ~80% of events when window (60s)
    was much smaller than the tick interval (600s).
    """

    events: tuple[EventInfo, ...]
    _fired: set[int] = None  # type: ignore[assignment]
    # Internal: index of the next event to consider. Events are sorted
    # ascending; once an index is fired, we never re-scan it.
    _next_idx: int = 0

    def __post_init__(self) -> None:
        prev_t = float("-inf")
        for ev in self.events:
            if not (ev.t_sec >= prev_t):
                raise ValueError(
                    f"events must be sorted by t_sec ascending; got "
                    f"{ev.t_sec} after {prev_t}"
                )
            prev_t = ev.t_sec
        self._fired = set()

    def event_at_tick(self, t_sec: float) -> EventInfo | None:
        """Return the next-pending event whose `t_sec` is ≤ current tick,
        else None. Each event fires at most once."""
        while self._next_idx < len(self.events):
            ev = self.events[self._next_idx]
            if ev.t_sec > t_sec:
                return None  # next event is in the future
            self._fired.add(self._next_idx)
            self._next_idx += 1
            return ev
        return None

    @staticmethod
    def from_iterable(triples: Iterable[tuple[float, float, float]],
                       ) -> "EventScheduleDetector":
        """Build from an iterable of `(lat, lon, t_sec)` tuples."""
        events = tuple(
            EventInfo(lat=lat, lon=lon, t_sec=t)
            for lat, lon, t in sorted(triples, key=lambda x: x[2])
        )
        return EventScheduleDetector(events=events)


@dataclass
class PoissonEventDetector:
    """Poisson-process event generator, seeded for reproducibility.

    Useful when the test wants to characterise mean event-trigger rate
    behaviour over a sweep without committing to specific times.
    `lambda_per_h` is the expected event rate; events are sampled lazily
    on each `event_at_tick` call by drawing from an exponential
    inter-arrival time at construction.
    """

    lambda_per_h: float
    seed: int = 0
    mission_h: float = 72.0
    # Internal: pre-generated event schedule.
    _delegate: EventScheduleDetector = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        rng = np.random.default_rng(self.seed)
        events: list[tuple[float, float, float]] = []
        t = 0.0
        rate_per_sec = self.lambda_per_h / 3600.0
        if rate_per_sec <= 0:
            self._delegate = EventScheduleDetector(events=())
            return
        while True:
            dt = float(rng.exponential(1.0 / rate_per_sec))
            t += dt
            if t > self.mission_h * 3600.0:
                break
            # No location info in the Poisson generator — caller can
            # post-process if event location matters.
            events.append((float("nan"), float("nan"), t))
        self._delegate = EventScheduleDetector.from_iterable(events)

    def event_at_tick(self, t_sec: float) -> EventInfo | None:
        return self._delegate.event_at_tick(t_sec)
