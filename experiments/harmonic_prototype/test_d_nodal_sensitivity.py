"""Test D: how much does nodal=True vs nodal=False actually change
M2/K1/O1 amplitudes and phases for our window?

Using the already-cached 2023 Apr–Jun bbox data. Fits each cell twice
(nodal on vs off), compares per-constituent Lsmaj and g.

Answers the oceanographer persona's "nodal=False is textbook misuse"
objection with numbers: is the effect big enough to matter at our
3-month window centered mid-2023, or was it overblown?

Expected magnitudes (from astronomy):
    M2 amp modulation at the nodal cycle: ±3.7%
    O1 amp modulation: ±18.7%
    K1 amp modulation: ±11.5%
For a 3-month window in 2023 (nodal cycle is roughly 2006→2024→2043),
we're ~year 17 of the cycle — near the peak of the modulation. If
nodal correction matters anywhere, here.
"""

from __future__ import annotations

import warnings

import numpy as np  # type: ignore[import-not-found]

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import utide  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon,
    bbox_latlon_arrays,
    fetch_bbox_months,
)


LAT_MIN, LAT_MAX = 49.25, 49.35
LON_MIN, LON_MAX = -123.78, -123.62
MONTHS = ["2023-04", "2023-05", "2023-06"]
CONST = ["M2", "S2", "K1", "O1"]


def main() -> None:
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    print(f"loading cache ...")
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats, _, bathy = bbox_latlon_arrays(bbox)
    times = ds["time"].values

    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]

    # Sample 20 cells across the bbox: even grid, skipping dry.
    sample_cells: list[tuple[int, int]] = []
    step_y = max(1, n_y // 5)
    step_x = max(1, n_x // 5)
    for iy in range(0, n_y, step_y):
        for ix in range(0, n_x, step_x):
            if bathy[iy, ix] > 0:
                sample_cells.append((iy, ix))
    print(f"sampling {len(sample_cells)} cells")
    print()

    diffs_amp: dict[str, list[float]] = {c: [] for c in CONST}
    diffs_phase_deg: dict[str, list[float]] = {c: [] for c in CONST}

    for (iy, ix) in sample_cells:
        u = ds["u_ms"].isel(gridY=iy, gridX=ix, depth=0).values
        v = ds["v_ms"].isel(gridY=iy, gridX=ix, depth=0).values
        lat = float(lats[iy, ix])
        if not np.any(np.abs(u) > 1e-9):
            continue

        try:
            coef_on = utide.solve(
                times, u, v, lat=lat, constit=CONST,
                nodal=True, trend=False, method="ols",
                conf_int="MC", verbose=False,
            )
            coef_off = utide.solve(
                times, u, v, lat=lat, constit=CONST,
                nodal=False, trend=False, method="ols",
                conf_int="MC", verbose=False,
            )
        except Exception as e:
            print(f"  utide fail at ({iy},{ix}): {e}")
            continue

        for i, name in enumerate(coef_on["name"]):
            if name not in CONST:
                continue
            i_off = list(coef_off["name"]).index(name)
            amp_on = float(abs(coef_on["Lsmaj"][i]))
            amp_off = float(abs(coef_off["Lsmaj"][i_off]))
            g_on = float(coef_on["g"][i])
            g_off = float(coef_off["g"][i_off])
            if amp_on > 0:
                rel_amp_diff = (amp_on - amp_off) / amp_on * 100
                diffs_amp[name].append(rel_amp_diff)
            # Wrap phase diff to [-180, 180].
            dg = (g_on - g_off + 180) % 360 - 180
            diffs_phase_deg[name].append(dg)

    print(f"{'const':<6s}  "
          f"{'amp rel-diff (%)':<26s}  "
          f"{'phase diff (deg)':<26s}")
    print(f"{'':6s}  "
          f"{'[on−off]/on  median / mean / range':<26s}  "
          f"{'on − off  median / mean / range':<26s}")
    for name in CONST:
        a = np.array(diffs_amp[name])
        p = np.array(diffs_phase_deg[name])
        if len(a) == 0:
            print(f"{name:<6s}  (no samples)")
            continue
        print(f"{name:<6s}  "
              f"{np.median(a):+6.2f}% / {np.mean(a):+6.2f}% / "
              f"[{a.min():+5.2f}, {a.max():+5.2f}]%  "
              f"{np.median(p):+6.2f}° / {np.mean(p):+6.2f}° / "
              f"[{p.min():+5.2f}, {p.max():+5.2f}]°")

    # Interpretation helper.
    print()
    print("Interpretation:")
    print(" - amp diff reflects how the nodal amplitude factor f(t)")
    print("   differs from 1 over this window. Known astronomy bounds:")
    print("     M2 ±3.7%, S2 0%, K1 ±11.5%, O1 ±18.7%.")
    print(" - phase diff reflects the astronomical argument u(t).")
    print(" - Any observed diff larger than 0.1% / 1° is a real signal")
    print("   that nodal correction matters; smaller means it doesn't")
    print("   for this specific window and consumers can drop it.")


if __name__ == "__main__":
    main()
