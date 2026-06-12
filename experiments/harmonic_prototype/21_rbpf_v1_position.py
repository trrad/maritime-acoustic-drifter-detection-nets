"""Phase 1: RBPF position-only driver.

Validates that a bootstrap PF over (lat, lon) + LoRa ranging at surface
actually bounds σ_pf under a realistic (~20 cm/s) forecast-error prior
between surface events. No flow sensor, no bias-field learning yet —
this is the baseline.

Sweeps:
  - σ_forecast ∈ {0 (perfect), 20 cm/s (realistic operational)}
  - Surfacing policy ∈ {fixed 6h, fixed 12h, projected-distance
    (surface when posterior_std + dead-reckon uncertainty > envelope)}

At each station, also runs a TRUTH-controller baseline (no PF, perfect
position knowledge) as the station-keeping ceiling.

Output: figures/24_rbpf_v1_position.png
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from ballast_controller import PerfectKnowledge, StationKeeper  # type: ignore[import-not-found]
from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
from rbpf_prototype import (  # type: ignore[import-not-found]
    Experiment, FixedIntervalPolicy, LoRaRangeSensor,
    UncertaintyGatedPolicy, run_one_station,
)
from rbpf_prototype.experiment import (  # type: ignore[import-not-found]
    PFConfig, SensorConfig, SimConfig, StationConfig,
)
from rbpf_prototype.surfacing import ProjectedDistancePolicy  # type: ignore[import-not-found]
from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
from submesoscale import build_multiscale_noise_field  # type: ignore[import-not-found]
from truth_field import EARTH_R_M, build_truth_field, distance_m  # type: ignore[import-not-found]


# --- Domain ---
LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
MONTHS = ["2023-04"]

# --- Station list ---
# Hand-picked from the earlier grid-sweep results (figures 14/15): the
# central-east band where baseline steering factor ≥ 5× and station-keeping
# is demonstrably feasible with perfect-knowledge control. Avoids outflow-
# channel stations (NW corner, southern edge) where baseline itself fails.
HAND_PICKED_STATIONS = [
    # (lat, lon, bathy_hint_m) — from 12_station_keeping_grid.py expanded run
    (49.3533, -123.7411, 289),   # steer 20.9×, ctrl_max 1130m
    (49.3533, -123.6892, 188),   # steer 16.1×
    (49.3924, -123.7411, 182),   # steer 12.8×
    (49.3924, -123.6374,  92),   # steer 10.0×
    (49.3091, -123.6773, 115),   # steer  6.4×
    (49.2699, -123.7033, 373),   # steer  6.0×
    (49.3287, -123.7810, 410),   # steer  5.3×
    (49.3533, -123.5855,  90),   # steer 15.3×
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

# --- Prior (calibrated to published regional-forecast skill) ---
SIGMA_FORECAST_SWEEP_MS = [0.0, 0.20]
NOISE_SLOW_FRACTION = 0.75
NOISE_SPATIAL_CELLS_SLOW = 10.0
NOISE_TEMPORAL_HOURS_SLOW = 36.0
NOISE_SPATIAL_CELLS_FAST = 2.0
NOISE_TEMPORAL_HOURS_FAST = 3.0

# --- Anchors (fixed offsets relative to each station) ---
ANCHOR_OFFSETS_KM = [(+5.0, +5.0), (-5.0, +5.0), (0.0, -6.0)]
LORA_SIGMA_M = 20.0
LORA_MAX_DEPTH_M = 1.0

# --- PF ---
PF_N = 500
PF_INIT_SIGMA_M = 20.0

# --- Metrics ---
ROUGH_ENVELOPE_M = 3000.0
ENVELOPES_M = [500.0, 1000.0, 2000.0, 4000.0, 6000.0]

FIG_DIR = Path(__file__).parent / "figures"


# ---------------------------------------------------------------------------
# Minimal noisy-prior wrapper (truth + multiscale noise).
# ---------------------------------------------------------------------------

@dataclass
class NoisyForecastPrior:
    truth: "object"
    noise: "object | None"   # None = perfect prior

    def get_current_at(self, lat, lon, depth_m, t_sec):
        ut, vt = self.truth.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        if self.noise is None:
            return ut, vt
        un, vn = self.noise.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        return ut + un, vt + vn


# ---------------------------------------------------------------------------
# Baseline: truth-controller (no PF) for ceiling comparison.
# ---------------------------------------------------------------------------

def run_baseline_truth(truth, station: StationConfig, cfg: SimConfig) -> dict:
    keeper = StationKeeper(
        station_lat=station.lat, station_lon=station.lon,
        available_depths_m=station.available_depths_m,
        lookahead_sec=cfg.lookahead_sec,
        knowledge=PerfectKnowledge(truth=truth),
    )

    def dyn_current(t_sec, lat, lon, depth_m):
        return truth.sample(lat, lon, depth_m, t_sec)

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
    print("=== Phase 1: RBPF position-only ===", flush=True)
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]

    print("building truth ...", flush=True)
    t0 = time.time()
    truth = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    print(f"  {time.time() - t0:.1f}s", flush=True)

    # Precompute one noise field per non-zero σ_forecast.
    noise_cache: dict[float, "object | None"] = {0.0: None}
    for sigma_fc in SIGMA_FORECAST_SWEEP_MS:
        if sigma_fc <= 0:
            continue
        sigma_slow = sigma_fc * np.sqrt(NOISE_SLOW_FRACTION)
        sigma_fast = sigma_fc * np.sqrt(1.0 - NOISE_SLOW_FRACTION)
        print(f"building multiscale noise σ_fc={sigma_fc*100:.0f}cm/s ...", flush=True)
        t0 = time.time()
        noise_cache[sigma_fc] = build_multiscale_noise_field(
            ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET,
            sigma_fast_ms=sigma_fast, sigma_slow_ms=sigma_slow,
            spatial_sigma_cells_fast=NOISE_SPATIAL_CELLS_FAST,
            temporal_sigma_hours_fast=NOISE_TEMPORAL_HOURS_FAST,
            spatial_sigma_cells_slow=NOISE_SPATIAL_CELLS_SLOW,
            temporal_sigma_hours_slow=NOISE_TEMPORAL_HOURS_SLOW,
            seed=42,
        )
        print(f"  built in {time.time() - t0:.1f}s", flush=True)

    # Hand-picked station list — snap each to the nearest cell in the
    # truth field's coordinate axes so sampling is exactly representable.
    candidates = []
    for s_lat_target, s_lon_target, _ in HAND_PICKED_STATIONS:
        gy = int(np.argmin(np.abs(truth.lat_axis - s_lat_target)))
        gx = int(np.argmin(np.abs(truth.lon_axis - s_lon_target)))
        candidates.append((gy, gx))
    print(f"stations: {len(candidates)} (hand-picked from central-east band)",
           flush=True)

    # Surfacing policies to compare.
    policies = {
        "fixed_6h": FixedIntervalPolicy(period_h=6.0),
        "fixed_12h": FixedIntervalPolicy(period_h=12.0),
        "projected": ProjectedDistancePolicy(
            envelope_m=3000.0,
            prior_error_per_hour_m=720.0,
            horizon_h=1.0, max_interval_h=12.0,
        ),
    }

    sim_cfg = SimConfig(
        run_hours=RUN_HOURS, dt_sec=DT_SEC,
        control_cadence_sec=CONTROL_CADENCE_SEC,
        lookahead_sec=LOOKAHEAD_SEC,
        w_z_max_ms=W_Z_MAX_MS, initial_depth_m=INITIAL_DEPTH_M,
        surface_dwell_h=SURFACE_DWELL_H, lora_cadence_sec=LORA_CADENCE_SEC,
    )
    pf_cfg = PFConfig(n_particles=PF_N, init_sigma_m=PF_INIT_SIGMA_M)

    results: dict[tuple[float, str], list[dict]] = {}
    baseline_per_station: list[dict] = []

    for idx, (gy, gx) in enumerate(candidates):
        s_lat = float(truth.lat_axis[gy])
        s_lon = float(truth.lon_axis[gx])
        s_bathy = float(bathy_grid[gy, gx])
        d_set = depth_set_for_bathy(s_bathy)
        if len(d_set) < 2:
            continue
        u0, v0 = truth.sample(s_lat, s_lon, INITIAL_DEPTH_M, 0.0)
        if not (np.isfinite(u0) and np.isfinite(v0)):
            continue

        station = StationConfig(
            lat=s_lat, lon=s_lon, envelope_m=ROUGH_ENVELOPE_M,
            available_depths_m=d_set,
        )
        print(f"\nstation {idx+1}/{len(candidates)}: "
              f"({s_lat:.4f}, {s_lon:.4f}) bathy={s_bathy:.0f}m", flush=True)

        # Baseline ceiling.
        t0 = time.time()
        b = run_baseline_truth(truth, station, sim_cfg)
        print(f"  baseline     mean={b['ctrl_mean_m']:5.0f}m "
              f"max={b['ctrl_max_m']:5.0f}m %<500m={b['envelope_fracs'][500.0]*100:3.0f}% "
              f"({time.time()-t0:.1f}s)", flush=True)
        b["station_lat"] = s_lat
        b["station_lon"] = s_lon
        baseline_per_station.append(b)

        # Anchors (station-local).
        anchors = [offsets_km_to_latlon(s_lat, s_lon, dn, de)
                   for (dn, de) in ANCHOR_OFFSETS_KM]
        sensor_cfg = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors,
                                   sigma_m=LORA_SIGMA_M,
                                   max_depth_m=LORA_MAX_DEPTH_M),
            flow=None,
        )

        # RBPF under each (σ_fc, policy).
        for sigma_fc in SIGMA_FORECAST_SWEEP_MS:
            noise = noise_cache[sigma_fc]
            prior = NoisyForecastPrior(truth=truth, noise=noise)
            for pname, policy in policies.items():
                key = (sigma_fc, pname)
                t0 = time.time()
                exp = Experiment(
                    station=station, sim=sim_cfg, sensor=sensor_cfg,
                    pf_cfg=pf_cfg, truth=truth, prior=prior,
                    surfacing=policy,
                )
                r = run_one_station(exp, seed=1000 + idx)
                dt = time.time() - t0
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
                })
                print(f"  σ_fc={sigma_fc*100:>3.0f} {pname:<10} "
                      f"mean={r.ctrl_mean_m():5.0f}m max={r.ctrl_max_m():5.0f}m "
                      f"%<500m={r.envelope_frac(500.0)*100:3.0f}% "
                      f"PFerr={np.mean(r.pf_err_m):4.0f}m "
                      f"surf={r.surface_events:>2} "
                      f"({dt:.1f}s)", flush=True)

    # --- Aggregate ---
    n_total = len(baseline_per_station)
    print("\n=== aggregate ===", flush=True)
    # Baseline.
    rough = sum(1 for r in baseline_per_station if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
    mm = float(np.mean([r["ctrl_mean_m"] for r in baseline_per_station]))
    e_500 = float(np.mean([r["envelope_fracs"][500.0] for r in baseline_per_station]))
    print(f"  baseline              rough={rough}/{n_total}  "
          f"%<500m={e_500*100:3.0f}%  mean={mm:4.0f}m", flush=True)

    for (sigma_fc, pname), rs in sorted(results.items()):
        rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        mm = float(np.mean([r["ctrl_mean_m"] for r in rs]))
        e_500 = float(np.mean([r["envelope_fracs"][500.0] for r in rs]))
        pf_err = float(np.mean([r["pf_err_mean_m"] for r in rs]))
        surf = float(np.mean([r["surface_events"] for r in rs]))
        print(f"  σ_fc={sigma_fc*100:>3.0f} {pname:<10}  rough={rough}/{n_total}  "
              f"%<500m={e_500*100:3.0f}%  mean={mm:4.0f}m  PFerr={pf_err:4.0f}m  "
              f"surf={surf:4.1f}", flush=True)

    # --- Plot ---
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))

    # Envelope success per config.
    ax = axes[0]
    labels = ["baseline"]
    v_500 = [float(np.mean([r["envelope_fracs"][500.0]
                             for r in baseline_per_station])) * 100]
    v_1k = [float(np.mean([r["envelope_fracs"][1000.0]
                            for r in baseline_per_station])) * 100]
    v_2k = [float(np.mean([r["envelope_fracs"][2000.0]
                            for r in baseline_per_station])) * 100]
    for (sigma_fc, pname), rs in sorted(results.items()):
        labels.append(f"σ{int(sigma_fc*100)} {pname}")
        v_500.append(float(np.mean([r["envelope_fracs"][500.0] for r in rs])) * 100)
        v_1k.append(float(np.mean([r["envelope_fracs"][1000.0] for r in rs])) * 100)
        v_2k.append(float(np.mean([r["envelope_fracs"][2000.0] for r in rs])) * 100)
    xs = np.arange(len(labels))
    w = 0.25
    ax.bar(xs - w, v_500, w, label="≤500m", color="tab:green")
    ax.bar(xs, v_1k, w, label="≤1km", color="tab:orange")
    ax.bar(xs + w, v_2k, w, label="≤2km", color="tab:red")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel(f"mean %-of-run within envelope ({n_total} stations)")
    ax.set_title("Envelope success")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")

    # Rough station-keeping count.
    ax = axes[1]
    counts = [sum(1 for r in baseline_per_station if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)]
    for (sigma_fc, pname), rs in sorted(results.items()):
        counts.append(sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M))
    ax.bar(range(len(counts)), counts, color="steelblue", alpha=0.85)
    for i, c in enumerate(counts):
        ax.text(i, c + 0.2, str(c), ha="center", fontsize=9)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel(f"# stations with ctrl_max ≤ {int(ROUGH_ENVELOPE_M)}m (of {n_total})")
    ax.set_title("Rough station-keeping count")
    ax.grid(alpha=0.3, axis="y")

    # PF error by config.
    ax = axes[2]
    pf_labels = []
    pf_means = []
    pf_surfs = []
    for (sigma_fc, pname), rs in sorted(results.items()):
        pf_labels.append(f"σ{int(sigma_fc*100)} {pname}")
        pf_means.append(float(np.mean([r["pf_err_mean_m"] for r in rs])))
        pf_surfs.append(float(np.mean([r["surface_events"] for r in rs])))
    xs = np.arange(len(pf_labels))
    ax.bar(xs, pf_means, color="tab:purple", alpha=0.75, label="PF err (m)")
    ax.set_ylabel("mean PF position error (m)")
    ax.set_xticks(xs)
    ax.set_xticklabels(pf_labels, rotation=30, ha="right", fontsize=8)
    ax.set_title("PF position error + surface-event count")
    ax2 = ax.twinx()
    ax2.plot(xs, pf_surfs, "o-", color="tab:red", label="# surface events")
    ax2.set_ylabel("# surface events over 72h", color="tab:red")
    ax2.tick_params(axis="y", colors="tab:red")
    ax.grid(alpha=0.3, axis="y")

    fig.suptitle(
        f"Phase 1 RBPF position-only: LoRa-at-surface + prior dead-reckon  "
        f"({n_total} stations, {RUN_HOURS}h, σ_fc ∈ {{0, 20 cm/s}} × 3 surfacing policies)",
        fontsize=11, y=1.02,
    )
    fig.tight_layout()
    out = FIG_DIR / "24_rbpf_v1_position.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}", flush=True)


if __name__ == "__main__":
    main()
