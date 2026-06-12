"""Fetch a deployment-scale bbox × 3 months of hourly u, v in central
Strait of Georgia. Leaves the cache populated for downstream analysis.

Bbox scale rationale:
    Central Strait of Georgia near Nanaimo–Sechelt is a realistic M1
    deployment zone: ~200 m water, away from narrow tidal passes, a
    natural ~50×200 km fleet-deployment basin. For a prototype we fetch
    a ~10×10 km cutout (~300 cells at 500 m native resolution) — enough
    to show spatial coherence of the tidal field across what would be
    a single LoRa cluster of drifters.

Time scale rationale:
    M2 and S2 differ by 0.08 cph and need ~14 days for Rayleigh
    separability; K1/O1 separability requires similar. 3 months is
    comfortable for the main 4 constituents plus the first overtides
    (M4, MS4, M6) if present. A prototype, not a full-year production
    analysis.

Wall-clock expectation:
    ~1.9 s per cell-month batched (measured empirically against the
    smoke test). 300 cells × 3 months ≈ 1700 s ≈ 28 min cold cache.
    Polite 1-s delay between monthly chunks. Resumable from cache.
"""

from __future__ import annotations

import time

from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon,
    bbox_latlon_arrays,
    cache_size_bytes,
    fetch_bbox_months,
)


# Central Strait of Georgia. Bbox roughly 10 km × 10 km.
# Centre: ~49.30°N, -123.70°W (between Vancouver and Nanaimo, deep basin).
LAT_MIN, LAT_MAX = 49.25, 49.35      # ~11 km lat
LON_MIN, LON_MAX = -123.78, -123.62  # ~11 km lon at 49°N

MONTHS = ["2023-04", "2023-05", "2023-06"]  # 3-month window


def main() -> None:
    print("=== Strait of Georgia deployment-scale fetch ===")
    print(f"lat: [{LAT_MIN}, {LAT_MAX}]  lon: [{LON_MIN}, {LON_MAX}]")
    print(f"months: {MONTHS}")
    print()

    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    print(f"grid bbox: {bbox}")
    print(f"n cells: {bbox.n_cells}")

    # Show the actual lat/lon / bathymetry extent of the chosen grid bbox.
    lats, lons, bathy = bbox_latlon_arrays(bbox)
    print(f"actual lat range: {lats.min():.4f} – {lats.max():.4f}")
    print(f"actual lon range: {lons.min():.4f} – {lons.max():.4f}")
    print(f"bathymetry range: {bathy[bathy > 0].min():.1f} m – {bathy.max():.1f} m "
          f"(wet-cell stats; {(bathy > 0).sum()} wet of {bathy.size} cells)")
    print()

    t0 = time.time()
    ds = fetch_bbox_months(bbox, MONTHS, verbose=True)
    dt = time.time() - t0

    print()
    print(f"=== summary ===")
    print(f"time steps: {ds.sizes.get('time', 0)}")
    print(f"depth levels: {ds.sizes.get('depth', 0)}")
    print(f"spatial shape: gridY={ds.sizes.get('gridY', 0)} × gridX={ds.sizes.get('gridX', 0)}")
    print(f"total wall-clock: {dt/60:.1f} min")
    print(f"cache size: {cache_size_bytes() / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
