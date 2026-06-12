"""v2 post-hoc analyzer for fleet-sweep results.

Reads the per-run npz at `figures/sweep_runs/<RUN_ID>/raw/results.npz`
(written by `_fleet_sweep_v0.py`) and emits the full v2 artifact set
into the same run subdir:

  numerical/
    summary_primary.txt           median + bootstrap-CI table per cell
    summary_calibration.txt       coverage at chi-squared quantiles
    summary_property_buckets.txt  PDOP + distance-fraction buckets
    summary_paired_modes.txt      mode-a vs mode-b paired deltas
    summary_failure_modes.txt     joint-flag failure taxonomy
    summary_thresholds.txt        deployed-filter sweep at 500m / 2km / 5km
    summary_anisotropy.txt        per-detector posterior anisotropy diag
    summary_sigma_scaling.txt     sigma_pos x2 counterfactual
    summary_three_way.txt         forward / mode-b / mode-a comparison
    summary_drifter_quality.txt   per-drifter station-keeping + PF err
    summary_per_track.txt         per-boat-track aggregates
  charts/
    01a_accuracy_heatmaps.png
    01b_operational_heatmaps.png
    01c_reliability_heatmaps.png
    02_detection_chain.png
    03_sigma_event_cdfs.png
    04_recon_error_cdfs.png
    05a_property_buckets_pdop.png
    05b_property_buckets_distance.png
    06_calibration_qq.png
    08_failure_modes.png
    09_paired_mode_deltas.png
    10_anisotropy_diagnostic.png
    11_threshold_sweep.png

Per-config maps + footprint heatmaps are deferred to a follow-up
iteration. The detection-chain and threshold-sweep charts depend only
on per-event scalars; they ship in this pass.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

import numpy as np   # type: ignore[import-not-found]


# Acoustic / detection model constants (must match _fleet_sim_v0).
C_WATER_MS = 1500.0
SIGMA_TOA_S = 0.005
DETECT_RANGE_M = 5000.0
EARTH_R_M = 111_320.0

# Basin extent (from _fleet_sim_v0 — duplicated to keep the analyzer
# self-contained). Used for the per-config basin map + footprint
# heatmap.
LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50


# ---- Loader ----

@dataclass
class DrifterRow:
    """One drifter's per-tick trajectory + diagnostics from the npz."""
    drifter_id: int
    t_sec: np.ndarray
    truth_lats: np.ndarray
    truth_lons: np.ndarray
    pf_mean_lats: np.ndarray
    pf_mean_lons: np.ndarray
    pf_cov_m: np.ndarray            # (T, 2, 2)
    smooth_means_local_m: np.ndarray  # (T, 2)
    smooth_covs_m: np.ndarray       # (T, 2, 2)
    smooth_ref_lat: float
    smooth_ref_lon: float
    lora_fix_mask: np.ndarray
    depths: np.ndarray
    station_keeping_per_tick: np.ndarray
    pf_err_per_tick: np.ndarray
    smooth_err_per_tick: np.ndarray
    station_lat: float
    station_lon: float
    n_surfacings: int
    n_lora_fix_ticks: int
    dt_sec: float
    ctrl_mean_m: float
    # Campaign-mode-only fields (None on single-mode runs). Present
    # when the per-drifter npz block carries `cycle_idx_per_tick` /
    # `cycle_boundaries_sec` keys (added by the campaign wrapper in
    # _fleet_sweep_v0._concatenate_cycles).
    cycle_idx_per_tick: np.ndarray | None = None
    cycle_boundaries_sec: np.ndarray | None = None
    n_cycles: int | None = None


@dataclass
class CellData:
    """One (density, policy) cell's full data."""
    density: str
    policy: str
    n_events: int
    n_drifters: int
    drifters: list[DrifterRow]
    # Per-event truth + categorical src.
    event_truth_lats: np.ndarray
    event_truth_lons: np.ndarray
    event_t_secs: np.ndarray
    event_src_int: np.ndarray
    event_src_label_table: np.ndarray
    # Per-event mode-a / mode-b reconstruction outputs.
    a_error_m: np.ndarray
    a_sigma_m: np.ndarray
    a_n_detectors: np.ndarray
    a_dist_centroid_m: np.ndarray
    a_recon_lat: np.ndarray
    a_recon_lon: np.ndarray
    a_recon_t_sec: np.ndarray
    a_sigma_post_3x3: np.ndarray   # (E, 3, 3)
    a_detector_ids: np.ndarray     # (E, max_n_dets) int, -1 padded
    a_detector_sigma_pos_used: np.ndarray  # (E, max_n_dets), NaN-padded
    b_error_m: np.ndarray
    b_sigma_m: np.ndarray
    b_n_detectors: np.ndarray
    b_dist_centroid_m: np.ndarray
    b_recon_lat: np.ndarray
    b_recon_lon: np.ndarray
    b_recon_t_sec: np.ndarray
    b_sigma_post_3x3: np.ndarray
    b_detector_ids: np.ndarray
    b_detector_sigma_pos_used: np.ndarray  # (E, max_n_dets), NaN-padded
    b_ttd_sec: np.ndarray


def _load_drifter(data: Any, prefix: str, di: int) -> DrifterRow:
    dp = f"{prefix}__drifter_{di}"
    cycle_idx = data[f"{dp}__cycle_idx_per_tick"] \
        if f"{dp}__cycle_idx_per_tick" in data.files else None
    cycle_bounds = data[f"{dp}__cycle_boundaries_sec"] \
        if f"{dp}__cycle_boundaries_sec" in data.files else None
    n_cycles = (int(data[f"{dp}__n_cycles"])
                if f"{dp}__n_cycles" in data.files else None)
    return DrifterRow(
        drifter_id=di,
        t_sec=data[f"{dp}__t_sec"],
        truth_lats=data[f"{dp}__truth_lats"],
        truth_lons=data[f"{dp}__truth_lons"],
        pf_mean_lats=data[f"{dp}__pf_mean_lats"],
        pf_mean_lons=data[f"{dp}__pf_mean_lons"],
        pf_cov_m=data[f"{dp}__pf_cov_m"],
        smooth_means_local_m=data[f"{dp}__smooth_means_local_m"],
        smooth_covs_m=data[f"{dp}__smooth_covs_m"],
        smooth_ref_lat=float(data[f"{dp}__smooth_ref_lat"]),
        smooth_ref_lon=float(data[f"{dp}__smooth_ref_lon"]),
        lora_fix_mask=data[f"{dp}__lora_fix_mask"].astype(bool),
        depths=data[f"{dp}__depths"],
        station_keeping_per_tick=data[f"{dp}__station_keeping_per_tick"],
        pf_err_per_tick=data[f"{dp}__pf_err_per_tick"],
        smooth_err_per_tick=data[f"{dp}__smooth_err_per_tick"],
        station_lat=float(data[f"{dp}__station_lat"]),
        station_lon=float(data[f"{dp}__station_lon"]),
        n_surfacings=int(data[f"{dp}__n_surfacings"]),
        n_lora_fix_ticks=int(data[f"{dp}__n_lora_fix_ticks"]),
        dt_sec=float(data[f"{dp}__dt_sec"]),
        ctrl_mean_m=float(data[f"{dp}__ctrl_mean_m"]),
        cycle_idx_per_tick=cycle_idx,
        cycle_boundaries_sec=cycle_bounds,
        n_cycles=n_cycles,
    )


def load_run(run_dir: str) -> list[CellData]:
    """Load all cells from a sweep run directory.

    Discovers (density, policy) pairs by scanning the npz key prefixes.
    """
    npz_path = os.path.join(run_dir, "raw", "results.npz")
    if not os.path.exists(npz_path):
        raise FileNotFoundError(f"results.npz not found at {npz_path}")
    data = np.load(npz_path, allow_pickle=False)
    # Discover prefixes from keys ending in __n_drifters.
    prefixes = sorted({
        k.rsplit("__", 1)[0]
        for k in data.files if k.endswith("__n_drifters")
    })
    cells: list[CellData] = []
    for prefix in prefixes:
        # `prefix` looks like "D1_4_tight__fixed_6h" — split on the
        # double underscore to recover (density, policy).
        density, policy = prefix.split("__", 1)
        n_drifters = int(data[f"{prefix}__n_drifters"])
        n_events = int(data[f"{prefix}__n_events"])
        drifters = [_load_drifter(data, prefix, di)
                     for di in range(n_drifters)]
        cells.append(CellData(
            density=density, policy=policy,
            n_events=n_events, n_drifters=n_drifters,
            drifters=drifters,
            event_truth_lats=data[f"{prefix}__event_truth_lats"],
            event_truth_lons=data[f"{prefix}__event_truth_lons"],
            event_t_secs=data[f"{prefix}__event_t_secs"],
            event_src_int=data[f"{prefix}__event_src_int"],
            event_src_label_table=data[f"{prefix}__event_src_label_table"],
            a_error_m=data[f"{prefix}__a__error_m"],
            a_sigma_m=data[f"{prefix}__a__sigma_m"],
            a_n_detectors=data[f"{prefix}__a__n_detectors"],
            a_dist_centroid_m=data[f"{prefix}__a__dist_centroid_m"],
            a_recon_lat=data[f"{prefix}__a__recon_lat"],
            a_recon_lon=data[f"{prefix}__a__recon_lon"],
            a_recon_t_sec=data[f"{prefix}__a__recon_t_sec"],
            a_sigma_post_3x3=data[f"{prefix}__a__sigma_post_3x3"],
            a_detector_ids=data[f"{prefix}__a__detector_ids"],
            a_detector_sigma_pos_used=data[
                f"{prefix}__a__detector_sigma_pos_used"
            ],
            b_error_m=data[f"{prefix}__b__error_m"],
            b_sigma_m=data[f"{prefix}__b__sigma_m"],
            b_n_detectors=data[f"{prefix}__b__n_detectors"],
            b_dist_centroid_m=data[f"{prefix}__b__dist_centroid_m"],
            b_recon_lat=data[f"{prefix}__b__recon_lat"],
            b_recon_lon=data[f"{prefix}__b__recon_lon"],
            b_recon_t_sec=data[f"{prefix}__b__recon_t_sec"],
            b_sigma_post_3x3=data[f"{prefix}__b__sigma_post_3x3"],
            b_detector_ids=data[f"{prefix}__b__detector_ids"],
            b_detector_sigma_pos_used=data[
                f"{prefix}__b__detector_sigma_pos_used"
            ],
            b_ttd_sec=data[f"{prefix}__b__ttd_sec"],
        ))
    return cells


# ---- Per-event metric helpers ----

def _enu_delta_m(
    truth_lat: float, truth_lon: float,
    recon_lat: float, recon_lon: float,
) -> np.ndarray:
    """Truth → recon displacement in local-ENU meters at truth_lat.

    Returns shape (2,): [east_m, north_m]. Used for Mahalanobis since
    Sigma_post is in local-ENU at the LSQ reference (also the centroid
    of detecting drifters, ~truth_lat for in-cluster events; small
    difference vs truth_lat→recon_lat ENU offset is negligible at
    cluster scales).
    """
    cos_lat = float(np.cos(np.deg2rad(truth_lat)))
    east = (recon_lon - truth_lon) * EARTH_R_M * cos_lat
    north = (recon_lat - truth_lat) * EARTH_R_M
    return np.array([east, north], dtype=float)


def mahalanobis_m2(
    truth_lat: np.ndarray, truth_lon: np.ndarray,
    recon_lat: np.ndarray, recon_lon: np.ndarray,
    sigma_post_3x3: np.ndarray,
) -> np.ndarray:
    """Compute Mahalanobis residual m^2 = dx^T Sigma_2^-1 dx per event.

    Sigma_2 = sigma_post_3x3[:2,:2] (marginal position covariance).
    NaN for events where Sigma_2 is singular or recon is non-finite.
    Under the LSQ Gaussian model, m^2 ~ chi^2_2.
    """
    n = truth_lat.size
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        rl = recon_lat[i]; ro = recon_lon[i]
        if not (np.isfinite(rl) and np.isfinite(ro)):
            continue
        S2 = sigma_post_3x3[i, :2, :2]
        if not np.all(np.isfinite(S2)):
            continue
        dx = _enu_delta_m(
            float(truth_lat[i]), float(truth_lon[i]),
            float(rl), float(ro),
        )
        try:
            S2_inv = np.linalg.inv(S2)
        except np.linalg.LinAlgError:
            continue
        m2 = float(dx @ S2_inv @ dx)
        if np.isfinite(m2) and m2 >= 0.0:
            out[i] = m2
    return out


def pdop_per_event(
    cell: CellData, mode: str,
) -> np.ndarray:
    """Position dilution of precision per event, computed from the
    geometric (length-units) Jacobian at the LSQ optimum.

    PDOP = sqrt(trace((J_norm^T J_norm)^-1 [:2,:2])) where rows of
    J_norm are unit direction vectors from each detecting drifter to
    the recon position. With sigma_pos -> 0, sigma_TOA contribution
    gives sigma_pos_event ≈ PDOP * sigma_TOA * c_water.

    NaN for events with <3 detectors, singular geometry, or non-finite
    recon. Computed in local-ENU at truth_lat for each event.
    """
    if mode == "a":
        recon_lat = cell.a_recon_lat; recon_lon = cell.a_recon_lon
        det_ids = cell.a_detector_ids
        n_dets = cell.a_n_detectors
    else:
        recon_lat = cell.b_recon_lat; recon_lon = cell.b_recon_lon
        det_ids = cell.b_detector_ids
        n_dets = cell.b_n_detectors

    e_truth_lat = cell.event_truth_lats
    e_t = cell.event_t_secs
    n_e = cell.n_events
    out = np.full(n_e, np.nan, dtype=float)
    for i in range(n_e):
        if n_dets[i] < 3:
            continue
        rl = recon_lat[i]; ro = recon_lon[i]
        if not (np.isfinite(rl) and np.isfinite(ro)):
            continue
        ids_row = det_ids[i]
        ids = ids_row[ids_row >= 0]
        if ids.size < 3:
            continue
        # Drifter positions at event time (use truth — LSQ uses
        # smoothed estimates, but PDOP is a geometry diagnostic so
        # truth positions are appropriate for the geometry value).
        cos_lat = float(np.cos(np.deg2rad(float(e_truth_lat[i]))))
        rows = []
        for d_id in ids:
            d_id = int(d_id)
            drow = cell.drifters[d_id]
            tlat, tlon = _interp_truth_at_t(drow, float(e_t[i]))
            east = (tlon - ro) * EARTH_R_M * cos_lat
            north = (tlat - rl) * EARTH_R_M
            dist = float(np.hypot(east, north))
            if dist < 1e-3:
                continue
            rows.append([east / dist, north / dist])
        if len(rows) < 3:
            continue
        J = np.asarray(rows, dtype=float)
        try:
            cov_pos = np.linalg.inv(J.T @ J)
        except np.linalg.LinAlgError:
            continue
        tr = float(cov_pos[0, 0] + cov_pos[1, 1])
        if tr > 0 and np.isfinite(tr):
            out[i] = float(np.sqrt(tr))
    return out


def _interp_truth_at_t(drow: DrifterRow, t_query_sec: float) -> tuple[float, float]:
    t = drow.t_sec
    if t_query_sec <= t[0]:
        return float(drow.truth_lats[0]), float(drow.truth_lons[0])
    if t_query_sec >= t[-1]:
        return float(drow.truth_lats[-1]), float(drow.truth_lons[-1])
    i = int(np.searchsorted(t, t_query_sec, side="right") - 1)
    a = (t_query_sec - t[i]) / max(t[i + 1] - t[i], 1e-9)
    lat = (1 - a) * float(drow.truth_lats[i]) + a * float(drow.truth_lats[i + 1])
    lon = (1 - a) * float(drow.truth_lons[i]) + a * float(drow.truth_lons[i + 1])
    return lat, lon


def _interp_cov_at_t(
    t_arr: np.ndarray, cov_arr: np.ndarray, t_query_sec: float,
) -> np.ndarray:
    """Linearly interp a (T, 2, 2) cov sequence to t_query."""
    if t_query_sec <= t_arr[0]:
        return cov_arr[0]
    if t_query_sec >= t_arr[-1]:
        return cov_arr[-1]
    i = int(np.searchsorted(t_arr, t_query_sec, side="right") - 1)
    a = (t_query_sec - t_arr[i]) / max(t_arr[i + 1] - t_arr[i], 1e-9)
    return (1 - a) * cov_arr[i] + a * cov_arr[i + 1]


def detector_anisotropy_ratio(
    cell: CellData, mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    """For each event, compute the per-detector posterior covariance's
    anisotropy ratio (larger eigenvalue / smaller eigenvalue) at event
    time. Returns (mean_ratio_per_event, max_ratio_per_event). Ratio
    of 1.0 = isotropic; >>1 = elongated.

    Mode (a) uses smoothed covs; mode (b) uses smoothed covs (same data
    available; mode-b's actual LSQ used windowed RTS but for the
    diagnostic the per-tick smoother cov is the right reference).

    NaN for events with no detectors.
    """
    if mode == "a":
        det_ids = cell.a_detector_ids
        n_dets = cell.a_n_detectors
    else:
        det_ids = cell.b_detector_ids
        n_dets = cell.b_n_detectors
    n_e = cell.n_events
    e_t = cell.event_t_secs
    mean_ratio = np.full(n_e, np.nan, dtype=float)
    max_ratio = np.full(n_e, np.nan, dtype=float)
    for i in range(n_e):
        if n_dets[i] == 0:
            continue
        ids_row = det_ids[i]
        ids = ids_row[ids_row >= 0]
        ratios = []
        for d_id in ids:
            d_id = int(d_id)
            drow = cell.drifters[d_id]
            cov = _interp_cov_at_t(
                drow.t_sec, drow.smooth_covs_m, float(e_t[i]),
            )
            try:
                eigs = np.linalg.eigvalsh(cov)
            except np.linalg.LinAlgError:
                continue
            eigs = np.maximum(eigs, 0.0)
            if eigs[0] <= 1e-18:
                continue
            ratios.append(float(eigs[1] / eigs[0]))
        if ratios:
            mean_ratio[i] = float(np.mean(ratios))
            max_ratio[i] = float(np.max(ratios))
    return mean_ratio, max_ratio


def _build_lsq_sigma_post(
    cell: CellData, det_ids: np.ndarray,
    recon_lat: float, recon_lon: float,
    e_lat: float, e_t: float,
    sigma_pos_per_detector: list[float],
) -> np.ndarray | None:
    """Re-derive Σ_post = (J^T W J)^-1 from drifter ids, recon position,
    and per-detector σ_pos. Returns 3×3 ndarray or None if singular.

    Matches `_trilaterate_tdoa`'s formulation: Jacobian rows in
    [s/m, s/m, dimensionless], W = diag(1/σ_eff_d²) where
    σ_eff_d² = σ_TOA² + (σ_pos_d / c)². ENU referenced to e_lat (the
    recon-frame approximation; centroid-of-detectors and e_lat differ
    by sub-cluster scales, negligible for Σ_post).
    """
    valid = []
    sigma_pos_kept = []
    cos_lat = float(np.cos(np.deg2rad(e_lat)))
    for d_id, sigma_pos in zip(det_ids, sigma_pos_per_detector):
        d_id = int(d_id)
        if d_id < 0:
            continue
        drow = cell.drifters[d_id]
        tlat, tlon = _interp_truth_at_t(drow, e_t)
        east = (tlon - recon_lon) * EARTH_R_M * cos_lat
        north = (tlat - recon_lat) * EARTH_R_M
        dist = float(np.hypot(east, north))
        if dist < 1e-3:
            continue
        valid.append((east / dist / C_WATER_MS,
                       north / dist / C_WATER_MS, -1.0))
        sigma_pos_kept.append(sigma_pos)
    if len(valid) < 3:
        return None
    J = np.asarray(valid, dtype=float)
    sigma_eff_sq = np.array([
        SIGMA_TOA_S ** 2 + (sp / C_WATER_MS) ** 2
        for sp in sigma_pos_kept
    ], dtype=float)
    inv_var = 1.0 / np.maximum(sigma_eff_sq, 1e-30)
    JtWJ = J.T @ (J * inv_var[:, None])
    try:
        return np.linalg.inv(JtWJ)
    except np.linalg.LinAlgError:
        return None


def _per_detector_sigma_at_event(
    cell: CellData, det_ids: np.ndarray, e_t: float, source: str,
) -> list[float]:
    """Look up each detector's scalar σ_pos at event time.

    `source` ∈ {"smooth", "forward"} selects which cov sequence:
      - "smooth": full-mission RTS smoothed cov (`smooth_covs_m`) —
        what mode-a's actual LSQ used. For mode-b it's an
        approximation: actual mode-b used a windowed RTS, but the
        full-mission smoother is the closest cov we save and shares
        the same forward-pass + LoRa-tick anchor structure.
      - "forward": forward-filter cov (`pf_cov_m`) — what the LSQ
        WOULD have used without any backward smoother pass.
    """
    out: list[float] = []
    for d_id in det_ids:
        d_id = int(d_id)
        if d_id < 0:
            out.append(float("nan"))
            continue
        drow = cell.drifters[d_id]
        if source == "smooth":
            cov = _interp_cov_at_t(drow.t_sec, drow.smooth_covs_m, e_t)
        elif source == "forward":
            cov = _interp_cov_at_t(drow.t_sec, drow.pf_cov_m, e_t)
        else:
            raise ValueError(f"unknown source {source!r}")
        out.append(float(np.sqrt(0.5 * (cov[0, 0] + cov[1, 1]))))
    return out


def _baseline_sigmas_used(
    cell: CellData, mode: str, i: int, ids: np.ndarray,
) -> list[float]:
    """The per-detector σ_pos values actually fed to this event's LSQ
    (saved in the npz). For mode-a these come from the full-mission
    smoother; for mode-b from the windowed RTS. The order matches
    `detector_ids[i]`."""
    if mode == "a":
        sig_row = cell.a_detector_sigma_pos_used[i]
    else:
        sig_row = cell.b_detector_sigma_pos_used[i]
    # ids is the trimmed list of valid drifter ids (len <= len(sig_row));
    # the saved row is in the same order, so take the first len(ids)
    # entries.
    return [float(sig_row[j]) for j in range(len(ids))]


def sigma_scaled_m2(
    cell: CellData, mode: str, scale: float,
) -> np.ndarray:
    """Counterfactual m²: re-derive Σ_post with per-detector σ_pos × scale.

    Starts from the σ_pos values ACTUALLY USED in the LSQ (saved per
    event), scales them, rebuilds Σ_post = (J^T W J)^-1 from the
    geometric Jacobian at the recon position. In the σ_pos-dominated
    regime Σ_post scales by `scale²` and m² by `1/scale²`; in the
    σ_TOA-dominated regime, scaling σ_pos has near-zero effect. The
    ratio (scaled m² / baseline m²) is a discriminator between
    multiplicative-σ_pos regime and σ_TOA-dominated regime.
    """
    if mode == "a":
        recon_lat = cell.a_recon_lat
        recon_lon = cell.a_recon_lon
        recon_t = cell.a_recon_t_sec
        det_ids = cell.a_detector_ids
    else:
        recon_lat = cell.b_recon_lat
        recon_lon = cell.b_recon_lon
        recon_t = cell.b_recon_t_sec
        det_ids = cell.b_detector_ids
    n_e = cell.n_events
    out = np.full(n_e, np.nan, dtype=float)
    for i in range(n_e):
        rl = float(recon_lat[i]); ro = float(recon_lon[i])
        rt = float(recon_t[i])
        if not (np.isfinite(rl) and np.isfinite(ro) and np.isfinite(rt)):
            continue
        ids_row = det_ids[i]
        ids = ids_row[ids_row >= 0]
        if ids.size < 3:
            continue
        sigmas_used = _baseline_sigmas_used(cell, mode, i, ids)
        sigmas_scaled = [s * scale for s in sigmas_used]
        _ = rt   # recon_t_sec was used inside the LSQ; kept for future
        Sigma_post = _build_lsq_sigma_post(
            cell, ids, rl, ro,
            float(cell.event_truth_lats[i]),
            float(cell.event_t_secs[i]),
            sigmas_scaled,
        )
        if Sigma_post is None:
            continue
        S2 = Sigma_post[:2, :2]
        if not np.all(np.isfinite(S2)):
            continue
        try:
            S2_inv = np.linalg.inv(S2)
        except np.linalg.LinAlgError:
            continue
        dx = _enu_delta_m(
            float(cell.event_truth_lats[i]),
            float(cell.event_truth_lons[i]),
            rl, ro,
        )
        m2 = float(dx @ S2_inv @ dx)
        if np.isfinite(m2) and m2 >= 0.0:
            out[i] = m2
    return out


def whitened_residual(
    cell: CellData, mode: str,
) -> np.ndarray:
    """Reduced-chi-square whitened residual norm per event.

    whitened = sqrt(sum_d (r_d / sigma_eff_d)^2 / (N - 3))
    where r_d is the TOA residual and sigma_eff_d = sqrt(sigma_TOA^2 + (sigma_pos_d / c)^2).

    Reconstruct r_d from recon position + drifter truth position:
       toa_pred_d = recon_t + ||drifter_truth_d - recon|| / c
    The recorded TOA was generated as:
       toa_obs_d = event_truth_t + ||drifter_truth_d - event_truth|| / c + eps
    so r_d = toa_obs - toa_pred. This recovers the residual seen by
    the LSQ at convergence (modulo the truth/smooth-position
    distinction the LSQ handled internally; we use truth here for the
    geometry, consistent with the as-detected physics). For a clean
    reconstruction whitened ≈ 1 ± sqrt(2/(N-3)).
    """
    if mode == "a":
        recon_lat = cell.a_recon_lat; recon_lon = cell.a_recon_lon
        recon_t = cell.a_recon_t_sec
        det_ids = cell.a_detector_ids
        n_dets = cell.a_n_detectors
    else:
        recon_lat = cell.b_recon_lat; recon_lon = cell.b_recon_lon
        recon_t = cell.b_recon_t_sec
        det_ids = cell.b_detector_ids
        n_dets = cell.b_n_detectors
    n_e = cell.n_events
    e_lat = cell.event_truth_lats
    e_lon = cell.event_truth_lons
    e_t = cell.event_t_secs
    out = np.full(n_e, np.nan, dtype=float)
    for i in range(n_e):
        if n_dets[i] < 4:
            # Need N > 3 for the reduced chi-square denominator.
            continue
        rl = recon_lat[i]; ro = recon_lon[i]; rt = recon_t[i]
        if not (np.isfinite(rl) and np.isfinite(ro) and np.isfinite(rt)):
            continue
        ids_row = det_ids[i]
        ids = ids_row[ids_row >= 0]
        if ids.size < 4:
            continue
        cos_lat = float(np.cos(np.deg2rad(float(e_lat[i]))))
        residuals_sq_norm = 0.0
        n_used = 0
        for d_id in ids:
            d_id = int(d_id)
            drow = cell.drifters[d_id]
            tlat, tlon = _interp_truth_at_t(drow, float(e_t[i]))
            # Distance from drifter truth to recon (predicted ToF arm).
            east = (tlon - ro) * EARTH_R_M * cos_lat
            north = (tlat - rl) * EARTH_R_M
            dist_to_recon = float(np.hypot(east, north))
            toa_pred = rt + dist_to_recon / C_WATER_MS
            # Distance from drifter truth to event truth (actual ToF arm).
            east_e = (float(e_lon[i]) - tlon) * EARTH_R_M * cos_lat
            north_e = (float(e_lat[i]) - tlat) * EARTH_R_M
            dist_to_event = float(np.hypot(east_e, north_e))
            toa_obs_meas = float(e_t[i]) + dist_to_event / C_WATER_MS
            # The injected eps is unrecoverable post-hoc; this is the
            # "geometric residual" — real residual differs by O(σ_TOA).
            r_d = toa_obs_meas - toa_pred
            # Per-detector noise: drifter posterior σ propagated to TOA.
            cov = _interp_cov_at_t(
                drow.t_sec, drow.smooth_covs_m, float(e_t[i]),
            )
            sigma_pos = float(np.sqrt(0.5 * (cov[0, 0] + cov[1, 1])))
            sigma_eff = float(np.sqrt(SIGMA_TOA_S ** 2
                                        + (sigma_pos / C_WATER_MS) ** 2))
            if sigma_eff <= 0:
                continue
            residuals_sq_norm += (r_d / sigma_eff) ** 2
            n_used += 1
        dof = n_used - 3
        if dof < 1:
            continue
        out[i] = float(np.sqrt(residuals_sq_norm / dof))
    return out


def forward_filter_m2(
    cell: CellData, mode: str,
) -> np.ndarray:
    """Three-way diagnostic: m² as it WOULD have been if the LSQ used
    the FORWARD-FILTER σ_pos at event time instead of the smoother.

    Rebuilds Σ_post from scratch using `pf_cov_m`-derived per-detector
    σ_pos and the geometric Jacobian at the recon position. Compares
    against the baseline (smoother-cov) m². Bounds the contribution of
    Q5/Q6 smoother bugs: forward filter doesn't use a backward pass,
    so the diagnostic isolates "smoother gave tighter cov than warranted"
    from "future-information benefit was real."
    """
    if mode == "a":
        recon_lat = cell.a_recon_lat
        recon_lon = cell.a_recon_lon
        recon_t = cell.a_recon_t_sec
        det_ids = cell.a_detector_ids
    else:
        recon_lat = cell.b_recon_lat
        recon_lon = cell.b_recon_lon
        recon_t = cell.b_recon_t_sec
        det_ids = cell.b_detector_ids
    n_e = cell.n_events
    out = np.full(n_e, np.nan, dtype=float)
    for i in range(n_e):
        rl = float(recon_lat[i]); ro = float(recon_lon[i])
        rt = float(recon_t[i])
        if not (np.isfinite(rl) and np.isfinite(ro) and np.isfinite(rt)):
            continue
        ids_row = det_ids[i]
        ids = ids_row[ids_row >= 0]
        if ids.size < 3:
            continue
        sigmas_forward = _per_detector_sigma_at_event(
            cell, ids, float(cell.event_t_secs[i]), source="forward",
        )
        _ = rt
        Sigma_post = _build_lsq_sigma_post(
            cell, ids, rl, ro,
            float(cell.event_truth_lats[i]),
            float(cell.event_t_secs[i]),
            sigmas_forward,
        )
        if Sigma_post is None:
            continue
        S2 = Sigma_post[:2, :2]
        if not np.all(np.isfinite(S2)):
            continue
        try:
            S2_inv = np.linalg.inv(S2)
        except np.linalg.LinAlgError:
            continue
        dx = _enu_delta_m(
            float(cell.event_truth_lats[i]),
            float(cell.event_truth_lons[i]),
            rl, ro,
        )
        m2 = float(dx @ S2_inv @ dx)
        if np.isfinite(m2) and m2 >= 0.0:
            out[i] = m2
    return out


# ---- Failure-mode flags (joint, not priority-bucketed) ----

@dataclass
class FailureFlags:
    """Per-event failure flags. Mutually independent — an event can fire
    multiple flags. The taxonomy distinguishes pre-LSQ filters (events
    that never reached the LSQ) from LSQ outcomes (failures + outliers
    in events the LSQ actually attempted)."""
    # Pre-LSQ filter: event was heard by < 3 drifters; LSQ never ran;
    # Σ_post is NaN by mathematical necessity (not a failure mode).
    insufficient_detectors: np.ndarray
    # LSQ-attempted (n_dets >= 3) outcomes:
    sigma_singular: np.ndarray   # JᵀWJ singular → Σ_post NaN/inf
    sigma_huge: np.ndarray       # σ_post finite but in (5km, 1e6m]
    err_catastrophic: np.ndarray  # err > 10 km
    residual_outlier: np.ndarray  # whitened > N-aware threshold


def failure_flags(cell: CellData, mode: str) -> FailureFlags:
    if mode == "a":
        err = cell.a_error_m
        sigma = cell.a_sigma_m
        sigma_post = cell.a_sigma_post_3x3
        n_dets = cell.a_n_detectors
    else:
        err = cell.b_error_m
        sigma = cell.b_sigma_m
        sigma_post = cell.b_sigma_post_3x3
        n_dets = cell.b_n_detectors
    n_e = err.size
    insufficient = (n_dets < 3)
    sigma_singular = np.zeros(n_e, dtype=bool)
    for i in range(n_e):
        if insufficient[i]:
            continue   # not an LSQ failure; LSQ never ran
        S2 = sigma_post[i, :2, :2]
        if (not np.all(np.isfinite(S2))
                or not np.isfinite(sigma[i])
                or sigma[i] > 1e6):
            sigma_singular[i] = True
    err_catastrophic = (np.isfinite(err) & (err > 10_000.0)
                         & ~insufficient)
    sigma_huge = (np.isfinite(sigma) & (sigma > 5_000.0)
                   & (sigma <= 1e6) & ~insufficient)
    whitened = whitened_residual(cell, mode)
    thresh = np.full(n_e, np.inf, dtype=float)
    for i in range(n_e):
        n = int(n_dets[i])
        if n > 3:
            thresh[i] = 1.0 + 3.0 * float(np.sqrt(2.0 / (n - 3)))
    residual_outlier = (np.isfinite(whitened)
                         & (whitened > thresh)
                         & ~insufficient)
    return FailureFlags(
        insufficient_detectors=insufficient,
        sigma_singular=sigma_singular,
        err_catastrophic=err_catastrophic,
        sigma_huge=sigma_huge,
        residual_outlier=residual_outlier,
    )


def clean_mask(cell: CellData, mode: str, flags: FailureFlags) -> np.ndarray:
    """Boolean mask: clean events. Excludes pre-LSQ filtered events AND
    any LSQ failure / outlier. The clean set is what feeds the
    deployed-σ / m² / coverage statistics."""
    if mode == "a":
        err = cell.a_error_m; sigma = cell.a_sigma_m
    else:
        err = cell.b_error_m; sigma = cell.b_sigma_m
    return (
        ~flags.insufficient_detectors
        & np.isfinite(err) & np.isfinite(sigma)
        & (sigma <= 5_000.0) & (err <= 10_000.0)
        & ~flags.sigma_singular
        & ~flags.err_catastrophic
        & ~flags.sigma_huge
        & ~flags.residual_outlier
    )


# ---- Aggregation: median + bootstrap CI + chi^2_2 coverage ----

def median_with_bootstrap_ci(
    x: np.ndarray, n_boot: int = 2000, seed: int = 0,
) -> tuple[float, float, float]:
    """Returns (median, ci_lo_95, ci_hi_95)."""
    x = x[np.isfinite(x)]
    if x.size == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    n = x.size
    medians = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        sample = rng.choice(x, size=n, replace=True)
        medians[b] = float(np.median(sample))
    return (
        float(np.median(x)),
        float(np.percentile(medians, 2.5)),
        float(np.percentile(medians, 97.5)),
    )


# χ²₂ quantiles: F⁻¹(p) = -2 ln(1 - p)
CHI2_2_Q50 = -2.0 * np.log(1.0 - 0.50)   # 1.386
CHI2_2_Q68 = -2.0 * np.log(1.0 - 0.68)   # 2.279
CHI2_2_Q95 = -2.0 * np.log(1.0 - 0.95)   # 5.991


def chi2_coverage(m2: np.ndarray) -> tuple[float, float, float]:
    """Empirical fraction of m² values <= chi^2_2 quantiles 50/68/95."""
    m = m2[np.isfinite(m2)]
    if m.size == 0:
        return float("nan"), float("nan"), float("nan")
    return (
        float(np.mean(m <= CHI2_2_Q50)),
        float(np.mean(m <= CHI2_2_Q68)),
        float(np.mean(m <= CHI2_2_Q95)),
    )


# ---- Per-cell aggregation entry point ----

@dataclass
class CellMetrics:
    density: str
    policy: str
    mode: str
    # Counts.
    n_total: int
    n_insufficient_dets: int   # heard by < 3 drifters; LSQ never ran
    n_lsq_attempted: int        # n_total − n_insufficient_dets
    n_clean: int
    n_singular_sigma: int
    n_err_catastrophic: int
    n_sigma_huge: int
    n_residual_outlier: int
    # Sigma + err on clean subset.
    sigma_p25: float
    sigma_p50: float
    sigma_p50_lo: float
    sigma_p50_hi: float
    sigma_p75: float
    sigma_p95: float
    sigma_mad: float
    err_p50: float
    err_p50_lo: float
    err_p50_hi: float
    err_p25: float
    err_p75: float
    err_p95: float
    # Mahalanobis.
    m2_p50: float
    m2_p50_lo: float
    m2_p50_hi: float
    cov_q50: float   # empirical coverage at χ²₂ q=50
    cov_q68: float
    cov_q95: float
    # σ-scaling counterfactual (×2): m² should drop by ~4× if multiplicative.
    m2_p50_scaled: float
    # Forward-filter diagnostic m² median (smoother contribution bound).
    m2_p50_forward: float
    # Anisotropy of per-detector posterior covs at event time.
    aniso_mean_p50: float
    aniso_max_p50: float
    # Operational latency (mode-b: ttd minutes; mode-a: NaN).
    ttd_min_p50: float
    ttd_min_p95: float
    # Station-keeping aggregated across drifters.
    sk_p50: float
    sk_p95: float
    n_surface_events_total: int


def _mad(x: np.ndarray) -> float:
    m = np.median(x)
    return float(np.median(np.abs(x - m)))


def compute_cell_metrics(cell: CellData, mode: str) -> CellMetrics:
    if mode == "a":
        err = cell.a_error_m; sigma = cell.a_sigma_m
        ttd = np.full(cell.n_events, np.nan, dtype=float)
    else:
        err = cell.b_error_m; sigma = cell.b_sigma_m
        ttd = cell.b_ttd_sec

    flags = failure_flags(cell, mode)
    clean = clean_mask(cell, mode, flags)
    n_clean = int(clean.sum())

    err_clean = err[clean]
    sigma_clean = sigma[clean]

    # Mahalanobis
    if mode == "a":
        m2 = mahalanobis_m2(
            cell.event_truth_lats, cell.event_truth_lons,
            cell.a_recon_lat, cell.a_recon_lon, cell.a_sigma_post_3x3,
        )
    else:
        m2 = mahalanobis_m2(
            cell.event_truth_lats, cell.event_truth_lons,
            cell.b_recon_lat, cell.b_recon_lon, cell.b_sigma_post_3x3,
        )
    m2_clean = m2[clean]
    cov50, cov68, cov95 = chi2_coverage(m2_clean)

    m2_scaled = sigma_scaled_m2(cell, mode, scale=2.0)
    m2_scaled_clean = m2_scaled[clean]

    m2_forward = forward_filter_m2(cell, mode)
    m2_forward_clean = m2_forward[clean]

    aniso_mean, aniso_max = detector_anisotropy_ratio(cell, mode)
    aniso_mean_clean = aniso_mean[clean]
    aniso_max_clean = aniso_max[clean]

    sigma_p50, sigma_p50_lo, sigma_p50_hi = median_with_bootstrap_ci(
        sigma_clean, n_boot=1000, seed=1,
    )
    err_p50, err_p50_lo, err_p50_hi = median_with_bootstrap_ci(
        err_clean, n_boot=1000, seed=2,
    )
    m2_p50, m2_p50_lo, m2_p50_hi = median_with_bootstrap_ci(
        m2_clean, n_boot=1000, seed=3,
    )

    def _q(arr: np.ndarray, p: float) -> float:
        a = arr[np.isfinite(arr)]
        return float(np.percentile(a, p)) if a.size else float("nan")

    # Station-keeping aggregated across drifters (use per-tick arrays).
    sk_all = []
    n_surface_events_total = 0
    for drow in cell.drifters:
        sk_all.append(drow.station_keeping_per_tick)
        n_surface_events_total += drow.n_surfacings
    sk_concat = np.concatenate(sk_all) if sk_all else np.array([])

    if mode == "b":
        ttd_min_clean = ttd[clean & np.isfinite(ttd)] / 60.0
        ttd_p50 = float(np.median(ttd_min_clean)) if ttd_min_clean.size else float("nan")
        ttd_p95 = (float(np.percentile(ttd_min_clean, 95))
                    if ttd_min_clean.size else float("nan"))
    else:
        ttd_p50 = float("nan"); ttd_p95 = float("nan")

    n_insufficient = int(flags.insufficient_detectors.sum())
    return CellMetrics(
        density=cell.density, policy=cell.policy, mode=mode,
        n_total=cell.n_events,
        n_insufficient_dets=n_insufficient,
        n_lsq_attempted=cell.n_events - n_insufficient,
        n_clean=n_clean,
        n_singular_sigma=int(flags.sigma_singular.sum()),
        n_err_catastrophic=int(flags.err_catastrophic.sum()),
        n_sigma_huge=int(flags.sigma_huge.sum()),
        n_residual_outlier=int(flags.residual_outlier.sum()),
        sigma_p25=_q(sigma_clean, 25),
        sigma_p50=sigma_p50, sigma_p50_lo=sigma_p50_lo, sigma_p50_hi=sigma_p50_hi,
        sigma_p75=_q(sigma_clean, 75),
        sigma_p95=_q(sigma_clean, 95),
        sigma_mad=_mad(sigma_clean) if sigma_clean.size else float("nan"),
        err_p25=_q(err_clean, 25),
        err_p50=err_p50, err_p50_lo=err_p50_lo, err_p50_hi=err_p50_hi,
        err_p75=_q(err_clean, 75),
        err_p95=_q(err_clean, 95),
        m2_p50=m2_p50, m2_p50_lo=m2_p50_lo, m2_p50_hi=m2_p50_hi,
        cov_q50=cov50, cov_q68=cov68, cov_q95=cov95,
        m2_p50_scaled=float(np.median(m2_scaled_clean[np.isfinite(m2_scaled_clean)]))
            if m2_scaled_clean[np.isfinite(m2_scaled_clean)].size else float("nan"),
        m2_p50_forward=float(np.median(m2_forward_clean[np.isfinite(m2_forward_clean)]))
            if m2_forward_clean[np.isfinite(m2_forward_clean)].size else float("nan"),
        aniso_mean_p50=float(np.median(aniso_mean_clean[np.isfinite(aniso_mean_clean)]))
            if aniso_mean_clean[np.isfinite(aniso_mean_clean)].size else float("nan"),
        aniso_max_p50=float(np.median(aniso_max_clean[np.isfinite(aniso_max_clean)]))
            if aniso_max_clean[np.isfinite(aniso_max_clean)].size else float("nan"),
        ttd_min_p50=ttd_p50, ttd_min_p95=ttd_p95,
        sk_p50=float(np.median(sk_concat)) if sk_concat.size else float("nan"),
        sk_p95=float(np.percentile(sk_concat, 95)) if sk_concat.size else float("nan"),
        n_surface_events_total=n_surface_events_total,
    )


# ---- Reporting: text tables ----

def write_summary_primary(metrics: list[CellMetrics], out_path: str) -> None:
    lines = []
    header = (
        f"{'density':>14} {'policy':>22} {'mode':>4} "
        f"{'n_tot':>5} {'<3det':>5} {'lsq_n':>5} {'clean':>5} "
        f"{'σ_p50':>7} {'σ_p50_CI':>16} "
        f"{'err_p50':>8} {'err_p50_CI':>18} "
        f"{'m²_p50':>7} {'m²_CI':>14} "
        f"{'cov50':>6} {'cov68':>6} {'cov95':>6} "
        f"{'ttd_p50':>9} {'sk_p50':>7} "
        f"{'cat_e':>5} {'sng_σ':>5} {'hg_σ':>5} {'r_out':>5}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for m in metrics:
        lines.append(
            f"{m.density:>14} {m.policy:>22} {m.mode:>4} "
            f"{m.n_total:>5} {m.n_insufficient_dets:>5} "
            f"{m.n_lsq_attempted:>5} {m.n_clean:>5} "
            f"{m.sigma_p50:>7.0f} [{m.sigma_p50_lo:>5.0f},{m.sigma_p50_hi:>5.0f}] "
            f"{m.err_p50:>8.0f} [{m.err_p50_lo:>6.0f},{m.err_p50_hi:>6.0f}] "
            f"{m.m2_p50:>7.2f} [{m.m2_p50_lo:>4.2f},{m.m2_p50_hi:>4.2f}] "
            f"{m.cov_q50:>6.2f} {m.cov_q68:>6.2f} {m.cov_q95:>6.2f} "
            f"{m.ttd_min_p50:>9.1f} {m.sk_p50:>7.0f} "
            f"{m.n_err_catastrophic:>5} {m.n_singular_sigma:>5} "
            f"{m.n_sigma_huge:>5} {m.n_residual_outlier:>5}"
        )
    lines.append("")
    lines.append("Column key:")
    lines.append("  n_tot:  total events in cell")
    lines.append("  <3det:  events heard by < 3 drifters (LSQ never ran; not a failure)")
    lines.append("  lsq_n:  events the LSQ attempted (n_tot − <3det)")
    lines.append("  clean:  events passing all flags + deployed thresholds")
    lines.append("  cat_e:  err > 10 km AND ≥3 detectors (LSQ converged far from truth)")
    lines.append("  sng_σ:  ≥3 detectors but JᵀWJ singular → Σ_post NaN/inf")
    lines.append("  hg_σ:   ≥3 detectors, σ_post finite but in (5km, 1e6m]")
    lines.append("  r_out:  N-aware whitened residual > 1 + 3·sqrt(2/(N-3))")
    lines.append("")
    lines.append("Coverage interpretation (chi^2_2 calibrated targets):")
    lines.append("  cov50: target 0.50 (Q50 = 1.386)")
    lines.append("  cov68: target 0.68 (Q68 = 2.279)")
    lines.append("  cov95: target 0.95 (Q95 = 5.991)")
    lines.append("  m²_p50: target 1.39 (median chi^2_2)")
    lines.append("  ratios > target = OVER-confident posterior (σ underestimated);")
    lines.append("  ratios < target = UNDER-confident posterior (σ overestimated).")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_three_way_summary(
    metrics: list[CellMetrics], out_path: str,
) -> None:
    """Smoother / filtered / σ×2 m² medians, side by side per cell.

    "Filtered" refers to the forward-filter posterior at event time —
    the drifter's real-time best estimate, before any backward-pass
    smoother. NOT a deployment scenario; a real drifter at next-surface
    always runs the backward pass since the future LoRa fix is free.
    The diagnostic isolates the smoother's contribution to calibration:
    if filtered and smoothed calibration agree, smoother bias is small;
    if they disagree, the smoother (Q5/Q6) is biasing σ_pos.
    """
    lines = [
        f"{'density':>14} {'policy':>22} {'mode':>4} "
        f"{'m²_p50':>7} {'m²_p50_filt':>12} {'m²_p50_×2':>10} "
        f"{'r_filt':>7} {'r_×2':>7}",
        "-" * 110,
    ]
    for m in metrics:
        rf = m.m2_p50_forward / m.m2_p50 if m.m2_p50 > 0 else float("nan")
        rs = m.m2_p50_scaled / m.m2_p50 if m.m2_p50 > 0 else float("nan")
        lines.append(
            f"{m.density:>14} {m.policy:>22} {m.mode:>4} "
            f"{m.m2_p50:>7.2f} {m.m2_p50_forward:>12.2f} "
            f"{m.m2_p50_scaled:>10.2f} "
            f"{rf:>7.2f} {rs:>7.2f}"
        )
    lines.append("")
    lines.append("Columns:")
    lines.append("  m²_p50:      median m² under the actual LSQ (smoother σ_pos)")
    lines.append("  m²_p50_filt: median m² under FORWARD-FILTER σ_pos at event time")
    lines.append("               (no backward pass — drifter's real-time posterior)")
    lines.append("  m²_p50_×2:   median m² with each detector's σ_pos doubled")
    lines.append("  r_filt:      m²_filt / m²_baseline — small if smoother dramatically tightens cov")
    lines.append("  r_×2:        m²_×2 / m²_baseline — ~0.25 if σ_pos-dominated, ~1 if σ_TOA-dominated")
    lines.append("")
    lines.append("Information ordering: forward ⊂ mode-b ⊂ mode-a")
    lines.append("→ σ ordering: σ_forward ≥ σ_mode-b ≥ σ_mode-a (more info → tighter cov)")
    lines.append("Population-median m² across modes is NOT directly orderable —")
    lines.append("each mode's LSQ produces its own recon (different Δx), so the")
    lines.append("ratio Δx²/Σ depends jointly on Σ shrinkage and accuracy gain.")
    lines.append("Use the diagnostic to compare a SINGLE mode's smoother-vs-filtered")
    lines.append("calibration, not to rank across modes.")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_anisotropy_summary(
    metrics: list[CellMetrics], out_path: str,
) -> None:
    lines = [
        f"{'density':>14} {'policy':>22} {'mode':>4} "
        f"{'aniso_mean_p50':>14} {'aniso_max_p50':>13}",
        "-" * 80,
    ]
    for m in metrics:
        lines.append(
            f"{m.density:>14} {m.policy:>22} {m.mode:>4} "
            f"{m.aniso_mean_p50:>14.2f} {m.aniso_max_p50:>13.2f}"
        )
    lines.append("")
    lines.append("Per-detector posterior cov anisotropy (max eig / min eig).")
    lines.append("1.0 = isotropic; high values = elongated post-fix posteriors.")
    lines.append("Hypothesis: post_event policy → fresh-fix → highly anisotropic →")
    lines.append("isotropic-σ_pos LSQ assumption violated → calibration deviation.")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_failure_modes_summary(
    cells: list[CellData], out_path: str,
) -> None:
    """Joint failure-mode table. Pre-LSQ filtered events (heard by <3
    drifters) are reported separately and EXCLUDED from the LSQ
    failure rates — those events never ran the LSQ."""
    lines = [
        f"{'density':>14} {'policy':>22} {'mode':>4} {'n_tot':>5} "
        f"{'<3det':>5} {'lsq_n':>5} {'pass':>5} "
        f"{'SI':>5} {'EC':>5} {'SH':>5} {'RO':>5} "
        f"{'SI+EC':>6} {'EC+SH':>6}  "
        f"{'lsq_fail_%':>10}",
        "-" * 130,
    ]
    for cell in cells:
        for mode in ("a", "b"):
            flags = failure_flags(cell, mode)
            n_total = cell.n_events
            insuff = flags.insufficient_detectors
            lsq_n = int((~insuff).sum())
            si = flags.sigma_singular
            ec = flags.err_catastrophic
            sh = flags.sigma_huge
            ro = flags.residual_outlier
            n_pass = int((~insuff & ~si & ~ec & ~sh & ~ro).sum())
            n_lsq_fail = int((~insuff & (si | ec | sh | ro)).sum())
            fail_pct = 100.0 * n_lsq_fail / max(lsq_n, 1)
            lines.append(
                f"{cell.density:>14} {cell.policy:>22} {mode:>4} "
                f"{n_total:>5} "
                f"{int(insuff.sum()):>5} {lsq_n:>5} {n_pass:>5} "
                f"{int(si.sum()):>5} {int(ec.sum()):>5} "
                f"{int(sh.sum()):>5} {int(ro.sum()):>5} "
                f"{int((si & ec).sum()):>6} "
                f"{int((ec & sh).sum()):>6}  "
                f"{fail_pct:>9.1f}%"
            )
    lines.append("")
    lines.append("Columns:")
    lines.append("  n_tot:    total events in cell")
    lines.append("  <3det:    events heard by < 3 drifters (LSQ never ran; not a failure)")
    lines.append("  lsq_n:    events the LSQ attempted (n_tot − <3det)")
    lines.append("  pass:     LSQ-attempted events passing all flags AND deployed thresholds")
    lines.append("  SI/EC/SH/RO: see flag definitions in summary_primary.txt")
    lines.append("  lsq_fail_%: (events with any flag fired) / lsq_n  — true LSQ failure rate")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_threshold_sweep_summary(
    cells: list[CellData], out_path: str,
) -> None:
    """For each cell + mode, count clean events at three deployed-σ thresholds."""
    thresholds_m = [500.0, 2_000.0, 5_000.0]
    lines = [
        f"{'density':>14} {'policy':>22} {'mode':>4} {'n_total':>7} "
        + " ".join(
            f"σ<{int(t):>5}m_yld" for t in thresholds_m
        )
        + "  "
        + " ".join(
            f"σ<{int(t):>5}m_p50" for t in thresholds_m
        ),
        "-" * 140,
    ]
    for cell in cells:
        for mode in ("a", "b"):
            err = cell.a_error_m if mode == "a" else cell.b_error_m
            sig = cell.a_sigma_m if mode == "a" else cell.b_sigma_m
            yields = []
            p50s = []
            for t in thresholds_m:
                mask = (
                    np.isfinite(err) & np.isfinite(sig)
                    & (sig <= t) & (err < 10_000.0)
                )
                yields.append(int(mask.sum()))
                arr = sig[mask]
                p50s.append(float(np.median(arr)) if arr.size else float("nan"))
            lines.append(
                f"{cell.density:>14} {cell.policy:>22} {mode:>4} "
                f"{cell.n_events:>7} "
                + " ".join(f"{y:>11d}" for y in yields)
                + "  "
                + " ".join(f"{p:>11.0f}" for p in p50s)
            )
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_drifter_quality_summary(
    cells: list[CellData], out_path: str,
) -> None:
    lines = [
        f"{'density':>14} {'policy':>22} {'di':>3} "
        f"{'sk_mean':>8} {'sk_p50':>7} {'sk_p95':>7} "
        f"{'pf_err_p50':>11} {'sm_err_p50':>11} "
        f"{'n_surf':>6} {'fix_ticks':>10}",
        "-" * 110,
    ]
    for cell in cells:
        for drow in cell.drifters:
            sk = drow.station_keeping_per_tick
            sk_finite = sk[np.isfinite(sk)]
            pf = drow.pf_err_per_tick
            pf_finite = pf[np.isfinite(pf)]
            sm = drow.smooth_err_per_tick
            sm_finite = sm[np.isfinite(sm)]
            lines.append(
                f"{cell.density:>14} {cell.policy:>22} {drow.drifter_id:>3} "
                f"{drow.ctrl_mean_m:>8.0f} "
                f"{float(np.median(sk_finite)) if sk_finite.size else float('nan'):>7.0f} "
                f"{float(np.percentile(sk_finite, 95)) if sk_finite.size else float('nan'):>7.0f} "
                f"{float(np.median(pf_finite)) if pf_finite.size else float('nan'):>11.0f} "
                f"{float(np.median(sm_finite)) if sm_finite.size else float('nan'):>11.0f} "
                f"{drow.n_surfacings:>6} {drow.n_lora_fix_ticks:>10}"
            )
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_per_track_summary(cells: list[CellData], out_path: str) -> None:
    """Aggregate boat-track reconstructions: per-track ping count, scatter,
    and span. Uses event_src_int categorization (point=0, boat:N=label).
    """
    lines = [
        f"{'density':>14} {'policy':>22} {'src':>20} "
        f"{'n_pings':>8} {'n_recon':>8} {'recon_yield_%':>14} "
        f"{'σ_p50':>7} {'err_p50':>8}",
        "-" * 110,
    ]
    for cell in cells:
        labels = cell.event_src_label_table
        src_int = cell.event_src_int
        for src_idx, src_label in enumerate(labels):
            mask = (src_int == src_idx)
            n_pings = int(mask.sum())
            if n_pings == 0:
                continue
            err_b = cell.b_error_m[mask]
            sig_b = cell.b_sigma_m[mask]
            recon_mask = (
                np.isfinite(err_b) & np.isfinite(sig_b)
                & (sig_b <= 5_000.0) & (err_b < 10_000.0)
            )
            n_recon = int(recon_mask.sum())
            if n_recon > 0:
                err_p50 = float(np.median(err_b[recon_mask]))
                sig_p50 = float(np.median(sig_b[recon_mask]))
            else:
                err_p50 = float("nan"); sig_p50 = float("nan")
            lines.append(
                f"{cell.density:>14} {cell.policy:>22} "
                f"{str(src_label):>20} "
                f"{n_pings:>8} {n_recon:>8} "
                f"{100.0 * n_recon / n_pings:>14.1f} "
                f"{sig_p50:>7.0f} {err_p50:>8.0f}"
            )
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ---- Reporting: charts ----

def chart_calibration_qq(
    cells: list[CellData], out_path: str,
) -> None:
    import matplotlib.pyplot as plt   # type: ignore[import-not-found]
    n_cells = len(cells)
    n_modes = 2
    fig, axes = plt.subplots(n_cells, n_modes,
                              figsize=(4 * n_modes, 3 * n_cells),
                              squeeze=False)
    for ci, cell in enumerate(cells):
        for mi, mode in enumerate(("a", "b")):
            ax = axes[ci, mi]
            if mode == "a":
                m2 = mahalanobis_m2(
                    cell.event_truth_lats, cell.event_truth_lons,
                    cell.a_recon_lat, cell.a_recon_lon,
                    cell.a_sigma_post_3x3,
                )
            else:
                m2 = mahalanobis_m2(
                    cell.event_truth_lats, cell.event_truth_lons,
                    cell.b_recon_lat, cell.b_recon_lon,
                    cell.b_sigma_post_3x3,
                )
            flags = failure_flags(cell, mode)
            clean = clean_mask(cell, mode, flags)
            m2c = m2[clean]
            m2c = m2c[np.isfinite(m2c)]
            if m2c.size < 5:
                ax.text(0.5, 0.5, "n<5",
                         ha="center", va="center",
                         transform=ax.transAxes)
                continue
            # χ²₂ theoretical quantiles via inverse CDF: F⁻¹(p) = -2 ln(1-p).
            n = m2c.size
            ps = (np.arange(1, n + 1) - 0.5) / n
            theo = -2.0 * np.log(1.0 - ps)
            emp = np.sort(m2c)
            mx = max(float(theo.max()), float(emp.max()), 1.0)
            ax.plot([0, mx], [0, mx], "k--", lw=0.8, alpha=0.5)
            ax.plot(theo, emp, "o-", ms=2, lw=0.8, alpha=0.7)
            ax.set_xlabel("χ²₂ theoretical quantile")
            ax.set_ylabel("empirical m² quantile")
            ax.set_title(f"{cell.density}/{cell.policy}/{mode}",
                          fontsize=8)
            ax.set_xlim(0, mx); ax.set_ylim(0, mx)
            ax.grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def chart_accuracy_heatmaps(
    metrics: list[CellMetrics], densities: list[str],
    policies: list[str], out_path: str,
) -> None:
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    def _grid(getter, mode: str) -> np.ndarray:
        g = np.full((len(densities), len(policies)), np.nan)
        for m in metrics:
            if m.mode != mode:
                continue
            di = densities.index(m.density)
            pi = policies.index(m.policy)
            g[di, pi] = getter(m)
        return g

    def _heatmap(ax, g, title, fmt="{:.0f}", cmap="viridis",
                  vmin=None, vmax=None, log=False):
        gv = g.copy()
        if log:
            gv = np.log10(np.clip(gv, 1e-3, None))
        im = ax.imshow(gv, aspect="auto", cmap=cmap,
                        vmin=vmin, vmax=vmax)
        ax.set_xticks(range(len(policies)))
        ax.set_xticklabels(policies, rotation=20, ha="right",
                            fontsize=7)
        ax.set_yticks(range(len(densities)))
        ax.set_yticklabels(densities, fontsize=8)
        ax.set_title(title, fontsize=9)
        for di in range(g.shape[0]):
            for pi in range(g.shape[1]):
                v = g[di, pi]
                if np.isfinite(v):
                    ax.text(pi, di, fmt.format(v),
                             ha="center", va="center",
                             color="white", fontsize=7)
        plt.colorbar(im, ax=ax, fraction=0.046)

    _heatmap(axes[0, 0], _grid(lambda m: m.sigma_p50, "b"),
              "σ_event p50 (m), mode b")
    _heatmap(axes[0, 1], _grid(lambda m: m.err_p50, "b"),
              "recon err p50 (m), mode b")
    _heatmap(axes[1, 0], _grid(lambda m: m.m2_p50, "b"),
              "median m² (target ≈ 1.39), mode b",
              cmap="coolwarm", vmin=0.5, vmax=4.0,
              fmt="{:.2f}")
    _heatmap(axes[1, 1], _grid(lambda m: 100.0 * m.cov_q68, "b"),
              "coverage at χ²₂ q68 (target 68%), mode b",
              cmap="coolwarm", vmin=40, vmax=80,
              fmt="{:.0f}%")
    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def chart_sigma_event_cdfs(
    cells: list[CellData], out_path: str,
) -> None:
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for cell in cells:
        for mi, mode in enumerate(("a", "b")):
            ax = axes[mi]
            sigma = cell.a_sigma_m if mode == "a" else cell.b_sigma_m
            flags = failure_flags(cell, mode)
            clean = clean_mask(cell, mode, flags)
            s = sigma[clean]
            s = s[np.isfinite(s)]
            if s.size == 0:
                continue
            s_sorted = np.sort(s)
            ys = (np.arange(1, s_sorted.size + 1)) / s_sorted.size
            ax.plot(s_sorted, ys, lw=1.0, alpha=0.7,
                     label=f"{cell.density}/{cell.policy}")
    for mi, mode in enumerate(("a", "b")):
        ax = axes[mi]
        ax.set_xscale("log")
        ax.set_xlabel("σ_event (m)")
        ax.set_ylabel("CDF")
        ax.set_title(f"σ_event CDF (clean), mode {mode}")
        ax.legend(fontsize=6, loc="lower right", ncol=2)
        ax.grid(alpha=0.3, which="both")
    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def chart_pdop_buckets(
    cells: list[CellData], out_path: str,
) -> None:
    """σ_event and m² as a function of PDOP bucket, mode b."""
    import matplotlib.pyplot as plt
    pdop_edges = [0.0, 2.0, 4.0, 8.0, 16.0]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for cell in cells:
        pdop = pdop_per_event(cell, "b")
        sigma = cell.b_sigma_m
        m2 = mahalanobis_m2(
            cell.event_truth_lats, cell.event_truth_lons,
            cell.b_recon_lat, cell.b_recon_lon, cell.b_sigma_post_3x3,
        )
        flags = failure_flags(cell, "b")
        clean = clean_mask(cell, "b", flags)
        xs1, ys1 = [], []
        xs2, ys2 = [], []
        for lo, hi in zip(pdop_edges[:-1], pdop_edges[1:]):
            mask = clean & np.isfinite(pdop) & (pdop >= lo) & (pdop < hi)
            if mask.sum() < 3:
                continue
            xs1.append(0.5 * (lo + hi))
            ys1.append(float(np.median(sigma[mask])))
            m2_in = m2[mask]; m2_in = m2_in[np.isfinite(m2_in)]
            if m2_in.size:
                xs2.append(0.5 * (lo + hi))
                ys2.append(float(np.median(m2_in)))
        if xs1:
            axes[0].plot(xs1, ys1, "-o", lw=1.0, ms=3, alpha=0.7,
                          label=f"{cell.density}/{cell.policy}")
        if xs2:
            axes[1].plot(xs2, ys2, "-o", lw=1.0, ms=3, alpha=0.7,
                          label=f"{cell.density}/{cell.policy}")
    axes[0].set_xlabel("PDOP bucket center")
    axes[0].set_ylabel("σ_event p50 (m), mode b")
    axes[0].set_title("σ_event vs PDOP")
    axes[0].set_yscale("log")
    axes[0].legend(fontsize=6, ncol=2)
    axes[0].grid(alpha=0.3, which="both")
    axes[1].axhline(1.386, color="k", ls="--", lw=0.8, alpha=0.5,
                     label="χ²₂ median target (1.39)")
    axes[1].set_xlabel("PDOP bucket center")
    axes[1].set_ylabel("median m², mode b")
    axes[1].set_title("Mahalanobis m² vs PDOP")
    axes[1].legend(fontsize=6, ncol=2)
    axes[1].grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def chart_detection_chain(
    cells: list[CellData], out_path: str,
) -> None:
    """Stacked bars per (density, policy): event yield through stages
    of the detection + LSQ pipeline. Bars show:
      - <3det (pre-LSQ filtered)
      - LSQ-attempted-but-failed (any flag)
      - LSQ-passed-but-outside-deployed-σ
      - clean (operationally usable)
    """
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for mi, mode in enumerate(("a", "b")):
        ax = axes[mi]
        labels = [f"{c.density}/{c.policy}" for c in cells]
        x = np.arange(len(cells))
        n_insuff = []
        n_lsq_fail = []
        n_lsq_outside_dep = []
        n_clean = []
        for cell in cells:
            flags = failure_flags(cell, mode)
            err = cell.a_error_m if mode == "a" else cell.b_error_m
            sig = cell.a_sigma_m if mode == "a" else cell.b_sigma_m
            insuff = flags.insufficient_detectors
            lsq_attempted = ~insuff
            any_flag = (flags.sigma_singular | flags.err_catastrophic
                        | flags.sigma_huge | flags.residual_outlier)
            within_dep = (np.isfinite(err) & np.isfinite(sig)
                          & (sig <= 5_000.0) & (err <= 10_000.0))
            clean = lsq_attempted & ~any_flag & within_dep
            lsq_fail = lsq_attempted & any_flag
            lsq_outside_dep = (lsq_attempted & ~any_flag
                                & ~within_dep)
            n_insuff.append(int(insuff.sum()))
            n_lsq_fail.append(int(lsq_fail.sum()))
            n_lsq_outside_dep.append(int(lsq_outside_dep.sum()))
            n_clean.append(int(clean.sum()))
        n_insuff_a = np.array(n_insuff)
        n_lsq_fail_a = np.array(n_lsq_fail)
        n_lsq_outside_dep_a = np.array(n_lsq_outside_dep)
        n_clean_a = np.array(n_clean)
        ax.bar(x, n_clean_a, label="clean (deployable)",
                color="tab:green")
        ax.bar(x, n_lsq_outside_dep_a, bottom=n_clean_a,
                label="LSQ ok but σ>5km or err>10km",
                color="tab:olive")
        ax.bar(x, n_lsq_fail_a,
                bottom=n_clean_a + n_lsq_outside_dep_a,
                label="LSQ failure (any flag)",
                color="tab:red")
        ax.bar(x, n_insuff_a,
                bottom=n_clean_a + n_lsq_outside_dep_a + n_lsq_fail_a,
                label="<3 detectors (pre-LSQ)",
                color="tab:gray")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right",
                            fontsize=7)
        ax.set_ylabel("event count")
        ax.set_title(f"Detection chain stages, mode {mode}")
        ax.legend(fontsize=7, loc="upper right")
        ax.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def chart_failure_modes_joint(
    cells: list[CellData], out_path: str,
) -> None:
    """Per-cell stacked bar of LSQ-failure flag counts (excludes <3-det
    pre-filter). Shows each flag's contribution per cell. Multiple
    flags can fire per event so totals can exceed `lsq_fail` count."""
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for mi, mode in enumerate(("a", "b")):
        ax = axes[mi]
        labels = [f"{c.density}/{c.policy}" for c in cells]
        x = np.arange(len(cells))
        si = []; ec = []; sh = []; ro = []
        for cell in cells:
            f = failure_flags(cell, mode)
            si.append(int(f.sigma_singular.sum()))
            ec.append(int(f.err_catastrophic.sum()))
            sh.append(int(f.sigma_huge.sum()))
            ro.append(int(f.residual_outlier.sum()))
        si_a = np.array(si); ec_a = np.array(ec)
        sh_a = np.array(sh); ro_a = np.array(ro)
        ax.bar(x, si_a, label="SI: σ_singular", color="tab:red")
        ax.bar(x, ec_a, bottom=si_a,
                label="EC: err catastrophic",
                color="tab:orange")
        ax.bar(x, sh_a, bottom=si_a + ec_a,
                label="SH: σ huge",
                color="tab:olive")
        ax.bar(x, ro_a, bottom=si_a + ec_a + sh_a,
                label="RO: residual outlier",
                color="tab:purple")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right",
                            fontsize=7)
        ax.set_ylabel("flag-fired event count (events can fire multiple)")
        ax.set_title(f"LSQ failure-mode flags, mode {mode}")
        ax.legend(fontsize=7, loc="upper right")
        ax.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def chart_paired_mode_deltas(
    cells: list[CellData], out_path: str,
) -> None:
    """Within-event paired deltas: σ_b − σ_a and m²_b − m²_a.
    Restricted to events where BOTH modes produced a clean recon."""
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for cell in cells:
        flags_a = failure_flags(cell, "a")
        flags_b = failure_flags(cell, "b")
        clean_both = (clean_mask(cell, "a", flags_a)
                      & clean_mask(cell, "b", flags_b))
        sigma_a = cell.a_sigma_m
        sigma_b = cell.b_sigma_m
        m2_a = mahalanobis_m2(
            cell.event_truth_lats, cell.event_truth_lons,
            cell.a_recon_lat, cell.a_recon_lon,
            cell.a_sigma_post_3x3,
        )
        m2_b = mahalanobis_m2(
            cell.event_truth_lats, cell.event_truth_lons,
            cell.b_recon_lat, cell.b_recon_lon,
            cell.b_sigma_post_3x3,
        )
        d_sigma = sigma_b[clean_both] - sigma_a[clean_both]
        d_m2 = m2_b[clean_both] - m2_a[clean_both]
        d_sigma = d_sigma[np.isfinite(d_sigma)]
        d_m2 = d_m2[np.isfinite(d_m2)]
        if d_sigma.size:
            d_sigma_sorted = np.sort(d_sigma)
            ys = np.arange(1, d_sigma_sorted.size + 1) / d_sigma_sorted.size
            axes[0].plot(d_sigma_sorted, ys, lw=1.0, alpha=0.7,
                          label=f"{cell.density}/{cell.policy}")
        if d_m2.size:
            d_m2_sorted = np.sort(d_m2)
            ys = np.arange(1, d_m2_sorted.size + 1) / d_m2_sorted.size
            axes[1].plot(d_m2_sorted, ys, lw=1.0, alpha=0.7,
                          label=f"{cell.density}/{cell.policy}")
    axes[0].axvline(0, color="k", ls="--", lw=0.8, alpha=0.5)
    axes[0].set_xlabel("Δσ = σ_b − σ_a (m), within-event")
    axes[0].set_ylabel("CDF")
    axes[0].set_title("Paired Δσ (mode-b minus mode-a)")
    axes[0].legend(fontsize=6, ncol=2, loc="lower right")
    axes[0].grid(alpha=0.3)
    axes[1].axvline(0, color="k", ls="--", lw=0.8, alpha=0.5)
    axes[1].set_xlabel("Δm² = m²_b − m²_a")
    axes[1].set_ylabel("CDF")
    axes[1].set_title("Paired Δm² (mode-b minus mode-a)")
    axes[1].legend(fontsize=6, ncol=2, loc="lower right")
    axes[1].grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def chart_threshold_sweep(
    cells: list[CellData], out_path: str,
) -> None:
    """Filter-threshold sweep: yield and σ_p50 vs (500m, 2km, 5km).
    Mode b only — operational-relevant. Yield as % of n_total events;
    σ_p50 over events passing the threshold."""
    import matplotlib.pyplot as plt
    thresholds = [500.0, 2_000.0, 5_000.0]
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for cell in cells:
        err = cell.b_error_m
        sig = cell.b_sigma_m
        yields = []
        p50s = []
        for t in thresholds:
            mask = (np.isfinite(err) & np.isfinite(sig)
                    & (sig <= t) & (err < 10_000.0))
            yields.append(100.0 * mask.sum() / max(cell.n_events, 1))
            arr = sig[mask]
            p50s.append(float(np.median(arr)) if arr.size else float("nan"))
        axes[0].plot(thresholds, yields, "-o", lw=1.0, ms=4, alpha=0.8,
                      label=f"{cell.density}/{cell.policy}")
        axes[1].plot(thresholds, p50s, "-o", lw=1.0, ms=4, alpha=0.8,
                      label=f"{cell.density}/{cell.policy}")
    axes[0].set_xscale("log")
    axes[0].set_xticks(thresholds)
    axes[0].set_xticklabels([f"{int(t)}m" for t in thresholds])
    axes[0].set_xlabel("σ_event filter threshold")
    axes[0].set_ylabel("yield (% of total events)")
    axes[0].set_title("Mode-b yield vs σ-filter threshold")
    axes[0].legend(fontsize=6, ncol=2, loc="lower right")
    axes[0].grid(alpha=0.3, which="both")
    axes[1].set_xscale("log")
    axes[1].set_xticks(thresholds)
    axes[1].set_xticklabels([f"{int(t)}m" for t in thresholds])
    axes[1].set_xlabel("σ_event filter threshold")
    axes[1].set_ylabel("σ_event p50 (m), passing events")
    axes[1].set_title("Mode-b σ_p50 vs σ-filter threshold")
    axes[1].legend(fontsize=6, ncol=2, loc="lower right")
    axes[1].grid(alpha=0.3, which="both")
    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def chart_anisotropy_diagnostic(
    cells: list[CellData], out_path: str,
) -> None:
    """Per-cell histogram of per-event mean anisotropy (mode b).
    The leading-mechanism hypothesis predicts post_event >> fixed
    cadences in median anisotropy."""
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 6))
    for cell in cells:
        aniso_mean, _ = detector_anisotropy_ratio(cell, "b")
        a = aniso_mean[np.isfinite(aniso_mean)]
        if a.size == 0:
            continue
        a_sorted = np.sort(a)
        ys = np.arange(1, a_sorted.size + 1) / a_sorted.size
        ax.plot(a_sorted, ys, lw=1.0, alpha=0.7,
                 label=f"{cell.density}/{cell.policy}")
    ax.set_xscale("log")
    ax.set_xlabel("per-event mean of per-detector cov anisotropy ratio")
    ax.set_ylabel("CDF")
    ax.set_title(
        "Per-detector posterior anisotropy at event time (mode b).\n"
        "1.0 = isotropic; high = elongated post-fix posteriors."
    )
    ax.axvline(1.0, color="k", ls="--", lw=0.8, alpha=0.5)
    ax.legend(fontsize=7, ncol=2, loc="lower right")
    ax.grid(alpha=0.3, which="both")
    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


# ---- Per-config charts (basin map + detection footprint) ----

def _detection_footprint_grid(
    cell: CellData, n_lat: int = 40, n_lon: int = 40,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per grid cell over the basin, fraction of mission ticks where
    at least 3 drifters' truth positions are within DETECT_RANGE_M.
    Returns (lats, lons, footprint_fraction)."""
    lats = np.linspace(LAT_MIN, LAT_MAX, n_lat)
    lons = np.linspace(LON_MIN, LON_MAX, n_lon)
    glat, glon = np.meshgrid(lats, lons, indexing="ij")
    cos_lat = float(np.cos(np.deg2rad(0.5 * (LAT_MIN + LAT_MAX))))
    n_ticks = cell.drifters[0].t_sec.size
    if n_ticks == 0 or len(cell.drifters) == 0:
        return lats, lons, np.zeros_like(glat)
    coverage_count = np.zeros_like(glat, dtype=int)
    # Per tick, compute distance from each grid point to each drifter,
    # count how many are within DETECT_RANGE_M, and increment coverage
    # if count >= 3.
    for t_idx in range(n_ticks):
        in_range_count = np.zeros_like(glat, dtype=int)
        for drow in cell.drifters:
            dlat = float(drow.truth_lats[t_idx])
            dlon = float(drow.truth_lons[t_idx])
            dy_m = (glat - dlat) * EARTH_R_M
            dx_m = (glon - dlon) * EARTH_R_M * cos_lat
            dist_sq = dy_m * dy_m + dx_m * dx_m
            in_range_count += (dist_sq <= DETECT_RANGE_M ** 2).astype(int)
        coverage_count += (in_range_count >= 3).astype(int)
    return lats, lons, coverage_count / max(n_ticks, 1)


def _realistic_footprint_grid(
    cell: CellData, t_start_sec: float, t_end_sec: float,
    n_lat: int = 40, n_lon: int = 40,
    sigma_pos_source: str = "forward",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per grid cell over the basin, time-mean **σ_event** over ticks in
    [t_start_sec, t_end_sec), computed as the actual LSQ Σ_post would
    produce given each drifter's σ_pos at that tick.

    Construction:
      - For each tick t in window, for each drifter d:
        - r_d = unit vector from drifter truth position to grid point
        - in_range_d = (|r_d_world| ≤ DETECT_RANGE_M)
        - σ_pos_d(t) = drifter's posterior position σ from the chosen
          source (`pf_cov_m` for forward, `smooth_covs_m` for smooth)
        - σ_eff_d² = σ_TOA² + (σ_pos_d / c)²
      - Build the 3-parameter LSQ Jacobian J with rows `[r_x/c, r_y/c, -1]`
        (matches `_trilaterate_tdoa`'s convention, units [s/m, s/m, 1])
        and W = diag(1/σ_eff_d²).
      - JᵀWJ summed over drifters (in-range only via 0-weight masking).
      - Σ_post = (JᵀWJ)⁻¹, in mixed units; the position 2×2 block has
        units m². σ_event = sqrt(0.5·(Σ_post[0,0] + Σ_post[1,1])) m.

    Returns (lats, lons, σ_event_m_grid). NaN where <3 drifters in range
    or matrix singular. Aggregation across ticks: arithmetic mean over
    valid ticks per grid point.

    sigma_pos_source: "forward" uses each drifter's PF (real-time) cov;
        "smooth" uses the full-mission RTS smoother cov.
    """
    if sigma_pos_source not in ("forward", "smooth"):
        raise ValueError(f"unknown sigma_pos_source {sigma_pos_source!r}")

    lats = np.linspace(LAT_MIN, LAT_MAX, n_lat)
    lons = np.linspace(LON_MIN, LON_MAX, n_lon)
    glat, glon = np.meshgrid(lats, lons, indexing="ij")
    cos_lat = float(np.cos(np.deg2rad(0.5 * (LAT_MIN + LAT_MAX))))

    if not cell.drifters:
        return lats, lons, np.full_like(glat, np.nan)
    t_arr = cell.drifters[0].t_sec
    t_mask = (t_arr >= t_start_sec) & (t_arr < t_end_sec)
    tick_indices = np.where(t_mask)[0]
    if tick_indices.size == 0:
        return lats, lons, np.full_like(glat, np.nan)
    n_d = len(cell.drifters)

    # Pre-compute per-drifter scalar σ_pos at every tick (T,) once.
    sigma_pos_dt = np.zeros((n_d, t_arr.size))
    for d_idx, drow in enumerate(cell.drifters):
        covs = (drow.pf_cov_m if sigma_pos_source == "forward"
                 else drow.smooth_covs_m)
        sigma_pos_dt[d_idx] = np.sqrt(
            np.maximum(0.5 * (covs[:, 0, 0] + covs[:, 1, 1]), 0.0)
        )

    sigma_event_sum = np.zeros_like(glat)
    valid_count = np.zeros_like(glat, dtype=int)
    eye3 = np.eye(3)

    for t_idx in tick_indices:
        # Per-drifter σ_eff² at this tick → inverse-variance weights.
        sigma_pos_t = sigma_pos_dt[:, t_idx]
        sigma_eff_sq = (SIGMA_TOA_S ** 2
                         + (sigma_pos_t / C_WATER_MS) ** 2)
        inv_var = 1.0 / np.maximum(sigma_eff_sq, 1e-30)

        # Per-drifter unit-direction vectors and in-range mask at this
        # tick, shape (n_d, n_lat, n_lon) each.
        unit_x = np.zeros((n_d, n_lat, n_lon))
        unit_y = np.zeros((n_d, n_lat, n_lon))
        in_range = np.zeros((n_d, n_lat, n_lon), dtype=bool)
        for d_idx, drow in enumerate(cell.drifters):
            dlat = float(drow.truth_lats[t_idx])
            dlon = float(drow.truth_lons[t_idx])
            dy = (glat - dlat) * EARTH_R_M
            dx = (glon - dlon) * EARTH_R_M * cos_lat
            dist_sq = dx * dx + dy * dy
            in_range[d_idx] = (dist_sq <= DETECT_RANGE_M ** 2)
            dist = np.sqrt(np.maximum(dist_sq, 1.0))
            unit_x[d_idx] = np.where(dist > 1e-3, dx / dist, 0.0)
            unit_y[d_idx] = np.where(dist > 1e-3, dy / dist, 0.0)

        # Per-drifter weight at this tick, masked by in_range.
        w = inv_var[:, None, None] * in_range.astype(float)
        # JᵀWJ components — Jacobian rows [r_x/c, r_y/c, -1].
        JTWJ_xx = (unit_x * unit_x * w).sum(axis=0) / C_WATER_MS ** 2
        JTWJ_yy = (unit_y * unit_y * w).sum(axis=0) / C_WATER_MS ** 2
        JTWJ_xy = (unit_x * unit_y * w).sum(axis=0) / C_WATER_MS ** 2
        JTWJ_xt = -(unit_x * w).sum(axis=0) / C_WATER_MS
        JTWJ_yt = -(unit_y * w).sum(axis=0) / C_WATER_MS
        JTWJ_tt = w.sum(axis=0)
        n_in_range = in_range.sum(axis=0)
        valid = (n_in_range >= 3)

        # Build the 3x3 JᵀWJ tensor across the grid; set invalid grid
        # points to identity so np.linalg.inv doesn't choke (we mask
        # out their σ_event below).
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
        # → JᵀWJ singular). Vectorized np.linalg.inv fails the whole
        # batch if any matrix is singular, so we explicitly mark
        # singular cells invalid and set them to identity for the inv
        # call.
        det = np.linalg.det(JTWJ)
        valid = valid & (np.abs(det) > 1e-12)
        JTWJ[~valid] = eye3

        with np.errstate(invalid="ignore", divide="ignore"):
            Sigma = np.linalg.inv(JTWJ)
            sigma_event_sq = 0.5 * (Sigma[..., 0, 0]
                                     + Sigma[..., 1, 1])
            sigma_event_m = np.where(
                valid & (sigma_event_sq > 0),
                np.sqrt(np.maximum(sigma_event_sq, 0.0)),
                np.nan,
            )
        good = np.isfinite(sigma_event_m)
        sigma_event_sum += np.where(good, sigma_event_m, 0.0)
        valid_count += good.astype(int)
    out = np.where(
        valid_count > 0,
        sigma_event_sum / np.maximum(valid_count, 1),
        np.nan,
    )
    return lats, lons, out


# ---- Coverage timeseries (Phase 2: campaign coverage decay) ----
#
# For each (cell, time bin), compute the fraction of patrol-area grid
# points that meet a coverage criterion at the bin midpoint. The
# criterion: ≥3 drifters within DETECT_RANGE_M AND the LSQ-implied
# σ_event_floor at that grid point < threshold.
#
# Patrol bbox is taken from the union of station positions in the cell
# (with a buffer); this matches the "focused-band patrol" framing —
# coverage is evaluated where stations were actually placed, not over
# the basin bbox at large.

# Default threshold for coverage classification (m). Grid points with
# σ_event_floor below this are "covered" at that bin midpoint. 500 m
# matches the operational target band that prior σ_event runs landed in.
COVERAGE_SIGMA_THRESHOLD_M = 500.0


def patrol_bbox_for_cell(
    cell: CellData, buffer_m: float = 2000.0,
) -> tuple[float, float, float, float]:
    """Return (lat_min, lat_max, lon_min, lon_max) for the patrol area
    of `cell`. Bounds are the station-position bbox extended by
    `buffer_m` (≈2 km default), so the coverage metric is evaluated on
    the band drifters were placed to monitor — not the whole basin.
    """
    if not cell.drifters:
        return LAT_MIN, LAT_MAX, LON_MIN, LON_MAX
    s_lats = np.asarray([d.station_lat for d in cell.drifters])
    s_lons = np.asarray([d.station_lon for d in cell.drifters])
    cos_lat = float(np.cos(np.deg2rad(0.5 * (s_lats.min() + s_lats.max()))))
    dlat = buffer_m / EARTH_R_M
    dlon = buffer_m / (EARTH_R_M * cos_lat)
    return (
        float(s_lats.min() - dlat),
        float(s_lats.max() + dlat),
        float(s_lons.min() - dlon),
        float(s_lons.max() + dlon),
    )


def coverage_at_bin_midpoints(
    cell: CellData, bin_sec: float = 3600.0,
    n_lat: int = 30, n_lon: int = 30,
    sigma_threshold_m: float = COVERAGE_SIGMA_THRESHOLD_M,
    sigma_pos_source: str = "forward",
) -> tuple[np.ndarray, np.ndarray]:
    """Per time bin (default 1h), fraction of patrol-area grid points
    where ≥3 drifters are within DETECT_RANGE_M and σ_event_floor (LSQ
    Σ_post position σ) < `sigma_threshold_m`.

    Returns (bin_midpoints_sec, coverage_fraction) — both shape (n_bins,).

    Evaluates at bin midpoints only (one LSQ-grid pass per bin). Per-tick
    integration would be ~6× more expensive with negligible additional
    information for the coverage-decay shape we care about.
    """
    if sigma_pos_source not in ("forward", "smooth"):
        raise ValueError(f"unknown sigma_pos_source {sigma_pos_source!r}")
    if not cell.drifters:
        return np.zeros(0), np.zeros(0)

    lat_min, lat_max, lon_min, lon_max = patrol_bbox_for_cell(cell)
    lats = np.linspace(lat_min, lat_max, n_lat)
    lons = np.linspace(lon_min, lon_max, n_lon)
    glat, glon = np.meshgrid(lats, lons, indexing="ij")
    cos_lat = float(np.cos(np.deg2rad(0.5 * (lat_min + lat_max))))
    n_d = len(cell.drifters)
    eye3 = np.eye(3)

    t_arr = cell.drifters[0].t_sec
    if t_arr.size == 0:
        return np.zeros(0), np.zeros(0)
    t_max = float(t_arr[-1])
    n_bins = int(np.ceil((t_max + bin_sec) / bin_sec))
    bin_mids = np.array([(b + 0.5) * bin_sec for b in range(n_bins)])
    coverage = np.full(n_bins, np.nan)

    # Pre-compute per-drifter scalar σ_pos at every tick — same shape
    # as in `_realistic_footprint_grid`.
    sigma_pos_dt = np.zeros((n_d, t_arr.size))
    for d_idx, drow in enumerate(cell.drifters):
        covs = (drow.pf_cov_m if sigma_pos_source == "forward"
                 else drow.smooth_covs_m)
        sigma_pos_dt[d_idx] = np.sqrt(
            np.maximum(0.5 * (covs[:, 0, 0] + covs[:, 1, 1]), 0.0)
        )

    n_grid_total = n_lat * n_lon
    for b in range(n_bins):
        t_mid = bin_mids[b]
        if t_mid > t_max:
            break
        # Find tick index closest to bin midpoint.
        t_idx = int(np.searchsorted(t_arr, t_mid, side="right") - 1)
        t_idx = max(0, min(t_idx, t_arr.size - 1))

        sigma_pos_t = sigma_pos_dt[:, t_idx]
        sigma_eff_sq = (SIGMA_TOA_S ** 2
                         + (sigma_pos_t / C_WATER_MS) ** 2)
        inv_var = 1.0 / np.maximum(sigma_eff_sq, 1e-30)

        unit_x = np.zeros((n_d, n_lat, n_lon))
        unit_y = np.zeros((n_d, n_lat, n_lon))
        in_range = np.zeros((n_d, n_lat, n_lon), dtype=bool)
        for d_idx, drow in enumerate(cell.drifters):
            dlat = float(drow.truth_lats[t_idx])
            dlon = float(drow.truth_lons[t_idx])
            dy = (glat - dlat) * EARTH_R_M
            dx = (glon - dlon) * EARTH_R_M * cos_lat
            dist_sq = dx * dx + dy * dy
            in_range[d_idx] = (dist_sq <= DETECT_RANGE_M ** 2)
            dist = np.sqrt(np.maximum(dist_sq, 1.0))
            unit_x[d_idx] = np.where(dist > 1e-3, dx / dist, 0.0)
            unit_y[d_idx] = np.where(dist > 1e-3, dy / dist, 0.0)

        w = inv_var[:, None, None] * in_range.astype(float)
        JTWJ_xx = (unit_x * unit_x * w).sum(axis=0) / C_WATER_MS ** 2
        JTWJ_yy = (unit_y * unit_y * w).sum(axis=0) / C_WATER_MS ** 2
        JTWJ_xy = (unit_x * unit_y * w).sum(axis=0) / C_WATER_MS ** 2
        JTWJ_xt = -(unit_x * w).sum(axis=0) / C_WATER_MS
        JTWJ_yt = -(unit_y * w).sum(axis=0) / C_WATER_MS
        JTWJ_tt = w.sum(axis=0)
        n_in_range = in_range.sum(axis=0)
        valid = (n_in_range >= 3)

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
        det = np.linalg.det(JTWJ)
        valid = valid & (np.abs(det) > 1e-12)
        JTWJ_safe = np.where(valid[..., None, None], JTWJ, eye3)

        with np.errstate(invalid="ignore", divide="ignore"):
            Sigma = np.linalg.inv(JTWJ_safe)
            sigma_event_sq = 0.5 * (Sigma[..., 0, 0]
                                     + Sigma[..., 1, 1])
            covered = valid & (sigma_event_sq > 0) & (
                sigma_event_sq < sigma_threshold_m ** 2
            )
        coverage[b] = float(np.sum(covered)) / n_grid_total
    return bin_mids, coverage


def precompute_coverage_timeseries(
    cells: list[CellData],
    bin_sec: float = 3600.0,
    sigma_threshold_m: float = COVERAGE_SIGMA_THRESHOLD_M,
) -> dict[tuple[str, str], tuple[np.ndarray, np.ndarray]]:
    """Run `coverage_at_bin_midpoints` once per cell. Returned dict is
    keyed by (density, policy); used by both `chart_coverage_timeseries`
    and `write_coverage_timeseries_summary` so the per-cell LSQ pass
    isn't repeated."""
    return {
        (c.density, c.policy): coverage_at_bin_midpoints(
            c, bin_sec=bin_sec, sigma_threshold_m=sigma_threshold_m,
        )
        for c in cells
    }


def write_coverage_timeseries_summary(
    cells: list[CellData], out_path: str,
    bin_sec: float = 3600.0,
    sigma_threshold_m: float = COVERAGE_SIGMA_THRESHOLD_M,
    precomputed: dict[tuple[str, str],
                       tuple[np.ndarray, np.ndarray]] | None = None,
) -> None:
    """Write per-cell coverage timeseries + cumulative time-above-zero +
    fraction-of-mission-covered. One block per cell. Bin midpoints in
    hours."""
    blocks: list[str] = []
    blocks.append(
        f"# coverage timeseries — bin={bin_sec/3600:.1f}h, "
        f"σ_threshold={sigma_threshold_m:.0f}m, "
        f"sigma_pos_source=forward (per-tick deployment-honest σ)\n"
    )
    for cell in cells:
        if precomputed is not None:
            bin_mids, cov = precomputed[(cell.density, cell.policy)]
        else:
            bin_mids, cov = coverage_at_bin_midpoints(
                cell, bin_sec=bin_sec, sigma_threshold_m=sigma_threshold_m,
            )
        if cov.size == 0:
            continue
        cov_finite = cov[np.isfinite(cov)]
        cov_mean = float(cov_finite.mean()) if cov_finite.size else float("nan")
        any_cov = (cov > 0)
        time_with_cov_h = float(np.sum(any_cov)) * (bin_sec / 3600.0)
        # First bin where coverage drops below 50% of bin-0 value.
        if cov.size and cov[0] > 0:
            half = 0.5 * cov[0]
            below = np.where(cov < half)[0]
            half_life_h = (
                float(bin_mids[below[0]]) / 3600.0
                if below.size else float("nan")
            )
        else:
            half_life_h = float("nan")
        blocks.append(
            f"## {cell.density} / {cell.policy}\n"
            f"  mission_h = {bin_mids[-1]/3600:.1f}\n"
            f"  cov_t0    = {cov[0]:.3f}\n"
            f"  cov_mean  = {cov_mean:.3f}\n"
            f"  time_with_cov_h = {time_with_cov_h:.1f} "
            f"({100*time_with_cov_h/(bin_mids[-1]/3600 + bin_sec/3600):.0f}% of mission)\n"
            f"  half_life_h = {half_life_h:.1f}\n"
        )
        # Per-bin row: t_mid_h  cov_fraction
        rows = [
            f"    {bin_mids[i]/3600:7.2f}  {cov[i]:.4f}"
            for i in range(cov.size)
            if np.isfinite(cov[i])
        ]
        blocks.append("\n".join(rows) + "\n")
    with open(out_path, "w") as f:
        f.write("\n".join(blocks))
    print(f"  saved {out_path}", flush=True)


def chart_coverage_timeseries(
    cells: list[CellData], out_path: str,
    bin_sec: float = 3600.0,
    sigma_threshold_m: float = COVERAGE_SIGMA_THRESHOLD_M,
    precomputed: dict[tuple[str, str],
                       tuple[np.ndarray, np.ndarray]] | None = None,
) -> None:
    """One line per cell, coverage_fraction vs mission time. Vertical
    dashed lines at cycle boundaries (campaign mode only — read from
    the first drifter's `cycle_boundaries_sec` if present in the npz).
    """
    import matplotlib.pyplot as plt
    if not cells:
        return
    fig, ax = plt.subplots(figsize=(12, 6))
    cmap = plt.get_cmap("tab10")
    boundary_h: list[float] = []
    for ci, cell in enumerate(cells):
        if precomputed is not None:
            bin_mids, cov = precomputed[(cell.density, cell.policy)]
        else:
            bin_mids, cov = coverage_at_bin_midpoints(
                cell, bin_sec=bin_sec, sigma_threshold_m=sigma_threshold_m,
            )
        if cov.size == 0:
            continue
        label = f"{cell.density} / {cell.policy}"
        ax.plot(bin_mids / 3600.0, cov, "-",
                 lw=1.4, color=cmap(ci % 10), label=label, alpha=0.85)
        # Read campaign cycle boundaries from drifter 0 (all drifters
        # share the same cycle schedule per cell).
        d0 = cell.drifters[0] if cell.drifters else None
        if d0 is not None and d0.cycle_boundaries_sec is not None:
            for b_sec in d0.cycle_boundaries_sec:
                if b_sec > 0:
                    boundary_h.append(float(b_sec) / 3600.0)
    for bh in sorted(set(boundary_h)):
        ax.axvline(bh, color="gray", lw=0.6, ls="--", alpha=0.5)
    ax.set_xlabel("mission time (h)")
    ax.set_ylabel(f"patrol-area fraction with σ_event < "
                  f"{sigma_threshold_m:.0f} m")
    ax.set_title("Coverage fraction vs mission time "
                  "(forward-filter σ_pos at bin midpoint)")
    ax.set_ylim(-0.02, 1.02)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, loc="upper right", ncol=2)
    plt.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    print(f"  saved {out_path}", flush=True)
    plt.close(fig)


def chart_pdop_footprint_frames(
    cell: CellData, out_dir: str, bin_hours: float = 6.0,
    sigma_pos_source: str = "forward",
) -> None:
    """Save one PNG per `bin_hours` time bin of mission. Each frame is a
    realistic σ_event heatmap built from the LSQ Σ_post that would be
    produced if an event occurred at that grid point during that time
    bin, given each drifter's per-tick σ_pos posterior. Files named
    `t<start>h_<end>h.png`.

    The σ_pos source is configurable: "forward" uses each drifter's
    real-time PF posterior (`pf_cov_m`) — the on-node deployment-honest
    map; "smooth" uses the full-mission RTS smoother — the
    end-of-mission post-recovery upper bound.
    """
    import matplotlib.pyplot as plt
    if not cell.drifters:
        return
    t_arr = cell.drifters[0].t_sec
    t_max = float(t_arr[-1])
    bin_sec = bin_hours * 3600.0
    n_bins = int(np.ceil(t_max / bin_sec))
    for b in range(n_bins):
        t_start = b * bin_sec
        t_end = min((b + 1) * bin_sec, t_max + 1.0)
        lats, lons, sigma_event = _realistic_footprint_grid(
            cell, t_start, t_end, sigma_pos_source=sigma_pos_source,
        )
        fig, ax = plt.subplots(figsize=(8, 6))
        LON_grid, LAT_grid = np.meshgrid(lons, lats, indexing="xy")
        # Log-color, clip to [10, 10_000m] for legend stability.
        sigma_clip = np.clip(sigma_event, 10.0, 10_000.0)
        im = ax.pcolormesh(
            LON_grid, LAT_grid, np.log10(sigma_clip),
            cmap="viridis_r", shading="auto",
            vmin=1.0, vmax=4.0,
        )
        # Drifter truth positions at the MIDDLE of the time bin.
        t_mid = 0.5 * (t_start + t_end)
        for drow in cell.drifters:
            tlat, tlon = _interp_truth_at_t(drow, t_mid)
            ax.plot(tlon, tlat, "s", ms=6,
                     color="white", mec="black", mew=0.5)
            ax.plot(drow.station_lon, drow.station_lat, ".",
                     ms=4, color="red", alpha=0.6)
        cb = plt.colorbar(im, ax=ax, fraction=0.046)
        cb.set_label(
            f"log₁₀ σ_event (m), {sigma_pos_source}-σ_pos LSQ Σ_post.\n"
            "NaN where <3 drifters in range."
        )
        ax.set_xlim(LON_MIN, LON_MAX)
        ax.set_ylim(LAT_MIN, LAT_MAX)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("longitude")
        ax.set_ylabel("latitude")
        ax.set_title(
            f"{cell.density} / {cell.policy}  "
            f"({sigma_pos_source}-σ_pos)\n"
            f"σ_event at t = {int(t_start/3600)}–"
            f"{int(t_end/3600)}h "
            f"(white = drifter truth at t_mid; red = station target)"
        )
        out_path = os.path.join(
            out_dir,
            f"footprint_t{int(t_start/3600):03d}h-"
            f"{int(t_end/3600):03d}h.png",
        )
        fig.savefig(out_path, dpi=110, bbox_inches="tight")
        plt.close(fig)


def chart_per_config_map(
    cell: CellData, out_path: str,
) -> None:
    """For one cell, produce a 1×2 panel: (left) basin map with drifter
    trajectories + event truth + recon positions; (right) detection
    footprint heatmap (fraction of mission with ≥3 drifters in range
    of each grid point)."""
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))

    # Left panel: basin map.
    ax = axes[0]
    for drow in cell.drifters:
        ax.plot(drow.truth_lons, drow.truth_lats,
                 lw=0.5, alpha=0.4, color="tab:blue")
        ax.plot(drow.station_lon, drow.station_lat, "s",
                 ms=8, color="tab:blue", mec="black", mew=0.5)
    flags_b = failure_flags(cell, "b")
    clean_b = clean_mask(cell, "b", flags_b)
    insuff_b = flags_b.insufficient_detectors
    ax.scatter(
        cell.event_truth_lons[insuff_b],
        cell.event_truth_lats[insuff_b],
        s=4, color="tab:gray", alpha=0.3,
        label=f"<3 detect (n={int(insuff_b.sum())})",
    )
    ax.scatter(
        cell.event_truth_lons[clean_b],
        cell.event_truth_lats[clean_b],
        s=12, color="tab:green", alpha=0.7,
        edgecolor="black", linewidth=0.3,
        label=f"clean reconstruction (n={int(clean_b.sum())})",
    )
    failed_b = (~insuff_b & ~clean_b)
    if failed_b.any():
        ax.scatter(
            cell.event_truth_lons[failed_b],
            cell.event_truth_lats[failed_b],
            s=10, color="tab:red", alpha=0.5,
            marker="x",
            label=f"LSQ failure / out of dep (n={int(failed_b.sum())})",
        )
    ax.set_xlim(LON_MIN, LON_MAX)
    ax.set_ylim(LAT_MIN, LAT_MAX)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(
        f"{cell.density} / {cell.policy}\n"
        f"basin map: drifter tracks (blue) + events (mode b)"
    )
    ax.legend(fontsize=7, loc="lower left")
    ax.grid(alpha=0.3)

    # Right panel: detection footprint.
    ax = axes[1]
    lats_g, lons_g, footprint = _detection_footprint_grid(cell)
    LON_grid, LAT_grid = np.meshgrid(lons_g, lats_g, indexing="xy")
    # Use log color scale to expose tail dynamic range.
    pos_footprint = np.maximum(footprint, 1e-4)
    im = ax.pcolormesh(
        LON_grid, LAT_grid, np.log10(pos_footprint),
        cmap="viridis", shading="auto",
        vmin=-3, vmax=0,
    )
    for drow in cell.drifters:
        ax.plot(drow.station_lon, drow.station_lat, "s",
                 ms=6, color="white", mec="black", mew=0.5)
    cb = plt.colorbar(im, ax=ax, fraction=0.046)
    cb.set_label("log₁₀ fraction of mission ≥3 drifters in range")
    ax.set_xlim(LON_MIN, LON_MAX)
    ax.set_ylim(LAT_MIN, LAT_MAX)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(f"{cell.density} / {cell.policy}\n"
                  f"detection footprint (≥3 drifters within {int(DETECT_RANGE_M)}m)")

    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


# ---- Driver ----

def main() -> None:
    if len(sys.argv) < 2:
        print(
            "usage: _fleet_sweep_v0_analyze.py <run_dir>\n"
            "  e.g. experiments/harmonic_prototype/figures/sweep_runs/<RUN_ID>",
            file=sys.stderr,
        )
        sys.exit(1)
    run_dir = sys.argv[1]
    if not os.path.isdir(run_dir):
        print(f"ERROR: {run_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"loading {run_dir}/raw/results.npz", flush=True)
    cells = load_run(run_dir)
    print(f"  loaded {len(cells)} cells:", flush=True)
    for cell in cells:
        print(f"    {cell.density:>14}/{cell.policy:>22}: "
              f"{cell.n_events} events, {cell.n_drifters} drifters",
              flush=True)

    # Compute per-cell per-mode metrics.
    metrics: list[CellMetrics] = []
    for cell in cells:
        for mode in ("a", "b"):
            print(f"  computing metrics: {cell.density}/{cell.policy}/{mode}",
                  flush=True)
            metrics.append(compute_cell_metrics(cell, mode))

    # Output dirs.
    num_dir = os.path.join(run_dir, "numerical")
    chart_dir = os.path.join(run_dir, "charts")
    os.makedirs(num_dir, exist_ok=True)
    os.makedirs(chart_dir, exist_ok=True)

    densities = sorted({c.density for c in cells})
    policies = sorted({c.policy for c in cells})

    # Numerical tables.
    write_summary_primary(metrics, os.path.join(num_dir, "summary_primary.txt"))
    write_three_way_summary(metrics,
                              os.path.join(num_dir, "summary_three_way.txt"))
    write_anisotropy_summary(metrics,
                               os.path.join(num_dir, "summary_anisotropy.txt"))
    write_failure_modes_summary(cells,
                                  os.path.join(num_dir, "summary_failure_modes.txt"))
    write_threshold_sweep_summary(cells,
                                    os.path.join(num_dir, "summary_thresholds.txt"))
    write_drifter_quality_summary(cells,
                                    os.path.join(num_dir, "summary_drifter_quality.txt"))
    write_per_track_summary(cells,
                              os.path.join(num_dir, "summary_per_track.txt"))

    # Charts.
    chart_accuracy_heatmaps(
        metrics, densities, policies,
        os.path.join(chart_dir, "01a_accuracy_heatmaps.png"),
    )
    chart_detection_chain(
        cells, os.path.join(chart_dir, "02_detection_chain.png"))
    chart_sigma_event_cdfs(cells,
                             os.path.join(chart_dir, "03_sigma_event_cdfs.png"))
    chart_pdop_buckets(cells,
                         os.path.join(chart_dir, "05a_property_buckets_pdop.png"))
    chart_calibration_qq(cells,
                           os.path.join(chart_dir, "06_calibration_qq.png"))
    chart_failure_modes_joint(
        cells, os.path.join(chart_dir, "08_failure_modes.png"))
    chart_paired_mode_deltas(
        cells, os.path.join(chart_dir, "09_paired_mode_deltas.png"))
    chart_anisotropy_diagnostic(cells,
                                  os.path.join(chart_dir, "10_anisotropy_diagnostic.png"))
    chart_threshold_sweep(
        cells, os.path.join(chart_dir, "11_threshold_sweep.png"))
    coverage_ts = precompute_coverage_timeseries(cells)
    chart_coverage_timeseries(
        cells, os.path.join(chart_dir, "12_coverage_timeseries.png"),
        precomputed=coverage_ts)
    write_coverage_timeseries_summary(
        cells, os.path.join(num_dir, "summary_coverage_timeseries.txt"),
        precomputed=coverage_ts)

    # Per-config maps + footprint heatmaps (one PNG per cell).
    per_config_dir = os.path.join(run_dir, "per_config")
    os.makedirs(per_config_dir, exist_ok=True)
    for cell in cells:
        out_path = os.path.join(
            per_config_dir, f"{cell.density}__{cell.policy}__map.png",
        )
        print(f"  per-config map: {cell.density}/{cell.policy}",
              flush=True)
        chart_per_config_map(cell, out_path)
        # Time-binned PDOP-floor footprint frames per cell, one PNG
        # per 6h bin in a per-cell subdir.
        frames_dir = os.path.join(
            per_config_dir,
            f"{cell.density}__{cell.policy}__footprint_frames",
        )
        os.makedirs(frames_dir, exist_ok=True)
        print(f"  footprint frames (6h bins): "
              f"{cell.density}/{cell.policy}", flush=True)
        chart_pdop_footprint_frames(cell, frames_dir, bin_hours=6.0)

    print(f"\nv2 analyzer done. artifacts in {run_dir}/", flush=True)
    print(f"  numerical/  — {len(os.listdir(num_dir))} text tables",
          flush=True)
    print(f"  charts/     — {len(os.listdir(chart_dir))} PNGs",
          flush=True)
    print(f"  per_config/ — {len(os.listdir(per_config_dir))} cell maps",
          flush=True)


if __name__ == "__main__":
    main()
