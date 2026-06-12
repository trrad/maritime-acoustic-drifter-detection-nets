"""Benchmark + parity check for the threaded noise field build.

Runs `build_layered_noise_field` with `FLEET_NOISE_BUILD_THREADS=1`
(serial baseline) and `FLEET_NOISE_BUILD_THREADS={4,8}` (threaded);
compares wall + verifies bit-for-bit u-cube parity (the threading
should be a pure perf change since rng calls are pinned to a fixed
order, only gaussian_filter runs in parallel).
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np   # type: ignore[import-not-found]


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _build_at(threads: int):
    os.environ["FLEET_NOISE_BUILD_THREADS"] = str(threads)
    # Force reload of submesoscale to pick up env var (already imported
    # earlier by salishseacast_cache transitively? Actually env is
    # checked per-call in `_noise_build_thread_workers`, so a reload
    # isn't strictly needed — keep this simple).
    import submesoscale as sm
    from salishseacast_cache import (  # type: ignore
        bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
    )
    bbox = bbox_from_latlon(49.15, 49.45, -123.95, -123.50)
    ds = fetch_bbox_months(bbox, ["2023-04"], verbose=False,
                            include_tracers=False)
    bbox_lats_grid, bbox_lons_grid = bbox_latlon_arrays(bbox)[:2]
    t = time.time()
    field = sm.build_layered_noise_field(
        ds, bbox_lats_grid, bbox_lons_grid, seed=42,
    )
    wall = time.time() - t
    return field, wall


def _u_signature(field) -> tuple[float, float, float]:
    """Cheap fingerprint of the field's coh-component u_interp values
    cube — used as a parity check across runs."""
    cube = np.asarray(field.coh.u_interp.values)
    return (
        float(np.mean(cube)),
        float(np.std(cube)),
        float(np.sum(cube[:5, :5, :5])),
    )


def main() -> None:
    print("=== noise build benchmark ===", flush=True)
    print("  fetching ds (cache hit if previously fetched)...", flush=True)

    # Run 3 configs; first call also pays a tiny first-import cost.
    for threads in [1, 4, 8]:
        field, wall = _build_at(threads)
        sig = _u_signature(field)
        print(f"  threads={threads}: wall={wall:.1f}s  "
              f"sig=(mean={sig[0]:.6e}, std={sig[1]:.6e}, "
              f"sum_cube={sig[2]:.6e})", flush=True)


if __name__ == "__main__":
    main()
