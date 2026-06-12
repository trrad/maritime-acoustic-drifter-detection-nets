"""
⚠️  BROKEN FOUNDATION — DO NOT BUILD ON THIS SCRIPT
====================================================
This script stacks scalar bias-learning on top of script 18's broken
σ_pf-as-independent-knob model. Two compounding issues:
  1. σ_pf is still injected synthetic noise, not produced by real PF +
     surfacing physics (same bug as 18).
  2. Learning is collapsed to a scalar (u, v) correction applied
     uniformly in space/depth. Real PFs learn a FIELD; the scalar can't
     capture spatially or depth-varying forecast errors, which is
     exactly where the leverage is.

The learning mechanism itself works — the PF recovers 80-93% of the
slow-component magnitude as a scalar. But that recovery barely moves
station-keeping because the scalar dimensionality is wrong.

Keep for history. The right script is `20_*`.

--- original docstring below ---

Corrected sensitivity sweep: PF that actually does in-flight prior
refinement by learning from observed drift.

Previous script (18) treated the PF as just "noisy position" — it
couldn't reduce prior error. That was a wrong model of what a PF does.

A real PF observing (position, depth) over time has, via d(pos)/dt,
direct observations of the current at the depths it has been at. Those
observations let it update a bias-correction field that REDUCES the
effective prior error over the deployment.

Model here:
  - Prior field = truth + correlated noise (σ_forecast_initial in m/s).
  - PF maintains `learned_bias_uv` — an EMA estimate of "truth − prior"
    at depths the node has recently visited.
  - At each surface event, PF compares (position_now − position_last_fix)
    to the prior-predicted displacement over the submerged path at the
    node's depth history. The residual / elapsed time is the observed
    current error. EMA-update the learned bias.
  - Controller uses prior + learned_bias_uv as its effective current
    estimate.
  - Perceived position at decision time = truth + N(0, σ_pf) (same as
    before — the PF's position output quality).

This captures the qualitatively-important piece: PF accuracy and
learning combine to reduce effective prior error over deployment.

Sweeps:
  - σ_forecast_initial ∈ {0, 10, 20 cm/s}
  - σ_pf ∈ {50, 200 m}      (good vs mediocre PF)
  - learning ∈ {off, on}    (is the PF actually refining?)

Output: figures/22_pf_bias_learning.png — bar comparison showing how
much bias learning recovers vs the static-prior case.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from ballast_controller import PerfectKnowledge, StationKeeper  # type: ignore[import-not-found]
from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
from submesoscale import (  # type: ignore[import-not-found]
    build_multiscale_noise_field,
)
from truth_field import (  # type: ignore[import-not-found]
    EARTH_R_M, build_truth_field, distance_m,
)


LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
MONTHS = ["2023-04"]

STRIDE_Y = 10
STRIDE_X = 10
INTERIOR_MARGIN_Y = 8
INTERIOR_MARGIN_X = 8
MIN_BATHY_M = 60.0
CAP_DEPTH_MARGIN = 0.8
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]

RUN_HOURS = 72
DT_SEC = 3600.0
CONTROL_CADENCE_SEC = 1800.0
LOOKAHEAD_SEC = 1800.0
W_Z_MAX_MS = 0.1
INITIAL_DEPTH_M = 10.0

# Surface schedule (for learning observations).
SURFACE_PERIOD_H = 6.0
SURFACE_DWELL_H = 0.5
LORA_MAX_DEPTH_M = 1.0

# PF parameters.
LEARNING_EMA_ALPHA = 0.4   # how aggressively to update bias from each observation

# Sweep axes.
SIGMA_FORECAST_SWEEP_MS = [0.0, 0.10, 0.20]
SIGMA_PF_SWEEP_M = [50.0, 200.0]
LEARNING_MODES = [False, True]

# Multi-timescale noise breakdown: split σ_forecast into fast (chop —
# submesoscale eddies, short-correlation, NOT learnable) and slow
# (persistent bias — wind drift, freshet pulse, long-correlation,
# learnable). Typical breakdown: slow dominates for synoptic-scale
# forecast error, fast for pure submesoscale.
NOISE_SLOW_FRACTION = 0.75     # 75% of variance is in the learnable slow component

ROUGH_ENVELOPE_M = 3000.0
ENVELOPES_M = [500.0, 1000.0, 2000.0, 4000.0, 6000.0]

FIG_DIR = Path(__file__).parent / "figures"


def depth_set_for_bathy(bathy_m: float) -> list[float]:
    max_allowed = min(50.0, bathy_m * CAP_DEPTH_MARGIN)
    return [d for d in DEFAULT_DEPTH_SET if d <= max_allowed]


def surface_schedule_active(t_sec: float) -> bool:
    period_s = SURFACE_PERIOD_H * 3600.0
    dwell_s = SURFACE_DWELL_H * 3600.0
    return (t_sec % period_s) < dwell_s


@dataclass
class LearningPrior:
    """Prior currents + a single scalar (u, v) learned bias.

    The learned bias is an EMA estimate of (truth − noisy_prior), updated
    from observed drift residuals at surface events. Applied uniformly
    across depths (simplification — real PF would learn per-depth).
    """

    truth: "object"
    noise: "object"  # noise field whose (u,v) samples are the initial prior error
    bias_u_ms: float = 0.0
    bias_v_ms: float = 0.0

    def prior_without_bias(self, lat, lon, depth_m, t_sec):
        ut, vt = self.truth.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        return ut + un, vt + vn

    def get_current_at(
        self, lat: float, lon: float, depth_m: float, t_sec: float,
    ) -> tuple[float, float]:
        u0, v0 = self.prior_without_bias(lat, lon, depth_m, t_sec)
        if not np.isfinite(u0):
            return float("nan"), float("nan")
        return u0 + self.bias_u_ms, v0 + self.bias_v_ms

    def update_bias_from_drift(
        self,
        pos_start_latlon: tuple[float, float],
        pos_end_latlon: tuple[float, float],
        t_start: float, t_end: float,
        depth_history: list[tuple[float, float]],  # (t_sec, depth_m) samples
    ) -> None:
        """Compare observed displacement to what the current prior (with
        current bias) would have predicted. Update bias from the residual
        via EMA."""
        dt_total = t_end - t_start
        if dt_total <= 0:
            return

        # Observed displacement (meters).
        p0_lat, p0_lon = pos_start_latlon
        p1_lat, p1_lon = pos_end_latlon
        cos_lat = np.cos(np.deg2rad(0.5 * (p0_lat + p1_lat)))
        obs_dy_m = (p1_lat - p0_lat) * EARTH_R_M
        obs_dx_m = (p1_lon - p0_lon) * EARTH_R_M * cos_lat

        # Predicted displacement from prior + current bias, averaging
        # prior current over the depth history at the intermediate
        # position (approximated as start position for simplicity).
        pred_dx_m = 0.0
        pred_dy_m = 0.0
        for i in range(len(depth_history) - 1):
            ti, di = depth_history[i]
            tj, _ = depth_history[i + 1]
            dt = tj - ti
            u0, v0 = self.prior_without_bias(p0_lat, p0_lon, di, ti)
            if not np.isfinite(u0):
                continue
            pred_dx_m += (u0 + self.bias_u_ms) * dt
            pred_dy_m += (v0 + self.bias_v_ms) * dt

        # Residual per-axis average current error.
        resid_u = (obs_dx_m - pred_dx_m) / dt_total
        resid_v = (obs_dy_m - pred_dy_m) / dt_total

        # EMA update of bias.
        self.bias_u_ms += LEARNING_EMA_ALPHA * resid_u
        self.bias_v_ms += LEARNING_EMA_ALPHA * resid_v


def run_station(
    truth, station_lat: float, station_lon: float,
    depth_set: list[float],
    noise_field,                   # None for perfect prior; else precomputed
    sigma_pf_m: float,
    learning_on: bool,
    seed: int,
) -> dict:
    rng = np.random.default_rng(seed)

    if noise_field is None:
        prior = PerfectKnowledge(truth=truth)
    else:
        prior = LearningPrior(truth=truth, noise=noise_field)

    keeper = StationKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=depth_set,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=prior,
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
    bias_u_hist = np.zeros(n_steps + 1)
    bias_v_hist = np.zeros(n_steps + 1)
    lats[0], lons[0] = state.lat, state.lon
    t_sec = 0.0
    last_decision = -CONTROL_CADENCE_SEC

    # Track submerged leg to support bias learning at next surface event.
    leg_start = (state.lat, state.lon)
    leg_start_t = 0.0
    leg_depth_history: list[tuple[float, float]] = [(0.0, state.depth_m)]
    was_at_surface = False

    for i in range(n_steps):
        # Control decision.
        if surface_schedule_active(t_sec):
            state = set_setpoint(state, 0.5)
        elif t_sec - last_decision >= CONTROL_CADENCE_SEC - 1e-6:
            cos_lat = np.cos(np.deg2rad(state.lat))
            noise_lat = rng.normal(0, sigma_pf_m / EARTH_R_M)
            noise_lon = rng.normal(0, sigma_pf_m / (EARTH_R_M * cos_lat))
            chosen, _ = keeper.choose_depth(
                state.lat, state.lon, t_sec,
                perceived_lat=state.lat + noise_lat,
                perceived_lon=state.lon + noise_lon,
            )
            state = set_setpoint(state, chosen)
            last_decision = t_sec

        # Advance truth.
        state = step(state, t_sec, DT_SEC,
                     current_at=dyn_current, w_z_max_ms=W_Z_MAX_MS)
        t_sec += DT_SEC
        lats[i + 1], lons[i + 1] = state.lat, state.lon
        leg_depth_history.append((t_sec, state.depth_m))
        if isinstance(prior, LearningPrior):
            bias_u_hist[i + 1] = prior.bias_u_ms
            bias_v_hist[i + 1] = prior.bias_v_ms

        # Detect surface transitions; learn at arrival.
        now_at_surface = state.depth_m <= LORA_MAX_DEPTH_M
        if (now_at_surface and not was_at_surface
            and learning_on and isinstance(prior, LearningPrior)
            and t_sec > leg_start_t + 3600):  # need ≥1h submerged for signal
            # Position-observation noise σ_pf applied to learning too.
            obs_end_lat = state.lat + rng.normal(0, sigma_pf_m / EARTH_R_M)
            obs_end_lon = state.lon + rng.normal(
                0, sigma_pf_m / (EARTH_R_M * np.cos(np.deg2rad(state.lat))),
            )
            prior.update_bias_from_drift(
                pos_start_latlon=leg_start,
                pos_end_latlon=(obs_end_lat, obs_end_lon),
                t_start=leg_start_t, t_end=t_sec,
                depth_history=leg_depth_history,
            )
        # Reset leg tracking when we dive back down.
        if was_at_surface and not now_at_surface:
            leg_start = (state.lat, state.lon)
            leg_start_t = t_sec
            leg_depth_history = [(t_sec, state.depth_m)]
        was_at_surface = now_at_surface

    dists = np.array([
        distance_m(la, lo, station_lat, station_lon)
        for la, lo in zip(lats, lons)
    ])
    valid = np.isfinite(dists)
    if not valid.all():
        last = np.where(valid)[0]
        dists = (np.where(valid, dists, dists[last[-1]]) if len(last) > 0
                  else np.full_like(dists, np.inf))

    envelope_fracs = {e: float((dists <= e).mean()) for e in ENVELOPES_M}
    final_bias_mag = float(np.hypot(
        bias_u_hist[-1] if isinstance(prior, LearningPrior) else 0.0,
        bias_v_hist[-1] if isinstance(prior, LearningPrior) else 0.0,
    ))
    return {
        "station_lat": station_lat, "station_lon": station_lon,
        "ctrl_mean_m": float(np.nanmean(dists)),
        "ctrl_max_m": float(np.nanmax(dists)),
        "envelope_fracs": envelope_fracs,
        "final_bias_mag_ms": final_bias_mag,
        "bias_u_hist": bias_u_hist, "bias_v_hist": bias_v_hist,
    }


def main() -> None:
    print("=== PF with bias-learning sweep ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]

    print("building truth ...")
    t0 = time.time()
    truth = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    print(f"  {time.time() - t0:.1f}s")

    candidates: list[tuple[int, int]] = []
    for gy in range(INTERIOR_MARGIN_Y, n_y - INTERIOR_MARGIN_Y, STRIDE_Y):
        for gx in range(INTERIOR_MARGIN_X, n_x - INTERIOR_MARGIN_X, STRIDE_X):
            if bathy_grid[gy, gx] >= MIN_BATHY_M:
                candidates.append((gy, gx))
    print(f"stations: {len(candidates)}")

    # Build one noise field per σ_fc (shared across all stations — simulates
    # a single "weather realization" on that day across the bbox). Skip
    # σ_fc=0 (perfect prior).
    noise_cache: dict[float, object] = {}
    for sigma_fc in SIGMA_FORECAST_SWEEP_MS:
        if sigma_fc <= 0:
            noise_cache[sigma_fc] = None
            continue
        sigma_slow = sigma_fc * np.sqrt(NOISE_SLOW_FRACTION)
        sigma_fast = sigma_fc * np.sqrt(1.0 - NOISE_SLOW_FRACTION)
        print(f"building multiscale noise σ_fc={sigma_fc*100:.0f} "
              f"(slow={sigma_slow*100:.1f}, fast={sigma_fast*100:.1f}) ...", flush=True)
        t0 = time.time()
        noise_cache[sigma_fc] = build_multiscale_noise_field(
            ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET,
            sigma_fast_ms=sigma_fast, sigma_slow_ms=sigma_slow,
            seed=42,
        )
        print(f"  built in {time.time() - t0:.1f}s", flush=True)

    results: dict[tuple[float, float, bool], list[dict]] = {}
    for sigma_fc in SIGMA_FORECAST_SWEEP_MS:
        for sigma_pf in SIGMA_PF_SWEEP_M:
            for learn in LEARNING_MODES:
                # At σ_fc=0 there's nothing to learn; skip learn=True duplicate.
                if sigma_fc == 0 and learn:
                    continue
                key = (sigma_fc, sigma_pf, learn)
                print(f"\nσ_fc={sigma_fc*100:.0f}cm/s  σ_pf={sigma_pf:.0f}m  "
                      f"learn={learn}", flush=True)
                t0 = time.time()
                rs: list[dict] = []
                noise_field = noise_cache[sigma_fc]
                for i, (gy, gx) in enumerate(candidates):
                    s_lat = float(truth.lat_axis[gy])
                    s_lon = float(truth.lon_axis[gx])
                    s_bathy = float(bathy_grid[gy, gx])
                    d_set = depth_set_for_bathy(s_bathy)
                    if len(d_set) < 2:
                        continue
                    u0, v0 = truth.sample(s_lat, s_lon, INITIAL_DEPTH_M, 0.0)
                    if not (np.isfinite(u0) and np.isfinite(v0)):
                        continue
                    r = run_station(truth, s_lat, s_lon, d_set,
                                    noise_field, sigma_pf, learn,
                                    seed=1000 + i)
                    r["bathy_m"] = s_bathy
                    rs.append(r)
                results[key] = rs
                n_rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
                mean_500 = float(np.mean([r["envelope_fracs"][500.0] for r in rs]))
                mean_1k = float(np.mean([r["envelope_fracs"][1000.0] for r in rs]))
                mean_bias = float(np.mean([r["final_bias_mag_ms"] for r in rs]))
                print(f"  rough={n_rough}/{len(rs)}  %<500m={mean_500*100:.0f}%  "
                      f"%<1km={mean_1k*100:.0f}%  final |bias|={mean_bias*100:.1f}cm/s  "
                      f"({time.time() - t0:.1f}s)", flush=True)

    # --- Print aggregate ---
    print()
    print("=== summary ===")
    print(f"{'σ_fc':>8}  {'σ_pf':>6}  {'learn':>6}  {'rough':>7}  "
          + "  ".join(f"{'%<'+str(int(e))+'m':>8}" for e in ENVELOPES_M)
          + f"  {'|bias|':>8}")
    for key in sorted(results.keys()):
        sfc, spf, learn = key
        rs = results[key]
        n_rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        env_cells = "  ".join(
            f"{np.mean([r['envelope_fracs'][e] for r in rs])*100:>7.0f}%"
            for e in ENVELOPES_M
        )
        mb = float(np.mean([r["final_bias_mag_ms"] for r in rs]))
        ltag = "YES" if learn else "no"
        print(f"  {sfc*100:>5.0f}cm  {spf:>4.0f}m  {ltag:>6}  "
              f"{n_rough:>3}/{len(rs):<3}   {env_cells}  {mb*100:>5.1f}cm/s")

    # --- Plot ---
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    n_total = max(len(rs) for rs in results.values())

    # Bar chart: rough count per (sigma_fc, learn) for each sigma_pf.
    ax = axes[0]
    bar_labels = []
    bar_values = []
    bar_colors = []
    for sfc in SIGMA_FORECAST_SWEEP_MS:
        for learn in LEARNING_MODES:
            for spf in SIGMA_PF_SWEEP_M:
                key = (sfc, spf, learn)
                if key not in results:
                    continue
                rs = results[key]
                rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
                bar_labels.append(
                    f"fc={sfc*100:.0f}\npf={spf:.0f}\n{'learn' if learn else 'static'}"
                )
                bar_values.append(rough)
                bar_colors.append(
                    "tab:green" if learn else ("tab:gray" if sfc > 0 else "tab:blue")
                )
    ax.bar(range(len(bar_labels)), bar_values, color=bar_colors, alpha=0.8)
    for i, v in enumerate(bar_values):
        ax.text(i, v + 0.3, str(v), ha="center", fontsize=9)
    ax.set_xticks(range(len(bar_labels)))
    ax.set_xticklabels(bar_labels, fontsize=7)
    ax.set_ylabel(f"# stations with ctrl_max ≤ {int(ROUGH_ENVELOPE_M)}m (of {n_total})")
    ax.set_title("rough station-keeping by config")
    ax.grid(alpha=0.3, axis="y")

    # Line plot: final learned |bias| vs σ_forecast_initial.
    ax = axes[1]
    for spf in SIGMA_PF_SWEEP_M:
        xs = []
        ys = []
        for sfc in SIGMA_FORECAST_SWEEP_MS:
            key = (sfc, spf, True)
            if key not in results:
                continue
            rs = results[key]
            xs.append(sfc * 100)
            ys.append(float(np.mean([r["final_bias_mag_ms"] for r in rs])) * 100)
        ax.plot(xs, ys, "-o", lw=1.6, label=f"σ_pf={spf:.0f}m")
    ax.plot([0, 25], [0, 25], "--", color="gray", alpha=0.5,
             label="ideal (learns full error)")
    ax.set_xlabel("σ_forecast_initial (cm/s)")
    ax.set_ylabel("final learned |bias| (cm/s)")
    ax.set_title("How much of the forecast error did the PF recover?")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

    fig.suptitle(
        f"PF with bias-learning: does in-flight refinement recover station-keeping? "
        f"({n_total} stations, {RUN_HOURS}h, surface every {SURFACE_PERIOD_H}h)",
        fontsize=12, y=1.02,
    )
    fig.tight_layout()
    out = FIG_DIR / "22_pf_bias_learning.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")


if __name__ == "__main__":
    main()
