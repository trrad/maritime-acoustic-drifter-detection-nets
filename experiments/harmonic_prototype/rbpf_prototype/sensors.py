"""Sensor observation models.

Each sensor provides:
  - a `sample(truth_state, rng)` method producing a noisy observation
  - a `log_likelihood(particle_state, observation)` returning a log-prob
    for each particle given the observation

For v1, all likelihoods are Gaussian for simplicity. We can swap in
Student-t or mixture likelihoods (Zhang et al. 2024 for LoRa multipath)
in v2.

Coordinate conventions:
  - positions in (lat_deg, lon_deg)
  - velocities in m/s, (u = east, v = north)
  - time in seconds since truth-dataset t0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np  # type: ignore[import-not-found]

from truth_field import EARTH_R_M  # type: ignore[import-not-found]


@dataclass
class LoRaRangeSensor:
    """Noisy range observation to a set of anchors, usable only when node
    is within `max_depth_m` of surface.

    anchors: list of (lat, lon) for each anchor (anchors assumed to be at
      the surface, with GPS-accurate position; σ_anchor_pos ~1-5 m, well
      below typical σ_m ranging noise so absorbed into σ_m).
    sigma_m: per-range observation noise (1-sigma). Realistic over-sea
      LoRa at 915 MHz with surface multipath: 50–200 m. Default 100 m
      (Zhang et al. 2024 Ocean Engineering, mid-range of empirical SX1262
      RSSI-inversion residuals over open water; SX1262 has no native TOF
      ranging at 915 MHz so this value also subsumes the ranging-method
      residual).
    max_depth_m: LoRa propagation cutoff — no observation below this depth.
    max_range_m: Effective LoRa-over-sea LOS cap. Anchors farther than
      this from the drifter return NaN range (= "no observation"); the
      caller is expected to filter NaN before reweight / multilaterate.
      Default 20_000 m: marine LoRa at 915 MHz with elevated buoy
      antennas (3-5 m above MSL) and 20 dBm transmit / -130 dBm
      sensitivity is conservatively measured at 15–40 km LOS over open
      water; 20 km is the conservative midpoint that still gives the
      30-km SoG bbox enough redundant coverage for most positions to
      see ≥3 anchors. Tighten this for noisier environments (heavy
      multipath, low antennas, dense interference).
    """

    anchors: Sequence[tuple[float, float]]
    sigma_m: float = 100.0
    max_depth_m: float = 1.0
    max_range_m: float = 20_000.0

    def can_observe(self, depth_m: float) -> bool:
        return depth_m <= self.max_depth_m

    def sample(self, true_lat: float, true_lon: float,
               rng: np.random.Generator) -> list[float]:
        """Return one noisy range per anchor; NaN for out-of-range
        anchors so callers can filter (vs. silently propagating an
        unrealistically-noisy "observation" from outside LoRa LOS)."""
        out = []
        for alat, alon in self.anchors:
            r = _gc_distance_m(true_lat, true_lon, alat, alon)
            if r > self.max_range_m:
                out.append(float("nan"))
            else:
                out.append(r + rng.normal(0.0, self.sigma_m))
        return out

    def log_likelihood_per_particle(
        self, particle_lats: np.ndarray, particle_lons: np.ndarray,
        observations: Sequence[float],
    ) -> np.ndarray:
        """Sum of per-anchor Gaussian log-likelihoods, per particle.
        Skips NaN observations (anchors out of LoRa range)."""
        logw = np.zeros_like(particle_lats)
        for (alat, alon), z in zip(self.anchors, observations):
            if not np.isfinite(z):
                continue
            # Vectorized range from each particle to this anchor.
            cos_lat = np.cos(np.deg2rad(alat))
            dlat_m = (particle_lats - alat) * EARTH_R_M
            dlon_m = (particle_lons - alon) * EARTH_R_M * cos_lat
            r = np.sqrt(dlat_m**2 + dlon_m**2)
            logw += -0.5 * ((r - z) / self.sigma_m) ** 2
        return logw


@dataclass
class RelativeFlowSensor:
    """Measures water velocity (u, v) at the node's location.

    In reality this is a compact Doppler / rotor / pitot + empirical slip
    calibration that collectively delivers an (u, v) in world frame with
    effective sigma σ_flow_ms. The effective σ encapsulates:
      - raw sensor noise
      - slip-model residual after calibration
      - heading/orientation error propagated through body→world transform

    Published accuracy for comparable systems:
      - Drogued SVP surface (GPS-referenced): 1-3 cm/s (Niiler & Paduan 1995)
      - Active gliders + AD2CP: ~1 cm/s (Todd et al. 2017)
      - Passive subsurface w/ compact sensor: unverified, needs our own
        validation (see subagent note).

    We parameterize as the final delivered σ_flow and sweep it.
    """

    sigma_ms: float = 0.05            # 5 cm/s default — optimistic post-calibration
    cadence_sec: float = 600.0        # observation every 10 min (typical duty-cycled)
    # When the node is moving vertically (transitioning), wave/flow
    # transients make the reading unreliable. Above this magnitude skip.
    max_vertical_speed_ms: float = 0.02

    def sample(self, u_truth: float, v_truth: float,
                rng: np.random.Generator) -> tuple[float, float]:
        return (u_truth + rng.normal(0.0, self.sigma_ms),
                v_truth + rng.normal(0.0, self.sigma_ms))

    def log_likelihood_per_particle(
        self,
        particle_currents_u: np.ndarray,   # prior-predicted u at each particle
        particle_currents_v: np.ndarray,   # prior-predicted v at each particle
        obs_u: float, obs_v: float,
    ) -> np.ndarray:
        """Each particle's log-likelihood = Gaussian prob that the
        observation matches the PRIOR's prediction of current at the
        particle's position. This is the mechanism by which the PF
        identifies which positions are more consistent with observed
        currents."""
        resid_u = particle_currents_u - obs_u
        resid_v = particle_currents_v - obs_v
        return -0.5 * (resid_u**2 + resid_v**2) / self.sigma_ms**2


@dataclass
class CTDSensor:
    """Conductivity / temperature / depth sensor.

    Observation model (per `docs/reference/ctd_sensor_model.md` §3):

        S_obs = S_true(lat, lon, depth, t) + w_S
        T_obs = T_true(lat, lon, depth, t) + w_T

    with Gaussian noise σ_S ≈ 0.02 PSU, σ_T ≈ 0.01 °C for a compact
    matchbox-class sensor (RBR Coda³ T.D, Seabird SBE 39plus). The
    instrument noise is ~10× smaller than the SalishSeaCast forecast
    bias (Soontiens & Allen 2017: salinity bias −0.29 to −0.67 g/kg,
    temperature bias +0.3 to +0.5 °C across SoG sub-regions). The
    observation residual is therefore dominated by **forecast error**,
    not sensor noise — see `ctd_sensor_model.md` §1.

    Two PF integration roles:

      1. **Direct PF reweight every submerged tick.** Particles in
         water masses inconsistent with the observed (T, S) get
         exponentially down-weighted. Tightens the PF *between* LoRa
         surface events — currently the only between-surface
         observation we have.
      2. **Plume-offset observation** (v3 bias learner, future).
         Salinity residual `r_S = S_obs − S_predicted_at_particle`
         × `∂S/∂x_plume` recovers δ_plume, the plume-front mis-
         placement scalar. Adaptively informative — large signal at
         plume fronts, ~zero in basin water.

    No depth-transition gating (unlike `RelativeFlowSensor`): T/S are
    scalar properties of the water the node sits in; vertical motion
    doesn't compromise the reading the way it does for current
    sensors that infer flow from slip.
    """
    sigma_T_c: float = 0.01           # °C, compact-class
    sigma_S_psu: float = 0.02         # PSU
    cadence_sec: float = 600.0        # one observation per 10-min tick

    def sample(self, T_truth: float, S_truth: float,
                rng: np.random.Generator) -> tuple[float, float]:
        return (T_truth + float(rng.normal(0.0, self.sigma_T_c)),
                S_truth + float(rng.normal(0.0, self.sigma_S_psu)))

    def log_likelihood_per_particle(
        self,
        T_per_particle: np.ndarray,
        S_per_particle: np.ndarray,
        z_T: float, z_S: float,
    ) -> np.ndarray:
        """Gaussian log-likelihood over both channels (independent
        noise, so log-likelihoods sum). Returns shape (N,) array; sign
        is negative-or-zero, with zero achieved at exact match."""
        return (-0.5 * ((T_per_particle - z_T) / self.sigma_T_c) ** 2
                + -0.5 * ((S_per_particle - z_S) / self.sigma_S_psu) ** 2)


def _gc_distance_m(lat1, lon1, lat2, lon2):
    """Equirectangular distance (meters). Good enough for < 100 km."""
    cos_lat = np.cos(np.deg2rad(0.5 * (lat1 + lat2)))
    dlat_m = (lat1 - lat2) * EARTH_R_M
    dlon_m = (lon1 - lon2) * EARTH_R_M * cos_lat
    return float(np.sqrt(dlat_m**2 + dlon_m**2))
