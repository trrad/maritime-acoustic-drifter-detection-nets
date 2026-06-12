"""Direct diagnostic for controller rung-chatter (Step 3 motivation).

Hypothesis (from drifter-controls reviewer): the certainty-equivalent
StationKeeper picks depth from a 5-rung ladder via a 30-min greedy
look-ahead. As `b̂_mean` jitters by O(cm/s), the score ordering between
adjacent rungs flips and the controller chatters. This is the failure
mode that explains why the multi-seed Step 1 validation showed
PFerr -42% but station-keeping flat — sharper observer doesn't help
when the controller can't translate `(b̂_mean, P)` into a stable rung
choice.

This diagnostic doesn't run a sweep to indirectly confirm the story —
it instruments the controller directly:

  1. Monkey-patches `StationKeeper.choose_depth` to record every
     (t_sec, depth_chosen, scores_dict) decision tuple.
  2. After the mission, analyses the decision sequence per config:
       - rung-flip rate per hour
       - rung-dwell histogram (consecutive decisions at same rung)
       - score margin at flip events (winner − runner-up, in metres)
       - fraction of flips with margin < 100 m (likely chatter)
  3. Compares no_learn / grid / grid+ctd. If grid and grid+ctd show
     elevated flip rates AND lower score margins at flips relative to
     no_learn, that confirms `b̂_mean` jitter as the chatter driver.

Sample size: 1 station × 3 seeds × 3 configs = 9 runs, parallelised
with multiprocessing.Pool (N_PROCS=9). Each run is ~2 min wall +
~4 min noise build → ~7 min total.

This logging shape is also a prototype for what the Step 3 controller
implementation should expose for online observability (rung-flip rate,
score-margin distribution, posterior-variance gate firings).
"""

from __future__ import annotations

import os
import time
from multiprocessing import Pool, current_process

import numpy as np  # type: ignore[import-not-found]


LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]

STATION = (49.3533, -123.7411, 289)   # S1 from HAND_PICKED_STATIONS
SEED_BASE = 1000
N_SEEDS = 3
N_PROCS = int(os.environ.get("CHATTER_N_PROCS", "9"))

_W: dict = {}


class _RealCurrents:
    def __init__(self, nemo, noise):
        self.nemo = nemo
        self.noise = noise

    def sample(self, lat, lon, depth_m, t_sec):
        ut, vt = self.nemo.sample(lat, lon, depth_m, t_sec)
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, depth_m, t_sec)
        return ut + un, vt + vn

    def get_current_at(self, lat, lon, depth_m, t_sec):
        return self.sample(lat, lon, depth_m, t_sec)


class _NemoPrior:
    def __init__(self, nemo):
        self.nemo = nemo

    def sample(self, lat, lon, depth_m, t_sec):
        return self.nemo.sample(lat, lon, depth_m, t_sec)

    def get_current_at(self, lat, lon, depth_m, t_sec):
        return self.sample(lat, lon, depth_m, t_sec)


def _make_bias():
    from rbpf_prototype import BiasConfig  # type: ignore[import-not-found]
    bias_init = float(np.sqrt(0.04**2 + 0.02**2 + 0.05**2))
    return BiasConfig(
        n_cells=8, cell_size_m=2000.0,
        sigma_bias_init_ms=bias_init,
    )


def _init_worker():
    from salishseacast_cache import (  # type: ignore[import-not-found]
        bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
    )
    from submesoscale import build_layered_noise_field  # type: ignore[import-not-found]
    from truth_field import (  # type: ignore[import-not-found]
        build_tracer_field, build_truth_field,
    )
    label = current_process().name
    t0 = time.time()
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, ["2023-04"], verbose=False,
                            include_tracers=True)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    nemo = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    tracer = build_tracer_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    noise = build_layered_noise_field(ds, lats_grid, lons_grid, seed=42)
    _W["nemo"] = nemo
    _W["tracer"] = tracer
    _W["noise"] = noise
    _W["bathy_grid"] = bathy_grid
    print(f"[{label}] init done ({time.time() - t0:.1f}s)", flush=True)


def _run_one(args: tuple) -> dict:
    """Run one (seed, config) at the fixed station with controller logging.

    Monkey-patches `StationKeeper.choose_depth` to capture every decision.
    Returns aggregated chatter statistics.
    """
    from ballast_controller import TrajectoryStationKeeper  # type: ignore[import-not-found]
    from rbpf_prototype import (  # type: ignore[import-not-found]
        CTDSensor, Experiment, FixedIntervalPolicy, LoRaRangeSensor,
        PFConfig, SensorConfig, SimConfig, StationConfig,
        run_one_station,
    )
    from truth_field import EARTH_R_M  # type: ignore[import-not-found]

    seed_idx, cfg_name = args
    nemo = _W["nemo"]
    tracer = _W["tracer"]
    noise = _W["noise"]
    bathy_grid = _W["bathy_grid"]

    s_lat_target, s_lon_target, _ = STATION
    gy = int(np.argmin(np.abs(nemo.lat_axis - s_lat_target)))
    gx = int(np.argmin(np.abs(nemo.lon_axis - s_lon_target)))
    s_lat = float(nemo.lat_axis[gy])
    s_lon = float(nemo.lon_axis[gx])
    s_bathy = float(bathy_grid[gy, gx])
    max_d = min(50.0, s_bathy * 0.8)
    d_set = [d for d in DEFAULT_DEPTH_SET if d <= max_d]
    station = StationConfig(lat=s_lat, lon=s_lon, envelope_m=3000.0,
                              available_depths_m=d_set)
    cos_lat = float(np.cos(np.deg2rad(s_lat)))
    anchors = [
        (s_lat + dn * 1000.0 / EARTH_R_M,
         s_lon + de * 1000.0 / (EARTH_R_M * cos_lat))
        for (dn, de) in [(+5.0, +5.0), (-5.0, +5.0), (0.0, -6.0)]
    ]
    sim_cfg = SimConfig(
        run_hours=72, dt_sec=600.0,
        control_cadence_sec=1800.0, lookahead_sec=1800.0,
        w_z_max_ms=0.1, initial_depth_m=10.0,
        surface_dwell_h=0.5, lora_cadence_sec=60.0,
    )
    pf_cfg = PFConfig(n_particles=500, init_sigma_m=20.0,
                       process_noise_ms=0.08)

    if cfg_name == "no_learn":
        sensor_cfg = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0,
                                  max_depth_m=1.0),
            flow=None, ctd=None,
        )
        bias_cfg = None
    elif cfg_name == "grid":
        sensor_cfg = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0,
                                  max_depth_m=1.0),
            flow=None, ctd=None,
        )
        bias_cfg = _make_bias()
    elif cfg_name == "grid+ctd":
        sensor_cfg = SensorConfig(
            lora=LoRaRangeSensor(anchors=anchors, sigma_m=20.0,
                                  max_depth_m=1.0),
            flow=None, ctd=CTDSensor(),
        )
        bias_cfg = _make_bias()
    else:
        raise ValueError(f"unknown cfg_name {cfg_name}")

    real = _RealCurrents(nemo=nemo, noise=noise)
    nemo_prior = _NemoPrior(nemo=nemo)
    seed = SEED_BASE + seed_idx

    # --- Per-run controller decision log, instrumented via monkey-patch ---
    decisions: list[dict] = []
    orig_choose_depth = TrajectoryStationKeeper.choose_depth

    def logged_choose_depth(self, lat, lon, t_sec, current_depth_m,
                              perceived_lat=None, perceived_lon=None):
        best_d, scores = orig_choose_depth(
            self, lat, lon, t_sec, current_depth_m,
            perceived_lat, perceived_lon,
        )
        decisions.append({
            "t_sec": float(t_sec),
            "perceived_lat": float(perceived_lat
                                    if perceived_lat is not None else lat),
            "perceived_lon": float(perceived_lon
                                    if perceived_lon is not None else lon),
            "current_depth_m": float(current_depth_m),
            "chosen": float(best_d),
            "scores": dict(scores),
        })
        return best_d, scores

    TrajectoryStationKeeper.choose_depth = logged_choose_depth  # type: ignore[method-assign]
    try:
        t0 = time.time()
        exp = Experiment(
            station=station, sim=sim_cfg, sensor=sensor_cfg,
            pf_cfg=pf_cfg, truth=real, prior=nemo_prior,
            surfacing=FixedIntervalPolicy(period_h=6.0),
            bias_cfg=bias_cfg,
            tracer_truth=tracer, tracer_prior=tracer,
        )
        r = run_one_station(exp, seed=seed)
        dt = time.time() - t0
    finally:
        TrajectoryStationKeeper.choose_depth = orig_choose_depth  # type: ignore[method-assign]

    # --- Analyse decision sequence ---
    # Drop NaN-score decisions (out-of-domain): they're forced fallbacks,
    # not real choices.
    valid = [d for d in decisions if np.isfinite(d["chosen"])]
    n_dec = len(valid)
    if n_dec < 2:
        return {
            "cfg": cfg_name, "seed": seed, "dt": dt,
            "n_dec": n_dec, "n_flips": 0, "flip_rate_per_h": 0.0,
            "median_dwell": 0.0, "max_dwell": 0,
            "n_chatter_flips": 0, "median_margin_at_flip_m": 0.0,
            "mean_dist": r.ctrl_mean_m(), "pferr": float(np.mean(r.pf_err_m)),
            "depths_visited": [],
        }

    flips = []
    dwells: list[int] = []
    cur_dwell = 1
    chosen_seq = [d["chosen"] for d in valid]
    for i in range(1, n_dec):
        if chosen_seq[i] != chosen_seq[i - 1]:
            flips.append(i)
            dwells.append(cur_dwell)
            cur_dwell = 1
        else:
            cur_dwell += 1
    dwells.append(cur_dwell)

    n_flips = len(flips)
    total_h = (valid[-1]["t_sec"] - valid[0]["t_sec"]) / 3600.0
    flip_rate = n_flips / max(total_h, 1e-6)

    # Score margin at flip: at the post-flip decision, gap between the
    # winner's score and the runner-up's. A small gap means b̂_mean
    # jitter in the score by ~one margin would have flipped the choice
    # — chatter signature.
    margins_at_flip: list[float] = []
    for fi in flips:
        d = valid[fi]
        scores_sorted = sorted(s for s in d["scores"].values()
                                if np.isfinite(s))
        if len(scores_sorted) >= 2:
            margins_at_flip.append(scores_sorted[1] - scores_sorted[0])
    n_chatter_flips = sum(1 for m in margins_at_flip if m < 100.0)

    summary = {
        "cfg": cfg_name,
        "seed": seed,
        "dt": dt,
        "n_dec": n_dec,
        "n_flips": n_flips,
        "flip_rate_per_h": flip_rate,
        "median_dwell": float(np.median(dwells)),
        "max_dwell": int(max(dwells)),
        "n_chatter_flips": n_chatter_flips,
        "median_margin_at_flip_m": float(np.median(margins_at_flip))
                                     if margins_at_flip else 0.0,
        "mean_dist": r.ctrl_mean_m(),
        "pferr": float(np.mean(r.pf_err_m)),
        "depths_visited": sorted({float(d) for d in chosen_seq}),
    }
    label = current_process().name
    print(f"[{label}] {cfg_name:<10} seed={seed} "
          f"n_dec={n_dec:3d} flips={n_flips:3d} "
          f"rate={flip_rate:5.2f}/h "
          f"dwell_med={summary['median_dwell']:4.1f} "
          f"chatter_flips={n_chatter_flips:3d} "
          f"margin_med={summary['median_margin_at_flip_m']:5.0f}m "
          f"meandist={summary['mean_dist']:.0f}m "
          f"PFerr={summary['pferr']:.0f}m  ({dt:.1f}s)",
          flush=True)
    return summary


def main() -> None:
    print(f"=== rung-chatter diagnostic (Step 3 motivation, "
          f"N_PROCS={N_PROCS}) ===", flush=True)
    print(f"  station: {STATION[0]:.4f}, {STATION[1]:.4f}", flush=True)
    print(f"  seeds: {N_SEEDS}, configs: 3 → "
          f"{N_SEEDS * 3} total runs", flush=True)
    jobs = [(sd, c)
            for sd in range(N_SEEDS)
            for c in ("no_learn", "grid", "grid+ctd")]
    t0 = time.time()
    with Pool(processes=N_PROCS, initializer=_init_worker) as pool:
        results = pool.map(_run_one, jobs)
    print(f"\nall {len(results)} runs done; total wall-clock "
          f"{time.time() - t0:.0f}s", flush=True)

    rows_by_cfg: dict[str, list[dict]] = {
        "no_learn": [], "grid": [], "grid+ctd": [],
    }
    for row in results:
        rows_by_cfg[row["cfg"]].append(row)

    print(f"\n--- per-config aggregates over {N_SEEDS} seeds ---",
          flush=True)
    print(f"{'config':<10}  "
          f"{'n_dec':>6} {'flips':>6} {'rate/h':>7} "
          f"{'dwell_med':>10} {'max_dwell':>10} "
          f"{'chatter%':>9} {'margin_m':>10} "
          f"{'mean_dist':>10} {'PFerr':>7}",
          flush=True)
    print("-" * 110, flush=True)
    summaries: dict[str, dict] = {}
    for cfg_name in ["no_learn", "grid", "grid+ctd"]:
        rows = rows_by_cfg[cfg_name]
        if not rows:
            continue
        n_dec = np.array([r["n_dec"] for r in rows]).mean()
        n_flips = np.array([r["n_flips"] for r in rows]).mean()
        rate = np.array([r["flip_rate_per_h"] for r in rows]).mean()
        dwell_med = np.array([r["median_dwell"] for r in rows]).mean()
        max_dwell = np.array([r["max_dwell"] for r in rows]).mean()
        chatter_pct = np.array(
            [r["n_chatter_flips"] / max(r["n_flips"], 1) * 100.0
             for r in rows]
        ).mean()
        margin_med = np.array([r["median_margin_at_flip_m"]
                                for r in rows]).mean()
        mean_dist = np.array([r["mean_dist"] for r in rows]).mean()
        pferr = np.array([r["pferr"] for r in rows]).mean()
        s = {
            "n_dec": n_dec, "n_flips": n_flips, "rate": rate,
            "dwell_med": dwell_med, "max_dwell": max_dwell,
            "chatter_pct": chatter_pct, "margin_med": margin_med,
            "mean_dist": mean_dist, "pferr": pferr,
        }
        summaries[cfg_name] = s
        print(f"{cfg_name:<10}  "
              f"{n_dec:6.0f} {n_flips:6.0f} {rate:6.2f}/h "
              f"{dwell_med:10.1f} {max_dwell:10.1f} "
              f"{chatter_pct:8.0f}% {margin_med:9.0f}m "
              f"{mean_dist:9.0f}m {pferr:6.0f}m",
              flush=True)

    print(f"\n--- chatter signature interpretation ---", flush=True)
    if not summaries.get("no_learn") or not summaries.get("grid"):
        print("  (insufficient data)", flush=True)
        return
    nl = summaries["no_learn"]
    gr = summaries["grid"]
    rate_delta = gr["rate"] - nl["rate"]
    margin_delta = gr["margin_med"] - nl["margin_med"]
    chat_delta = gr["chatter_pct"] - nl["chatter_pct"]
    print(f"  grid vs no_learn:", flush=True)
    print(f"    flip rate:        "
          f"{nl['rate']:.2f}/h → {gr['rate']:.2f}/h  "
          f"(Δ = {rate_delta:+.2f}/h)", flush=True)
    print(f"    median margin:    "
          f"{nl['margin_med']:.0f}m → {gr['margin_med']:.0f}m  "
          f"(Δ = {margin_delta:+.0f}m)", flush=True)
    print(f"    chatter-flip %:   "
          f"{nl['chatter_pct']:.0f}% → {gr['chatter_pct']:.0f}%  "
          f"(Δ = {chat_delta:+.0f} pp)", flush=True)
    if "grid+ctd" in summaries:
        gc = summaries["grid+ctd"]
        rate_delta2 = gc["rate"] - nl["rate"]
        margin_delta2 = gc["margin_med"] - nl["margin_med"]
        chat_delta2 = gc["chatter_pct"] - nl["chatter_pct"]
        print(f"  grid+ctd vs no_learn:", flush=True)
        print(f"    flip rate:        "
              f"{nl['rate']:.2f}/h → {gc['rate']:.2f}/h  "
              f"(Δ = {rate_delta2:+.2f}/h)", flush=True)
        print(f"    median margin:    "
              f"{nl['margin_med']:.0f}m → {gc['margin_med']:.0f}m  "
              f"(Δ = {margin_delta2:+.0f}m)", flush=True)
        print(f"    chatter-flip %:   "
              f"{nl['chatter_pct']:.0f}% → {gc['chatter_pct']:.0f}%  "
              f"(Δ = {chat_delta2:+.0f} pp)", flush=True)

    # Verdict: bias-aware configs ramping flip rate AND dropping margin
    # AND raising chatter-% relative to no_learn = textbook chatter signature.
    print(f"\n--- verdict ---", flush=True)
    if (rate_delta > 0 and margin_delta < 0 and chat_delta > 0):
        print("  CHATTER SIGNATURE CONFIRMED — `grid` raises flip rate, "
              "lowers score margin at flips, and raises chatter-% vs "
              "`no_learn`. Consistent with controls reviewer #1: "
              "certainty-equivalent control over a 5-rung ladder "
              "amplifies `b̂_mean` jitter into rung-flip chatter. "
              "Step 3 (posterior-aware MPC) targets this directly.",
              flush=True)
    else:
        print("  CHATTER SIGNATURE NOT CONFIRMED — flip rate / margin / "
              "chatter-% don't collectively show the expected pattern. "
              "Step 3's failure-mode story may need refinement; check "
              "score-margin distribution per-decision rather than "
              "per-flip, or look at depth-rung occupancy by mission "
              "phase (early/mid/late).",
              flush=True)
    print(f"\n=== diagnostic complete ===", flush=True)


if __name__ == "__main__":
    main()
