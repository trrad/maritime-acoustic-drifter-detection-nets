"""Shared scaffolding for `_diag_*.py` worker pools.

The `_diag_site_authority.py`, `_diag_sog_site_survey.py`, and any
future `_diag_*` script that needs the NEMO truth field + layered
noise (but no tracer field) over the SoG bbox import from here.
Reduces three copies of `_RealCurrents`, `_init_worker`, and the
`_summarise(dists)` helper to one.

NOT used by `_fleet_sim_v0._init_worker`: that worker also loads the
tracer field for CTD observations and the layered tracer noise. The
diagnostics here are pure dynamics + control authority, so they skip
the tracer stack — meaningful compute saving on per-worker init.
"""

from __future__ import annotations

import time
from multiprocessing import current_process

import numpy as np  # type: ignore[import-not-found]


# SoG bbox + canonical depth ladder. These match `_fleet_sim_v0`'s
# constants of the same name; if a future diagnostic targets a
# different region, parameterize at that point.
LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]


class RealCurrents:
    """NEMO truth + layered noise wrapper. Same shape as the per-worker
    truth source the fleet sim uses, minus the tracer fields."""

    def __init__(self, nemo, noise):
        self.nemo = nemo
        self.noise = noise

    def sample(self, lat, lon, depth_m, t_sec):
        ut, vt = self.nemo.sample(lat, lon, depth_m, t_sec)
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, depth_m, t_sec)
        return ut + un, vt + vn

    def sample_batched(self, lats, lons, depths, t_sec):
        ut, vt = self.nemo.sample_batched(lats, lons, depths, t_sec)
        # Truth NaN propagates → particle off-domain. Noise NaN already
        # zero-clamped in StationaryField.sample_batched.
        un, vn = self.noise.sample_batched(lats, lons, depths, t_sec)
        return ut + un, vt + vn

    def get_current_at(self, lat, lon, depth_m, t_sec):
        return self.sample(lat, lon, depth_m, t_sec)


def init_diag_worker(worker_state: dict) -> None:
    """Build truth + noise once per worker. `worker_state` is the
    caller's per-worker cache dict (e.g., the module-level `_W` in
    each diagnostic). Populated keys: `nemo`, `noise`, `bathy_grid`.
    """
    from salishseacast_cache import (  # type: ignore[import-not-found]
        bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
    )
    from submesoscale import build_layered_noise_field  # type: ignore[import-not-found]
    from truth_field import build_truth_field  # type: ignore[import-not-found]

    label = current_process().name
    t0 = time.time()
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, ["2023-04"], verbose=False,
                            include_tracers=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    nemo = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    noise = build_layered_noise_field(ds, lats_grid, lons_grid, seed=42)
    worker_state["nemo"] = nemo
    worker_state["noise"] = noise
    worker_state["bathy_grid"] = bathy_grid
    print(f"[{label}] init done ({time.time() - t0:.1f}s)", flush=True)


def summarise_dists(dists: np.ndarray) -> dict:
    """Per-trajectory distance summary used by both diagnostics: forward-
    fills NaNs from the last finite value (= drifter went off-domain;
    score with the last in-bounds reading rather than dropping the run)
    then computes mean / max / coverage percentiles."""
    valid = np.isfinite(dists)
    if not valid.all():
        last = np.where(valid)[0]
        if len(last):
            dists = np.where(valid, dists, dists[last[-1]])
        else:
            dists = np.full_like(dists, np.inf)
    return {
        "mean": float(np.nanmean(dists)),
        "max": float(np.nanmax(dists)),
        "pct500": float((dists <= 500.0).mean() * 100),
        "pct1500": float((dists <= 1500.0).mean() * 100),
        "pct3000": float((dists <= 3000.0).mean() * 100),
    }
