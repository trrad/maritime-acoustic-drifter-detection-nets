"""Surfacing policies.

A policy decides, given (current time, PF posterior, time-since-last-
surface), whether the node should attempt to surface (force depth to
0.5 m until the dwell ends) or continue its normal submerged control
cycle.

Surfacing is the system's coordination axis: LoRa comms only happen at
surface, so any inter-node ranging requires pre-arranged windows. The
controller (MPC) does NOT own surfacing — it owns depth choice between
surfaces. The policy here owns timing.

Each policy may optionally implement `predicted_next_surface_time_sec
(t_sec, last_surface_t)`, which the MPC consults so its σ_pos rollout
applies a LoRa Kalman update at the predicted surface tick. Returning
None means "I cannot predict" — MPC's σ rollout falls back to the
no-surface ballistic path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Protocol

from truth_field import EARTH_R_M as _M_PER_DEG  # type: ignore[import-not-found]


class SurfacingPolicy(Protocol):
    def should_surface(self, t_sec: float, time_since_last_surface_s: float,
                       posterior_std_m: float) -> bool:
        ...

    # Optional: most policies can predict their next surface event;
    # MPC consults this to apply the LoRa Kalman update at the right
    # tick in its σ_pos rollout. Implementations that can't predict
    # (e.g., heavily uncertainty-driven or event-driven) should return
    # None — MPC then falls back to ballistic σ growth.
    def predicted_next_surface_time_sec(
        self, t_sec: float, last_surface_t: float,
    ) -> Optional[float]:
        ...


@dataclass
class FixedIntervalPolicy:
    """Surface every `period_h` hours, regardless of posterior state."""
    period_h: float = 6.0

    def should_surface(self, t_sec, time_since_last_surface_s, posterior_std_m):
        return time_since_last_surface_s >= self.period_h * 3600.0

    def predicted_next_surface_time_sec(
        self, t_sec: float, last_surface_t: float,
    ) -> Optional[float]:
        """Exact: next surface at `last_surface_t + period_h·3600`."""
        return last_surface_t + self.period_h * 3600.0


@dataclass
class UncertaintyGatedPolicy:
    """Surface when the PF's posterior uncertainty exceeds a threshold,
    OR a hard max-interval cap fires. The threshold should be set
    relative to the mission's station-keeping envelope — if posterior_std
    grows to 50% of the envelope, we've lost useful position info."""

    threshold_m: float = 1500.0   # at typical ~3km rough envelope, 1.5km std is a useful gate
    max_interval_h: float = 12.0  # safety cap

    def should_surface(self, t_sec, time_since_last_surface_s, posterior_std_m):
        return (posterior_std_m > self.threshold_m
                or time_since_last_surface_s >= self.max_interval_h * 3600.0)

    def predicted_next_surface_time_sec(
        self, t_sec: float, last_surface_t: float,
    ) -> Optional[float]:
        """Conservative: report the safety-cap deadline. The σ-trigger
        is unpredictable from MPC's vantage, but the cap is a hard
        lower bound on when surfacing will fire."""
        return last_surface_t + self.max_interval_h * 3600.0


@dataclass
class GeometricIntervalPolicy:
    """Leg `k` lasts `periods_h[min(k, len-1)]` hours; after the list is
    exhausted the last entry is repeated. Encodes the principled
    `T ∝ 1/σ_post` schedule where successive bias-field Kalman updates
    roughly halve posterior stddev, so each leg can be ~2× as long as
    the previous one. Defaults come from the per-leg variance-reduction
    analysis in `/22_rbpf_v2_bias_learning.py` with R_max=3 km,
    σ_slow=0.17 m/s, σ_fast=0.10 m/s, τ_fast=3 h.
    """
    periods_h: list[float]

    def __post_init__(self) -> None:
        # Leg index k — number of surface events already issued.
        self._leg_idx = 0

    def should_surface(self, t_sec, time_since_last_surface_s, posterior_std_m):
        target_h = self.periods_h[min(self._leg_idx,
                                       len(self.periods_h) - 1)]
        if time_since_last_surface_s >= target_h * 3600.0:
            self._leg_idx += 1
            return True
        return False

    def predicted_next_surface_time_sec(
        self, t_sec: float, last_surface_t: float,
    ) -> Optional[float]:
        target_h = self.periods_h[min(self._leg_idx,
                                       len(self.periods_h) - 1)]
        return last_surface_t + target_h * 3600.0


@dataclass
class ProjectedDistancePolicy:
    """Surface when posterior uncertainty PLUS the expected dead-reckoning
    error until next decision exceeds the mission envelope. More
    operationally-relevant than raw posterior_std — asks 'will my next
    depth-choice decision be wrong because I don't know where I am?'."""

    envelope_m: float = 3000.0
    # Expected per-hour prior error in meters; derived from σ_forecast × 1h.
    prior_error_per_hour_m: float = 720.0  # ≈ 20 cm/s × 3600 s
    horizon_h: float = 1.0
    max_interval_h: float = 12.0

    def should_surface(self, t_sec, time_since_last_surface_s, posterior_std_m):
        projected = posterior_std_m + self.prior_error_per_hour_m * self.horizon_h
        return (projected > self.envelope_m
                or time_since_last_surface_s >= self.max_interval_h * 3600.0)

    def predicted_next_surface_time_sec(
        self, t_sec: float, last_surface_t: float,
    ) -> Optional[float]:
        return last_surface_t + self.max_interval_h * 3600.0


@dataclass
class EventTriggeredPolicy:
    """Surface ASAP after a detected acoustic event, with a hard
    max-interval safety cap.

    Stub implementation: takes an `event_detector` whose
    `event_at_tick(t_sec)` is consulted on each `should_surface` call.
    Real fleets would consult an onboard acoustic processor here; for
    v1 the detector is the deterministic `EventScheduleDetector` or
    Poisson generator from `acoustic_events.py`.

    `max_interval_h` is the safety cap — if no event has fired within
    `max_interval_h` of the last surface, surface anyway. This is the
    coordination contract: never go more than N hours without LoRa
    sync regardless of detection state.
    """

    event_detector: object             # has event_at_tick(t_sec) -> EventInfo | None
    max_interval_h: float = 12.0

    def should_surface(self, t_sec, time_since_last_surface_s, posterior_std_m):
        ev = self.event_detector.event_at_tick(t_sec)  # type: ignore[attr-defined]
        if ev is not None:
            return True
        return time_since_last_surface_s >= self.max_interval_h * 3600.0

    def predicted_next_surface_time_sec(
        self, t_sec: float, last_surface_t: float,
    ) -> Optional[float]:
        # Conservative: report the safety-cap deadline. The event
        # trigger is unpredictable from MPC's vantage.
        return last_surface_t + self.max_interval_h * 3600.0


@dataclass
class PostEventSurfacingPolicy:
    """Surface a SHORT, fixed delay after a detected acoustic event whose
    state has diverged significantly from the last track we exfiltrated
    for that source — plus a max-gap safety cap. Optimised for the
    retroactive-triangulation mission framing:

      - Most ticks, no event → don't surface → save power.
      - First-ever ping for a source → schedule a surface
        `post_event_delay_min` later. At surface, LoRa fix anchors the
        cluster and a track snapshot (lat/lon + estimated v) is treated
        as exfiltrated state.
      - Subsequent pings of the SAME source whose actual position is
        within `track_divergence_threshold_m` of the projection from the
        last-exfiltrated state are silently dropped: the existing track
        snapshot already lets the downstream consumer predict where the
        source is, so re-surfacing adds no information and only burns
        comms time / battery.
      - When the source's actual state diverges from projection beyond
        the threshold (course change, acceleration, vessel re-entering
        range after a gap), schedule a fresh surface.

    This replaces the naive "every ping → schedule a surface" policy
    that turned a single 30-minute boat track at 60-pings/hour into ~1
    surfacing per submerged window (each surface dwell + descent + first
    underwater ping → re-trigger), incinerating the battery and leaving
    the drifter at-surface (= not listening) most of the mission.

    Order of magnitudes for `post_event_delay_min = 30 min`:
      drift over delay ≈ σ_v · t ≈ 0.05 m/s · 1800 s ≈ 90 m
      LoRa fix σ ≈ 20 m
      smoothed σ at event time ≈ √(20² + 90²) ≈ 92 m
    Comfortably under the 250 m operational target.

    `track_divergence_threshold_m` defaults to 500 m — a few times the
    expected drift over the post-event delay; smaller thresholds add
    surfacings without proportionate downstream-knowledge gain.

    Sources without an `EventInfo.src` field, or with src="unknown",
    are bucketed under one synthetic per-event source: each carries a
    unique cursor index so every "unknown" ping is novel and triggers a
    surface. This preserves the legacy "every distinct event triggers"
    behavior for stress-test point-event generators that don't carry
    source identity.
    """

    event_detector: object
    post_event_delay_min: float = 30.0
    max_interval_h: float = 12.0
    track_divergence_threshold_m: float = 500.0

    # --- Internal state ---
    # Timestamp of the most recent unfired detection that scheduled a
    # post-event surface, or -1.0 if no surface is pending.
    _trigger_t: float = field(default=-1.0, init=False, repr=False)
    # Per-src "as of last surfacing" track snapshot. Maps src → tuple
    # (ref_t, ref_lat, ref_lon, vn_ms, ve_ms, has_velocity).
    _src_state: dict = field(
        default_factory=dict, init=False, repr=False
    )
    # Per-src buffer of the last 2 observations. Used at the next surface
    # to estimate velocity and refresh `_src_state`.
    _src_recent: dict = field(
        default_factory=dict, init=False, repr=False
    )
    # Counter for synthesizing unique src tags for src="unknown" pings,
    # so each unidentified ping behaves as a novel source.
    _unknown_counter: int = field(default=0, init=False, repr=False)

    @staticmethod
    def _haversine_m(lat1: float, lon1: float,
                      lat2: float, lon2: float) -> float:
        cos_lat = math.cos(math.radians(0.5 * (lat1 + lat2)))
        dlat = (lat1 - lat2) * _M_PER_DEG
        dlon = (lon1 - lon2) * _M_PER_DEG * cos_lat
        return math.sqrt(dlat * dlat + dlon * dlon)

    def _project(self, src: str, t_query: float
                  ) -> Optional[tuple[float, float]]:
        st = self._src_state.get(src)
        if st is None:
            return None
        # State tuple: (ref_t, ref_lat, ref_lon, vn, ve, has_v, cos_lat).
        # `cos_lat` is cached at refresh-time so we don't recompute it
        # per detection on the hot path.
        ref_t, ref_lat, ref_lon, vn, ve, has_v, cos_lat = st
        if not has_v:
            return ref_lat, ref_lon
        dt = t_query - ref_t
        d_lat = (vn * dt) / _M_PER_DEG
        d_lon = (ve * dt) / (_M_PER_DEG * cos_lat)
        return ref_lat + d_lat, ref_lon + d_lon

    def _refresh_src_states_at_surface(self) -> None:
        """At surface fire, update `_src_state` from the buffered
        observations of each src so subsequent pings are checked against
        a current projection base.

        Synthesized `unknown:{n}` keys (one per unidentified ping — see
        `should_surface`) are skipped: they're unique per ping and will
        never be queried again, so writing them into `_src_state` is a
        memory leak that grows with mission length under point-event
        opt-in.
        """
        for src, buf in self._src_recent.items():
            if src.startswith("unknown:"):
                continue
            if len(buf) >= 2:
                t1, lat1, lon1 = buf[-2]
                t2, lat2, lon2 = buf[-1]
                dt = max(t2 - t1, 1e-6)
                cos_lat = math.cos(math.radians(0.5 * (lat1 + lat2)))
                vn = (lat2 - lat1) * _M_PER_DEG / dt
                ve = (lon2 - lon1) * _M_PER_DEG * cos_lat / dt
                self._src_state[src] = (t2, lat2, lon2, vn, ve,
                                          True, cos_lat)
            elif len(buf) == 1:
                # Single fresh observation — update position/time but
                # keep prior velocity if we had one (better than
                # collapsing to v=0).
                t1, lat1, lon1 = buf[-1]
                old = self._src_state.get(src)
                cos_lat = math.cos(math.radians(lat1))
                if old is not None and old[5]:
                    self._src_state[src] = (
                        t1, lat1, lon1, old[3], old[4], True, cos_lat,
                    )
                else:
                    self._src_state[src] = (
                        t1, lat1, lon1, 0.0, 0.0, False, cos_lat,
                    )

    def should_surface(self, t_sec, time_since_last_surface_s, posterior_std_m):
        # Drain ALL events that have fired at-or-before this tick.
        # Each ping updates the per-src observation buffer; first-time
        # srcs trigger an immediate post-event surface (if no trigger
        # already pending), and known srcs trigger only when their
        # observed position diverges from projection.
        while True:
            ev = self.event_detector.event_at_tick(t_sec)  # type: ignore[attr-defined]
            if ev is None:
                break
            src = getattr(ev, "src", "unknown")
            if src == "unknown":
                # Synthesize a unique src so every unidentified ping is
                # treated as a novel source (legacy point-event behavior).
                src = f"unknown:{self._unknown_counter}"
                self._unknown_counter += 1

            buf = self._src_recent.setdefault(src, [])
            buf.append((float(ev.t_sec), float(ev.lat), float(ev.lon)))
            if len(buf) > 2:
                buf.pop(0)

            # If a surface is already scheduled, don't double-schedule;
            # any new srcs / divergences will be exfiltrated at the
            # already-pending surface event.
            if self._trigger_t >= 0:
                continue

            projected = self._project(src, ev.t_sec)
            if projected is None:
                # Novel src — trigger.
                self._trigger_t = float(t_sec)
            else:
                proj_lat, proj_lon = projected
                resid = self._haversine_m(
                    float(ev.lat), float(ev.lon), proj_lat, proj_lon,
                )
                if resid > self.track_divergence_threshold_m:
                    self._trigger_t = float(t_sec)

        # Post-event surface fires after the configured delay.
        if (self._trigger_t >= 0
            and t_sec - self._trigger_t >= self.post_event_delay_min * 60.0):
            self._refresh_src_states_at_surface()
            self._src_recent.clear()
            self._trigger_t = -1.0
            return True
        # Safety cap — never go more than max_interval_h without LoRa.
        if time_since_last_surface_s >= self.max_interval_h * 3600.0:
            self._refresh_src_states_at_surface()
            self._src_recent.clear()
            self._trigger_t = -1.0
            return True
        return False

    def predicted_next_surface_time_sec(
        self, t_sec: float, last_surface_t: float,
    ) -> Optional[float]:
        if self._trigger_t >= 0:
            return self._trigger_t + self.post_event_delay_min * 60.0
        # No pending event — conservative max-gap deadline.
        return last_surface_t + self.max_interval_h * 3600.0


@dataclass
class HybridPolicy:
    """Surface when ANY of the wrapped policies would. Models the real
    deployment pattern: combine a fixed-cadence safety net with
    uncertainty- or event-driven triggers.
    """

    policies: tuple[SurfacingPolicy, ...]

    def should_surface(self, t_sec, time_since_last_surface_s, posterior_std_m):
        return any(
            p.should_surface(t_sec, time_since_last_surface_s, posterior_std_m)
            for p in self.policies
        )

    def predicted_next_surface_time_sec(
        self, t_sec: float, last_surface_t: float,
    ) -> Optional[float]:
        # Earliest predicted next-surface across wrapped policies.
        candidates = []
        for p in self.policies:
            pred = p.predicted_next_surface_time_sec(t_sec, last_surface_t)
            if pred is not None:
                candidates.append(pred)
        return min(candidates) if candidates else None
