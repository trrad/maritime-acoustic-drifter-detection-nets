"""Parallel fetcher for remaining prior-year months.

Usage:
    uv run --with xarray,netCDF4,requests,numpy python \\
        experiments/harmonic_prototype/fetch_parallel.py YYYY-MM YYYY-MM ...

Spawns one Python subprocess per month. Each runs the same `fetch_bbox_months`
against the 1080-cell central-Strait-of-Georgia bbox that we've been using.
Each writes to the shared cache (deterministic hash per month → no
collision). Polite 1 s delays are per-process; the net concurrent request
rate is low.

Why not asyncio / threading: the xarray ERDDAP open_dataset call holds the
GIL during NetCDF parse, so threads don't help. Multiprocessing gives
clean isolation and no shared-state bugs.
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import time

from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon,
    cache_size_bytes,
    fetch_bbox_months,
)


LAT_MIN, LAT_MAX = 49.25, 49.35
LON_MIN, LON_MAX = -123.78, -123.62


def fetch_one_month(year_month: str) -> str:
    """Worker: fetch exactly one month and return a status line."""
    t0 = time.time()
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    try:
        fetch_bbox_months(bbox, [year_month], verbose=False)
        return f"[{year_month}] done in {time.time()-t0:.1f}s"
    except Exception as e:
        return f"[{year_month}] FAILED after {time.time()-t0:.1f}s: {e}"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: fetch_parallel.py YYYY-MM [YYYY-MM ...]")
        sys.exit(1)
    months = sys.argv[1:]

    n_workers = min(len(months), 4)  # cap at 4 concurrent — polite
    print(f"=== parallel fetch: {len(months)} months, {n_workers} workers ===")
    print(f"months: {months}")
    print()
    t0 = time.time()

    with mp.Pool(processes=n_workers) as pool:
        for status in pool.imap_unordered(fetch_one_month, months):
            print(f"  {status}", flush=True)

    print()
    print(f"total wall-clock: {(time.time()-t0)/60:.1f} min")
    print(f"cache size: {cache_size_bytes() / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
