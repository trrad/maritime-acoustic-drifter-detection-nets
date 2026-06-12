"""DRAFT (not run yet): minimal simulator-in-the-loop scaffold.

Shape of the test we want to run once the climatology models are in
place. Directly evaluates the actual prototype question:

    Given truth (SalishSeaCast 2023) + some compressed onboard
    climatology, can a PF track position, and can a ballast drifter
    steer usefully, with realistic sensor noise?

NOT implementing the PF here yet — stubbed. The PF will eventually
be a simplified standalone or reuse `rtl/vectors/maritime/pf_float.py`.
The structure below is the shape the caller needs regardless of which
PF implementation goes in the middle.

Pieces (in order of dependency):

    1. Truth: `velocity_at(lat, lon, t, depth) → (u, v)` from the
       SalishSeaCast cache. Already built in 08_drifter_trajectories.py.
       Refactor into shared helper.

    2. Compressed climatology: `ClimatologyLike.velocity_at(lat, lon, t, depth)`
       → (u, v, var). One per candidate model (perfect, harmonic-4,
       harmonic+DoY, etc.). Fed to the PF as its predict-stage prior.

    3. Sensor models (at each tick):
        - GPS: noisy (lat, lon) obs at sparse cadence (every N ticks).
        - IMU: not used in this prototype — we're testing currents,
          not dead-reckoning accuracy.
        - Baro: depth observation (known for controlled ballast anyway).
        - LoRa to anchor: noisy range to each of K known anchor points.

    4. PF: maintains particle cloud in (lat, lon, depth) space. Predict
       step uses climatology + process noise. Weight step uses sensor
       obs likelihoods.

    5. Ballast controller (for ballast-class drifter only): at each
       ballast-decision cadence (much slower than tick — every few
       hours), picks the depth level that best serves an objective:
        - station-keeping: minimize distance from home
        - directional drift: maximize drift toward goal

    6. Scoring:
        - PF tracking: mean position error over run
        - Steering: achieved displacement toward goal vs controllable
          envelope measured in step 7 of FINDINGS

Two experiments we want to run:

    E1. Fixed-depth pure drifter, different priors.
        - Compare PF tracking error under:
            (a) perfect climatology (truth itself)
            (b) harmonic-only
            (c) harmonic + DoY residual
            (d) zero prior (dead-reckoning only)
        - Answers: how much does the climatology's quality matter to
          the PF's position estimate?

    E2. Ballast-class drifter, depth-control under prior.
        - Same priors as E1.
        - Controller chooses depth to maximize "toward-goal" drift.
        - Measure: how close does the controlled drifter get to the
          goal vs the passive-fixed-depth case?
        - Answers: does the onboard prior support useful steering, or
          does the compression destroy the signal the controller
          needs?

Deferred issues:
- Bbox size (20×20 km) limits simulation to < 24h. Need bigger fetch
  for multi-day trajectory experiments. Don't address now — run short
  sims first.
- PF implementation — stubbed here, not prototyped.
- Anchor placement for LoRa — use 2 fixed anchors at bbox corners as
  a first pass.
"""

# --- stub out the moving pieces ---

from dataclasses import dataclass


@dataclass
class DrifterState:
    lat_deg: float
    lon_deg: float
    depth_m: float


@dataclass
class SensorSuite:
    gps_period_sec: float
    gps_noise_m: float
    baro_noise_m: float
    lora_anchor_positions: list[tuple[float, float]]  # [(lat, lon), ...]
    lora_period_sec: float
    lora_noise_m: float


@dataclass
class SimConfig:
    duration_sec: float
    dt_sec: float
    start: DrifterState
    sensors: SensorSuite
    is_ballast: bool
    ballast_available_depths_m: list[float]       # discrete choices
    ballast_control_period_sec: float
    climatology_name: str                          # "perfect" | "harmonic_4" | "harmonic_doy" | "none"


def run_simulation(cfg: SimConfig) -> dict:
    """NOT IMPLEMENTED. Returns per-tick:
        - truth_state (lat, lon, depth)
        - pf_estimate (mean, cov_diag, n_effective)
        - observations (list)
        - ballast_command (if is_ballast)
    """
    raise NotImplementedError("simulator skeleton only — see module docstring")


if __name__ == "__main__":
    print(__doc__)
