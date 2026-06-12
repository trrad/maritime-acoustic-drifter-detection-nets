"""Progressive degradation of current-field knowledge for Phase B.

Tiers:
  B0 — TruthKnowledge: perfect truth (same as Phase A baseline).
  B1 — SpatiallySmoothedTruth: truth after a 2D Gaussian blur of each
       depth-level current cube, approximating coarser effective resolution.
  B2 — TemporallySmoothedTruth: truth after a 6h rolling time-average at
       each cell + depth. Tests instantaneous vs smoothed knowledge.
  B3 — HistoricalPriorKnowledge: a different year's same-day-of-year cell
       value, pulled from the prior-years cache. No harmonic extraction —
       raw sample from the same calendar date in a different year. (Plan
       says "use 2022 as prior for 2023"; if a month is missing from cache
       we fall back to the nearest available prior year automatically.)
  B4 — PF-estimated: see 11_station_keeping_degraded.py. Not implemented
       here because the PF needs truth-sensor glue that belongs in the
       driver.

Each tier implements the same interface: get_current_at(lat, lon, depth_m,
t_sec_since_truth_t0). t_sec is always measured against the truth window's
t0 for consistency with Phase A's harness — internal mapping to the
prior-years time axis happens inside the prior source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]
from scipy.interpolate import RegularGridInterpolator  # type: ignore[import-not-found]
from scipy.ndimage import gaussian_filter  # type: ignore[import-not-found]

from truth_field import TruthField, build_truth_field  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# B0 — truth
# ---------------------------------------------------------------------------

@dataclass
class TruthKnowledge:
    """Perfect knowledge — identical to Phase A's PerfectKnowledge."""

    truth: TruthField

    def get_current_at(
        self, lat: float, lon: float, depth_m: float, t_sec: float,
    ) -> tuple[float, float]:
        return self.truth.sample(lat, lon, depth_m, t_sec)


# ---------------------------------------------------------------------------
# B1 — spatially smoothed truth
# ---------------------------------------------------------------------------

def build_spatially_smoothed(
    ds: xr.Dataset,
    bbox_lats_grid: np.ndarray,
    bbox_lons_grid: np.ndarray,
    target_depths_m: list[float],
    blur_sigma_m: float = 1000.0,
) -> TruthField:
    """Return a TruthField whose per-depth cubes have been Gaussian-smoothed
    in (gridY, gridX) by a kernel of σ ≈ blur_sigma_m / cell_size.

    SalishSeaCast cells are ~500 m. Default σ=1000 m → ~2 cells, effective
    smoothing radius ≈ 2 km. Temporal dimension is left alone.
    """
    # Rough cell spacing from lat axis step.
    lat_row_means = np.asarray(bbox_lats_grid.mean(axis=1), dtype=float)
    lat_row_means_sorted = np.sort(lat_row_means)
    lat_step_deg = float(np.abs(np.diff(lat_row_means_sorted)).mean())
    cell_m = lat_step_deg * 111_320.0
    sigma_cells = max(blur_sigma_m / cell_m, 0.5)

    depth_values = ds["depth"].values
    time_values = ds["time"].values
    t0 = time_values[0]
    times_sec = ((time_values - t0) / np.timedelta64(1, "s")).astype(float)

    lat_axis = bbox_lats_grid.mean(axis=1)
    lon_axis = bbox_lons_grid.mean(axis=0)
    flip_lat = lat_axis[0] > lat_axis[-1]
    flip_lon = lon_axis[0] > lon_axis[-1]
    if flip_lat:
        lat_axis = lat_axis[::-1]
    if flip_lon:
        lon_axis = lon_axis[::-1]

    from truth_field import DepthInterp  # local import; prevents cycles
    interps: dict[float, DepthInterp] = {}
    for target in target_depths_m:
        k = int(np.argmin(np.abs(depth_values - target)))
        actual = float(depth_values[k])
        u_cube = np.array(ds["u_ms"].isel(depth=k).values)
        v_cube = np.array(ds["v_ms"].isel(depth=k).values)
        # Per-timestep 2D Gaussian blur.
        for ti in range(u_cube.shape[0]):
            u_cube[ti] = gaussian_filter(u_cube[ti], sigma=sigma_cells, mode="nearest")
            v_cube[ti] = gaussian_filter(v_cube[ti], sigma=sigma_cells, mode="nearest")
        if flip_lat:
            u_cube = u_cube[:, ::-1, :]
            v_cube = v_cube[:, ::-1, :]
        if flip_lon:
            u_cube = u_cube[:, :, ::-1]
            v_cube = v_cube[:, :, ::-1]
        u_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), u_cube,
            bounds_error=False, fill_value=np.nan,
        )
        v_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), v_cube,
            bounds_error=False, fill_value=np.nan,
        )
        interps[target] = DepthInterp(u=u_i, v=v_i, actual_depth_m=actual)

    return TruthField(
        interps=interps, times_sec=times_sec, t0=t0,
        lat_axis=lat_axis, lon_axis=lon_axis,
    )


@dataclass
class SpatiallySmoothedTruth:
    """B1: current field blurred to ~coarser effective spatial resolution."""

    field: TruthField

    def get_current_at(
        self, lat: float, lon: float, depth_m: float, t_sec: float,
    ) -> tuple[float, float]:
        return self.field.sample(lat, lon, depth_m, t_sec)


# ---------------------------------------------------------------------------
# B2 — temporally smoothed truth
# ---------------------------------------------------------------------------

def build_temporally_smoothed(
    ds: xr.Dataset,
    bbox_lats_grid: np.ndarray,
    bbox_lons_grid: np.ndarray,
    target_depths_m: list[float],
    window_hours: int = 6,
) -> TruthField:
    """Return a TruthField with a rolling (centered) time-mean applied to
    each depth-level cube. Spatial dims are left alone.
    """
    depth_values = ds["depth"].values
    time_values = ds["time"].values
    t0 = time_values[0]
    times_sec = ((time_values - t0) / np.timedelta64(1, "s")).astype(float)

    lat_axis = bbox_lats_grid.mean(axis=1)
    lon_axis = bbox_lons_grid.mean(axis=0)
    flip_lat = lat_axis[0] > lat_axis[-1]
    flip_lon = lon_axis[0] > lon_axis[-1]
    if flip_lat:
        lat_axis = lat_axis[::-1]
    if flip_lon:
        lon_axis = lon_axis[::-1]

    def rolling_mean(cube: np.ndarray, w: int) -> np.ndarray:
        # cube is (time, lat, lon). Centered rolling mean across axis=0,
        # reflective at boundaries.
        n_t = cube.shape[0]
        out = np.empty_like(cube)
        half = w // 2
        for i in range(n_t):
            lo = max(0, i - half)
            hi = min(n_t, i + half + 1)
            out[i] = cube[lo:hi].mean(axis=0)
        return out

    from truth_field import DepthInterp
    interps: dict[float, DepthInterp] = {}
    for target in target_depths_m:
        k = int(np.argmin(np.abs(depth_values - target)))
        actual = float(depth_values[k])
        u_cube = rolling_mean(np.array(ds["u_ms"].isel(depth=k).values), window_hours)
        v_cube = rolling_mean(np.array(ds["v_ms"].isel(depth=k).values), window_hours)
        if flip_lat:
            u_cube = u_cube[:, ::-1, :]
            v_cube = v_cube[:, ::-1, :]
        if flip_lon:
            u_cube = u_cube[:, :, ::-1]
            v_cube = v_cube[:, :, ::-1]
        u_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), u_cube,
            bounds_error=False, fill_value=np.nan,
        )
        v_i = RegularGridInterpolator(
            (times_sec, lat_axis, lon_axis), v_cube,
            bounds_error=False, fill_value=np.nan,
        )
        interps[target] = DepthInterp(u=u_i, v=v_i, actual_depth_m=actual)

    return TruthField(
        interps=interps, times_sec=times_sec, t0=t0,
        lat_axis=lat_axis, lon_axis=lon_axis,
    )


@dataclass
class TemporallySmoothedTruth:
    """B2: current field time-averaged over N hours."""

    field: TruthField

    def get_current_at(
        self, lat: float, lon: float, depth_m: float, t_sec: float,
    ) -> tuple[float, float]:
        return self.field.sample(lat, lon, depth_m, t_sec)


# ---------------------------------------------------------------------------
# B3 — historical prior (same calendar date in a different year)
# ---------------------------------------------------------------------------

@dataclass
class HistoricalPriorKnowledge:
    """B3: read a different year's current at the same day-of-year.

    `prior_field` is built from a prior year's Apr–Jun cache. We map a
    query t_sec (since truth t0) to a prior_t_sec by shifting by the
    year gap.
    """

    prior_field: TruthField
    year_gap_sec: float  # truth_t0_year - prior_t0_year in seconds

    @staticmethod
    def from_datasets(
        prior_ds: xr.Dataset,
        truth_t0: np.datetime64,
        bbox_lats_grid: np.ndarray,
        bbox_lons_grid: np.ndarray,
        target_depths_m: list[float],
    ) -> "HistoricalPriorKnowledge":
        prior_field = build_truth_field(
            prior_ds, bbox_lats_grid, bbox_lons_grid, target_depths_m,
        )
        # Days between truth t0 and prior t0, measured in seconds.
        gap_ns = (truth_t0 - prior_field.t0).astype("timedelta64[ns]").astype(np.int64)
        gap_sec = float(gap_ns) / 1e9
        return HistoricalPriorKnowledge(prior_field=prior_field, year_gap_sec=gap_sec)

    def get_current_at(
        self, lat: float, lon: float, depth_m: float, t_sec: float,
    ) -> tuple[float, float]:
        # t_sec is on the truth axis; shift back to the prior-year axis.
        prior_t = t_sec + self.year_gap_sec
        return self.prior_field.sample(lat, lon, depth_m, prior_t)


def available_prior_months(candidate_months: Sequence[str], bbox_key_probe) -> list[str]:
    """Return the subset of candidate_months for which the cache has data.

    `bbox_key_probe` is a no-arg callable that returns Path for a month.
    """
    return [m for m in candidate_months if bbox_key_probe(m).exists()]
