"""Per-component OU process-noise model for PF predict + MPC σ_pos rollout.

Mirrors `submesoscale.LayeredNoiseField` (5 components: coh, plume,
submeso, inertial-amp, white) so the PF predict's per-tick velocity
variance matches truth's per-tick variance in expectation, and the MPC
σ_pos rollout uses the same model end-to-end (single source of truth).

Closes the i.i.d.-vs-OU calibration deficit observed at Step 2.2:
PF reported σ_pos ≈ 115-140 m while actual error was 200-400 m
(calib ratio ≈ 2.4-2.9). The i.i.d. process-noise model
(`process_noise_ms`-driven N(0, σ²) per tick) over-randomises and
under-grows; the OU-integrated form below replaces it.

Each component carries its own (σ, τ) and vertical profile. Inertial is
the rotating-amplitude representation: two independent stationary
amplitude fields (c1, c2) at OU time-constant `tau_inertial_sec`;
instantaneous velocity at time t is c1·cos(ft) + c2·sin(ft) per
direction times the surface-trap profile.

This file holds shared definitions used by both `PositionRBPF.predict`
and `MPCStationKeeper`. Defining it as a tiny standalone module avoids
the experiment.py ↔ ballast_controller.py ↔ rbpf.py circular import
that would otherwise force one of the consumers to duck-type the config.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np  # type: ignore[import-not-found]


@dataclass(frozen=True)
class ProcessNoiseConfig:
    """OU process-noise model. Defaults match `build_layered_noise_field`
    defaults for central-SoG April so PF predict and MPC σ_pos use the
    same physics as the simulator's truth-side noise.
    """

    # Per-component velocity amplitude (m/s). Mirrors LayeredNoiseField.
    sigma_coh_ms: float = 0.04
    sigma_plume_ms: float = 0.02
    sigma_submeso_ms: float = 0.05
    sigma_inertial_ms: float = 0.04
    sigma_white_ms: float = 0.015

    # Per-component OU temporal correlation time (sec). Each component
    # decorrelates at its own scale; coh/inertial slow, white fast.
    tau_coh_sec: float = 36.0 * 3600.0
    tau_plume_sec: float = 24.0 * 3600.0
    tau_submeso_sec: float = 12.0 * 3600.0
    tau_inertial_sec: float = 24.0 * 3600.0
    tau_white_sec: float = 3.0 * 3600.0

    # Vertical-profile parameters — match LayeredNoiseField.
    L_z_surf_m: float = 20.0
    L_z_inertial_m: float = 20.0
    plume_base_m: float = 5.0
    plume_width_m: float = 2.0

    # Inertial rotation period (h). 49°N → 16.5 h.
    f_inertial_period_h: float = 16.5

    @property
    def f_rad_per_sec(self) -> float:
        return float(2.0 * np.pi / (self.f_inertial_period_h * 3600.0))


# ---------------------------------------------------------------------------
# Vertical-profile evaluators (component → depth scaling).
# ---------------------------------------------------------------------------

def plume_profile(depth_m, cfg: ProcessNoiseConfig):
    """Tanh plume profile: 1 at surface, ~0 below `plume_base_m`."""
    z = np.maximum(depth_m, 0.0)
    return 0.5 * (1.0 - np.tanh(
        (z - cfg.plume_base_m) / max(cfg.plume_width_m, 0.1)
    ))


def submeso_profile(depth_m, cfg: ProcessNoiseConfig):
    """Surface-trapped exp profile."""
    z = np.maximum(depth_m, 0.0)
    return np.exp(-z / max(cfg.L_z_surf_m, 1e-6))


def inertial_profile(depth_m, cfg: ProcessNoiseConfig):
    """Near-inertial surface-trapped exp profile."""
    z = np.maximum(depth_m, 0.0)
    return np.exp(-z / max(cfg.L_z_inertial_m, 1e-6))


# ---------------------------------------------------------------------------
# OU evolution + integrated variance.
# ---------------------------------------------------------------------------

def ou_step(eta: np.ndarray, sigma: float, tau_sec: float, dt_sec: float,
             rng: np.random.Generator) -> np.ndarray:
    """Single-step exact OU update.

    For OU process dη/dt = -η/τ + (√(2/τ)·σ)·dW with stationary var σ²:
        η(t+dt) = γ · η(t) + σ · √(1 - γ²) · z,  z ~ N(0, I)
        γ = exp(-dt/τ)

    `eta`, returned array shapes preserved. Input `eta.shape` = `(N, 2)`
    typically (per-particle (u, v) amplitude).
    """
    if sigma <= 0 or tau_sec <= 0 or dt_sec <= 0:
        return eta
    gamma = float(np.exp(-dt_sec / tau_sec))
    z = rng.standard_normal(size=eta.shape)
    return gamma * eta + sigma * float(np.sqrt(max(1.0 - gamma ** 2, 0.0))) * z


def ou_integrated_var(sigma_sq: float, tau_sec: float, dt_sec: float) -> float:
    """Variance of ∫₀^dt η(s) ds for an OU process with stationary var σ².

    Used for σ_pos growth in MPC rollout — the per-tick position-step
    variance contribution from each component.

    Closed form:
        var = σ² · 2τ · (dt - τ·(1 - exp(-dt/τ)))

    Same formula used by `_compute_sigma_obs_per_particle` for the leg-
    integrated bias-Kalman σ_obs (single source of truth).
    """
    if sigma_sq <= 0 or tau_sec <= 0 or dt_sec <= 0:
        return 0.0
    return float(sigma_sq * 2.0 * tau_sec * (
        dt_sec - tau_sec * (1.0 - np.exp(-dt_sec / max(tau_sec, 1e-6)))
    ))


def sigma_pos_growth_rate_per_axis(
    depth_m: float, t_since_anchor_sec: float, cfg: ProcessNoiseConfig,
) -> float:
    """Instantaneous σ_pos²-per-axis growth RATE (m²/s) at given depth
    and time-since-anchor.

    For OU velocity at stationarity, the per-axis position variance:
        σ_pos²(T) = σ_anchor² + σ²·2τ·(T - τ·(1 - exp(-T/τ)))
    Differentiating in T:
        d(σ_pos²)/dT = σ²·2τ·(1 - exp(-T/τ))

    For T << τ this rate grows linearly in T (ballistic regime —
    velocity is approximately constant at its initial draw, so
    position grows as σ·T per particle, variance as σ²·T²); for
    T >> τ the rate plateaus at σ²·2τ (diffusive regime — velocity
    has decorrelated, position is a random walk).

    `t_since_anchor_sec` = time since the most recent position
    observation (LoRa fix). At T=0 the rate is 0 — the cluster doesn't
    spread until the velocity correlation has had time to develop into
    a position-variance contribution.

    Multiplying this rate by sub_dt gives the per-substep σ_pos²
    increment. Sum over components is performed inside this helper.

    The earlier per-substep `ou_integrated_var(dt)` formula was wrong:
    it gives σ²·dt² per substep regardless of elapsed time, and summing
    that across N substeps gives diffusive σ²·dt·T growth. Correct OU
    growth at T << τ is ballistic σ²·T² — orders of magnitude larger
    for slow components (coh: τ=36h, so over a 1-h horizon T/τ=0.028
    and the diffusive formula understates variance by ≈T/τ ≈ 36×).
    """
    p_z = plume_profile(depth_m, cfg)
    s_z = submeso_profile(depth_m, cfg)
    i_z = inertial_profile(depth_m, cfg)

    def _rate(sigma_sq: float, tau_sec: float) -> float:
        if sigma_sq <= 0 or tau_sec <= 0:
            return 0.0
        return float(sigma_sq * 2.0 * tau_sec * (
            1.0 - np.exp(-t_since_anchor_sec / max(tau_sec, 1e-6))
        ))

    return (
        _rate(cfg.sigma_coh_ms ** 2, cfg.tau_coh_sec)
        + _rate((p_z * cfg.sigma_plume_ms) ** 2, cfg.tau_plume_sec)
        + _rate((s_z * cfg.sigma_submeso_ms) ** 2, cfg.tau_submeso_sec)
        + _rate((i_z * cfg.sigma_inertial_ms) ** 2, cfg.tau_inertial_sec)
        + _rate(cfg.sigma_white_ms ** 2, cfg.tau_white_sec)
    )


def sigma_pos_growth_rate_per_axis_vec(
    depth_m_arr: np.ndarray, t_since_anchor_arr: np.ndarray,
    cfg: ProcessNoiseConfig,
) -> np.ndarray:
    """Vectorized version of `sigma_pos_growth_rate_per_axis` for MPC's
    per-beam rollout. `depth_m_arr` and `t_since_anchor_arr` must have
    the same shape; returns a same-shape array of growth rates (m²/s).

    Used by MPCStationKeeper's per-substep σ_pos² update so each beam
    can carry its own (depth_setpoint, t_since_anchor) without a Python
    for-loop over beams. Assumes σ_*, τ_* > 0 (always true with default
    ProcessNoiseConfig); the scalar guards in the non-vectorized form
    don't generalize cleanly to arrays and aren't needed in practice.
    """
    p_z = plume_profile(depth_m_arr, cfg)
    s_z = submeso_profile(depth_m_arr, cfg)
    i_z = inertial_profile(depth_m_arr, cfg)

    def _rate(sigma_sq, tau_sec):
        return sigma_sq * 2.0 * tau_sec * (
            1.0 - np.exp(-t_since_anchor_arr / tau_sec)
        )

    return (
        _rate(cfg.sigma_coh_ms ** 2, cfg.tau_coh_sec)
        + _rate((p_z * cfg.sigma_plume_ms) ** 2, cfg.tau_plume_sec)
        + _rate((s_z * cfg.sigma_submeso_ms) ** 2, cfg.tau_submeso_sec)
        + _rate((i_z * cfg.sigma_inertial_ms) ** 2, cfg.tau_inertial_sec)
        + _rate(cfg.sigma_white_ms ** 2, cfg.tau_white_sec)
    )
