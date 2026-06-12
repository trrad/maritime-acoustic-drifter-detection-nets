"""Fleet sweep over (density × surfacing policy) — answers:
    - How long does it take for the fleet to detect events?
    - When detected, how accurate is the localisation?
    - How well-calibrated is the σ_event posterior vs realised error?
across a coarse menu of fleet densities and surfacing policies.

Imports the heavy lifting from `_fleet_sim_v0.py`. Does NOT share event
RNG seeds across configs — each (density, policy) gets its own random
event set, so aggregation is by event PROPERTIES (distance to
detector centroid, # detectors) rather than by paired event matching.

Surfacing-policy menu (no underwater inter-drifter comms):
    - FixedInterval(2h, 6h, 12h): pre-agreed phase-aligned surface times
    - PostEvent(30 min after detection, 12h safety cap): each drifter
      that DETECTS the event surfaces 30 min later. The fleet
      "coordinates" by physics — same event acoustically heard by
      multiple drifters → all surface within ~30 min of each other.

Density configurations (3 levels):
    - D1: N=4 tight cluster (current baseline)
    - D2: N=16 mid-basin, 4×4 grid
    - D3: N=16 wider deployment, 4×4 grid spanning ~2× linear

Wall budget: ~3 hours total (12 configs × ~12 min mission + shared init).

Outputs:
    - Per-config detail PNGs in figures/sweep_{density}_{policy}.png
    - Cross-cut summary chart in figures/sweep_summary.png
    - Numerical table in figures/sweep_summary.txt
    - Property-bucket breakdown in figures/sweep_property_breakdown.png
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from multiprocessing import Pool

import numpy as np   # type: ignore[import-not-found]


# Imports from the per-policy harness; we reuse its helpers and keep
# the sweep driver minimal.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fleet_sim_v0 as fs  # noqa: E402


# --- Configuration ---

# Point-event Poisson rate. Default 0 in the realistic-region sweep:
# the threat model is BOAT TRACKS (`fs.BOAT_COUNT` boats with
# minute-cadence pings), and randomly-spawned single point pings are
# not part of the operational target. Set
# `FLEET_SWEEP_EVENT_RATE_PER_H=N` to opt back in for coverage stress
# tests (e.g., basin-wide synthetic event saturation).
POINT_EVENT_RATE_PER_H = float(os.environ.get(
    "FLEET_SWEEP_EVENT_RATE_PER_H", "0.0",
))

RUN_HOURS = int(os.environ.get("FLEET_SWEEP_RUN_HOURS", "72"))
N_PROCS = int(os.environ.get("FLEET_SWEEP_N_PROCS", "16"))
SEED_BASE = 5000   # different from _fleet_sim_v0's SEED_BASE so missions
                    # are independent of any prior runs

# Optional per-cell knob axes (CSV-parsed env vars). Each defaults to a
# single-element list matching the legacy single-value behavior. The
# sweep iterates the cartesian product (density × policy × these).
#   FLEET_SWEEP_LORA_SIGMAS_M       e.g. "20,100,200"
#   FLEET_SWEEP_CONTROL_CADENCES_S  e.g. "1800,7200" (30-min, 2-hour)
#   FLEET_SWEEP_RUN_HOURS_LIST      e.g. "168,672"   (1-week, 4-week)
def _csv_floats(env_name: str, default: str) -> tuple[float, ...]:
    raw = os.environ.get(env_name, default).strip()
    return tuple(float(x.strip()) for x in raw.split(",") if x.strip())

def _csv_ints(env_name: str, default: str) -> tuple[int, ...]:
    raw = os.environ.get(env_name, default).strip()
    return tuple(int(x.strip()) for x in raw.split(",") if x.strip())

LORA_SIGMAS_M = _csv_floats(
    "FLEET_SWEEP_LORA_SIGMAS_M", str(fs.LORA_SIGMA_M),
)
CONTROL_CADENCES_S = _csv_floats(
    "FLEET_SWEEP_CONTROL_CADENCES_S", "1800",
)
RUN_HOURS_LIST = _csv_ints(
    "FLEET_SWEEP_RUN_HOURS_LIST", str(RUN_HOURS),
)

# Campaign mode — multi-deployment with periodic redeployment.
# Each cycle is a FRESH drifter (PF/bias state restarts; particle init
# seeded distinctly per cycle so cycles don't share noise realization).
# Per-tick arrays are concatenated along time with absolute t_sec;
# mode-b windowed RTS and next-surface lookup are clipped to a single
# cycle (a redeployed drifter cannot exfil a previous-cycle event's
# TOA).
#
# Smart redeploy: at each cycle boundary, evaluate per-drifter
# triggers on cycle-end state:
#   - out_of_zone: drifter (lat, lon) at cycle end exits the target
#     detection polygon (default = the basin bbox).
#   - high_sigma:  PF posterior σ_pos sustained > threshold for the
#     last `SUSTAINED_H` hours of the cycle.
# Flagged → next cycle deployed at original station_target (= "ship
# visits, picks up, drops fresh"). Unflagged → next cycle inherits the
# drifter's physical end-of-cycle position (= "ship leaves the drifter
# in place; PF re-anchored on next surface fix"). PF state itself does
# NOT carry over (would require PF serialization).
#
#   FLEET_SWEEP_CAMPAIGN_MODE = "single" | "redeploy"  (default "single")
#   FLEET_SWEEP_REDEPLOY_INTERVAL_H                     (default 72.0)
#   FLEET_SWEEP_ZONE_BBOX               (lat_min,lon_min,lat_max,lon_max);
#                                       default = full basin bbox
#   FLEET_SWEEP_SIGMA_THRESHOLD_M        sustained-σ trigger (default 500.0)
#   FLEET_SWEEP_SIGMA_SUSTAINED_H        window for sustained-σ test
#                                        (default 6.0)
CAMPAIGN_MODE = os.environ.get("FLEET_SWEEP_CAMPAIGN_MODE", "single")
REDEPLOY_INTERVAL_H = float(
    os.environ.get("FLEET_SWEEP_REDEPLOY_INTERVAL_H", "72.0"),
)


def _parse_zone_bbox(env_str: str | None) -> tuple[float, float, float, float]:
    if env_str is None or not env_str.strip():
        return (fs.LAT_MIN, fs.LON_MIN, fs.LAT_MAX, fs.LON_MAX)
    parts = [float(x.strip()) for x in env_str.split(",") if x.strip()]
    if len(parts) != 4:
        raise ValueError(
            f"FLEET_SWEEP_ZONE_BBOX must be 4 csv floats "
            f"(lat_min,lon_min,lat_max,lon_max); got {env_str!r}"
        )
    return (parts[0], parts[1], parts[2], parts[3])


ZONE_BBOX = _parse_zone_bbox(os.environ.get("FLEET_SWEEP_ZONE_BBOX"))
SIGMA_THRESHOLD_M = float(
    os.environ.get("FLEET_SWEEP_SIGMA_THRESHOLD_M", "500.0"),
)
SIGMA_SUSTAINED_H = float(
    os.environ.get("FLEET_SWEEP_SIGMA_SUSTAINED_H", "6.0"),
)


@dataclass(frozen=True)
class DensityConfig:
    name: str
    label: str
    stations: tuple[tuple[float, float, float], ...]   # (lat, lon, depth_hint)
    # Fixed-anchor buoy set shared by every drifter in this density. If
    # None, falls back to `fs.DEFAULT_FIXED_ANCHORS` (the 4-buoy
    # edge+center default over the SoG bbox). Per-density override lets
    # a tightly-clustered patrol density override with anchors closer
    # to its operational area, or a wide-coverage density specify a 6-
    # or 8-buoy set to keep PDOP reasonable across the whole bbox.
    anchors: tuple[tuple[float, float], ...] | None = None


def _grid_stations(
    center_lat: float, center_lon: float,
    n_per_side: int, spacing_deg_lat: float, spacing_deg_lon: float,
) -> tuple[tuple[float, float, float], ...]:
    """Build a regular n_per_side × n_per_side grid of stations.

    `depth_hint` set to a placeholder 100 m; the per-drifter mission
    code resolves the actual bathymetry-bounded depth set per station.
    """
    half = (n_per_side - 1) / 2.0
    stations = []
    for i in range(n_per_side):
        for j in range(n_per_side):
            lat = center_lat + (i - half) * spacing_deg_lat
            lon = center_lon + (j - half) * spacing_deg_lon
            stations.append((lat, lon, 100.0))
    return tuple(stations)


# Basin centre (mid-SoG, where the existing 4-station cluster sits).
BASIN_CENTER_LAT = 49.3729
BASIN_CENTER_LON = -123.7032

DENSITY_CONFIGS: tuple[DensityConfig, ...] = (
    DensityConfig(
        name="D1_4_tight",
        label="N=4 tight cluster",
        stations=tuple(fs.STATIONS),     # use existing cluster
    ),
    DensityConfig(
        name="D2_16_mid",
        label="N=16 mid-basin (4×4)",
        stations=_grid_stations(
            BASIN_CENTER_LAT, BASIN_CENTER_LON,
            n_per_side=4,
            # ~4 km between stations latitudinally, ~6 km longitudinally
            spacing_deg_lat=4_000.0 / fs.EARTH_R_M,
            spacing_deg_lon=6_000.0 / (fs.EARTH_R_M
                * float(np.cos(np.deg2rad(BASIN_CENTER_LAT)))),
        ),
    ),
    DensityConfig(
        name="D3_16_wide",
        label="N=16 wide (4×4)",
        stations=_grid_stations(
            BASIN_CENTER_LAT, BASIN_CENTER_LON,
            n_per_side=4,
            # ~5 km between stations lat, ~7 km lon → 15×21 km coverage,
            # ~1.25× linear D2 with similar in-basin margin so all
            # stations stay within the NEMO bbox.
            spacing_deg_lat=5_000.0 / fs.EARTH_R_M,
            spacing_deg_lon=7_000.0 / (fs.EARTH_R_M
                * float(np.cos(np.deg2rad(BASIN_CENTER_LAT)))),
        ),
    ),
    DensityConfig(
        name="D4_16_dense",
        label="N=16 dense patrol (4×4)",
        stations=_grid_stations(
            BASIN_CENTER_LAT, BASIN_CENTER_LON,
            n_per_side=4,
            # 1.5 km lat × 2.0 km lon → ~4.5 × 6 km patrol coverage —
            # the focused-band scenario 16 nodes were originally
            # conceived for: a tight cluster monitoring a small high-
            # consequence remote area (MPA boundary, IUU patrol band)
            # rather than thinly tiling a basin.
            spacing_deg_lat=1_500.0 / fs.EARTH_R_M,
            spacing_deg_lon=2_000.0 / (fs.EARTH_R_M
                * float(np.cos(np.deg2rad(BASIN_CENTER_LAT)))),
        ),
    ),
    DensityConfig(
        name="D6_empirical",
        label="N=16 empirical-trajectory optimized (20260429_v5)",
        stations=(
            (49.375158, -123.710010, 100.0),
            (49.348208, -123.710010, 100.0),
            (49.402107, -123.689316, 100.0),
            (49.361683, -123.730704, 100.0),
            (49.388632, -123.668621, 100.0),
            (49.375158, -123.730704, 100.0),
            (49.375158, -123.627233, 100.0),
            (49.375158, -123.772092, 100.0),
            (49.334734, -123.730704, 100.0),
            (49.402107, -123.730704, 100.0),
            (49.361683, -123.668621, 100.0),
            (49.361683, -123.792786, 100.0),
            (49.415582, -123.668621, 100.0),
            (49.334734, -123.751398, 100.0),
            (49.321259, -123.647927, 100.0),
            (49.361683, -123.813481, 100.0),
        ),
    ),
    DensityConfig(
        # N=12 subset of D6_empirical, picked via farthest-point greedy
        # to maximize spatial spread. Suits 12-worker pool with one batch
        # per cycle (no overflow).
        name="D6_12_subset",
        label="N=12 subset of D6_empirical (farthest-point sampled)",
        stations=(
            (49.375158, -123.710010, 100.0),
            (49.321259, -123.647927, 100.0),
            (49.361683, -123.813481, 100.0),
            (49.415582, -123.668621, 100.0),
            (49.375158, -123.627233, 100.0),
            (49.375158, -123.772092, 100.0),
            (49.402107, -123.730704, 100.0),
            (49.334734, -123.730704, 100.0),
            (49.388632, -123.668621, 100.0),
            (49.348208, -123.710010, 100.0),
            (49.402107, -123.689316, 100.0),
            (49.361683, -123.730704, 100.0),
        ),
    ),
    DensityConfig(
        # N=24 extension of D6_empirical with 8 gap-filling stations
        # placed at midpoints of the largest pairwise distances. Suits
        # 12-worker pool with exactly two batches per cycle.
        name="D6_24_extended",
        label="N=24 extension of D6_empirical (8 gap-fillers added)",
        stations=(
            # All 16 D6_empirical stations:
            (49.375158, -123.710010, 100.0),
            (49.348208, -123.710010, 100.0),
            (49.402107, -123.689316, 100.0),
            (49.361683, -123.730704, 100.0),
            (49.388632, -123.668621, 100.0),
            (49.375158, -123.730704, 100.0),
            (49.375158, -123.627233, 100.0),
            (49.375158, -123.772092, 100.0),
            (49.334734, -123.730704, 100.0),
            (49.402107, -123.730704, 100.0),
            (49.361683, -123.668621, 100.0),
            (49.361683, -123.792786, 100.0),
            (49.415582, -123.668621, 100.0),
            (49.334734, -123.751398, 100.0),
            (49.321259, -123.647927, 100.0),
            (49.361683, -123.813481, 100.0),
            # 8 additional stations interpolated to fill the largest gaps:
            (49.395, -123.640, 100.0),     # NE corner fill
            (49.395, -123.785, 100.0),     # NW corner fill
            (49.328, -123.770, 100.0),     # SW corner fill
            (49.408, -123.755, 100.0),     # N edge fill
            (49.345, -123.800, 100.0),     # SW edge fill
            (49.388, -123.700, 100.0),     # central-N gap
            (49.348, -123.680, 100.0),     # central-S gap
            (49.378, -123.683, 100.0),     # mid-N-E gap
        ),
    ),
    DensityConfig(
        name="D5_optimized",
        label="N=16 optimized (20260429_v4_d4dense)",
        stations=(
            (49.370121, -123.674683, 100.0),
            (49.357192, -123.696214, 100.0),
            (49.389177, -123.716071, 100.0),
            (49.343717, -123.661723, 100.0),
            (49.379649, -123.744500, 100.0),
            (49.411090, -123.675519, 100.0),
            (49.361683, -123.689316, 100.0),
            (49.379649, -123.703112, 100.0),
            (49.352700, -123.799684, 100.0),
            (49.370666, -123.620335, 100.0),
            (49.352700, -123.730704, 100.0),
            (49.397616, -123.703112, 100.0),
            (49.415582, -123.675519, 100.0),
            (49.325751, -123.620335, 100.0),
            (49.397616, -123.785888, 100.0),
            (49.379649, -123.647927, 100.0),
        ),
    ),
)


@dataclass(frozen=True)
class PolicyConfig:
    name: str
    label: str


POLICIES: tuple[PolicyConfig, ...] = (
    PolicyConfig(name="fixed_2h", label="Fixed cadence (2h)"),
    PolicyConfig(name="fixed_6h", label="Fixed cadence (6h)"),
    PolicyConfig(name="fixed_12h", label="Fixed cadence (12h)"),
    PolicyConfig(name="post_event_30m_12h",
                  label="Post-event (30min, 12h cap)"),
)


# Patch the surfacing-policy factory to handle the new fixed-cadence
# variants. We don't want to monkeypatch fs; instead provide our own
# factory and inject it via fs._build_surfacing_policy reassignment.
_orig_build_policy = fs._build_surfacing_policy


def _sweep_build_policy(name: str, audible_events: list):
    from rbpf_prototype import (  # type: ignore[import-not-found]
        FixedIntervalPolicy,
    )
    if name == "fixed_2h":
        return FixedIntervalPolicy(period_h=2.0)
    if name == "fixed_12h":
        return FixedIntervalPolicy(period_h=12.0)
    return _orig_build_policy(name, audible_events)


fs._build_surfacing_policy = _sweep_build_policy   # type: ignore[assignment]


# --- Campaign-mode wrapper ---

# Cycle-seed offset: each cycle uses s_idx_eff = s_idx + cycle*OFFSET so
# `seed = SEED_BASE + s_idx_eff*100` differs per (drifter, cycle) and
# cycles never collide with neighboring drifters' seed lattice (16
# drifters × 1000 step = always distinct from cycle k+1's lane).
_CAMPAIGN_CYCLE_OFFSET = 1000


# --- Smart-redeploy trigger helpers ---


def _bbox_contains(lat: float, lon: float,
                    bbox: tuple[float, float, float, float]) -> bool:
    """bbox = (lat_min, lon_min, lat_max, lon_max)."""
    return (bbox[0] <= lat <= bbox[2]) and (bbox[1] <= lon <= bbox[3])


def _cycle_end_summary(cycle_r: dict, sustained_h: float) -> dict:
    """Extract end-of-cycle metrics for redeploy-trigger evaluation.

    `final_lat/lon`: drifter physical truth position at cycle end.
    `sustained_sigma_pos_m`: median PF posterior σ_pos over the last
    `sustained_h` hours of the cycle. Median (not max) so a single
    transient PF spike doesn't trigger replacement; sustained means
    the drifter has actually been losing position info, not a momentary
    glitch.
    """
    truth_lats = np.asarray(cycle_r["truth_lats"], dtype=float)
    truth_lons = np.asarray(cycle_r["truth_lons"], dtype=float)
    t_sec = np.asarray(cycle_r["t_sec"], dtype=float)
    pf_cov_m = np.asarray(cycle_r["pf_cov_m"], dtype=float)  # (T, 2, 2)
    sigma_pos = np.sqrt(0.5 * (pf_cov_m[:, 0, 0] + pf_cov_m[:, 1, 1]))

    final_lat = float(truth_lats[-1])
    final_lon = float(truth_lons[-1])
    window_s = sustained_h * 3600.0
    cutoff_t = float(t_sec[-1]) - window_s
    in_window = t_sec >= cutoff_t
    if not bool(np.any(in_window)):
        sustained_sigma_pos = float(np.nanmedian(sigma_pos))
    else:
        sustained_sigma_pos = float(np.nanmedian(sigma_pos[in_window]))
    return {
        "final_lat": final_lat,
        "final_lon": final_lon,
        "sustained_sigma_pos_m": sustained_sigma_pos,
    }


def _evaluate_redeploy_triggers(summary: dict,
                                  zone_bbox: tuple[float, float, float, float],
                                  sigma_threshold_m: float) -> dict:
    """Returns {out_of_zone, high_sigma, flagged} for the cycle-end summary."""
    out_of_zone = not _bbox_contains(
        summary["final_lat"], summary["final_lon"], zone_bbox,
    )
    high_sigma = summary["sustained_sigma_pos_m"] > sigma_threshold_m
    return {
        "out_of_zone": bool(out_of_zone),
        "high_sigma": bool(high_sigma),
        "flagged": bool(out_of_zone or high_sigma),
    }


def _run_one_drifter_campaign(args: tuple) -> dict:
    """Multi-cycle wrapper around `fs._run_one_drifter`.

    Args layout (extends single-mode tuple by trailing fields):
      (s_idx, policy_name, audible_events_full, station_target,
       total_run_hours, redeploy_interval_h,
       lora_sigma_m=None, control_cadence_sec=None,
       anchors=None)

    `lora_sigma_m` overrides the module-level LORA_SIGMA_M for this
    drifter's missions; `control_cadence_sec` overrides the SimConfig
    default decision interval (1800 → e.g. 7200 for 2-hour decisions
    paired with horizon_n=12 → 24-hour MPC plan). `anchors` is the
    shared fixed-buoy set for the cell (every drifter in the fleet
    sees the same anchors). All default to None which falls through to
    `fs._run_one_drifter`'s own defaults.

    Each cycle runs `fs._run_one_drifter` with `run_hours=cycle_hours`,
    a distinct effective s_idx (so seed differs), and audible events
    pre-filtered to the cycle window AND time-rebased to cycle-local
    time (cycle-local t=0 corresponds to absolute t=cycle_t_start).

    Per-tick arrays from each cycle are concatenated with absolute
    t_sec offsets. Smoother means_local_m are re-encoded into cycle-0's
    ref frame; covs are treated as frame-invariant (small Δref → no
    rotation, only translation).
    """
    s_idx, policy_name, audible_events_full = args[0], args[1], args[2]
    station_target = args[3]
    total_run_hours = float(args[4])
    redeploy_interval_h = float(args[5])
    lora_sigma_m = args[6] if len(args) >= 7 else None
    control_cadence_sec = args[7] if len(args) >= 8 else None
    anchors = args[8] if len(args) >= 9 else None

    cycle_dur_s = redeploy_interval_h * 3600.0
    n_cycles = int(np.ceil(total_run_hours / redeploy_interval_h))

    # Smart-redeploy v1 state: track end-of-prev-cycle summary + trigger
    # flags so the next cycle can pick its `station_target` based on
    # whether the drifter was flagged for replacement or stays.
    prev_summary: dict | None = None
    prev_flags: dict | None = None

    cycle_results = []
    for k in range(n_cycles):
        t_start_k = k * cycle_dur_s
        t_end_k = min((k + 1) * cycle_dur_s,
                       total_run_hours * 3600.0)
        cycle_hours = (t_end_k - t_start_k) / 3600.0
        # Round to int for SimConfig; cycle_dur is always integer-h
        # in practice. Guard: if last cycle is < 1 hour, skip it.
        cycle_hours_int = int(round(cycle_hours))
        if cycle_hours_int <= 0:
            continue

        # --- Smart redeploy: pick this cycle's station_target ---
        # Cycle 0 always uses the original station_target (initial drop).
        # Subsequent cycles: flagged drifters (out-of-zone OR sustained
        # high σ_pos) redeploy at the original station_target — the ship
        # visits and drops a fresh unit at its planned site. Unflagged
        # drifters stay where they physically drifted to at cycle-end.
        # PF/bias state does NOT carry over (would require PF
        # serialization). The fallback "redeploy at original station"
        # can be replaced with `_drop_point_optimizer.optimize_replacements`
        # once a fleet-synchronized cycle barrier is in place; the
        # required Python API is already implemented.
        if k == 0 or prev_flags is None or prev_flags["flagged"]:
            cycle_target = station_target
        else:
            assert prev_summary is not None
            cycle_target = (
                prev_summary["final_lat"],
                prev_summary["final_lon"],
                station_target[2],   # depth_hint preserved
            )

        # Filter audible events to this cycle's absolute-time window,
        # then rebase to cycle-local time (subtract t_start_k). Each
        # cycle's drifter sees events at cycle-local t_sec ∈ [0,
        # cycle_dur_s). PostEventSurfacingPolicy fires off
        # cycle-local time, so this is the right rebasing.
        cycle_audible = [
            fs.AcousticEvent(
                lat=e.lat, lon=e.lon,
                t_sec=e.t_sec - t_start_k,
                src=e.src,
            )
            for e in audible_events_full
            if t_start_k <= e.t_sec < t_end_k
        ]

        # Effective s_idx → seed = SEED_BASE + s_idx_eff*100. The +k
        # offset varies particle init across cycles (cycles don't share
        # noise realization). station_target is passed explicitly so
        # the effective s_idx is NOT used to index STATIONS.
        s_idx_eff = s_idx + k * _CAMPAIGN_CYCLE_OFFSET
        sub_args = (
            s_idx_eff, policy_name, cycle_audible,
            cycle_target, cycle_hours_int,
            lora_sigma_m, control_cadence_sec, anchors,
        )
        cycle_r = fs._run_one_drifter(sub_args)
        cycle_r["_cycle_idx"] = k
        cycle_r["_cycle_t_start_sec"] = t_start_k

        # Evaluate triggers on this cycle's end state — the result
        # decides what NEXT cycle's station_target is.
        summary = _cycle_end_summary(cycle_r, SIGMA_SUSTAINED_H)
        flags = _evaluate_redeploy_triggers(
            summary, ZONE_BBOX, SIGMA_THRESHOLD_M,
        )
        cycle_r["_redeploy_summary"] = summary
        cycle_r["_redeploy_triggers"] = flags
        cycle_r["_redeploy_target_used"] = cycle_target
        prev_summary = summary
        prev_flags = flags

        cycle_results.append(cycle_r)

    return _concatenate_cycles(s_idx, policy_name, cycle_results)


def _concatenate_cycles(
    s_idx: int, policy_name: str, cycle_results: list[dict],
) -> dict:
    """Stitch per-cycle outputs into one drifter dict matching the
    schema of `fs._run_one_drifter`. Adds `cycle_idx_per_tick` and
    `cycle_boundaries_sec` for downstream cycle-aware lookups (mode-b
    windowed RTS, next-surface clipping, analyzer plot annotations)."""
    if not cycle_results:
        raise ValueError(f"campaign for drifter {s_idx} produced no cycles")
    c0 = cycle_results[0]
    ref_lat0 = float(c0["smooth_ref_lat"])
    ref_lon0 = float(c0["smooth_ref_lon"])
    cos_ref0 = float(np.cos(np.deg2rad(ref_lat0)))

    parts: dict[str, list] = {
        "truth_lats": [], "truth_lons": [],
        "pf_mean_lats": [], "pf_mean_lons": [],
        "pf_cov_m": [], "lora_fix_mask": [],
        "depths": [], "t_sec": [],
        "smooth_means_local_m": [], "smooth_covs_m": [],
        "station_keeping_per_tick": [],
        "pf_err_per_tick": [], "smooth_err_per_tick": [],
        "cycle_idx_per_tick": [],
    }
    n_surf = 0
    n_lora = 0
    cycle_boundaries_sec = []   # cycle k start time (absolute)

    for k, c in enumerate(cycle_results):
        t_start = float(c["_cycle_t_start_sec"])
        cycle_boundaries_sec.append(t_start)
        t_local = np.asarray(c["t_sec"], dtype=float)
        T_k = t_local.size

        parts["truth_lats"].append(np.asarray(c["truth_lats"], dtype=float))
        parts["truth_lons"].append(np.asarray(c["truth_lons"], dtype=float))
        parts["pf_mean_lats"].append(np.asarray(c["pf_mean_lats"], dtype=float))
        parts["pf_mean_lons"].append(np.asarray(c["pf_mean_lons"], dtype=float))
        parts["pf_cov_m"].append(np.asarray(c["pf_cov_m"], dtype=float))
        parts["lora_fix_mask"].append(np.asarray(c["lora_fix_mask"], dtype=bool))
        parts["depths"].append(np.asarray(c["depths"], dtype=float))
        parts["t_sec"].append(t_local + t_start)

        # Re-encode smoother means_local_m into cycle-0's ref frame by
        # converting to absolute lat/lon then re-projecting.
        m_k = np.asarray(c["smooth_means_local_m"], dtype=float)  # (T,2): [east_m, north_m]
        rk_lat = float(c["smooth_ref_lat"])
        rk_lon = float(c["smooth_ref_lon"])
        cos_k = float(np.cos(np.deg2rad(rk_lat)))
        # NOTE: fs._smoothed_at_t reads m[1] as north and m[0] as east.
        abs_lat = rk_lat + m_k[:, 1] / fs.EARTH_R_M
        abs_lon = rk_lon + m_k[:, 0] / (fs.EARTH_R_M * cos_k)
        new_east = (abs_lon - ref_lon0) * fs.EARTH_R_M * cos_ref0
        new_north = (abs_lat - ref_lat0) * fs.EARTH_R_M
        parts["smooth_means_local_m"].append(
            np.stack([new_east, new_north], axis=1))
        # Covs are nominally rotation-invariant under tiny ref shifts
        # (sub-1km Δref, sub-arcminute → cos shift ≈ 1e-6 relative).
        # Carry through as-is.
        parts["smooth_covs_m"].append(np.asarray(c["smooth_covs_m"], dtype=float))

        parts["station_keeping_per_tick"].append(
            np.asarray(c["station_keeping_per_tick"], dtype=float))
        parts["pf_err_per_tick"].append(
            np.asarray(c["pf_err_per_tick"], dtype=float))
        parts["smooth_err_per_tick"].append(
            np.asarray(c["smooth_err_per_tick"], dtype=float))
        parts["cycle_idx_per_tick"].append(
            np.full(T_k, k, dtype=np.int32))
        n_surf += int(c["n_surfacings"])
        n_lora += int(c["n_lora_fix_ticks"])

    sk_concat = np.concatenate(parts["station_keeping_per_tick"])
    pf_err_concat = np.concatenate(parts["pf_err_per_tick"])
    smooth_err_concat = np.concatenate(parts["smooth_err_per_tick"])

    out = {
        "s_idx": s_idx,
        "drifter_id": s_idx,
        "policy_name": policy_name,
        "seed": cycle_results[0]["seed"],
        "dt_sec": cycle_results[0]["dt_sec"],
        "station_lat": cycle_results[0]["station_lat"],
        "station_lon": cycle_results[0]["station_lon"],
        "n_surfacings": n_surf,
        "n_lora_fix_ticks": n_lora,
        "truth_lats": np.concatenate(parts["truth_lats"]),
        "truth_lons": np.concatenate(parts["truth_lons"]),
        "pf_mean_lats": np.concatenate(parts["pf_mean_lats"]),
        "pf_mean_lons": np.concatenate(parts["pf_mean_lons"]),
        "pf_cov_m": np.concatenate(parts["pf_cov_m"], axis=0),
        "lora_fix_mask": np.concatenate(parts["lora_fix_mask"]),
        "depths": np.concatenate(parts["depths"]),
        "t_sec": np.concatenate(parts["t_sec"]),
        "smooth_means_local_m": np.concatenate(
            parts["smooth_means_local_m"], axis=0),
        "smooth_covs_m": np.concatenate(parts["smooth_covs_m"], axis=0),
        "smooth_ref_lat": ref_lat0,
        "smooth_ref_lon": ref_lon0,
        "ctrl_mean_m": float(np.mean(sk_concat)),
        "pf_err_mean": float(np.nanmean(pf_err_concat)),
        "smooth_err_mean": float(np.nanmean(smooth_err_concat)),
        "station_keeping_per_tick": sk_concat,
        "pf_err_per_tick": pf_err_concat,
        "smooth_err_per_tick": smooth_err_concat,
        "cycle_idx_per_tick": np.concatenate(parts["cycle_idx_per_tick"]),
        "cycle_boundaries_sec": np.asarray(cycle_boundaries_sec, dtype=float),
        "n_cycles": len(cycle_results),
        # Smart-redeploy v1 audit trail — one entry per cycle, in cycle
        # order. Downstream analyzers can correlate flagged cycles
        # against recon performance.
        "redeploy_summaries": [c.get("_redeploy_summary") for c in cycle_results],
        "redeploy_triggers": [c.get("_redeploy_triggers") for c in cycle_results],
        "redeploy_targets_used": [c.get("_redeploy_target_used") for c in cycle_results],
    }
    return out


# --- Single-config runner ---

def _run_one_config(
    density: DensityConfig, policy: PolicyConfig, pool,
    event_seed: int,
    lora_sigma_m: float | None = None,
    control_cadence_sec: float | None = None,
    run_hours: int | None = None,
) -> dict:
    """Run all drifters for one (density, policy, [lora_sigma_m,
    control_cadence_sec, run_hours]) cell, generate events for the
    resulting cluster, do mode-(a) + mode-(b) detection + reconstruction,
    return aggregate dict.

    `lora_sigma_m` overrides the module's LORA_SIGMA_M default for this
    cell only. `control_cadence_sec` overrides the SimConfig decision
    interval (1800 default → 7200 for 2-hour decisions / 24-hour MPC
    plan with horizon_n=12). `run_hours` overrides the module RUN_HOURS
    for the per-cell mission length.
    """
    cell_run_hours = int(run_hours) if run_hours is not None else RUN_HOURS
    cell_label = f"density={density.name} | policy={policy.name}"
    if lora_sigma_m is not None:
        cell_label += f" | σ_m={lora_sigma_m:g}m"
    if control_cadence_sec is not None:
        cell_label += f" | cad={control_cadence_sec:g}s"
    if run_hours is not None and run_hours != RUN_HOURS:
        cell_label += f" | run_h={cell_run_hours}"
    print(f"\n=== {cell_label} ===", flush=True)
    _ = len(density.stations)  # kept for parity-check w/ legacy logging

    # --- Generate events for THIS config (independent random set). ---
    mission_dur = cell_run_hours * 3600.0
    point_events = fs._generate_point_events(
        rate_per_h=POINT_EVENT_RATE_PER_H,
        mission_dur_sec=mission_dur, seed=event_seed,
    )
    boat_tracks, boat_events = fs._generate_boat_tracks(
        n_boats=fs.BOAT_COUNT, mission_dur_sec=mission_dur,
        speed_ms=fs.BOAT_SPEED_MS, ping_interval_s=fs.BOAT_PING_INTERVAL_S,
        seed=event_seed + 1,
    )
    all_events = sorted(point_events + boat_events,
                         key=lambda e: e.t_sec)
    print(f"  events: {len(point_events)} point + "
          f"{len(boat_events)} boat = {len(all_events)} total",
          flush=True)

    # --- Build per-drifter audible-event lists + jobs. ---
    AUDIBLE_RADIUS_M = fs.AUDIBLE_EVENT_RADIUS_M
    jobs = []
    for s_idx, station in enumerate(density.stations):
        s_lat_t, s_lon_t, _ = station
        audible = [
            e for e in all_events
            if fs._distance_m_latlon(e.lat, e.lon, s_lat_t, s_lon_t)
               <= AUDIBLE_RADIUS_M
        ]
        # Resolve the cell's anchor set. Per-density override wins; fall
        # back to the module default placed across the SoG bbox.
        cell_anchors = (density.anchors if density.anchors is not None
                        else fs.DEFAULT_FIXED_ANCHORS)
        if CAMPAIGN_MODE == "redeploy":
            # Tuple shape: (s_idx, policy_name, audible_events,
            #   station_target, total_run_hours, redeploy_interval_h,
            #   lora_sigma_m, control_cadence_sec, anchors).
            jobs.append((s_idx, policy.name, audible, station,
                          float(cell_run_hours), REDEPLOY_INTERVAL_H,
                          lora_sigma_m, control_cadence_sec,
                          cell_anchors))
        elif CAMPAIGN_MODE == "single":
            # Tuple shape: (s_idx, policy_name, audible_events,
            #   station_target, run_hours, lora_sigma_m,
            #   control_cadence_sec, anchors).
            jobs.append((s_idx, policy.name, audible, station,
                          cell_run_hours, lora_sigma_m,
                          control_cadence_sec, cell_anchors))
        else:
            raise ValueError(
                f"FLEET_SWEEP_CAMPAIGN_MODE must be 'single' or "
                f"'redeploy'; got {CAMPAIGN_MODE!r}"
            )

    # --- Run missions in parallel. ---
    t_pol = time.time()
    runner = (_run_one_drifter_campaign
              if CAMPAIGN_MODE == "redeploy"
              else fs._run_one_drifter)
    drifters = pool.map(runner, jobs)
    print(f"  {len(drifters)} missions done "
          f"(wall {time.time() - t_pol:.0f}s)", flush=True)

    # --- Detect + reconstruct (both modes). ---
    recons_a, recons_b, detect_counts = fs._do_detect_and_reconstruct(
        drifters, all_events,
    )

    n_recon_a = sum(1 for r in recons_a if np.isfinite(r.error_m))
    n_recon_b = sum(1 for r in recons_b if np.isfinite(r.error_m))
    print(f"  mode-a reconstructed: {n_recon_a} / {len(all_events)} "
          f"({100 * n_recon_a / max(len(all_events), 1):.1f}%)",
          flush=True)
    print(f"  mode-b reconstructed: {n_recon_b} / {len(all_events)} "
          f"({100 * n_recon_b / max(len(all_events), 1):.1f}%)",
          flush=True)

    return {
        "density": density,
        "policy": policy,
        "drifters": drifters,
        "recons_a": recons_a,
        "recons_b": recons_b,
        "detect_counts": detect_counts,
        "n_events": len(all_events),
        "n_point_events": len(point_events),
        "n_boat_events": len(boat_events),
        "boat_tracks": boat_tracks,
        "event_seed": event_seed,
    }


# --- Aggregation + reporting ---

def _summarise_recons(recons: list) -> dict:
    finite = [r for r in recons if np.isfinite(r.error_m)]
    n = len(finite)
    if n == 0:
        return {"n": 0}
    errs = np.array([r.error_m for r in finite])
    sigmas = np.array([r.sigma_m for r in finite])
    expected_mean_err = 1.25 * sigmas.mean()
    return {
        "n": n,
        "err_mean": float(errs.mean()),
        "err_p50": float(np.median(errs)),
        "err_p95": float(np.percentile(errs, 95)),
        "sigma_mean": float(sigmas.mean()),
        "sigma_p50": float(np.median(sigmas)),
        "sigma_p95": float(np.percentile(sigmas, 95)),
        "calibration_ratio":
            float(errs.mean() / max(expected_mean_err, 1e-9)),
    }


def _summarise_recons_b(recons: list) -> dict:
    base = _summarise_recons(recons)
    finite = [r for r in recons if np.isfinite(r.error_m)]
    if not finite:
        return base
    ttd = np.array([r.time_to_detect_sec for r in finite
                    if np.isfinite(r.time_to_detect_sec)]) / 60.0   # → min
    if ttd.size > 0:
        base["ttd_min_mean"] = float(ttd.mean())
        base["ttd_min_p50"] = float(np.median(ttd))
        base["ttd_min_p95"] = float(np.percentile(ttd, 95))
    return base


def _bucket_by_distance(recons: list, edges: list) -> list[dict]:
    """Bucket reconstructions by distance to detector centroid; return
    summary per bucket."""
    finite = [r for r in recons
              if np.isfinite(r.error_m)
              and np.isfinite(r.dist_to_detector_centroid_m)]
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        bucket = [r for r in finite
                   if lo <= r.dist_to_detector_centroid_m < hi]
        if not bucket:
            out.append({"lo": lo, "hi": hi, "n": 0})
            continue
        out.append({
            "lo": lo, "hi": hi,
            **_summarise_recons(bucket),
        })
    return out


def _print_summary(results: list[dict]) -> None:
    print(f"\n=== sweep summary ===", flush=True)
    print(f"  cols: density, policy, mode, n, σ_event_p50, "
          f"recon_err_p50, calibration_ratio, ttd_min_p50", flush=True)
    for r in results:
        for mode_letter, key in [("a", "recons_a"), ("b", "recons_b")]:
            recs = r[key]
            if mode_letter == "b":
                s = _summarise_recons_b(recs)
            else:
                s = _summarise_recons(recs)
            ttd = s.get("ttd_min_p50", float("nan"))
            calib = s.get("calibration_ratio", float("nan"))
            print(
                f"  {r['density'].name:>14}  {r['policy'].name:>20}  "
                f"mode-{mode_letter}  n={s.get('n', 0):>4}  "
                f"σ_p50={s.get('sigma_p50', float('nan')):>7.0f}m  "
                f"err_p50={s.get('err_p50', float('nan')):>7.0f}m  "
                f"calib={calib:>5.2f}  "
                f"ttd_p50={ttd:>5.1f}min",
                flush=True,
            )


def _save_summary_table(results: list[dict], out_path: str) -> None:
    lines = []
    lines.append(
        f"{'density':>14}  {'policy':>22}  {'mode':>4}  "
        f"{'n':>5}  "
        f"{'σ_mean':>7}  {'σ_p50':>7}  {'σ_p95':>7}  "
        f"{'err_mean':>9}  {'err_p50':>9}  {'err_p95':>9}  "
        f"{'calib':>6}  "
        f"{'ttd_p50':>9}  {'ttd_p95':>9}  "
        f"{'sk_mean':>9}  "
        f"{'surf':>6}  "
    )
    for r in results:
        d = r["drifters"]
        sk_mean = float(np.mean([dr["ctrl_mean_m"] for dr in d]))
        n_surf = sum(dr["n_surfacings"] for dr in d)
        for mode_letter, key in [("a", "recons_a"), ("b", "recons_b")]:
            if mode_letter == "b":
                s = _summarise_recons_b(r[key])
            else:
                s = _summarise_recons(r[key])
            lines.append(
                f"{r['density'].name:>14}  {r['policy'].name:>22}  "
                f"{mode_letter:>4}  {s.get('n', 0):>5}  "
                f"{s.get('sigma_mean', float('nan')):>7.0f}  "
                f"{s.get('sigma_p50', float('nan')):>7.0f}  "
                f"{s.get('sigma_p95', float('nan')):>7.0f}  "
                f"{s.get('err_mean', float('nan')):>9.0f}  "
                f"{s.get('err_p50', float('nan')):>9.0f}  "
                f"{s.get('err_p95', float('nan')):>9.0f}  "
                f"{s.get('calibration_ratio', float('nan')):>6.2f}  "
                f"{s.get('ttd_min_p50', float('nan')):>9.1f}  "
                f"{s.get('ttd_min_p95', float('nan')):>9.1f}  "
                f"{sk_mean:>9.0f}  "
                f"{n_surf:>6}  "
            )
    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"  saved {out_path}", flush=True)


def _build_summary_chart(results: list[dict], out_path: str) -> None:
    import matplotlib.pyplot as plt   # type: ignore[import-not-found]
    fig, axes = plt.subplots(2, 3, figsize=(18, 10))

    densities = [d.name for d in DENSITY_CONFIGS]
    policies = [p.name for p in POLICIES]

    # Build a (density × policy) grid for each scalar metric, mode (a)
    # and mode (b) separately. Plot heatmaps + trade-off scatters.
    def _grid(metric_key: str, mode_key: str, summarize) -> np.ndarray:
        g = np.full((len(densities), len(policies)), np.nan)
        for r in results:
            di = densities.index(r["density"].name)
            pi = policies.index(r["policy"].name)
            s = summarize(r[mode_key])
            g[di, pi] = s.get(metric_key, np.nan)
        return g

    # Panel (0,0): mode-a σ_event p50 heatmap
    ax = axes[0, 0]
    g = _grid("sigma_p50", "recons_a", _summarise_recons)
    im = ax.imshow(g, aspect="auto", cmap="viridis")
    ax.set_xticks(range(len(policies)))
    ax.set_xticklabels(policies, rotation=20, ha="right", fontsize=8)
    ax.set_yticks(range(len(densities)))
    ax.set_yticklabels(densities, fontsize=8)
    ax.set_title("σ_event p50 (m) — mode (a) full-retro")
    for di in range(len(densities)):
        for pi in range(len(policies)):
            v = g[di, pi]
            if np.isfinite(v):
                ax.text(pi, di, f"{v:.0f}", ha="center", va="center",
                         color="white", fontsize=8)
    plt.colorbar(im, ax=ax, fraction=0.046)

    # Panel (0,1): mode-b σ_event p50 heatmap
    ax = axes[0, 1]
    g = _grid("sigma_p50", "recons_b", _summarise_recons_b)
    im = ax.imshow(g, aspect="auto", cmap="viridis")
    ax.set_xticks(range(len(policies)))
    ax.set_xticklabels(policies, rotation=20, ha="right", fontsize=8)
    ax.set_yticks(range(len(densities)))
    ax.set_yticklabels(densities, fontsize=8)
    ax.set_title("σ_event p50 (m) — mode (b) next-surface")
    for di in range(len(densities)):
        for pi in range(len(policies)):
            v = g[di, pi]
            if np.isfinite(v):
                ax.text(pi, di, f"{v:.0f}", ha="center", va="center",
                         color="white", fontsize=8)
    plt.colorbar(im, ax=ax, fraction=0.046)

    # Panel (0,2): mode-b time-to-detect p50 heatmap (minutes)
    ax = axes[0, 2]
    g = _grid("ttd_min_p50", "recons_b", _summarise_recons_b)
    im = ax.imshow(g, aspect="auto", cmap="plasma")
    ax.set_xticks(range(len(policies)))
    ax.set_xticklabels(policies, rotation=20, ha="right", fontsize=8)
    ax.set_yticks(range(len(densities)))
    ax.set_yticklabels(densities, fontsize=8)
    ax.set_title("time-to-detect p50 (min) — mode (b)")
    for di in range(len(densities)):
        for pi in range(len(policies)):
            v = g[di, pi]
            if np.isfinite(v):
                ax.text(pi, di, f"{v:.1f}", ha="center", va="center",
                         color="white", fontsize=8)
    plt.colorbar(im, ax=ax, fraction=0.046)

    # Panel (1,0): coverage heatmap (events reconstructed / total)
    ax = axes[1, 0]
    g = np.full((len(densities), len(policies)), np.nan)
    for r in results:
        di = densities.index(r["density"].name)
        pi = policies.index(r["policy"].name)
        s = _summarise_recons_b(r["recons_b"])
        g[di, pi] = 100.0 * s.get("n", 0) / max(r["n_events"], 1)
    im = ax.imshow(g, aspect="auto", cmap="cividis")
    ax.set_xticks(range(len(policies)))
    ax.set_xticklabels(policies, rotation=20, ha="right", fontsize=8)
    ax.set_yticks(range(len(densities)))
    ax.set_yticklabels(densities, fontsize=8)
    ax.set_title("coverage % (mode b reconstructions / events)")
    for di in range(len(densities)):
        for pi in range(len(policies)):
            v = g[di, pi]
            if np.isfinite(v):
                ax.text(pi, di, f"{v:.1f}", ha="center", va="center",
                         color="white", fontsize=8)
    plt.colorbar(im, ax=ax, fraction=0.046)

    # Panel (1,1): calibration ratio mode (b)
    ax = axes[1, 1]
    g = _grid("calibration_ratio", "recons_b", _summarise_recons_b)
    im = ax.imshow(g, aspect="auto", cmap="coolwarm",
                    vmin=0.5, vmax=2.5)
    ax.set_xticks(range(len(policies)))
    ax.set_xticklabels(policies, rotation=20, ha="right", fontsize=8)
    ax.set_yticks(range(len(densities)))
    ax.set_yticklabels(densities, fontsize=8)
    ax.set_title("calibration ratio (1.0 = well-calibrated)")
    for di in range(len(densities)):
        for pi in range(len(policies)):
            v = g[di, pi]
            if np.isfinite(v):
                ax.text(pi, di, f"{v:.2f}", ha="center", va="center",
                         color="black", fontsize=8)
    plt.colorbar(im, ax=ax, fraction=0.046)

    # Panel (1,2): scatter — mean fleet surface events vs σ_event_p50 mode(b)
    ax = axes[1, 2]
    for r in results:
        d = r["drifters"]
        n_surf = sum(dr["n_surfacings"] for dr in d)
        sb = _summarise_recons_b(r["recons_b"])
        if "sigma_p50" not in sb:
            continue
        di = densities.index(r["density"].name)
        ax.scatter(n_surf, sb["sigma_p50"], s=80,
                    color=f"C{di}",
                    label=f"{r['density'].name}/{r['policy'].name}")
        ax.annotate(f"{r['policy'].name}",
                     (n_surf, sb["sigma_p50"]),
                     fontsize=6, alpha=0.7,
                     xytext=(5, 5), textcoords="offset points")
    ax.set_xlabel("fleet surface events (power proxy)")
    ax.set_ylabel("σ_event p50 (m), mode (b)")
    ax.set_title("Power vs σ_event tradeoff (mode b)")
    ax.set_yscale("log")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=6, loc="upper right")

    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    print(f"  saved {out_path}", flush=True)
    import matplotlib.pyplot as plt2
    plt2.close(fig)


def _build_property_breakdown_chart(
    results: list[dict], out_path: str,
) -> None:
    """Per (density × policy × mode), bucket reconstructions by
    distance-to-cluster and N_detectors; plot σ_event vs property."""
    import matplotlib.pyplot as plt   # type: ignore[import-not-found]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Distance buckets in km (linear).
    dist_edges = [0, 1000, 2000, 3000, 5000, 8000]

    # Panel (0,0): σ_event vs distance, mode (a), one line per (density, policy)
    ax = axes[0, 0]
    for r in results:
        recs = r["recons_a"]
        buckets = _bucket_by_distance(recs, dist_edges)
        xs = [(b["lo"] + b["hi"]) / 2.0 for b in buckets if b.get("n", 0) > 0]
        ys = [b["sigma_p50"] for b in buckets if b.get("n", 0) > 0]
        if xs:
            ax.plot(xs, ys, "-o", lw=1.0, ms=3, alpha=0.7,
                     label=f"{r['density'].name}/{r['policy'].name}")
    ax.set_xlabel("distance to detector centroid (m)")
    ax.set_ylabel("σ_event p50 (m), mode (a)")
    ax.set_yscale("log")
    ax.set_title("σ_event vs event-cluster distance (mode a)")
    ax.legend(fontsize=6, loc="upper left", ncol=2)
    ax.grid(alpha=0.3)

    # Panel (0,1): σ_event vs distance, mode (b)
    ax = axes[0, 1]
    for r in results:
        recs = r["recons_b"]
        buckets = _bucket_by_distance(recs, dist_edges)
        xs = [(b["lo"] + b["hi"]) / 2.0 for b in buckets if b.get("n", 0) > 0]
        ys = [b["sigma_p50"] for b in buckets if b.get("n", 0) > 0]
        if xs:
            ax.plot(xs, ys, "-o", lw=1.0, ms=3, alpha=0.7,
                     label=f"{r['density'].name}/{r['policy'].name}")
    ax.set_xlabel("distance to detector centroid (m)")
    ax.set_ylabel("σ_event p50 (m), mode (b)")
    ax.set_yscale("log")
    ax.set_title("σ_event vs event-cluster distance (mode b)")
    ax.legend(fontsize=6, loc="upper left", ncol=2)
    ax.grid(alpha=0.3)

    # Panel (1,0): recon_error vs distance, mode (b)
    ax = axes[1, 0]
    for r in results:
        recs = r["recons_b"]
        buckets = _bucket_by_distance(recs, dist_edges)
        xs = [(b["lo"] + b["hi"]) / 2.0 for b in buckets if b.get("n", 0) > 0]
        ys = [b["err_p50"] for b in buckets if b.get("n", 0) > 0]
        if xs:
            ax.plot(xs, ys, "-o", lw=1.0, ms=3, alpha=0.7,
                     label=f"{r['density'].name}/{r['policy'].name}")
    ax.set_xlabel("distance to detector centroid (m)")
    ax.set_ylabel("recon error p50 (m), mode (b)")
    ax.set_yscale("log")
    ax.set_title("recon error vs event-cluster distance (mode b)")
    ax.legend(fontsize=6, loc="upper left", ncol=2)
    ax.grid(alpha=0.3)

    # Panel (1,1): time-to-detect vs distance, mode (b)
    ax = axes[1, 1]
    for r in results:
        recs = r["recons_b"]
        # Bucket finite time-to-detect by distance.
        finite = [r0 for r0 in recs
                   if np.isfinite(r0.error_m)
                   and np.isfinite(r0.dist_to_detector_centroid_m)
                   and np.isfinite(r0.time_to_detect_sec)]
        xs_per_bucket = []
        ys_per_bucket = []
        for lo, hi in zip(dist_edges[:-1], dist_edges[1:]):
            bucket = [r0 for r0 in finite
                       if lo <= r0.dist_to_detector_centroid_m < hi]
            if not bucket:
                continue
            xs_per_bucket.append((lo + hi) / 2.0)
            ys_per_bucket.append(np.median([r0.time_to_detect_sec / 60.0
                                              for r0 in bucket]))
        if xs_per_bucket:
            ax.plot(xs_per_bucket, ys_per_bucket, "-o", lw=1.0, ms=3,
                     alpha=0.7,
                     label=f"{r['density'].name}/{r['policy'].name}")
    ax.set_xlabel("distance to detector centroid (m)")
    ax.set_ylabel("time-to-detect p50 (min), mode (b)")
    ax.set_title("Time-to-detect vs event-cluster distance (mode b)")
    ax.legend(fontsize=6, loc="upper left", ncol=2)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    print(f"  saved {out_path}", flush=True)
    import matplotlib.pyplot as plt2
    plt2.close(fig)


# --- Per-run output: npz packing + README ---

def _build_npz_dict(results: list[dict]) -> dict:
    """Pack one sweep's worth of results into a flat key→ndarray dict for
    `np.savez`. Layout:

      Per (density, policy) cell:
        # Per-event mode-a/b reconstruction outputs.
        {p}__{mode}__error_m, sigma_m (E,)
        {p}__{mode}__n_detectors, dist_centroid_m (E,)
        {p}__{mode}__recon_lat, recon_lon, recon_t_sec (E,)
        {p}__{mode}__sigma_post_3x3 (E, 3, 3) — NaN on LSQ failure
        {p}__{mode}__detector_ids (E, max_n_dets) — int, -1 padded
        {p}__b__ttd_sec (E,)
        # Per-event truth + categorical src.
        {p}__event_truth_lats, event_truth_lons, event_t_secs (E,)
        {p}__event_src_int (E,) + {p}__event_src_label_table (K,) <U64
        # Cell scalars.
        {p}__n_drifters, n_events (scalar int)
      Per drifter i in cell:
        {p}__drifter_{i}__truth_lats, truth_lons (T,)
        {p}__drifter_{i}__pf_mean_lats, pf_mean_lons (T,)
        {p}__drifter_{i}__pf_cov_m (T, 2, 2)
        {p}__drifter_{i}__lora_fix_mask (T,) bool
        {p}__drifter_{i}__depths (T,)
        {p}__drifter_{i}__t_sec (T,)
        {p}__drifter_{i}__smooth_means_local_m (T, 2)
        {p}__drifter_{i}__smooth_covs_m (T, 2, 2)
        {p}__drifter_{i}__smooth_ref_lat, smooth_ref_lon (scalar)
        {p}__drifter_{i}__station_keeping_per_tick (T,)
        {p}__drifter_{i}__pf_err_per_tick (T,)
        {p}__drifter_{i}__smooth_err_per_tick (T,)
        {p}__drifter_{i}__station_lat, station_lon (scalar)
        {p}__drifter_{i}__n_surfacings, n_lora_fix_ticks (scalar int)
        {p}__drifter_{i}__dt_sec, ctrl_mean_m (scalar)

    `recons_a` covers ALL events (placeholder for events with <3
    detectors), so per-event truth + categorical src are derived from
    `recons_a` for canonical event ordering. `recons_b` matches the
    same ordering 1:1 (also includes placeholders).
    """
    out: dict = {}
    # When ANY of the new per-cell axes (lora_sigma_m, control_cadence_sec,
    # run_hours) varies across the sweep, include them in the npz key
    # prefix so cells with the same (density, policy) but different
    # axis values don't overwrite each other. When axes are constant
    # across the run, fall back to the legacy `density__policy` prefix
    # for analyzer compatibility.
    sigmas_seen = {r.get("lora_sigma_m") for r in results}
    cadences_seen = {r.get("control_cadence_sec") for r in results}
    run_hours_seen = {r.get("run_hours") for r in results}
    sigma_varies = len(sigmas_seen) > 1
    cadence_varies = len(cadences_seen) > 1
    run_hours_varies = len(run_hours_seen) > 1

    def _cell_prefix(r: dict) -> str:
        p = f"{r['density'].name}__{r['policy'].name}"
        if sigma_varies:
            sm = r.get("lora_sigma_m")
            if sm is not None:
                p += f"__s{sm:g}"
        if cadence_varies:
            cad = r.get("control_cadence_sec")
            if cad is not None:
                p += f"__c{cad:g}"
        if run_hours_varies:
            rh = r.get("run_hours")
            if rh is not None:
                p += f"__h{rh}"
        return p

    # Stash the axis-varies flags + the discovered axis values as
    # top-level scalars so post-hoc analyzers know how to slice the npz.
    out["__axes_sigma_varies"] = np.bool_(sigma_varies)
    out["__axes_cadence_varies"] = np.bool_(cadence_varies)
    out["__axes_run_hours_varies"] = np.bool_(run_hours_varies)
    if sigma_varies:
        out["__axes_sigmas"] = np.array(
            sorted(s for s in sigmas_seen if s is not None), dtype=float)
    if cadence_varies:
        out["__axes_cadences"] = np.array(
            sorted(c for c in cadences_seen if c is not None), dtype=float)
    if run_hours_varies:
        out["__axes_run_hours"] = np.array(
            sorted(h for h in run_hours_seen if h is not None), dtype=int)

    for r in results:
        prefix = _cell_prefix(r)
        for mode_letter, mode_key in [("a", "recons_a"),
                                        ("b", "recons_b")]:
            recs = r[mode_key]
            mp = f"{prefix}__{mode_letter}"
            out[f"{mp}__error_m"] = np.array(
                [rc.error_m for rc in recs], dtype=float)
            out[f"{mp}__sigma_m"] = np.array(
                [rc.sigma_m for rc in recs], dtype=float)
            out[f"{mp}__n_detectors"] = np.array(
                [rc.n_detectors for rc in recs], dtype=int)
            out[f"{mp}__dist_centroid_m"] = np.array(
                [rc.dist_to_detector_centroid_m for rc in recs],
                dtype=float)
            out[f"{mp}__recon_lat"] = np.array(
                [rc.recon_lat for rc in recs], dtype=float)
            out[f"{mp}__recon_lon"] = np.array(
                [rc.recon_lon for rc in recs], dtype=float)
            out[f"{mp}__recon_t_sec"] = np.array(
                [rc.recon_t_sec for rc in recs], dtype=float)
            if recs:
                out[f"{mp}__sigma_post_3x3"] = np.stack(
                    [np.asarray(rc.sigma_post_3x3, dtype=float)
                     for rc in recs], axis=0)
            else:
                out[f"{mp}__sigma_post_3x3"] = np.full(
                    (0, 3, 3), np.nan, dtype=float)
            max_n_dets = max((len(rc.detector_ids) for rc in recs),
                              default=0)
            ids_pad = np.full((len(recs), max(max_n_dets, 1)),
                                -1, dtype=int)
            sig_pad = np.full((len(recs), max(max_n_dets, 1)),
                                np.nan, dtype=float)
            for i, rc in enumerate(recs):
                if rc.detector_ids:
                    ids_pad[i, :len(rc.detector_ids)] = rc.detector_ids
                if rc.detector_sigma_pos_used:
                    sig_pad[i, :len(rc.detector_sigma_pos_used)] = (
                        rc.detector_sigma_pos_used
                    )
            out[f"{mp}__detector_ids"] = ids_pad
            out[f"{mp}__detector_sigma_pos_used"] = sig_pad
        out[f"{prefix}__b__ttd_sec"] = np.array(
            [rc.time_to_detect_sec for rc in r["recons_b"]],
            dtype=float)

        # Per-event truth + categorical src (derived from recons_a).
        recs_a = r["recons_a"]
        out[f"{prefix}__event_truth_lats"] = np.array(
            [rc.truth_lat for rc in recs_a], dtype=float)
        out[f"{prefix}__event_truth_lons"] = np.array(
            [rc.truth_lon for rc in recs_a], dtype=float)
        out[f"{prefix}__event_t_secs"] = np.array(
            [rc.truth_t_sec for rc in recs_a], dtype=float)
        src_strings = [rc.src for rc in recs_a]
        src_unique = sorted(set(src_strings))
        src_to_int = {s: i for i, s in enumerate(src_unique)}
        out[f"{prefix}__event_src_int"] = np.array(
            [src_to_int[s] for s in src_strings], dtype=int)
        out[f"{prefix}__event_src_label_table"] = np.array(
            src_unique, dtype="<U64")
        out[f"{prefix}__n_events"] = np.int64(len(recs_a))
        out[f"{prefix}__n_drifters"] = np.int64(len(r["drifters"]))

        # Per-drifter trajectories + diagnostics.
        for di, drifter in enumerate(r["drifters"]):
            dp = f"{prefix}__drifter_{di}"
            out[f"{dp}__truth_lats"] = np.asarray(
                drifter["truth_lats"], dtype=float)
            out[f"{dp}__truth_lons"] = np.asarray(
                drifter["truth_lons"], dtype=float)
            out[f"{dp}__pf_mean_lats"] = np.asarray(
                drifter["pf_mean_lats"], dtype=float)
            out[f"{dp}__pf_mean_lons"] = np.asarray(
                drifter["pf_mean_lons"], dtype=float)
            out[f"{dp}__pf_cov_m"] = np.asarray(
                drifter["pf_cov_m"], dtype=float)
            out[f"{dp}__lora_fix_mask"] = np.asarray(
                drifter["lora_fix_mask"], dtype=bool)
            out[f"{dp}__depths"] = np.asarray(
                drifter["depths"], dtype=float)
            out[f"{dp}__t_sec"] = np.asarray(
                drifter["t_sec"], dtype=float)
            out[f"{dp}__smooth_means_local_m"] = np.asarray(
                drifter["smooth_means_local_m"], dtype=float)
            out[f"{dp}__smooth_covs_m"] = np.asarray(
                drifter["smooth_covs_m"], dtype=float)
            out[f"{dp}__smooth_ref_lat"] = np.float64(
                drifter["smooth_ref_lat"])
            out[f"{dp}__smooth_ref_lon"] = np.float64(
                drifter["smooth_ref_lon"])
            out[f"{dp}__station_keeping_per_tick"] = np.asarray(
                drifter["station_keeping_per_tick"], dtype=float)
            out[f"{dp}__pf_err_per_tick"] = np.asarray(
                drifter["pf_err_per_tick"], dtype=float)
            out[f"{dp}__smooth_err_per_tick"] = np.asarray(
                drifter["smooth_err_per_tick"], dtype=float)
            out[f"{dp}__station_lat"] = np.float64(drifter["station_lat"])
            out[f"{dp}__station_lon"] = np.float64(drifter["station_lon"])
            out[f"{dp}__n_surfacings"] = np.int64(drifter["n_surfacings"])
            out[f"{dp}__n_lora_fix_ticks"] = np.int64(
                drifter["n_lora_fix_ticks"])
            out[f"{dp}__dt_sec"] = np.float64(drifter["dt_sec"])
            out[f"{dp}__ctrl_mean_m"] = np.float64(drifter["ctrl_mean_m"])
            # Campaign-mode-only fields. Single-mode runs omit these
            # keys so the analyzer can detect campaign vs. single by
            # presence (no sentinel value).
            if "cycle_idx_per_tick" in drifter:
                out[f"{dp}__cycle_idx_per_tick"] = np.asarray(
                    drifter["cycle_idx_per_tick"], dtype=np.int32)
            if "cycle_boundaries_sec" in drifter:
                out[f"{dp}__cycle_boundaries_sec"] = np.asarray(
                    drifter["cycle_boundaries_sec"], dtype=float)
            if "n_cycles" in drifter:
                out[f"{dp}__n_cycles"] = np.int64(drifter["n_cycles"])
    return out


def _git_status() -> dict[str, str]:
    """Best-effort git commit hash + dirty flag. Empty strings on failure."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        commit = ""
    try:
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        dirty_flag = "dirty" if dirty else "clean"
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        dirty_flag = ""
    return {"commit": commit, "dirty": dirty_flag}


def _write_run_readme(
    run_dir: str, run_id: str, t_start: float, t_end: float,
    results: list[dict],
) -> None:
    """Write a minimal README.md inside the run subdir capturing config,
    git state, host info, wall time. Lets a future reader figure out
    what code produced the npz."""
    git_info = _git_status()
    config = {
        "run_id": run_id,
        "run_hours": RUN_HOURS,
        "n_procs": N_PROCS,
        "point_event_rate_per_h": POINT_EVENT_RATE_PER_H,
        "seed_base": SEED_BASE,
        "campaign_mode": CAMPAIGN_MODE,
        "redeploy_interval_h": (REDEPLOY_INTERVAL_H
                                  if CAMPAIGN_MODE == "redeploy"
                                  else None),
        "densities": [
            {"name": d.name, "label": d.label,
             "n_stations": len(d.stations)}
            for d in DENSITY_CONFIGS
        ],
        "policies": [
            {"name": p.name, "label": p.label} for p in POLICIES
        ],
        "n_cells": len(results),
        "n_events_per_cell": [
            {"density": r["density"].name,
             "policy": r["policy"].name,
             "n_events": r["n_events"],
             "n_point_events": r["n_point_events"],
             "n_boat_events": r["n_boat_events"]}
            for r in results
        ],
    }
    lines = [
        f"# Fleet sweep run `{run_id}`",
        "",
        f"Wall: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t_start))}"
        f" → {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t_end))}"
        f"  ({t_end - t_start:.0f}s = {(t_end - t_start)/60:.1f} min)",
        "",
        f"Host: {platform.node()}  ({platform.platform()})",
        f"Python: {platform.python_version()}",
        f"Git commit: `{git_info.get('commit', '?') or '?'}` "
        f"({git_info.get('dirty', '?') or '?'})",
        "",
        "## Config",
        "",
        "```json",
        json.dumps(config, indent=2),
        "```",
        "",
        "## Files",
        "",
        "- `raw/results.npz` — full per-event + per-drifter arrays (the v2 analyzer reads this).",
        "- `sweep_summary.txt`, `sweep_summary.png` — quick-look v1-style summary (sanity-check, not the v2 deliverable).",
        "- `sweep_property_breakdown.png` — quick-look bucket plots.",
        "",
        "## Known gaps (sim-fidelity caveats inherited at this run)",
        "",
        "- Idealized LoRa fix model: σ_range = 20 m i.i.d. Gaussian to 3 always-in-range anchors at fixed offsets from each station target. Real ranging is heavier-tailed, range-gated, geometry-dependent, multipath-affected.",
        "- No peer-to-peer LoRa exchange between drifters; no fleet-shared belief-state meta-filter.",
        "- Smoother Q5/Q6 deferred (P3 review) — windowed RTS handles LoRa-fix ticks asymmetrically and linearly interpolates cov entries between ticks.",
        "- LSQ uses an isotropic scalar σ_pos per detector; per-drifter Σ_pos at event time is anisotropic (likely the leading mechanism behind the calibration deviation we see).",
        "- Per-ping framing: each boat ping is reconstructed independently. Track-level LSQ with kinematic prior would tighten the deployment-relevant per-track σ; deferred.",
        "- Binary detection model: drifter detects iff distance < DETECT_RANGE_M. No SNR/propagation/multipath model. No biological/shipping distractors.",
    ]
    readme_path = os.path.join(run_dir, "README.md")
    with open(readme_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  saved {readme_path}", flush=True)


# --- Driver ---

def main() -> None:
    # Optional env-var filters for smoke / partial runs. Comma-separated
    # density and policy names; default = all configs.
    only_d_env = os.environ.get("FLEET_SWEEP_ONLY_DENSITIES", "").strip()
    only_p_env = os.environ.get("FLEET_SWEEP_ONLY_POLICIES", "").strip()
    if only_d_env:
        keep_d = {s.strip() for s in only_d_env.split(",") if s.strip()}
        densities_run = tuple(d for d in DENSITY_CONFIGS if d.name in keep_d)
    else:
        densities_run = DENSITY_CONFIGS
    if only_p_env:
        keep_p = {s.strip() for s in only_p_env.split(",") if s.strip()}
        policies_run = tuple(p for p in POLICIES if p.name in keep_p)
    else:
        policies_run = POLICIES
    if not densities_run or not policies_run:
        print(
            "ERROR: filters left zero configs to run "
            f"(densities={[d.name for d in densities_run]}, "
            f"policies={[p.name for p in policies_run]})", flush=True,
        )
        sys.exit(1)

    campaign_str = (
        f"campaign=redeploy@{REDEPLOY_INTERVAL_H:g}h"
        if CAMPAIGN_MODE == "redeploy" else "campaign=single"
    )
    n_cells = (len(densities_run) * len(policies_run) * len(LORA_SIGMAS_M)
                * len(CONTROL_CADENCES_S) * len(RUN_HOURS_LIST))
    print(f"=== fleet sweep ({n_cells} cells: "
          f"{len(densities_run)} densities × {len(policies_run)} policies × "
          f"{len(LORA_SIGMAS_M)} σ_m × {len(CONTROL_CADENCES_S)} cadences × "
          f"{len(RUN_HOURS_LIST)} mission_h, "
          f"event_rate={POINT_EVENT_RATE_PER_H}/h, {campaign_str}) ===",
          flush=True)
    print(f"  densities:    {[d.name for d in densities_run]}", flush=True)
    print(f"  policies:     {[p.name for p in policies_run]}", flush=True)
    print(f"  σ_m (m):      {list(LORA_SIGMAS_M)}", flush=True)
    print(f"  cadence (s):  {list(CONTROL_CADENCES_S)}", flush=True)
    print(f"  mission (h):  {list(RUN_HOURS_LIST)}", flush=True)

    # We need enough workers to run the largest density's drifters in
    # parallel. For N_drifters > N_PROCS, we wait — but the sweep won't
    # overload memory.
    max_n_drifters = max(len(d.stations) for d in densities_run)
    n_workers = min(N_PROCS, max_n_drifters)
    print(f"  workers: {n_workers} (max drifters per config = "
          f"{max_n_drifters})", flush=True)

    # Event-seed policy: by default, all densities running the same
    # policy use the SAME event_seed → cross-density comparisons are
    # apples-to-apples (identical events). Set
    # `FLEET_SWEEP_EVENT_SEED_MODE=per_config` to revert to legacy
    # behavior where each (density, policy) cell gets its own seed
    # (statistical independence at the cost of cross-density noise).
    event_seed_mode = os.environ.get(
        "FLEET_SWEEP_EVENT_SEED_MODE", "per_policy",
    )
    # Optional offset added to the policy index when computing the
    # per_policy seed. Lets a partial-policy run align its seeds with
    # a previous full-policy run by skipping leading indices. E.g., a
    # follow-up run with FLEET_SWEEP_ONLY_POLICIES=fixed_12h,post_event
    # and FLEET_SWEEP_POLICY_INDEX_OFFSET=1 produces seeds 5100 and
    # 5200 — matching the original run's [fixed_6h, fixed_12h,
    # post_event] indexing where fixed_12h was at index 1.
    policy_index_offset = int(
        os.environ.get("FLEET_SWEEP_POLICY_INDEX_OFFSET", "0")
    )
    print(f"  event-seed mode: {event_seed_mode}"
          f"{f' (policy_index_offset={policy_index_offset})' if policy_index_offset else ''}",
          flush=True)

    t_start = time.time()
    results: list[dict] = []
    with Pool(processes=n_workers, initializer=fs._init_worker) as pool:
        # The first call waits for all workers to finish init (~4 min).
        config_idx = 0
        for _di, density in enumerate(densities_run):
            for pi, policy in enumerate(policies_run):
                for sigma_m in LORA_SIGMAS_M:
                    for cadence in CONTROL_CADENCES_S:
                        for run_h in RUN_HOURS_LIST:
                            if event_seed_mode == "per_policy":
                                event_seed = SEED_BASE + (
                                    pi + policy_index_offset) * 100
                            elif event_seed_mode == "per_config":
                                event_seed = SEED_BASE + config_idx * 100
                            else:
                                raise ValueError(
                                    f"unknown FLEET_SWEEP_EVENT_SEED_MODE: "
                                    f"{event_seed_mode!r}"
                                )
                            r = _run_one_config(
                                density, policy, pool, event_seed,
                                lora_sigma_m=sigma_m,
                                control_cadence_sec=cadence,
                                run_hours=run_h,
                            )
                            # Tag the per-cell axes onto the result so
                            # downstream analyzers can stratify by them.
                            r["lora_sigma_m"] = float(sigma_m)
                            r["control_cadence_sec"] = float(cadence)
                            r["run_hours"] = int(run_h)
                            results.append(r)
                            config_idx += 1

    print(f"\n=== sweep done (total wall {time.time() - t_start:.0f}s) ===",
          flush=True)

    _print_summary(results)

    # v2 output convention — per-run subdir under figures/sweep_runs/.
    # The legacy figures/sweep_results.npz from v1 stays untouched as a
    # historical record; new runs accumulate under sweep_runs/ keyed by
    # timestamp.
    run_id = (os.environ.get("FLEET_SWEEP_RUN_ID")
              or (time.strftime("%Y%m%d-%H%M%S") + "_v2_baseline"))
    fig_dir = os.path.join(os.path.dirname(__file__), "figures")
    run_dir = os.path.join(fig_dir, "sweep_runs", run_id)
    raw_dir = os.path.join(run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    print(f"  run_dir: {run_dir}", flush=True)

    # Persist full raw arrays FIRST — npz before charts so if a chart
    # call fails (e.g., log-scale on all-NaN data) we don't lose the
    # whole run. The v2 analyzer reads everything from this single npz.
    summary_npz = os.path.join(raw_dir, "results.npz")
    np.savez(summary_npz, **_build_npz_dict(results))
    print(f"  saved {summary_npz}", flush=True)

    # Run-metadata README for reproducibility.
    _write_run_readme(run_dir=run_dir, run_id=run_id,
                       t_start=t_start, t_end=time.time(),
                       results=results)

    # Quick-look v1-style summary in the run subdir. Wrapped in try
    # so a chart crash doesn't lose the npz output above. The v2
    # analyzer produces the proper artifacts anyway.
    try:
        _save_summary_table(
            results, os.path.join(run_dir, "sweep_summary.txt"),
        )
        _build_summary_chart(
            results, os.path.join(run_dir, "sweep_summary.png"),
        )
        _build_property_breakdown_chart(
            results,
            os.path.join(run_dir, "sweep_property_breakdown.png"),
        )
    except Exception as e:   # noqa: BLE001 (intentionally broad)
        print(f"  WARN: v1 quick-look chart failed: {e!r} — npz saved, "
              f"v2 analyzer is the authoritative output", flush=True)


if __name__ == "__main__":
    main()
