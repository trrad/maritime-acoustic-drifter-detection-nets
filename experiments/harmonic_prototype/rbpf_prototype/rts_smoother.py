"""Rauch-Tung-Striebel smoother on a recorded PF trajectory.

The deployment metric for this prototype is **σ_pos at acoustic-event
timestamps** (per `docs/maritime_buoy_design.md` and the phase-2.1+ status
doc). At an event time t_event, what we want is

    p(x_{t_event} | y_{1:T})

— the posterior over drifter position GIVEN the full observation history,
including LoRa fixes that happen AFTER t_event. A real-time forward
filter only sees `y_{1:t_event}`; mid-leg σ is dominated by 6-h
dead-reckon uncertainty. With the backward pass, σ at mid-leg can be
shrunk substantially because the next LoRa fix tells us where the
drifter actually was.

## Algorithm (Särkkä 2013 §8.2.4)

State: position (x, y) in local-ENU meters relative to the deployment
point. Dynamics treated as a random walk with per-tick process noise
covariance Q_t derived from the OU model in `process_noise.py`:

    x_{t+1} = x_t + drift_t + ε_t,    ε_t ~ N(0, Q_t)
    F = I    (random-walk; the deterministic drift is folded into the
              recorded mean differences)

Forward pass: ALREADY DONE by the PF. We consume `pf_mean_lats`,
`pf_mean_lons`, `pf_cov_m` from `ExperimentResult` directly; these are
the filtered (mean, cov) at every tick.

Backward pass (RTS):

    P_{t+1|t} = P_{t|t} + Q_t
    C_t = P_{t|t} · P_{t+1|t}^{-1}
    x_t|T = x_t|t + C_t · (x_{t+1|T} - x_{t+1|t})
    P_t|T = P_t|t + C_t · (P_{t+1|T} - P_{t+1|t}) · C_t^T

where x_{t+1|t} = x_{t|t} + drift_t. Drift estimated from the empirical
forward-filtered mean differences. At LoRa-fix ticks the empirical
"drift" includes the LoRa-correction jump — we use the same value for
both the predicted mean and the recorded next-tick filtered mean, so
the (predicted − filtered) discrepancy at LoRa ticks is zero in the
mean recursion (correct behavior: the LoRa update has already been
absorbed into the filtered mean, so there's no residual jump for the
smoother to propagate backward via the mean term — only via the
cov term, where Q_t < 0 effectively because P_{t+1|t+1} < P_{t+1|t}).

Note: this is a Gaussian approximation to the PF posterior at each
tick. Standard practice for offline PF post-processing (the underlying
trajectory is approximately Gaussian on the time scale of inter-LoRa
legs; the PF's non-Gaussianity comes from multi-modality near
ambiguous fixes, which we don't have here with 3-anchor LoRa).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from process_noise import (  # type: ignore[import-not-found]
    ProcessNoiseConfig, sigma_pos_growth_rate_per_axis,
)

EARTH_R_M: float = 111_320.0   # meters per degree (matches truth_field)


@dataclass
class SmoothedTrajectory:
    """Output of the RTS smoother.

    `means_local_m` is shape (T, 2) in local-ENU meters relative to
    `ref_lat, ref_lon`. `covs_m` is shape (T, 2, 2) in m². `t_sec` is
    shape (T,) — the tick timestamps. Convert back to (lat, lon) via
    `to_latlon(...)`.
    """
    t_sec: np.ndarray
    means_local_m: np.ndarray   # (T, 2): col 0 = x (east), col 1 = y (north)
    covs_m: np.ndarray          # (T, 2, 2)
    ref_lat: float
    ref_lon: float

    def sigma_pos_per_axis_m(self) -> np.ndarray:
        """Per-tick √(0.5·(cov_xx + cov_yy)) — same convention as the
        PF's `posterior_std_m`. Shape (T,)."""
        tr = self.covs_m[:, 0, 0] + self.covs_m[:, 1, 1]
        return np.sqrt(0.5 * tr)

    def to_latlon(self) -> tuple[np.ndarray, np.ndarray]:
        """Convert the smoothed local-ENU mean back to (lat, lon)."""
        cos_lat = float(np.cos(np.deg2rad(self.ref_lat)))
        lats = self.ref_lat + self.means_local_m[:, 1] / EARTH_R_M
        lons = self.ref_lon + self.means_local_m[:, 0] / (EARTH_R_M * cos_lat)
        return lats, lons

    def query_at_t(self, t_query_sec: float) -> tuple[np.ndarray, np.ndarray]:
        """Linearly interpolate the smoothed mean and cov at an arbitrary
        timestamp. Returns (mean_local_m_2vec, cov_m_2x2)."""
        if t_query_sec <= self.t_sec[0]:
            return self.means_local_m[0], self.covs_m[0]
        if t_query_sec >= self.t_sec[-1]:
            return self.means_local_m[-1], self.covs_m[-1]
        i = int(np.searchsorted(self.t_sec, t_query_sec, side="right") - 1)
        t0, t1 = self.t_sec[i], self.t_sec[i + 1]
        a = (t_query_sec - t0) / max(t1 - t0, 1e-9)
        m = (1 - a) * self.means_local_m[i] + a * self.means_local_m[i + 1]
        # Linear interp on cov entries — fine for small Δt; for big gaps
        # a more honest answer would forward-evolve cov from t_i with Q.
        c = (1 - a) * self.covs_m[i] + a * self.covs_m[i + 1]
        return m, c


def _per_tick_Q(
    depths: np.ndarray, t_since_anchor: np.ndarray, dt_sec: float,
    cfg: ProcessNoiseConfig,
) -> np.ndarray:
    """Per-tick process noise covariance Q_t, shape (T-1, 2, 2).

    Only the OU process-noise contribution lives here; the bias-state's
    contribution to position variance is **already baked into**
    `pf_cov_m[t]` via the dwell-quadratic-form path integral in
    `rbpf.cov_m`. Adding a separate bias-Q term here would double-count.
    """
    n = depths.size
    Q = np.zeros((n - 1, 2, 2))
    for t in range(n - 1):
        rate = sigma_pos_growth_rate_per_axis(
            float(depths[t]), float(t_since_anchor[t]), cfg,
        )
        q_ou = rate * dt_sec
        Q[t, 0, 0] = q_ou
        Q[t, 1, 1] = q_ou
    return Q


def _t_since_anchor(t_sec: np.ndarray, lora_fix_mask: np.ndarray) -> np.ndarray:
    """Time since the most recent LoRa fix at each tick, shape (T,)."""
    out = np.zeros_like(t_sec)
    last = t_sec[0]
    for t in range(t_sec.size):
        if lora_fix_mask[t]:
            last = t_sec[t]
        out[t] = t_sec[t] - last
    return out


def rts_smooth_trajectory(
    pf_mean_lats: np.ndarray,
    pf_mean_lons: np.ndarray,
    pf_cov_m: np.ndarray,
    depths: np.ndarray,
    lora_fix_mask: np.ndarray,
    dt_sec: float,
    process_noise_cfg: ProcessNoiseConfig,
    *,
    ref_lat: float | None = None,
    ref_lon: float | None = None,
) -> SmoothedTrajectory:
    """Run the RTS backward pass on a recorded mission trajectory.

    Inputs come straight from `ExperimentResult`. Returns a
    `SmoothedTrajectory` with smoothed (mean, cov) per tick and a
    timestamp axis. `ref_lat`/`ref_lon` default to the first tick's
    PF-mean position (≈ deployment point).

    `pf_cov_m` already includes the bias-state path-integral
    contribution via `rbpf.cov_m`'s dwell-quadratic-form. So Q_t here
    only adds the OU process-noise contribution between observations
    (adding a bias-Q term would double-count). At LoRa-fix ticks the
    smoother sees P_{t+1|t+1} << P_{t+1|t}; the gain C_t naturally
    pulls earlier ticks toward the LoRa-corrected mean.
    """
    n = pf_mean_lats.size
    if ref_lat is None:
        ref_lat = float(pf_mean_lats[0])
    if ref_lon is None:
        ref_lon = float(pf_mean_lons[0])
    cos_lat = float(np.cos(np.deg2rad(ref_lat)))

    # Convert filtered means to local-ENU meters.
    x_filt = np.zeros((n, 2))
    x_filt[:, 0] = (pf_mean_lons - ref_lon) * EARTH_R_M * cos_lat   # east (x)
    x_filt[:, 1] = (pf_mean_lats - ref_lat) * EARTH_R_M             # north (y)
    P_filt = pf_cov_m.copy()  # already in m²

    t_sec = np.arange(n) * dt_sec
    tsa = _t_since_anchor(t_sec, lora_fix_mask)
    Q = _per_tick_Q(depths, tsa, dt_sec, process_noise_cfg)  # (n-1, 2, 2)

    # Backward pass.
    x_smooth = x_filt.copy()
    P_smooth = P_filt.copy()
    for t in range(n - 2, -1, -1):
        # Predicted at t+1: x_{t+1|t} = x_{t|t} + drift_t (drift folded in
        # via empirical mean diff); P_{t+1|t} = P_{t|t} + Q_t.
        # We approximate drift_t as the empirical x_filt[t+1] - x_filt[t]
        # at non-LoRa ticks (deterministic prior + bias contribution),
        # zero at LoRa ticks (the jump there is observation, not drift).
        if lora_fix_mask[t + 1]:
            x_pred = x_filt[t]   # no deterministic drift assigned at fix
        else:
            x_pred = x_filt[t + 1]   # filtered mean already includes drift
        P_pred = P_filt[t] + Q[t]

        # Smoother gain C_t = P_{t|t} · P_{t+1|t}^{-1}.
        try:
            C = P_filt[t] @ np.linalg.inv(P_pred)
        except np.linalg.LinAlgError:
            # Singular cov (e.g., collapsed posterior): skip refinement
            # at this tick.
            continue

        x_smooth[t] = x_filt[t] + C @ (x_smooth[t + 1] - x_pred)
        P_smooth[t] = P_filt[t] + C @ (P_smooth[t + 1] - P_pred) @ C.T

    return SmoothedTrajectory(
        t_sec=t_sec,
        means_local_m=x_smooth,
        covs_m=P_smooth,
        ref_lat=ref_lat, ref_lon=ref_lon,
    )
