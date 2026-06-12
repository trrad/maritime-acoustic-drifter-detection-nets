"""Realistic single-node sim with surface/submerged duty cycle.

Key hardware constraints that weren't in Phase B:
  - No onboard GPS. Position fixes come from LoRa ranging to anchors.
  - LoRa needs the node near surface (depth < LORA_MAX_DEPTH_M).
  - Reaching surface takes pump work + time; surface period has its own
    current regime that the node must accept while it's there.

Duty cycle:
  - Scheduled surface event every SURFACE_PERIOD_H.
  - During a surface event, the depth setpoint is forced to 0.5 m for
    SURFACE_DWELL_H. The controller's depth choice is overridden.
  - LoRa ranging to each anchor fires every LORA_CADENCE_SEC while the
    node is actually at surface (depth < LORA_MAX_DEPTH_M).
  - Outside surface events, the controller picks depth from the standard
    set; LoRa is unavailable unless the controller independently picked
    0.5 m (which it rarely does since surface currents are often not the
    best-steering depth).

PF:
  - 400 particles over (lat, lon).
  - Predict: advance particles using a CLIMATOLOGY prior (reuse B3
    HistoricalPriorKnowledge so the prior is a different year's data,
    matching the realistic "we have last year's tide + climate" case).
  - Update: Gaussian range update per LoRa range measurement.
  - Depth observation is effectively perfect (pressure).

Comparison runs (at each station):
  - BASELINE: controller reads truth position (Phase A upper bound).
  - DUTY_CYCLE: controller reads PF mean position, PF gets LoRa ranges
    only during scheduled surface events.

Output: figures/20_duty_cycle_single_node.png showing station-keeping
success rate under the realistic sensor stack vs the perfect-knowledge
ceiling.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from ballast_controller import PerfectKnowledge, StationKeeper  # type: ignore[import-not-found]
from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
from knowledge_sources import HistoricalPriorKnowledge, TruthKnowledge  # type: ignore[import-not-found]
from salishseacast_cache import (  # type: ignore[import-not-found]
    _cache_path, U_DATASET,
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
from truth_field import (  # type: ignore[import-not-found]
    EARTH_R_M, build_truth_field, distance_m, lat_lon_step_from_velocity,
)


# --- Domain ---
# Use the original smaller cached bbox since that has both truth (2023-04)
# and prior (2020-04) cached. The expanded 60×60 km bbox would also work
# once 2020-04 is fetched there (background task running).
LAT_MIN, LAT_MAX = 49.25, 49.35
LON_MIN, LON_MAX = -123.78, -123.62
TRUTH_MONTHS = ["2023-04"]
PRIOR_MONTHS = ["2020-04"]

# Prior mode:
#   "same_year"  — use 2023 truth as the prior too. Isolates PF+duty-cycle
#                  architecture cost from prior-quality error. NOT
#                  operationally honest; we cannot "load" 2023 before it
#                  happens. Eventually we'd test with a contemporaneous
#                  forecast (CIOPS or similar) generated in real time.
#   "prior_year" — use 2020 truth as prior. Honest "we have last year's
#                  hindcast" case.
PRIOR_MODE = "same_year"

# --- Grid-sweep settings (subset) ---
STRIDE_Y = 8          # coarser — duty-cycle sim is slower per station
STRIDE_X = 10
INTERIOR_MARGIN_Y = 6
INTERIOR_MARGIN_X = 8
MIN_BATHY_M = 60.0
CAP_DEPTH_MARGIN = 0.8
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]

# --- Sim ---
RUN_HOURS = 72
DT_SEC = 600.0                    # 10-min step (finer than before for duty cycle)
CONTROL_CADENCE_SEC = 1800.0      # 30-min decisions
LOOKAHEAD_SEC = 1800.0
W_Z_MAX_MS = 0.1
INITIAL_DEPTH_M = 10.0

# --- Duty cycle ---
SURFACE_PERIOD_H = 6.0
SURFACE_DWELL_H = 0.5
LORA_MAX_DEPTH_M = 1.0
LORA_CADENCE_SEC = 60.0           # range ping cadence while at surface
LORA_SIGMA_M = 20.0

# --- Anchors ---
# Three anchor buoys roughly around the deployment region.
ANCHOR_OFFSETS_KM = [
    (+5.0, +5.0),     # NE
    (-5.0, +5.0),     # NW
    (0.0, -6.0),      # S
]

# --- PF ---
PF_N_PARTICLES = 400
PF_INIT_SIGMA_M = 20.0
# Process noise represents our uncertainty about how the node moves between
# observations. Set to 0 when the prior is truth (no ignorance to model);
# increase for realistic priors where the prior-vs-truth gap adds real
# between-observation uncertainty.
PF_PROCESS_NOISE_MS = 0.0 if True else 0.03  # flip via PRIOR_MODE below

# --- Metrics ---
ROUGH_ENVELOPE_M = 3000.0
ENVELOPES_M = [500.0, 1000.0, 2000.0, 4000.0, 6000.0]

FIG_DIR = Path(__file__).parent / "figures"


def depth_set_for_bathy(bathy_m: float) -> list[float]:
    max_allowed = min(50.0, bathy_m * CAP_DEPTH_MARGIN)
    return [d for d in DEFAULT_DEPTH_SET if d <= max_allowed]


def offsets_km_to_latlon(
    ref_lat: float, ref_lon: float, dnorth_km: float, deast_km: float,
) -> tuple[float, float]:
    cos_lat = np.cos(np.deg2rad(ref_lat))
    dlat = dnorth_km * 1000.0 / EARTH_R_M
    dlon = deast_km * 1000.0 / (EARTH_R_M * cos_lat)
    return ref_lat + dlat, ref_lon + dlon


@dataclass
class PF2D:
    lats: np.ndarray
    lons: np.ndarray
    weights: np.ndarray

    @staticmethod
    def init(mean_lat: float, mean_lon: float, sigma_m: float, n: int,
             seed: int = 0) -> "PF2D":
        rng = np.random.default_rng(seed)
        dlat = rng.normal(0, sigma_m / EARTH_R_M, n)
        dlon = rng.normal(0, sigma_m / (EARTH_R_M * np.cos(np.deg2rad(mean_lat))), n)
        return PF2D(
            lats=mean_lat + dlat,
            lons=mean_lon + dlon,
            weights=np.full(n, 1.0 / n),
        )

    def mean(self) -> tuple[float, float]:
        return float(np.sum(self.lats * self.weights)), float(
            np.sum(self.lons * self.weights)
        )

    def ess(self) -> float:
        return float(1.0 / np.sum(self.weights**2))

    def predict(
        self, t_sec: float, dt_sec: float,
        prior_field, depth_m: float,
        process_noise_ms: float, rng: np.random.Generator,
    ) -> None:
        for i in range(len(self.lats)):
            u, v = prior_field.get_current_at(self.lats[i], self.lons[i],
                                               depth_m, t_sec)
            if not (np.isfinite(u) and np.isfinite(v)):
                u, v = 0.0, 0.0
            u += rng.normal(0, process_noise_ms)
            v += rng.normal(0, process_noise_ms)
            dlat, dlon = lat_lon_step_from_velocity(u, v, self.lats[i], dt_sec)
            self.lats[i] += dlat
            self.lons[i] += dlon

    def update_range(
        self, anchor_lat: float, anchor_lon: float,
        z_range_m: float, sigma_m: float,
    ) -> None:
        cos_lat = np.cos(np.deg2rad(anchor_lat))
        dlat_m = (self.lats - anchor_lat) * EARTH_R_M
        dlon_m = (self.lons - anchor_lon) * EARTH_R_M * cos_lat
        dist = np.sqrt(dlat_m**2 + dlon_m**2)
        resid = dist - z_range_m
        log_w = -0.5 * (resid**2) / sigma_m**2
        log_w -= log_w.max()
        w = np.exp(log_w) * self.weights
        s = w.sum()
        if s > 0:
            self.weights = w / s

    def maybe_resample(self, rng: np.random.Generator) -> None:
        n = len(self.lats)
        if self.ess() >= n / 2:
            return
        positions = (np.arange(n) + rng.uniform(0, 1, n)) / n
        cum = np.cumsum(self.weights)
        idx = np.searchsorted(cum, positions)
        idx = np.clip(idx, 0, n - 1)
        self.lats = self.lats[idx].copy()
        self.lons = self.lons[idx].copy()
        self.weights = np.full(n, 1.0 / n)


def surface_schedule_active(t_sec: float) -> bool:
    """Return True if the node is inside a scheduled surface window at time t."""
    period_s = SURFACE_PERIOD_H * 3600.0
    dwell_s = SURFACE_DWELL_H * 3600.0
    phase = t_sec % period_s
    return phase < dwell_s


def run_station(
    truth, prior_knowledge,
    station_lat: float, station_lon: float,
    depth_set: list[float],
) -> dict:
    """Run the duty-cycle PF-driven controller for one station."""
    rng = np.random.default_rng(42)
    anchor_latlons = [
        offsets_km_to_latlon(station_lat, station_lon, dn, de)
        for (dn, de) in ANCHOR_OFFSETS_KM
    ]

    keeper = StationKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=depth_set,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=prior_knowledge,  # controller plans on prior, not truth
    )

    def dyn_current(t_sec, lat, lon, depth_m):
        return truth.sample(lat, lon, depth_m, t_sec)

    state = BallastState(
        lat=station_lat, lon=station_lon,
        depth_m=INITIAL_DEPTH_M, depth_setpoint_m=INITIAL_DEPTH_M,
    )
    pf = PF2D.init(station_lat, station_lon, PF_INIT_SIGMA_M, PF_N_PARTICLES)

    n_steps = int(RUN_HOURS * 3600 / DT_SEC)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    depths = np.zeros(n_steps + 1)
    pf_mean_lats = np.zeros(n_steps + 1)
    pf_mean_lons = np.zeros(n_steps + 1)
    pf_err_m = np.zeros(n_steps + 1)
    pf_ess = np.zeros(n_steps + 1)
    at_surface = np.zeros(n_steps + 1, dtype=bool)
    lora_fix_count = 0

    lats[0], lons[0], depths[0] = state.lat, state.lon, state.depth_m
    ml, mo = pf.mean()
    pf_mean_lats[0], pf_mean_lons[0] = ml, mo
    pf_err_m[0] = distance_m(state.lat, state.lon, ml, mo)
    pf_ess[0] = pf.ess()

    t_sec = 0.0
    last_decision = -CONTROL_CADENCE_SEC
    last_lora = -LORA_CADENCE_SEC

    for i in range(n_steps):
        # Scheduled surface override.
        if surface_schedule_active(t_sec):
            state = set_setpoint(state, 0.5)
            at_surface[i + 1] = True
        elif t_sec - last_decision >= CONTROL_CADENCE_SEC - 1e-6:
            ml, mo = pf.mean()
            chosen, _ = keeper.choose_depth(
                state.lat, state.lon, t_sec,
                perceived_lat=ml, perceived_lon=mo,
            )
            state = set_setpoint(state, chosen)
            last_decision = t_sec

        # Advance truth dynamics.
        state = step(state, t_sec, DT_SEC,
                     current_at=dyn_current, w_z_max_ms=W_Z_MAX_MS)

        # PF predict using the climatology prior at the node's *actual*
        # depth (observed via pressure).
        pf.predict(t_sec, DT_SEC, prior_knowledge, state.depth_m,
                   PF_PROCESS_NOISE_MS, rng)

        t_sec += DT_SEC

        # LoRa ranging: fires only when node is genuinely near surface.
        if (state.depth_m <= LORA_MAX_DEPTH_M
            and t_sec - last_lora >= LORA_CADENCE_SEC - 1e-6):
            for alat, alon in anchor_latlons:
                true_range = distance_m(state.lat, state.lon, alat, alon)
                z = true_range + rng.normal(0, LORA_SIGMA_M)
                pf.update_range(alat, alon, z, LORA_SIGMA_M)
            pf.maybe_resample(rng)
            last_lora = t_sec
            lora_fix_count += 1

        # Record trajectory.
        lats[i + 1], lons[i + 1], depths[i + 1] = state.lat, state.lon, state.depth_m
        ml, mo = pf.mean()
        pf_mean_lats[i + 1], pf_mean_lons[i + 1] = ml, mo
        pf_err_m[i + 1] = distance_m(state.lat, state.lon, ml, mo)
        pf_ess[i + 1] = pf.ess()

    dists = np.array([
        distance_m(la, lo, station_lat, station_lon)
        for la, lo in zip(lats, lons)
    ])
    valid = np.isfinite(dists)
    if not valid.all():
        last = np.where(valid)[0]
        if len(last) > 0:
            dists = np.where(valid, dists, dists[last[-1]])
        else:
            dists = np.full_like(dists, np.inf)

    envelope_fracs = {e: float((dists <= e).mean()) for e in ENVELOPES_M}
    return {
        "station_lat": station_lat, "station_lon": station_lon,
        "anchors": anchor_latlons,
        "lats": lats, "lons": lons, "depths": depths,
        "pf_mean_lats": pf_mean_lats, "pf_mean_lons": pf_mean_lons,
        "pf_err_m": pf_err_m, "pf_ess": pf_ess,
        "at_surface": at_surface,
        "lora_fix_events": lora_fix_count,
        "dists_m": dists,
        "ctrl_mean_m": float(np.nanmean(dists)),
        "ctrl_max_m": float(np.nanmax(dists)),
        "envelope_fracs": envelope_fracs,
        "mean_pf_err_m": float(np.nanmean(pf_err_m)),
        "max_pf_err_m": float(np.nanmax(pf_err_m)),
    }


def run_baseline_truth_controller(
    truth, station_lat: float, station_lon: float,
    depth_set: list[float],
) -> dict:
    """Phase A-style perfect-knowledge baseline, but at 10-min step to match."""
    keeper = StationKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=depth_set,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=PerfectKnowledge(truth=truth),
    )

    def dyn_current(t_sec, lat, lon, depth_m):
        return truth.sample(lat, lon, depth_m, t_sec)

    state = BallastState(
        lat=station_lat, lon=station_lon,
        depth_m=INITIAL_DEPTH_M, depth_setpoint_m=INITIAL_DEPTH_M,
    )
    n_steps = int(RUN_HOURS * 3600 / DT_SEC)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    lats[0], lons[0] = state.lat, state.lon
    t_sec = 0.0
    last_decision = -CONTROL_CADENCE_SEC
    for i in range(n_steps):
        if t_sec - last_decision >= CONTROL_CADENCE_SEC - 1e-6:
            chosen, _ = keeper.choose_depth(state.lat, state.lon, t_sec)
            state = set_setpoint(state, chosen)
            last_decision = t_sec
        state = step(state, t_sec, DT_SEC,
                     current_at=dyn_current, w_z_max_ms=W_Z_MAX_MS)
        t_sec += DT_SEC
        lats[i + 1], lons[i + 1] = state.lat, state.lon
    dists = np.array([
        distance_m(la, lo, station_lat, station_lon)
        for la, lo in zip(lats, lons)
    ])
    valid = np.isfinite(dists)
    if not valid.all():
        last = np.where(valid)[0]
        if len(last) > 0:
            dists = np.where(valid, dists, dists[last[-1]])
        else:
            dists = np.full_like(dists, np.inf)
    envelope_fracs = {e: float((dists <= e).mean()) for e in ENVELOPES_M}
    return {
        "lats": lats, "lons": lons,
        "dists_m": dists,
        "ctrl_mean_m": float(np.nanmean(dists)),
        "ctrl_max_m": float(np.nanmax(dists)),
        "envelope_fracs": envelope_fracs,
    }


def main() -> None:
    print("=== Realistic duty-cycle single-node sim ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds_truth = fetch_bbox_months(bbox, TRUTH_MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    n_y, n_x = ds_truth.sizes["gridY"], ds_truth.sizes["gridX"]
    print(f"bbox: {bbox}   {n_y}×{n_x} cells")

    print("building truth ...")
    t0 = time.time()
    truth = build_truth_field(ds_truth, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    print(f"  {time.time()-t0:.1f}s")

    # Prior selection.
    if PRIOR_MODE == "same_year":
        print("building prior (SAME-YEAR truth — upper-bound diagnostic) ...")
        prior = TruthKnowledge(truth=truth)
        print(f"  prior uses 2023 truth directly (not operationally honest)")
    else:  # "prior_year"
        missing = [m for m in PRIOR_MONTHS
                   if not _cache_path(U_DATASET, bbox.key(), m).exists()]
        if missing:
            raise SystemExit(f"prior months {missing} not in cache.")
        print("building prior (2020-04 same-month different-year) ...")
        t0 = time.time()
        ds_prior = fetch_bbox_months(bbox, PRIOR_MONTHS, verbose=False)
        prior = HistoricalPriorKnowledge.from_datasets(
            ds_prior, truth_t0=truth.t0,
            bbox_lats_grid=lats_grid, bbox_lons_grid=lons_grid,
            target_depths_m=DEFAULT_DEPTH_SET,
        )
        print(f"  prior built in {time.time()-t0:.1f}s")

    # Station candidates (coarser stride than grid sweep to keep runtime tractable).
    candidates: list[tuple[int, int]] = []
    for gy in range(INTERIOR_MARGIN_Y, n_y - INTERIOR_MARGIN_Y, STRIDE_Y):
        for gx in range(INTERIOR_MARGIN_X, n_x - INTERIOR_MARGIN_X, STRIDE_X):
            if bathy_grid[gy, gx] >= MIN_BATHY_M:
                candidates.append((gy, gx))
    print(f"stations: {len(candidates)}")

    baseline_results: list[dict] = []
    duty_results: list[dict] = []
    for gy, gx in candidates:
        s_lat = float(truth.lat_axis[gy])
        s_lon = float(truth.lon_axis[gx])
        s_bathy = float(bathy_grid[gy, gx])
        d_set = depth_set_for_bathy(s_bathy)
        if len(d_set) < 2:
            continue
        u0, v0 = truth.sample(s_lat, s_lon, INITIAL_DEPTH_M, 0.0)
        if not (np.isfinite(u0) and np.isfinite(v0)):
            continue
        print(f"\nstation ({s_lat:.4f}, {s_lon:.4f})  bathy={s_bathy:.0f}m")
        t0 = time.time()
        b = run_baseline_truth_controller(truth, s_lat, s_lon, d_set)
        print(f"  baseline (truth ctrl)  ctrl_max={b['ctrl_max_m']:.0f}m "
              f"mean={b['ctrl_mean_m']:.0f}m  %<500m={b['envelope_fracs'][500.0]*100:.0f}%  "
              f"({time.time()-t0:.1f}s)")
        t0 = time.time()
        d = run_station(truth, prior, s_lat, s_lon, d_set)
        print(f"  duty-cycle  ctrl_max={d['ctrl_max_m']:.0f}m "
              f"mean={d['ctrl_mean_m']:.0f}m  %<500m={d['envelope_fracs'][500.0]*100:.0f}%  "
              f"LoRa fixes={d['lora_fix_events']}  "
              f"PF mean err={d['mean_pf_err_m']:.0f}m max={d['max_pf_err_m']:.0f}m  "
              f"({time.time()-t0:.1f}s)")
        baseline_results.append(b | {"station_lat": s_lat, "station_lon": s_lon})
        duty_results.append(d)

    if not duty_results:
        print("no valid stations")
        return

    # --- Aggregate ---
    print()
    print("=== aggregate ===")
    def summary(rs, label):
        rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        e_frac = {e: float(np.mean([r["envelope_fracs"][e] for r in rs]))
                   for e in ENVELOPES_M}
        env = "  ".join(f"{'%<'+str(int(e))+'m':>8}: {e_frac[e]*100:>3.0f}%"
                        for e in ENVELOPES_M)
        cm = float(np.mean([r["ctrl_mean_m"] for r in rs]))
        cmx = float(np.mean([r["ctrl_max_m"] for r in rs]))
        print(f"  {label:<22} rough={rough}/{len(rs)}  {env}  "
              f"mean-mean={cm:.0f}m  mean-max={cmx:.0f}m")
    summary(baseline_results, "baseline (truth ctrl)")
    summary(duty_results, "duty-cycle PF+prior")

    pf_err_mean = float(np.mean([r["mean_pf_err_m"] for r in duty_results]))
    pf_err_max = float(np.mean([r["max_pf_err_m"] for r in duty_results]))
    n_fixes = float(np.mean([r["lora_fix_events"] for r in duty_results]))
    print(f"  duty-cycle PF error: mean={pf_err_mean:.0f}m  max={pf_err_max:.0f}m  "
          f"mean LoRa fixes/run={n_fixes:.0f}")

    # --- Plot ---
    masked_bathy = np.where(bathy_grid > 0, bathy_grid, np.nan)
    n = len(duty_results)
    fig = plt.figure(figsize=(18, 10))
    gs = fig.add_gridspec(2, 3, height_ratios=[1.2, 1.0], hspace=0.3, wspace=0.3)

    # Trajectory comparison per station.
    ax_traj = fig.add_subplot(gs[0, :])
    ax_traj.imshow(masked_bathy, origin="lower", cmap="Blues", alpha=0.25,
                    extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX), aspect="auto")
    for b, d in zip(baseline_results, duty_results):
        ax_traj.plot(b["lons"], b["lats"], "-", color="tab:green",
                      lw=0.9, alpha=0.7)
        ax_traj.plot(d["lons"], d["lats"], "-", color="tab:red",
                      lw=1.3, alpha=0.85)
        # station marker
        ax_traj.plot(d["station_lon"], d["station_lat"], "o",
                      markerfacecolor="white", markeredgecolor="black",
                      markersize=7, zorder=5)
        # anchors (unique positions per station)
        for alat, alon in d["anchors"]:
            ax_traj.plot(alon, alat, "^", color="C1", markersize=5, alpha=0.7)
    from matplotlib.lines import Line2D  # type: ignore[import-not-found]
    proxies = [
        Line2D([0], [0], color="tab:green", lw=1.2, label="baseline (truth-ctrl)"),
        Line2D([0], [0], color="tab:red", lw=1.5, label="duty-cycle PF+prior"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="white",
               markeredgecolor="black", markersize=7, label="station"),
        Line2D([0], [0], marker="^", color="C1", lw=0, markersize=6,
               label="LoRa anchor"),
    ]
    ax_traj.legend(handles=proxies, loc="lower left", fontsize=9)
    ax_traj.set_xlim(LON_MIN, LON_MAX)
    ax_traj.set_ylim(LAT_MIN, LAT_MAX)
    ax_traj.set_xlabel("Longitude (°)")
    ax_traj.set_ylabel("Latitude (°)")
    ax_traj.set_title(
        f"Duty-cycle single-node vs baseline  "
        f"(surface every {SURFACE_PERIOD_H}h for {SURFACE_DWELL_H*60:.0f}min, "
        f"LoRa σ={LORA_SIGMA_M:.0f}m)"
    )
    ax_traj.grid(alpha=0.25)

    # Envelope-success comparison.
    ax = fig.add_subplot(gs[1, 0])
    xs = np.arange(len(ENVELOPES_M))
    w = 0.4
    b_frac = [np.mean([r["envelope_fracs"][e] for r in baseline_results]) * 100
              for e in ENVELOPES_M]
    d_frac = [np.mean([r["envelope_fracs"][e] for r in duty_results]) * 100
              for e in ENVELOPES_M]
    ax.bar(xs - w/2, b_frac, w, label="baseline", color="tab:green", alpha=0.75)
    ax.bar(xs + w/2, d_frac, w, label="duty-cycle", color="tab:red", alpha=0.75)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"≤{int(e)}m" for e in ENVELOPES_M])
    ax.set_xlabel("envelope")
    ax.set_ylabel(f"mean % of {RUN_HOURS}h within envelope")
    ax.set_title("Envelope success (aggregate)")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, axis="y")

    # PF error vs time (overlay all stations).
    ax = fig.add_subplot(gs[1, 1])
    hrs = np.arange(duty_results[0]["pf_err_m"].shape[0]) * (DT_SEC / 3600)
    for r in duty_results:
        ax.plot(hrs, r["pf_err_m"], "-", color="tab:red", alpha=0.4, lw=0.8)
    # Mark surface windows.
    mask = [bool(surface_schedule_active(h * 3600)) for h in hrs]
    ax.fill_between(hrs, 0, 1, where=mask,
                     transform=ax.get_xaxis_transform(),
                     color="tab:blue", alpha=0.08, label="surface window")
    ax.set_xlabel("hours since start")
    ax.set_ylabel("PF mean-error (m)")
    ax.set_title("PF position error vs time (all stations)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # Distance-from-station timeseries.
    ax = fig.add_subplot(gs[1, 2])
    for b, d in zip(baseline_results, duty_results):
        ax.plot(hrs, b["dists_m"], "-", color="tab:green", lw=0.8, alpha=0.5)
        ax.plot(hrs, d["dists_m"], "-", color="tab:red", lw=0.9, alpha=0.6)
    for e in [ROUGH_ENVELOPE_M]:
        ax.axhline(e, ls="--", color="black", alpha=0.4,
                   label=f"{int(e)}m rough threshold")
    ax.set_xlabel("hours since start")
    ax.set_ylabel("distance from station (m)")
    ax.set_title("dist-from-station (all stations)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    fig.suptitle(
        f"Realistic duty-cycle single-node   ({n} stations, {RUN_HOURS}h)   "
        f"controller uses prior; PF ranges to {len(ANCHOR_OFFSETS_KM)} anchors "
        f"only at surface",
        fontsize=12, y=1.0,
    )
    fig.tight_layout()
    out = FIG_DIR / "20_duty_cycle_single_node.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")


if __name__ == "__main__":
    main()
