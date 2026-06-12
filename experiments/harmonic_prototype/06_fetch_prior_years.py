"""Fetch Apr-Jun × 2018-2022 at the same 1080-cell bbox we used for 2023.

Purpose: build a multi-year Apr-Jun hindcast archive from which we can
compute various climatology models (harmonic-only, harmonic+monthly,
harmonic+weekly, harmonic+day-of-year). 2023 Apr-Jun stays as the held-
out "truth" to score each model against.

Bbox matches 03_fetch_strait_of_georgia.py exactly. 5 years × 3 months
= 15 months × ~500 s/month ≈ 75 min on a cold cache. Resumable.
"""

from __future__ import annotations

import time

from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon,
    cache_size_bytes,
    fetch_bbox_months,
)


LAT_MIN, LAT_MAX = 49.25, 49.35
LON_MIN, LON_MAX = -123.78, -123.62

PRIOR_YEARS = [2018, 2019, 2020, 2021, 2022]
MONTHS_PER_YEAR = [4, 5, 6]  # Apr, May, Jun — matching the 2023 truth window.


def main() -> None:
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    months = [f"{y}-{m:02d}" for y in PRIOR_YEARS for m in MONTHS_PER_YEAR]
    print(f"=== prior-years fetch ===")
    print(f"bbox: {bbox}")
    print(f"n cells: {bbox.n_cells}")
    print(f"months: {months}")
    print(f"total months: {len(months)}")
    print()

    t0 = time.time()
    ds = fetch_bbox_months(bbox, months, verbose=True)
    dt = time.time() - t0

    print()
    print(f"=== summary ===")
    print(f"combined time steps: {ds.sizes.get('time', 0)}")
    print(f"wall-clock: {dt/60:.1f} min")
    print(f"cache size: {cache_size_bytes() / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
