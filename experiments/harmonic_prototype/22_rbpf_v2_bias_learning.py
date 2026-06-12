"""Phase 2: RBPF with reduced-rank bias-field learning — process-noise sweep.

Extends the Phase 1 position-only RBPF (script 21) with a per-particle
station-relative grid basis × depth for the forecast-error bias. At each
surface event the 6-h leg displacement residual is a linear-Gaussian
observation of the dwell-weighted cell biases; each particle runs a
diagonal Kalman update in closed form.

Initial Phase 2 run (σ_fc = 20 cm/s, no PF process noise) showed bias
learning hurt as often as it helped, with PF error actually *larger*
than the no-learn baseline at some stations. Root cause: with zero
process noise and 20 m init spread, the particle cluster dead-reckons
*identically* through a leg — every particle sees the same prior at
(essentially) the same position. When they surface all at the same
wrong place, LoRa reweighting can't discriminate (uniform weights, no
resample), and my Kalman update then applies the same bias to every
particle — effectively anchoring the cluster at whatever offset leg 1
produced. A failed tight cluster stays failed.

This sweep tests whether per-tick process noise on the PF predict
(stdev `process_noise_ms`) gives the cluster enough diversity that LoRa
can pull the posterior back toward truth, and whether bias learning
then becomes a net positive. σ_fc held at 20 cm/s (the hard regime).

Configs per process-noise value:
  - Phase 1 no-learn baseline (bias_cfg=None)
  - Phase 2 v1 grid, no smoothing

`grid_smooth` dropped — first-pass results showed smoothing diluted the
learned bias when the node station-kept in one cell; revisit after the
dispersion question is resolved.

Output: figures/25_rbpf_v2_bias_learning.png.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np  # type: ignore[import-not-found]

from ballast_controller import StationKeeper  # type: ignore[import-not-found]
from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
from rbpf_prototype import (  # type: ignore[import-not-found]
    BiasConfig, CTDSensor, Experiment, FixedIntervalPolicy,
    GeometricIntervalPolicy, LoRaRangeSensor, PFConfig, SensorConfig,
    SimConfig, StationConfig, run_one_station,
)
from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
from submesoscale import build_layered_noise_field  # type: ignore[import-not-found]
from truth_field import (  # type: ignore[import-not-found]
    EARTH_R_M, build_tracer_field, build_truth_field, distance_m,
)


# --- Domain (matches Phase 1) ---
LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
MONTHS = ["2023-04"]

# --- Stations (identical to Phase 1) ---
HAND_PICKED_STATIONS = [
    (49.3533, -123.7411, 289),
    (49.3533, -123.6892, 188),
    (49.3924, -123.7411, 182),
    (49.3924, -123.6374,  92),
    (49.3091, -123.6773, 115),
    (49.2699, -123.7033, 373),
    (49.3287, -123.7810, 410),
    (49.3533, -123.5855,  90),
]
CAP_DEPTH_MARGIN = 0.8
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]

# --- Sim ---
RUN_HOURS = 72
DT_SEC = 600.0
CONTROL_CADENCE_SEC = 1800.0
LOOKAHEAD_SEC = 1800.0
W_Z_MAX_MS = 0.1
INITIAL_DEPTH_M = 10.0
SURFACE_DWELL_H = 0.5
LORA_CADENCE_SEC = 60.0

# --- Prior ---
# Five-component layered noise model for central-SoG forecast error
# (docs/reference/noise_model_design.md §3; domain-review rationale at
# docs/reference/noise_model_boundary_review_2026-04-24.md).
#
# At σ_fc_ref = 0.08 m/s (Halverson 2018 central-SoG surface anchor):
#   coh       4.0 cm/s  depth-coherent      barotropic+baroclinic-tide residual
#   plume     2.0 cm/s  tanh(base=5m, w=2m) Fraser plume slab (April: small)
#   submeso   5.0 cm/s  exp(-z/20m)         submeso + Ekman wind-slab
#   inertial  4.0 cm/s  exp(-z/20m)         rotating at f(49°N), period 16.5 h
#   white     1.5 cm/s  flat, fast          unlearnable small-scale residual
# Surface total per-component RMS = √(16+4+25+16+2.25) = 7.95 cm/s ≈ 8 cm/s.
# Deep floor = √(16 + 2.25) = 4.3 cm/s (barotropic + white; cannot escape).
#
# The reference month is April — pre-freshet, plume influence minimal.
# Seasonal variation (summer freshet peak → σ_plume up ~3×, σ_submeso
# slightly down, ML shallower so L_z_surf → 15 m) is a future knob.
#
# SIGMA_FC_MS scales all five components uniformly (ratios preserved);
# sweep {8, 12, 15} covers central basin → plume-adjacent → wind-event.
SIGMA_FORECAST_MS = float(os.environ.get("SIGMA_FC_MS", "0.08"))
NOISE_SEED = int(os.environ.get("NOISE_SEED", "42"))

SIGMA_FC_REF_MS = 0.08
SIGMA_COH_REF_MS = 0.04
SIGMA_PLUME_REF_MS = 0.02
SIGMA_SUBMESO_REF_MS = 0.05
SIGMA_INERTIAL_REF_MS = 0.04
SIGMA_WHITE_REF_MS = 0.015

# Per-component vertical structure. April values (winter-deep ML,
# Kastner 2018 plume thickness).
PLUME_BASE_M = 5.0
PLUME_WIDTH_M = 2.0
L_Z_SURF_M = 20.0
L_Z_INERTIAL_M = 20.0

# Per-component spatial/temporal correlation scales (slow components
# match their physical scales; white is the small-scale residual).
COH_SIGMA_S_CELLS = 10.0        # 5 km
COH_SIGMA_T_HOURS = 36.0
PLUME_SIGMA_S_CELLS = 4.0       # 2 km (plume fronts narrow)
PLUME_SIGMA_T_HOURS = 24.0
SUBMESO_SIGMA_S_CELLS = 10.0    # 5 km (submeso + Ekman)
SUBMESO_SIGMA_T_HOURS = 12.0
INERTIAL_SIGMA_S_CELLS = 40.0   # 20 km (wind events regional)
INERTIAL_SIGMA_T_HOURS = 24.0
WHITE_SIGMA_S_CELLS = 2.0       # 1 km
WHITE_SIGMA_T_HOURS = 3.0

# Per-tick velocity perturbation stddev on each particle. Set to
# σ_forecast so PF integrated variance matches the forecast error the
# PF is trying to track — otherwise particles cluster within 10-100 m
# while the true state has drifted kilometres, and LoRa reweighting
# can't resolve the gap (sample impoverishment, Claus & Bachmayer 2015
# J. Field Robotics — their Slocum TAN used calibrated jitter and held
# 33-50 m RMS vs km-scale dead-reckoning).
PROCESS_NOISE_MS = SIGMA_FORECAST_MS

# --- Anchors ---
ANCHOR_OFFSETS_KM = [(+5.0, +5.0), (-5.0, +5.0), (0.0, -6.0)]
LORA_SIGMA_M = 20.0
LORA_MAX_DEPTH_M = 1.0

# --- PF ---
PF_N = 500
PF_INIT_SIGMA_M = 20.0

# --- Bias (v1 grid) ---
BIAS_N_CELLS = 8
BIAS_CELL_SIZE_M = 2000.0
BIAS_OBS_POSITION_SIGMA_M = 20.0


def _scale(ref: float, sigma_fc: float) -> float:
    return ref * sigma_fc / SIGMA_FC_REF_MS


def sigma_coh_ms(sigma_fc: float) -> float:
    return _scale(SIGMA_COH_REF_MS, sigma_fc)


def sigma_plume_ms(sigma_fc: float) -> float:
    return _scale(SIGMA_PLUME_REF_MS, sigma_fc)


def sigma_submeso_ms(sigma_fc: float) -> float:
    return _scale(SIGMA_SUBMESO_REF_MS, sigma_fc)


def sigma_inertial_ms(sigma_fc: float) -> float:
    return _scale(SIGMA_INERTIAL_REF_MS, sigma_fc)


def sigma_white_ms(sigma_fc: float) -> float:
    return _scale(SIGMA_WHITE_REF_MS, sigma_fc)


def sigma_slow_ms(sigma_fc: float) -> float:
    """Learnable (slow) amplitude proxy: quadrature of coh + surface-
    trapped components the bias learner could in principle recover
    (coh + plume + submeso + inertial). Inertial is rotating at 16.5 h,
    so over a multi-day learning window it averages out of displacement
    observations; still include it as a conservative upper bound so the
    Kalman prior sigma doesn't underestimate learnable structure.
    """
    return float(np.sqrt(
        sigma_coh_ms(sigma_fc) ** 2
        + sigma_plume_ms(sigma_fc) ** 2
        + sigma_submeso_ms(sigma_fc) ** 2
        + sigma_inertial_ms(sigma_fc) ** 2
    ))


# Integrated unlearnable-component drift σ over a 6h leg.
# OU process with correlation time τ_white=3h, integrated over
# T=6h → drift RMS = σ_white · sqrt(2·τ·T). With τ and T in seconds,
# sqrt(2·10800·21600) ≈ 21600 s. In the 5-component model the white
# residual is the only truly unlearnable component (inertial averages
# out of displacement over multi-day spans; plume/submeso/coh are
# slowly-varying and learnable in principle).
OBS_LEG_COEFF = float(np.sqrt(2.0 * 3.0 * 3600.0 * 6.0 * 3600.0))


def bias_sigma_obs_leg_m(sigma_fc: float) -> float:
    # Floor so the Kalman denominator stays well-conditioned at σ=0.
    return max(sigma_white_ms(sigma_fc) * OBS_LEG_COEFF, 50.0)

# --- Metrics ---
ROUGH_ENVELOPE_M = 3000.0
ENVELOPES_M = [500.0, 1000.0, 2000.0, 4000.0, 6000.0]

FIG_DIR = Path(__file__).parent / "figures"


# ---------------------------------------------------------------------------
# Semantics (corrected from Phase 1):
#   SalishSeaCast = the 500 m resolution NEMO hindcast we fetched from ERDDAP
#     — the *model* available to an operator.
#   Reality = SalishSeaCast + unresolved submesoscale noise — what a real
#     deployed drifter would actually drift in.
#   The node's dynamics advect it in reality; the PF and controller
#   consume SalishSeaCast alone (matches how a real deployment would run).
# ---------------------------------------------------------------------------

@dataclass
class RealCurrents:
    """Ground truth physics for the sim: SalishSeaCast + unresolved submesoscale.

    Exposes both `.sample()` (for the ballast dynamics) and
    `.get_current_at()` (for `PerfectKnowledge`-style consumers, e.g. the
    `baseline_real` controller). Identical computation; two names so
    duck-typed code doesn't need to care which it calls.
    """
    nemo: "object"             # TruthField
    noise: "object | None"     # SubmesoscaleField-like; None = clean

    def sample(self, lat, lon, depth_m, t_sec):
        ut, vt = self.nemo.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        if self.noise is None:
            return ut, vt
        un, vn = self.noise.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        return ut + un, vt + vn

    def get_current_at(self, lat, lon, depth_m, t_sec):
        return self.sample(lat, lon, depth_m, t_sec)


@dataclass
class NemoPrior:
    """Clean-SalishSeaCast knowledge source — what the operator's
    deterministic hindcast/forecast actually provides. Used as the PF's
    prior and the PF-config controller's knowledge."""
    nemo: "object"             # TruthField

    def sample(self, lat, lon, depth_m, t_sec):
        return self.nemo.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]

    def get_current_at(self, lat, lon, depth_m, t_sec):
        return self.sample(lat, lon, depth_m, t_sec)


def run_baseline(dynamics, controller_knowledge,
                  station: StationConfig, cfg: SimConfig) -> dict:
    """Controller-ceiling baseline — no PF, perfect position.

    `dynamics`: object with `.sample(lat, lon, depth, t)`; drives the
      node's horizontal advection. Should be `real` (SalishSeaCast+noise)
      for an honest sim.
    `controller_knowledge`: object with `.get_current_at(lat, lon, depth, t)`;
      passed as the `StationKeeper`'s knowledge. `real` → absolute
      physics ceiling; `nemo` → operator-ceiling (what a real
      deployment with the same depth controller can achieve).
    """
    keeper = StationKeeper(
        station_lat=station.lat, station_lon=station.lon,
        available_depths_m=station.available_depths_m,
        lookahead_sec=cfg.lookahead_sec,
        knowledge=controller_knowledge,
    )

    def dyn_current(t_sec, lat, lon, depth_m):
        return dynamics.sample(lat, lon, depth_m, t_sec)

    state = BallastState(
        lat=station.lat, lon=station.lon,
        depth_m=cfg.initial_depth_m, depth_setpoint_m=cfg.initial_depth_m,
    )
    n_steps = int(cfg.run_hours * 3600 / cfg.dt_sec)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    lats[0], lons[0] = state.lat, state.lon
    t_sec = 0.0
    last_decision = -cfg.control_cadence_sec
    for i in range(n_steps):
        if t_sec - last_decision >= cfg.control_cadence_sec - 1e-6:
            chosen, _ = keeper.choose_depth(state.lat, state.lon, t_sec)
            state = set_setpoint(state, chosen)
            last_decision = t_sec
        state = step(state, t_sec, cfg.dt_sec,
                     current_at=dyn_current, w_z_max_ms=cfg.w_z_max_ms)
        t_sec += cfg.dt_sec
        lats[i + 1], lons[i + 1] = state.lat, state.lon
    dists = np.array([distance_m(la, lo, station.lat, station.lon)
                       for la, lo in zip(lats, lons)])
    valid = np.isfinite(dists)
    if not valid.all():
        last = np.where(valid)[0]
        dists = (np.where(valid, dists, dists[last[-1]]) if len(last) > 0
                  else np.full_like(dists, np.inf))
    return {
        "ctrl_mean_m": float(np.nanmean(dists)),
        "ctrl_max_m": float(np.nanmax(dists)),
        "envelope_fracs": {e: float((dists <= e).mean()) for e in ENVELOPES_M},
        "lats": lats, "lons": lons, "dists_m": dists,
    }


def offsets_km_to_latlon(ref_lat, ref_lon, dn_km, de_km):
    cos_lat = np.cos(np.deg2rad(ref_lat))
    return (ref_lat + dn_km * 1000.0 / EARTH_R_M,
            ref_lon + de_km * 1000.0 / (EARTH_R_M * cos_lat))


def depth_set_for_bathy(bathy_m):
    max_allowed = min(50.0, bathy_m * CAP_DEPTH_MARGIN)
    return [d for d in DEFAULT_DEPTH_SET if d <= max_allowed]


def main() -> None:
    print("=== Phase 2: RBPF with bias-field learning ===", flush=True)
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    # M2: tracer fetch alongside u/v, both cached separately so the U/V
    # cache is unaffected. After the first run the T/S cache is on disk
    # and subsequent calls return ~instantly.
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False, include_tracers=True)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)

    print("building SalishSeaCast (u, v) interpolator ...", flush=True)
    t0 = time.time()
    nemo = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    print(f"  {time.time() - t0:.1f}s", flush=True)

    print("building SalishSeaCast (T, S) interpolator ...", flush=True)
    t0 = time.time()
    tracer = build_tracer_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    print(f"  {time.time() - t0:.1f}s", flush=True)

    print(f"building layered noise σ_fc={SIGMA_FORECAST_MS*100:.0f}cm/s "
          f"[coh={sigma_coh_ms(SIGMA_FORECAST_MS)*100:.1f}, "
          f"plume={sigma_plume_ms(SIGMA_FORECAST_MS)*100:.1f}, "
          f"submeso={sigma_submeso_ms(SIGMA_FORECAST_MS)*100:.1f}, "
          f"inertial={sigma_inertial_ms(SIGMA_FORECAST_MS)*100:.1f}, "
          f"white={sigma_white_ms(SIGMA_FORECAST_MS)*100:.1f} cm/s]",
          flush=True)
    t0 = time.time()
    noise = build_layered_noise_field(
        ds, lats_grid, lons_grid,
        sigma_coh_ms=sigma_coh_ms(SIGMA_FORECAST_MS),
        sigma_plume_ms=sigma_plume_ms(SIGMA_FORECAST_MS),
        sigma_submeso_ms=sigma_submeso_ms(SIGMA_FORECAST_MS),
        sigma_inertial_ms=sigma_inertial_ms(SIGMA_FORECAST_MS),
        sigma_white_ms=sigma_white_ms(SIGMA_FORECAST_MS),
        plume_base_m=PLUME_BASE_M,
        plume_width_m=PLUME_WIDTH_M,
        L_z_surf_m=L_Z_SURF_M,
        L_z_inertial_m=L_Z_INERTIAL_M,
        coh_sigma_s_cells=COH_SIGMA_S_CELLS,
        coh_sigma_t_hours=COH_SIGMA_T_HOURS,
        plume_sigma_s_cells=PLUME_SIGMA_S_CELLS,
        plume_sigma_t_hours=PLUME_SIGMA_T_HOURS,
        submeso_sigma_s_cells=SUBMESO_SIGMA_S_CELLS,
        submeso_sigma_t_hours=SUBMESO_SIGMA_T_HOURS,
        inertial_sigma_s_cells=INERTIAL_SIGMA_S_CELLS,
        inertial_sigma_t_hours=INERTIAL_SIGMA_T_HOURS,
        white_sigma_s_cells=WHITE_SIGMA_S_CELLS,
        white_sigma_t_hours=WHITE_SIGMA_T_HOURS,
        seed=NOISE_SEED,
    )
    print(f"  built in {time.time() - t0:.1f}s  "
          f"surface_rms={noise.surface_rms_ms()*100:.2f} cm/s  "
          f"deep_rms={noise.deep_rms_ms()*100:.2f} cm/s", flush=True)

    # Assemble the two world models used throughout:
    #   `real`  — SalishSeaCast + unresolved noise (drives the dynamics).
    #   `nemo_prior` — clean SalishSeaCast (operator's model; PF + controller).
    real = RealCurrents(nemo=nemo, noise=noise)
    nemo_prior = NemoPrior(nemo=nemo)

    # Snap hand-picked stations to the SalishSeaCast cell grid.
    candidates = []
    for s_lat_target, s_lon_target, _ in HAND_PICKED_STATIONS:
        gy = int(np.argmin(np.abs(nemo.lat_axis - s_lat_target)))
        gx = int(np.argmin(np.abs(nemo.lon_axis - s_lon_target)))
        candidates.append((gy, gx))
    print(f"stations: {len(candidates)}", flush=True)

    # Surfacing-cadence sweep: fixed 1 h vs fixed 6 h vs geometric (the
    # principled T ∝ 1/σ_post schedule; see `/docs/phase2_surfacing.md`
    # if it gets written up). Each run instantiates a fresh policy
    # because GeometricIntervalPolicy is stateful (tracks leg index).
    GEOMETRIC_PERIODS_H = [2.5, 4.0, 6.0, 12.0, 30.0]

    def make_policy(name: str):
        if name == "fixed_3h":
            return FixedIntervalPolicy(period_h=3.0)
        if name == "fixed_6h":
            return FixedIntervalPolicy(period_h=6.0)
        if name == "fixed_12h":
            return FixedIntervalPolicy(period_h=12.0)
        if name == "geometric":
            return GeometricIntervalPolicy(periods_h=list(GEOMETRIC_PERIODS_H))
        raise ValueError(name)

    POLICIES = ["fixed_3h", "fixed_6h", "fixed_12h", "geometric"]

    sim_cfg = SimConfig(
        run_hours=RUN_HOURS, dt_sec=DT_SEC,
        control_cadence_sec=CONTROL_CADENCE_SEC,
        lookahead_sec=LOOKAHEAD_SEC,
        w_z_max_ms=W_Z_MAX_MS, initial_depth_m=INITIAL_DEPTH_M,
        surface_dwell_h=SURFACE_DWELL_H, lora_cadence_sec=LORA_CADENCE_SEC,
    )
    pf_cfg = PFConfig(n_particles=PF_N, init_sigma_m=PF_INIT_SIGMA_M,
                       process_noise_ms=PROCESS_NOISE_MS)

    def make_bias_cfg() -> BiasConfig:
        # σ_obs is now computed analytically per-leg from the unlearnable
        # layered-noise components × dwell-weighted depth attenuation;
        # the old sigma_obs_leg_m / obs_position_sigma_m knobs are gone.
        return BiasConfig(
            n_cells=BIAS_N_CELLS, cell_size_m=BIAS_CELL_SIZE_M,
            sigma_bias_init_ms=sigma_slow_ms(SIGMA_FORECAST_MS),
        )

    # Keys: (policy_name, config_name).
    results: dict[tuple[str, str], list[dict]] = {}
    baseline_real_per_station: list[dict] = []
    baseline_nemo_per_station: list[dict] = []

    for idx, (gy, gx) in enumerate(candidates):
        s_lat = float(nemo.lat_axis[gy])
        s_lon = float(nemo.lon_axis[gx])
        s_bathy = float(bathy_grid[gy, gx])
        d_set = depth_set_for_bathy(s_bathy)
        if len(d_set) < 2:
            continue
        u0, v0 = nemo.sample(s_lat, s_lon, INITIAL_DEPTH_M, 0.0)
        if not (np.isfinite(u0) and np.isfinite(v0)):
            continue

        station = StationConfig(
            lat=s_lat, lon=s_lon, envelope_m=ROUGH_ENVELOPE_M,
            available_depths_m=d_set,
        )
        print(f"\nstation {idx+1}/{len(candidates)}: "
              f"({s_lat:.4f}, {s_lon:.4f}) bathy={s_bathy:.0f}m", flush=True)

        # Two baselines: absolute ceiling (controller knows reality) and
        # operator ceiling (controller knows only the model). The gap
        # between them is the "unresolved physics tax".
        t0 = time.time()
        b_real = run_baseline(real, real, station, sim_cfg)
        b_real["station_lat"] = s_lat; b_real["station_lon"] = s_lon
        baseline_real_per_station.append(b_real)
        print(f"  baseline_real    mean={b_real['ctrl_mean_m']:5.0f}m "
              f"max={b_real['ctrl_max_m']:5.0f}m "
              f"%<500m={b_real['envelope_fracs'][500.0]*100:3.0f}% "
              f"({time.time()-t0:.1f}s)", flush=True)

        t0 = time.time()
        b_nemo = run_baseline(real, nemo_prior, station, sim_cfg)
        b_nemo["station_lat"] = s_lat; b_nemo["station_lon"] = s_lon
        baseline_nemo_per_station.append(b_nemo)
        print(f"  baseline_nemo    mean={b_nemo['ctrl_mean_m']:5.0f}m "
              f"max={b_nemo['ctrl_max_m']:5.0f}m "
              f"%<500m={b_nemo['envelope_fracs'][500.0]*100:3.0f}% "
              f"({time.time()-t0:.1f}s)", flush=True)

        anchors = [offsets_km_to_latlon(s_lat, s_lon, dn, de)
                   for (dn, de) in ANCHOR_OFFSETS_KM]
        sensor_cfg_no_ctd = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors,
                                   sigma_m=LORA_SIGMA_M,
                                   max_depth_m=LORA_MAX_DEPTH_M),
            flow=None,
            ctd=None,
        )
        sensor_cfg_with_ctd = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors,
                                   sigma_m=LORA_SIGMA_M,
                                   max_depth_m=LORA_MAX_DEPTH_M),
            flow=None,
            ctd=CTDSensor(),
        )

        for policy_name in POLICIES:
            # Three configs: no_learn (baseline), grid (v1 bias learner),
            # grid+ctd (v1 bias learner + CTD per-tick reweight). The CTD
            # config uses the same bias-field plumbing as `grid`; the
            # only delta is the per-submerged-tick T/S likelihood.
            configs: list[tuple[str, BiasConfig | None, SensorConfig]] = [
                ("no_learn", None, sensor_cfg_no_ctd),
                ("grid", make_bias_cfg(), sensor_cfg_no_ctd),
                ("grid+ctd", make_bias_cfg(), sensor_cfg_with_ctd),
            ]
            for cfg_name, bias_cfg, sensor_cfg in configs:
                t0 = time.time()
                exp = Experiment(
                    station=station, sim=sim_cfg, sensor=sensor_cfg,
                    pf_cfg=pf_cfg, truth=real, prior=nemo_prior,
                    surfacing=make_policy(policy_name), bias_cfg=bias_cfg,
                    tracer_truth=tracer, tracer_prior=tracer,
                )
                r = run_one_station(exp, seed=1000 + idx)
                dt = time.time() - t0
                key = (policy_name, cfg_name)
                results.setdefault(key, []).append({
                    "station_lat": s_lat, "station_lon": s_lon,
                    "dists_m": r.dists_m,
                    "ctrl_mean_m": r.ctrl_mean_m(),
                    "ctrl_max_m": r.ctrl_max_m(),
                    "envelope_fracs": {e: r.envelope_frac(e) for e in ENVELOPES_M},
                    "pf_err_mean_m": float(np.mean(r.pf_err_m)),
                    "pf_err_max_m": float(np.max(r.pf_err_m)),
                    "pf_std_mean_m": float(np.mean(r.pf_std_m)),
                    "surface_events": r.surface_events,
                    "lora_updates": r.lora_updates,
                    "bias_updates": r.bias_updates,
                    "bias_learned_fraction": r.bias_learned_fraction,
                    "bias_mean_learned_mag_ms": r.bias_mean_learned_mag_ms,
                    "bias_max_learned_mag_ms": r.bias_max_learned_mag_ms,
                    "bias_mean_learned_var_ms2": r.bias_mean_learned_var_ms2,
                    "ctd_updates": r.ctd_updates,
                })
                print(f"  {policy_name:<10} {cfg_name:<9} "
                      f"mean={r.ctrl_mean_m():5.0f}m "
                      f"max={r.ctrl_max_m():5.0f}m "
                      f"%<500m={r.envelope_frac(500.0)*100:3.0f}% "
                      f"PFerr={np.mean(r.pf_err_m):4.0f}m "
                      f"PFstd={np.mean(r.pf_std_m):4.0f}m "
                      f"surf={r.surface_events:>2} "
                      f"bias={r.bias_updates:>2}/"
                      f"|b|={r.bias_max_learned_mag_ms*100:4.1f}cm/s "
                      f"ctd={r.ctd_updates:>3} "
                      f"({dt:.1f}s)", flush=True)

    # --- Aggregate ---
    n_total = len(baseline_real_per_station)
    print("\n=== aggregate ===", flush=True)

    def print_baseline(label: str, rs: list[dict]) -> None:
        rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        mm = float(np.mean([r["ctrl_mean_m"] for r in rs]))
        e_500 = float(np.mean([r["envelope_fracs"][500.0] for r in rs]))
        print(f"  {label:<18}  rough={rough}/{n_total}  "
              f"%<500m={e_500*100:3.0f}%  mean={mm:4.0f}m", flush=True)

    print_baseline("baseline_real", baseline_real_per_station)
    print_baseline("baseline_nemo", baseline_nemo_per_station)

    for key in sorted(results.keys()):
        policy_name, cfg_name = key
        rs = results[key]
        if not rs:
            continue
        rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        mm = float(np.mean([r["ctrl_mean_m"] for r in rs]))
        e_500 = float(np.mean([r["envelope_fracs"][500.0] for r in rs]))
        pf_err = float(np.mean([r["pf_err_mean_m"] for r in rs]))
        mag = float(np.mean([r["bias_max_learned_mag_ms"] for r in rs]))
        surf = float(np.mean([r["surface_events"] for r in rs]))
        print(f"  {policy_name:<10} {cfg_name:<9}  rough={rough}/{n_total}  "
              f"%<500m={e_500*100:3.0f}%  mean={mm:4.0f}m  "
              f"PFerr={pf_err:4.0f}m  |b|_max={mag*100:4.1f}cm/s  "
              f"surf={surf:4.1f}", flush=True)

    # --- JSON dump: one file per (σ, seed), consumed by the
    # aggregation script to build the multi-panel sweep plot. ---
    import json
    out_records: list[dict] = []
    for (policy_name, cfg_name), rs in results.items():
        for r in rs:
            out_records.append({
                "sigma_fc_ms": SIGMA_FORECAST_MS,
                "noise_seed": NOISE_SEED,
                "process_noise_ms": PROCESS_NOISE_MS,
                "station_lat": r["station_lat"],
                "station_lon": r["station_lon"],
                "policy": policy_name,
                "config": cfg_name,
                "ctrl_mean_m": r["ctrl_mean_m"],
                "ctrl_max_m": r["ctrl_max_m"],
                "envelope_fracs": r["envelope_fracs"],
                "pf_err_mean_m": r["pf_err_mean_m"],
                "pf_err_max_m": r["pf_err_max_m"],
                "pf_std_mean_m": r["pf_std_mean_m"],
                "surface_events": r["surface_events"],
                "bias_updates": r["bias_updates"],
                "bias_max_learned_mag_ms": r["bias_max_learned_mag_ms"],
                "bias_learned_fraction": r["bias_learned_fraction"],
                "bias_mean_learned_var_ms2": r["bias_mean_learned_var_ms2"],
                "ctd_updates": r["ctd_updates"],
            })
    baseline_records: list[dict] = []
    for b_real, b_nemo in zip(baseline_real_per_station,
                                baseline_nemo_per_station):
        baseline_records.append({
            "sigma_fc_ms": SIGMA_FORECAST_MS,
            "noise_seed": NOISE_SEED,
            "station_lat": b_real["station_lat"],
            "station_lon": b_real["station_lon"],
            "baseline_real": {
                "ctrl_mean_m": b_real["ctrl_mean_m"],
                "ctrl_max_m": b_real["ctrl_max_m"],
                "envelope_fracs": b_real["envelope_fracs"],
            },
            "baseline_nemo": {
                "ctrl_mean_m": b_nemo["ctrl_mean_m"],
                "ctrl_max_m": b_nemo["ctrl_max_m"],
                "envelope_fracs": b_nemo["envelope_fracs"],
            },
        })
    payload = {"pf_runs": out_records, "baselines": baseline_records}
    tag = f"sigma{int(SIGMA_FORECAST_MS*100):02d}_seed{NOISE_SEED:03d}"
    json_out = FIG_DIR / f"25_rbpf_v2_bias_learning_{tag}.json"
    with open(json_out, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\n[data] wrote {json_out}", flush=True)


if __name__ == "__main__":
    main()
