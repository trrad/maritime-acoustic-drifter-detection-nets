"""Drop-point optimizer — first pass.

Greedy placement of N drifters within a patrol-area bbox plus a drift
buffer, optimizing for p95 σ_event coverage of the patrol area over a
72-hour mission. Geometric only — no LSQ on real events, just the σ_post
the LSQ would produce given each drifter's predicted trajectory and a
saw-tooth σ_pos schedule from a stated surfacing cadence.

Trajectory ladder (passed via `--trajectory`):
  - static     — drifter stays at drop point. Optimistic ceiling
                 (perfect controller).
  - ballistic  — drifter drifts with NEMO currents from drop, no
                 control. Pessimistic floor.
  - interp(α)  — linear blend: lat(t) = (1-α)·static + α·ballistic.
                 α=0 → static; α=1 → ballistic. Default α=0.5.

σ_pos model (saw-tooth from a stated fix cadence):
  σ_pos(t) = σ_at_fix + σ_growth_rate · (t mod fix_cadence)
  σ_at_fix = sigma_LoRa × pdop_at_fix (legacy default ~30 m assumed
    σ_LoRa=20 m × pdop≈1.5 from a co-located 3-anchor geometry; with
    fixed-anchor v1 the realistic σ_LoRa is ~100 m and pdop varies
    across the bbox — pass `--sigma-at-fix` to match the cell's
    geometry, or accept the default as an optimistic lower bound).
  σ_growth_rate from `process_noise.sigma_pos_growth_rate_per_axis`
  at depth ~10 m (drifter typical operating depth).

Metric: p95 σ_event over (eval_grid × time_bins) within the patrol
polygon. Eval grid points with <3 detecting drifters within
DETECT_RANGE_M get NaN; replaced with a large penalty value so the
p95 properly penalizes uncovered area.

Output:
  - drop_points.json — list of (lat, lon) per drifter + run config + score
  - coverage_diag.png — patrol-area σ_event heatmap with drop points overlaid
  - python_snippet.txt — paste-ready DensityConfig for _fleet_sweep_v0.py

This is the first pass; full-sim verification of the resulting placement
is done by re-running the simulator with these drop points.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field

import numpy as np   # type: ignore[import-not-found]


# Acoustic / geometry constants (must match _fleet_sim_v0).
from truth_field import EARTH_R_M, distance_m as _haversine_m  # type: ignore[import-not-found]

C_WATER_MS = 1500.0
SIGMA_TOA_S = 0.005
DETECT_RANGE_M = 5000.0


# ---- Config dataclasses ----

@dataclass
class PatrolArea:
    """Patrol polygon (currently bbox only). Drifters can be dropped
    outside this and drift in; coverage is evaluated only over points
    INSIDE the patrol polygon."""
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def width_m(self) -> float:
        cos_lat = float(np.cos(np.deg2rad(0.5 * (self.lat_min + self.lat_max))))
        return (self.lon_max - self.lon_min) * EARTH_R_M * cos_lat

    def height_m(self) -> float:
        return (self.lat_max - self.lat_min) * EARTH_R_M

    def expanded(self, buffer_m: float) -> "PatrolArea":
        cos_lat = float(np.cos(np.deg2rad(0.5 * (self.lat_min + self.lat_max))))
        d_lat = buffer_m / EARTH_R_M
        d_lon = buffer_m / (EARTH_R_M * cos_lat)
        return PatrolArea(
            lat_min=self.lat_min - d_lat, lat_max=self.lat_max + d_lat,
            lon_min=self.lon_min - d_lon, lon_max=self.lon_max + d_lon,
        )


@dataclass
class OptConfig:
    n_drifters: int = 16
    mission_hours: float = 72.0
    # How far outside the patrol bbox we're allowed to drop drifters.
    # They can drift in via currents.
    drop_buffer_m: float = 5_000.0
    # Spacing of the candidate grid for greedy placement.
    candidate_spacing_m: float = 1_000.0
    # Spacing of the patrol-area eval grid for coverage scoring.
    eval_spacing_m: float = 500.0
    # Time bins for coverage evaluation. With 72h × 6h bins → 12 bins.
    time_bin_h: float = 6.0
    # Refinement passes after greedy. Each tries 8-neighbor perturbations.
    refinement_passes: int = 3
    refinement_radius_m: float = 1_500.0

    # Trajectory + σ_pos parameters.
    # "static"     — drifter pinned at drop point (perfect controller).
    # "ballistic"  — Euler-integrate truth currents from drop, no control.
    # "interp"     — linear blend of static and ballistic at `interp_alpha`.
    # "empirical"  — look up mean trajectory at nearest grid point of a
    #                pre-built mobility map (the operationally honest
    #                version — full PF/bias/MPC stack already ran the
    #                drifter forward).
    trajectory_model: str = "interp"
    interp_alpha: float = 0.5            # weight for ballistic in interp model
    drifter_depth_m: float = 10.0         # depth at which we sample currents
    fix_cadence_h: float = 6.0            # surfacing-policy cadence (tunable)
    sigma_at_fix_m: float = 30.0          # σ_pos right after a fix
    sigma_pos_growth_rate_m_per_h: float = 130.0
    # NaN-coverage penalty for the p95 metric — a value huge enough that
    # any uncovered (eval, time) bin dominates over near-bound coverage.
    nan_penalty_sigma_event_m: float = 50_000.0


# ---- Mobility-map loader ----

def _load_mobility_map(path: str) -> dict:
    """Load a pre-built drifter mobility map produced by
    `_drifter_mobility_map.py`. Returns a dict-of-arrays with the
    fields the empirical trajectory predictor needs.
    """
    raw = np.load(path, allow_pickle=False)
    out = {
        "grid_lats": raw["grid_lats"],
        "grid_lons": raw["grid_lons"],
        "mean_truth_lats": raw["mean_truth_lats"],
        "mean_truth_lons": raw["mean_truth_lons"],
        "t_sec": raw["t_sec"],
        "policy": str(raw["policy"]),
        "n_drops": int(raw["n_drops"]),
        "n_seeds": int(raw["n_seeds"]),
        "run_hours": int(raw["run_hours"]),
        # Derived stats kept for diagnostic / station-keeping filtering.
        "sk_p50_per_seed": raw["sk_p50_per_seed"],
        "sk_p95_per_seed": raw["sk_p95_per_seed"],
    }
    return out


# ---- Truth field loader (NEMO mean currents only — no noise field) ----

def _load_nemo_field():
    """Load NEMO mean currents over the SoG bbox. Heavy first-call cost
    (~3-4 min); cached by `salishseacast_cache`."""
    from salishseacast_cache import (
        bbox_from_latlon, fetch_bbox_months, bbox_latlon_arrays,
    )
    from truth_field import build_truth_field

    LAT_MIN, LAT_MAX = 49.15, 49.45
    LON_MIN, LON_MAX = -123.95, -123.50
    DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]

    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, ["2023-04"], verbose=False,
                            include_tracers=False)
    lats_grid, lons_grid, _bathy_grid = bbox_latlon_arrays(bbox)
    return build_truth_field(ds, lats_grid, lons_grid,
                              DEFAULT_DEPTH_SET)


# ---- Trajectory predictors ----

def predict_traj_static(
    drop_lat: float, drop_lon: float,
    t_h: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.full_like(t_h, drop_lat),
        np.full_like(t_h, drop_lon),
    )


def predict_traj_ballistic(
    drop_lat: float, drop_lon: float,
    t_h: np.ndarray, depth_m: float, nemo,
) -> tuple[np.ndarray, np.ndarray]:
    """Forward Euler integration of NEMO currents from drop point.

    No controller, no noise — just the deterministic mean current field.
    Drifter starts at (drop_lat, drop_lon) at t=0 and is advected.
    Returns (lat[t], lon[t]) at each tick in t_h.
    """
    n = t_h.size
    lats = np.zeros(n); lons = np.zeros(n)
    lats[0] = drop_lat; lons[0] = drop_lon
    for i in range(1, n):
        dt_sec = (t_h[i] - t_h[i - 1]) * 3600.0
        u, v = nemo.sample(
            float(lats[i - 1]), float(lons[i - 1]),
            depth_m, float(t_h[i - 1] * 3600.0),
        )
        if not (np.isfinite(u) and np.isfinite(v)):
            lats[i] = lats[i - 1]; lons[i] = lons[i - 1]
            continue
        cos_lat = float(np.cos(np.deg2rad(lats[i - 1])))
        d_lat = (v * dt_sec) / EARTH_R_M
        d_lon = (u * dt_sec) / (EARTH_R_M * cos_lat)
        lats[i] = lats[i - 1] + d_lat
        lons[i] = lons[i - 1] + d_lon
    return lats, lons


def predict_traj_interp(
    static_lats: np.ndarray, static_lons: np.ndarray,
    ballistic_lats: np.ndarray, ballistic_lons: np.ndarray,
    alpha: float,
) -> tuple[np.ndarray, np.ndarray]:
    a = float(np.clip(alpha, 0.0, 1.0))
    return (
        (1.0 - a) * static_lats + a * ballistic_lats,
        (1.0 - a) * static_lons + a * ballistic_lons,
    )


def predict_traj_empirical(
    drop_lat: float, drop_lon: float, t_h: np.ndarray,
    mobility_map: dict,
) -> tuple[np.ndarray, np.ndarray]:
    """Look up nearest grid point in pre-built mobility map and return
    its mean-across-seeds trajectory at the requested time bins.

    Mobility map's tick spacing (typically 600s per tick over 72h)
    differs from the optimizer's bin spacing (6h). We linearly
    interpolate the empirical mean trajectory to t_h.
    """
    glats = mobility_map["grid_lats"]
    glons = mobility_map["grid_lons"]
    cos_lat = float(np.cos(np.deg2rad(drop_lat)))
    dists = np.hypot(
        (glats - drop_lat) * EARTH_R_M,
        (glons - drop_lon) * EARTH_R_M * cos_lat,
    )
    nearest = int(np.argmin(dists))
    mean_lats = mobility_map["mean_truth_lats"][nearest]   # (T_map,)
    mean_lons = mobility_map["mean_truth_lons"][nearest]
    map_t_sec = mobility_map["t_sec"]                       # (T_map,)
    t_sec_q = t_h * 3600.0
    lats_out = np.interp(t_sec_q, map_t_sec, mean_lats)
    lons_out = np.interp(t_sec_q, map_t_sec, mean_lons)
    return lats_out, lons_out


def predict_drifter_trajectory(
    drop_lat: float, drop_lon: float, cfg: OptConfig, nemo,
    t_h: np.ndarray, mobility_map: dict | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    if cfg.trajectory_model == "static":
        return predict_traj_static(drop_lat, drop_lon, t_h)
    if cfg.trajectory_model == "ballistic":
        return predict_traj_ballistic(
            drop_lat, drop_lon, t_h, cfg.drifter_depth_m, nemo,
        )
    if cfg.trajectory_model == "interp":
        s_lats, s_lons = predict_traj_static(drop_lat, drop_lon, t_h)
        b_lats, b_lons = predict_traj_ballistic(
            drop_lat, drop_lon, t_h, cfg.drifter_depth_m, nemo,
        )
        return predict_traj_interp(
            s_lats, s_lons, b_lats, b_lons, cfg.interp_alpha,
        )
    if cfg.trajectory_model == "empirical":
        if mobility_map is None:
            raise ValueError(
                "empirical trajectory model requires --mobility-map"
            )
        return predict_traj_empirical(
            drop_lat, drop_lon, t_h, mobility_map,
        )
    raise ValueError(
        f"unknown trajectory model {cfg.trajectory_model!r}")


# ---- σ_pos schedule ----

def sigma_pos_schedule(
    t_h: np.ndarray, cfg: OptConfig,
) -> np.ndarray:
    """Saw-tooth σ_pos vs time. Resets to `sigma_at_fix_m` at every
    `fix_cadence_h` interval, grows linearly between fixes.

    Real σ_pos is non-linear in time (OU process), but linear is a
    reasonable approximation over the 6-h-scale fix cycles we're
    operating in here. The growth rate is calibrated against the
    project's `process_noise` model at typical drifter depth.
    """
    time_in_cycle_h = t_h % cfg.fix_cadence_h
    return (
        cfg.sigma_at_fix_m
        + cfg.sigma_pos_growth_rate_m_per_h * time_in_cycle_h
    )


# ---- Coverage evaluator ----

def _eval_grid_in_polygon(
    patrol: PatrolArea, eval_spacing_m: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build an evaluation lat/lon grid covering the patrol bbox at
    the given spacing. Returns (eval_lats_1d, eval_lons_1d, mask_in_polygon).

    For bbox-only patrol areas the mask is all True. Future polygon
    support fills in the mask.
    """
    cos_lat = float(np.cos(np.deg2rad(
        0.5 * (patrol.lat_min + patrol.lat_max),
    )))
    d_lat = eval_spacing_m / EARTH_R_M
    d_lon = eval_spacing_m / (EARTH_R_M * cos_lat)
    n_lat = int(np.ceil((patrol.lat_max - patrol.lat_min) / d_lat))
    n_lon = int(np.ceil((patrol.lon_max - patrol.lon_min) / d_lon))
    lats = np.linspace(patrol.lat_min, patrol.lat_max, max(n_lat, 2))
    lons = np.linspace(patrol.lon_min, patrol.lon_max, max(n_lon, 2))
    glat, glon = np.meshgrid(lats, lons, indexing="ij")
    mask = np.ones_like(glat, dtype=bool)   # bbox: all in polygon
    return lats, lons, mask


def compute_sigma_event_grid_at_tick(
    drifter_lats_t: np.ndarray, drifter_lons_t: np.ndarray,
    sigma_pos_t: np.ndarray,
    glat: np.ndarray, glon: np.ndarray,
    nan_penalty: float = 50_000.0,
) -> np.ndarray:
    """At a single time, compute σ_event at every grid cell of (glat,
    glon). Inputs are per-drifter position + σ_pos at that time, shape
    (n_drifters,). Output shape = glat.shape.

    For cells with ≥3 detectors in range AND non-singular geometry,
    σ_event comes from the LSQ Σ_post construction.

    For cells with k<3 detectors in range, σ_event is filled with a
    GRADED penalty proportional to (4-k) × nan_penalty:
       k=0 → 4·penalty (fully uncovered)
       k=1 → 3·penalty (close to coverage but missing 2 drifters)
       k=2 → 2·penalty (one short of coverage)
    This gives the optimizer a gradient through the cold-start regime
    where no cells have 3-detector coverage yet — placements that
    increase the detector count anywhere are rewarded.

    Vectorized over the grid; scalar-loop only over drifters.
    """
    n_d = drifter_lats_t.size
    n_lat, n_lon = glat.shape
    cos_lat = float(np.cos(np.deg2rad(np.mean(glat))))
    sigma_eff_sq = (SIGMA_TOA_S ** 2
                     + (sigma_pos_t / C_WATER_MS) ** 2)
    inv_var = 1.0 / np.maximum(sigma_eff_sq, 1e-30)

    unit_x = np.zeros((n_d, n_lat, n_lon))
    unit_y = np.zeros((n_d, n_lat, n_lon))
    in_range = np.zeros((n_d, n_lat, n_lon), dtype=bool)
    for d in range(n_d):
        dlat = float(drifter_lats_t[d])
        dlon = float(drifter_lons_t[d])
        dy = (glat - dlat) * EARTH_R_M
        dx = (glon - dlon) * EARTH_R_M * cos_lat
        dist_sq = dx * dx + dy * dy
        in_range[d] = (dist_sq <= DETECT_RANGE_M ** 2)
        dist = np.sqrt(np.maximum(dist_sq, 1.0))
        unit_x[d] = np.where(dist > 1e-3, dx / dist, 0.0)
        unit_y[d] = np.where(dist > 1e-3, dy / dist, 0.0)

    w = inv_var[:, None, None] * in_range.astype(float)
    JTWJ_xx = (unit_x * unit_x * w).sum(axis=0) / C_WATER_MS ** 2
    JTWJ_yy = (unit_y * unit_y * w).sum(axis=0) / C_WATER_MS ** 2
    JTWJ_xy = (unit_x * unit_y * w).sum(axis=0) / C_WATER_MS ** 2
    JTWJ_xt = -(unit_x * w).sum(axis=0) / C_WATER_MS
    JTWJ_yt = -(unit_y * w).sum(axis=0) / C_WATER_MS
    JTWJ_tt = w.sum(axis=0)
    n_in_range = in_range.sum(axis=0)
    valid = (n_in_range >= 3)

    eye3 = np.eye(3)
    JTWJ = np.zeros((n_lat, n_lon, 3, 3))
    JTWJ[..., 0, 0] = JTWJ_xx
    JTWJ[..., 1, 1] = JTWJ_yy
    JTWJ[..., 2, 2] = JTWJ_tt
    JTWJ[..., 0, 1] = JTWJ_xy
    JTWJ[..., 1, 0] = JTWJ_xy
    JTWJ[..., 0, 2] = JTWJ_xt
    JTWJ[..., 2, 0] = JTWJ_xt
    JTWJ[..., 1, 2] = JTWJ_yt
    JTWJ[..., 2, 1] = JTWJ_yt
    # Pre-filter geometrically degenerate cells (collinear drifters
    # → JᵀWJ singular) BEFORE the vectorized inv, since np.linalg.inv
    # fails the whole batch if any matrix is singular.
    det = np.linalg.det(JTWJ)
    valid = valid & (np.abs(det) > 1e-12)
    JTWJ[~valid] = eye3
    with np.errstate(invalid="ignore", divide="ignore"):
        Sigma = np.linalg.inv(JTWJ)
        s_event_sq = 0.5 * (Sigma[..., 0, 0] + Sigma[..., 1, 1])
        out = np.where(
            valid & (s_event_sq > 0),
            np.sqrt(np.maximum(s_event_sq, 0.0)),
            (4 - np.minimum(n_in_range, 3)) * nan_penalty,
        )
    return out


def compute_coverage_metric(
    drop_points: list[tuple[float, float]],
    cfg: OptConfig, patrol: PatrolArea, nemo,
    glat: np.ndarray, glon: np.ndarray, mask_in_polygon: np.ndarray,
    mobility_map: dict | None = None,
) -> tuple[float, float, np.ndarray]:
    """For a given drop-point set, compute time-integrated σ_event over
    the patrol grid. Returns (mean_score, p95, mean_map).

    Mean is the optimization driver — it has continuous gradient through
    the discrete coverage transitions (k=0 → 1 → 2 → 3+) where p95
    quantizes too sharply to be useful. p95 is reported as a
    secondary metric.

    NaN-coverage cells get a graded penalty (per-tick helper) — k<3
    cells contribute (4-k)·penalty so placements that increase cell
    detector counts get a proper score gradient.
    """
    n_d = len(drop_points)
    if n_d == 0:
        big = float(cfg.nan_penalty_sigma_event_m) * 4.0
        return big, big, np.full_like(glat, big)

    n_t = max(int(np.ceil(cfg.mission_hours / cfg.time_bin_h)), 1)
    # Use the bin midpoint for trajectory + σ_pos sampling.
    t_h = (np.arange(n_t) + 0.5) * cfg.time_bin_h

    # Predict trajectories per drifter.
    drifter_lats_t = np.zeros((n_d, n_t))
    drifter_lons_t = np.zeros((n_d, n_t))
    for d_idx, (drop_lat, drop_lon) in enumerate(drop_points):
        lats_d, lons_d = predict_drifter_trajectory(
            drop_lat, drop_lon, cfg, nemo, t_h,
            mobility_map=mobility_map,
        )
        drifter_lats_t[d_idx] = lats_d
        drifter_lons_t[d_idx] = lons_d

    # σ_pos schedule per drifter — currently identical for all (same
    # cadence, same growth rate). Per-drifter cadence variation could
    # be added later (e.g., post-event policies).
    sigma_pos_t_per_d = np.tile(
        sigma_pos_schedule(t_h, cfg)[None, :], (n_d, 1),
    )

    # Stack σ_event over time bins. The per-tick helper fills sub-3-
    # detector cells with a graded penalty already, so no extra NaN
    # handling here.
    n_lat, n_lon = glat.shape
    sigma_event_stack = np.zeros((n_t, n_lat, n_lon))
    for ti in range(n_t):
        sigma_event_stack[ti] = compute_sigma_event_grid_at_tick(
            drifter_lats_t[:, ti], drifter_lons_t[:, ti],
            sigma_pos_t_per_d[:, ti], glat, glon,
            nan_penalty=cfg.nan_penalty_sigma_event_m,
        )

    in_poly_stack = np.broadcast_to(mask_in_polygon[None, :, :],
                                      sigma_event_stack.shape)
    flat = sigma_event_stack[in_poly_stack].ravel()
    mean_score = float(np.mean(flat))
    p95 = float(np.percentile(flat, 95))
    mean_map = sigma_event_stack.mean(axis=0)
    return mean_score, p95, mean_map


# ---- Greedy + local refinement ----

def _candidate_grid(
    patrol: PatrolArea, cfg: OptConfig,
) -> list[tuple[float, float]]:
    """Build a regular candidate grid over patrol_area + drop_buffer at
    `candidate_spacing_m`. Drifters can be dropped here and drift
    later."""
    expanded = patrol.expanded(cfg.drop_buffer_m)
    cos_lat = float(np.cos(np.deg2rad(
        0.5 * (expanded.lat_min + expanded.lat_max))))
    d_lat = cfg.candidate_spacing_m / EARTH_R_M
    d_lon = cfg.candidate_spacing_m / (EARTH_R_M * cos_lat)
    lats = np.arange(expanded.lat_min, expanded.lat_max + d_lat * 0.5,
                      d_lat)
    lons = np.arange(expanded.lon_min, expanded.lon_max + d_lon * 0.5,
                      d_lon)
    return [(float(la), float(lo)) for la in lats for lo in lons]


def greedy_place(
    cfg: OptConfig, patrol: PatrolArea, nemo,
    glat: np.ndarray, glon: np.ndarray, mask_in_polygon: np.ndarray,
    candidate_pool: list[tuple[float, float]],
    mobility_map: dict | None = None,
    initial_placed: list[tuple[float, float]] | None = None,
    n_to_add: int | None = None,
) -> tuple[list[tuple[float, float]], list[float]]:
    """Place drifters one at a time, picking the candidate that most
    reduces mean σ_event. Returns (placed, score_after_each_placement).

    `initial_placed`: if given, start the greedy loop with these
    positions as the already-placed baseline. Used by fixed-existing-
    fleet mode (smart-redeploy partial replacement) to optimize only
    the NEW positions while keeping the existing fleet fixed.

    `n_to_add`: how many NEW drifters to add. When None, falls back to
    `cfg.n_drifters - len(initial_placed)` so the total ends at
    cfg.n_drifters; when given, adds exactly n_to_add regardless of
    cfg.n_drifters.
    """
    placed: list[tuple[float, float]] = list(initial_placed) if initial_placed else []
    n_initial = len(placed)
    if n_to_add is None:
        n_to_add = max(cfg.n_drifters - n_initial, 0)
    target_total = n_initial + n_to_add
    scores: list[float] = []
    for k in range(n_initial, target_total):
        best_score = float("inf")
        best_pt: tuple[float, float] | None = None
        # Skip candidates within `candidate_spacing_m / 2` of an
        # already-placed drifter (avoid stacking).
        cand_filtered = [
            c for c in candidate_pool
            if all(
                _haversine_m(c[0], c[1], p[0], p[1])
                > 0.5 * cfg.candidate_spacing_m
                for p in placed
            )
        ]
        for c in cand_filtered:
            trial = placed + [c]
            score, _, _ = compute_coverage_metric(
                trial, cfg, patrol, nemo, glat, glon, mask_in_polygon,
                mobility_map=mobility_map,
            )
            if score < best_score:
                best_score = score
                best_pt = c
        if best_pt is None:
            break
        placed.append(best_pt)
        scores.append(best_score)
        print(f"  greedy placed {k + 1:>2d}/{target_total}: "
              f"({best_pt[0]:.4f}, {best_pt[1]:.4f}) → "
              f"mean σ_event = {best_score:.0f} m", flush=True)
    return placed, scores


def local_refine(
    placed: list[tuple[float, float]], cfg: OptConfig,
    patrol: PatrolArea, nemo,
    glat: np.ndarray, glon: np.ndarray, mask_in_polygon: np.ndarray,
    mobility_map: dict | None = None,
    refine_indices: list[int] | None = None,
) -> tuple[list[tuple[float, float]], float, float]:
    """For each placed drifter, try perturbing within `refinement_radius_m`
    in 8 cardinal directions; keep the perturbation if score improves.
    Repeat for `refinement_passes` passes.

    `refine_indices`: if given, only the listed drifters are perturbed;
    others stay fixed. Used by fixed-existing-fleet mode to refine only
    the newly-placed replacement drifters while keeping the unflagged
    existing fleet pinned.
    """
    expanded = patrol.expanded(cfg.drop_buffer_m)
    current = list(placed)
    cur_score, cur_p95, _ = compute_coverage_metric(
        current, cfg, patrol, nemo, glat, glon, mask_in_polygon,
        mobility_map=mobility_map,
    )
    cos_lat = float(np.cos(np.deg2rad(
        0.5 * (expanded.lat_min + expanded.lat_max))))
    r_lat = cfg.refinement_radius_m / EARTH_R_M
    r_lon = cfg.refinement_radius_m / (EARTH_R_M * cos_lat)
    angles = np.linspace(0, 2 * np.pi, 9)[:-1]
    indices_to_refine = (list(refine_indices) if refine_indices is not None
                         else list(range(len(current))))
    for p_idx in range(cfg.refinement_passes):
        improved = False
        for k in indices_to_refine:
            la0, lo0 = current[k]
            for ang in angles:
                la_new = la0 + r_lat * float(np.sin(ang))
                lo_new = lo0 + r_lon * float(np.cos(ang))
                if not (expanded.lat_min <= la_new <= expanded.lat_max
                        and expanded.lon_min <= lo_new
                        <= expanded.lon_max):
                    continue
                trial = current.copy()
                trial[k] = (la_new, lo_new)
                # Avoid stacking on neighbors.
                if any(_haversine_m(la_new, lo_new, p[0], p[1])
                       < 0.5 * cfg.candidate_spacing_m
                       for j, p in enumerate(trial) if j != k):
                    continue
                score, p95_t, _ = compute_coverage_metric(
                    trial, cfg, patrol, nemo, glat, glon,
                    mask_in_polygon, mobility_map=mobility_map,
                )
                if score < cur_score:
                    cur_score = score
                    cur_p95 = p95_t
                    current = trial
                    improved = True
                    print(f"  refine pass {p_idx + 1} drifter {k}: "
                          f"mean → {cur_score:.0f} m, "
                          f"p95 → {cur_p95:.0f} m", flush=True)
                    break
        if not improved:
            print(f"  refine pass {p_idx + 1}: no improvement, stop",
                  flush=True)
            break
    return current, cur_score, cur_p95


# ---- Fixed-existing-fleet replacement mode ----


def optimize_replacements(
    existing_fleet: list[tuple[float, float]],
    n_replacements: int,
    cfg: OptConfig, patrol: PatrolArea, nemo,
    glat: np.ndarray, glon: np.ndarray, mask_in_polygon: np.ndarray,
    candidate_pool: list[tuple[float, float]],
    mobility_map: dict | None = None,
) -> tuple[list[tuple[float, float]], float, float]:
    """Greedy + refinement placement of `n_replacements` new drifters
    with `existing_fleet` positions held fixed. Returns
    (new_drop_points, final_mean_sigma_event_m, final_p95_sigma_event_m).

    Used by the smart-redeploy orchestrator at each cycle boundary:
    after triggers identify N flagged drifters, the unflagged
    M = (fleet_size - N) drifters' end-of-cycle positions become
    `existing_fleet`, and `optimize_replacements(existing_fleet, N, ...)`
    picks the N new drop points that minimize the cell-wide σ_event
    given the surviving fleet's geometry.

    The greedy loop seeds with `existing_fleet` then adds N more; the
    refinement pass perturbs ONLY the new drifters (existing fleet
    positions are operationally fixed — the ship can't relocate
    drifters that aren't being replaced).

    `existing_fleet` may be empty (= initial-deploy mode); in that
    case this is equivalent to a from-scratch greedy place of
    n_replacements drifters.
    """
    initial = list(existing_fleet)
    n_existing = len(initial)
    placed, _scores = greedy_place(
        cfg, patrol, nemo, glat, glon, mask_in_polygon, candidate_pool,
        mobility_map=mobility_map,
        initial_placed=initial,
        n_to_add=n_replacements,
    )
    refine_idx = list(range(n_existing, len(placed)))
    if refine_idx:
        placed, mean_score, p95 = local_refine(
            placed, cfg, patrol, nemo, glat, glon, mask_in_polygon,
            mobility_map=mobility_map,
            refine_indices=refine_idx,
        )
    else:
        # Nothing to refine (n_replacements=0) — just compute the score
        # of the existing fleet alone.
        mean_score, p95, _ = compute_coverage_metric(
            placed, cfg, patrol, nemo, glat, glon, mask_in_polygon,
            mobility_map=mobility_map,
        )
    new_drops = placed[n_existing:]
    return new_drops, float(mean_score), float(p95)


# ---- Output ----

def save_outputs(
    placed: list[tuple[float, float]], mean_score: float,
    p95_score: float,
    cfg: OptConfig, patrol: PatrolArea, mean_map: np.ndarray,
    glat: np.ndarray, glon: np.ndarray,
    out_dir: str, run_tag: str,
) -> None:
    os.makedirs(out_dir, exist_ok=True)
    # JSON
    json_path = os.path.join(out_dir, f"{run_tag}_drop_points.json")
    with open(json_path, "w") as f:
        json.dump({
            "run_tag": run_tag,
            "mean_sigma_event_m": mean_score,
            "p95_sigma_event_m": p95_score,
            "n_drifters": len(placed),
            "patrol": asdict(patrol),
            "config": asdict(cfg),
            "drop_points": [
                {"drifter_id": i, "lat": la, "lon": lo}
                for i, (la, lo) in enumerate(placed)
            ],
        }, f, indent=2)
    print(f"  saved {json_path}", flush=True)

    # Python snippet for fast pasting into _fleet_sweep_v0.py.
    snippet_path = os.path.join(out_dir, f"{run_tag}_density_snippet.py")
    snippet = (
        f"# Paste into _fleet_sweep_v0.py DENSITY_CONFIGS:\n"
        f"DensityConfig(\n"
        f"    name=\"D5_optimized\",\n"
        f"    label=\"N={len(placed)} optimized ({run_tag})\",\n"
        f"    stations=(\n"
    )
    for la, lo in placed:
        snippet += f"        ({la:.6f}, {lo:.6f}, 100.0),\n"
    snippet += "    ),\n),\n"
    with open(snippet_path, "w") as f:
        f.write(snippet)
    print(f"  saved {snippet_path}", flush=True)

    # Diagnostic chart.
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 7))
    LON_grid, LAT_grid = np.meshgrid(glon, glat, indexing="xy")
    sigma_clip = np.clip(mean_map, 10.0, 10_000.0)
    im = ax.pcolormesh(
        LON_grid, LAT_grid, np.log10(sigma_clip),
        cmap="viridis_r", shading="auto",
        vmin=1.0, vmax=4.0,
    )
    cb = plt.colorbar(im, ax=ax, fraction=0.046)
    cb.set_label("log₁₀ mean σ_event (m) over mission")
    # Patrol polygon outline.
    ax.plot(
        [patrol.lon_min, patrol.lon_max, patrol.lon_max,
         patrol.lon_min, patrol.lon_min],
        [patrol.lat_min, patrol.lat_min, patrol.lat_max,
         patrol.lat_max, patrol.lat_min],
        "r--", lw=1.5, label="patrol polygon",
    )
    # Drop points.
    for i, (la, lo) in enumerate(placed):
        ax.plot(lo, la, "wo", ms=8, mec="black", mew=1.0)
        ax.annotate(str(i), (lo, la), fontsize=7,
                     xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(
        f"{run_tag}\nmean σ_event = {mean_score:.0f} m, "
        f"p95 = {p95_score:.0f} m, "
        f"{len(placed)} drifters, traj={cfg.trajectory_model}"
    )
    ax.legend(fontsize=8)
    chart_path = os.path.join(out_dir, f"{run_tag}_coverage_diag.png")
    fig.savefig(chart_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {chart_path}", flush=True)


# ---- CLI ----

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--patrol-bbox", type=str, required=True,
                    help="lat_min,lon_min,lat_max,lon_max")
    p.add_argument("--n", type=int, default=16)
    p.add_argument("--mission-hours", type=float, default=72.0)
    p.add_argument("--trajectory", type=str,
                    choices=("static", "ballistic", "interp",
                             "empirical"),
                    default="interp")
    p.add_argument("--mobility-map", type=str, default=None,
                    help="Path to mobility-map npz from "
                         "`_drifter_mobility_map.py`. Required if "
                         "--trajectory empirical.")
    p.add_argument("--snap-to-mobility-grid", action="store_true",
                    help="When using empirical trajectory, also "
                         "restrict the candidate-drop pool to the "
                         "mobility-map grid points (avoids the "
                         "trajectory-lookup quantization issue where "
                         "many candidates round to the same grid "
                         "point).")
    p.add_argument("--interp-alpha", type=float, default=0.5)
    p.add_argument("--fix-cadence-h", type=float, default=6.0)
    p.add_argument("--candidate-spacing-m", type=float, default=1_000.0)
    p.add_argument("--eval-spacing-m", type=float, default=500.0)
    p.add_argument("--drop-buffer-m", type=float, default=5_000.0)
    p.add_argument("--refinement-passes", type=int, default=3)
    p.add_argument("--out-dir", type=str,
                    default="experiments/harmonic_prototype/figures/"
                            "drop_optimizer")
    p.add_argument("--tag", type=str,
                    default=time.strftime("%Y%m%d-%H%M%S"))
    args = p.parse_args()

    a, b, c, d = (float(x) for x in args.patrol_bbox.split(","))
    patrol = PatrolArea(lat_min=a, lon_min=b, lat_max=c, lon_max=d)
    cfg = OptConfig(
        n_drifters=args.n,
        mission_hours=args.mission_hours,
        trajectory_model=args.trajectory,
        interp_alpha=args.interp_alpha,
        fix_cadence_h=args.fix_cadence_h,
        candidate_spacing_m=args.candidate_spacing_m,
        eval_spacing_m=args.eval_spacing_m,
        drop_buffer_m=args.drop_buffer_m,
        refinement_passes=args.refinement_passes,
    )
    print(
        f"=== drop-point optimizer ===\n"
        f"  patrol: {patrol}\n"
        f"  cfg: {cfg}\n", flush=True,
    )

    # Load mobility map (if requested).
    mobility_map: dict | None = None
    if args.mobility_map is not None:
        print(f"loading mobility map: {args.mobility_map}", flush=True)
        mobility_map = _load_mobility_map(args.mobility_map)
        print(
            f"  policy={mobility_map['policy']} "
            f"n_drops={mobility_map['n_drops']} "
            f"n_seeds={mobility_map['n_seeds']} "
            f"run_hours={mobility_map['run_hours']}", flush=True,
        )
    if cfg.trajectory_model == "empirical" and mobility_map is None:
        print("ERROR: --trajectory empirical requires --mobility-map",
              file=sys.stderr)
        sys.exit(1)

    # NEMO is needed for ballistic / interp trajectory models. Empirical
    # mode skips this — saves ~3-4 min on first call.
    nemo = None
    if cfg.trajectory_model in ("ballistic", "interp"):
        print("loading NEMO truth field "
              "(this takes ~3-4 min on first call)...", flush=True)
        t0 = time.time()
        nemo = _load_nemo_field()
        print(f"  loaded in {time.time() - t0:.0f}s", flush=True)

    eval_lats, eval_lons, mask_in_polygon = _eval_grid_in_polygon(
        patrol, cfg.eval_spacing_m,
    )
    glat, glon = np.meshgrid(eval_lats, eval_lons, indexing="ij")
    print(f"  eval grid: {glat.shape[0]}×{glat.shape[1]} = "
          f"{glat.size} cells over patrol area", flush=True)

    if args.snap_to_mobility_grid and mobility_map is not None:
        cand_pool = [
            (float(la), float(lo))
            for la, lo in zip(mobility_map["grid_lats"],
                                mobility_map["grid_lons"])
        ]
        print(f"  candidate pool snapped to mobility-map grid: "
              f"{len(cand_pool)} drops", flush=True)
    else:
        cand_pool = _candidate_grid(patrol, cfg)
        print(f"  candidate grid: {len(cand_pool)} cells "
              f"in {patrol.expanded(cfg.drop_buffer_m)}", flush=True)

    print("\n--- greedy placement ---", flush=True)
    t0 = time.time()
    placed, scores = greedy_place(
        cfg, patrol, nemo, glat, glon, mask_in_polygon, cand_pool,
        mobility_map=mobility_map,
    )
    print(f"  greedy done in {time.time() - t0:.0f}s, "
          f"final mean = {scores[-1]:.0f} m", flush=True)

    print("\n--- local refinement ---", flush=True)
    t0 = time.time()
    placed, ref_mean, ref_p95 = local_refine(
        placed, cfg, patrol, nemo, glat, glon, mask_in_polygon,
        mobility_map=mobility_map,
    )
    print(f"  refine done in {time.time() - t0:.0f}s, "
          f"final mean = {ref_mean:.0f} m, p95 = {ref_p95:.0f} m",
          flush=True)

    final_mean, final_p95, mean_map = compute_coverage_metric(
        placed, cfg, patrol, nemo, glat, glon, mask_in_polygon,
        mobility_map=mobility_map,
    )
    print(f"\n  final placement: mean σ_event = {final_mean:.0f} m, "
          f"p95 = {final_p95:.0f} m", flush=True)

    print(f"\n--- saving ---", flush=True)
    save_outputs(
        placed, final_mean, final_p95, cfg, patrol, mean_map,
        eval_lats, eval_lons, args.out_dir, args.tag,
    )
    print(f"  done.", flush=True)


if __name__ == "__main__":
    main()
