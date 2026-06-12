"""Diagnostic: verify the LayeredTracerNoiseField RMS profile matches
the design (Soontiens 2017 calibration).

Samples the layered tracer noise at the sim's operating conditions
(8 stations × 72 h × 10-min cadence) and verifies:

  (a) Surface S RMS ≈ √(0.5² + 0.3² + 0.1²) = 0.59 g/kg — within
      Soontiens' reported 0.29-0.67 g/kg basin-mean range.
  (b) Deep S RMS ≈ √(0.5² + 0.1²) = 0.51 g/kg (plume contribution
      dies below the tanh halocline base).
  (c) T RMS ≈ √(0.4² + 0.05²) = 0.40 °C — middle of Soontiens'
      +0.24 to +0.48 °C range; depth-flat (no vertical structure on T).
  (d) Plume_S falls off sharply by `plume_base_m` (sanity-check the
      tanh profile, not exp).

Print-only; no figure output. ~60 s runtime — dominated by the same
padded-cube build as `_diag_layered_noise_rms.py`.
"""

from __future__ import annotations

import time

import numpy as np  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
from submesoscale import build_layered_tracer_noise_field  # type: ignore[import-not-found]


LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
MONTHS = ["2023-04"]

DEPTHS_PROBE_M = [0.5, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0, 200.0]

# 8 hand-picked stations (matches the sim driver and the velocity-noise
# diagnostic for like-for-like comparison).
STATIONS = [
    (49.3533, -123.7411), (49.3533, -123.6892),
    (49.3924, -123.7411), (49.3924, -123.6374),
    (49.3091, -123.6773), (49.2699, -123.7033),
    (49.3287, -123.7810), (49.3533, -123.5855),
]


def main() -> None:
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats_grid, lons_grid, _ = bbox_latlon_arrays(bbox)

    t0 = time.time()
    field = build_layered_tracer_noise_field(
        ds, lats_grid, lons_grid,
        seed=42,
    )
    print(f"built layered tracer-noise field in {time.time()-t0:.1f}s")
    print(f"  systematic mean S offset = {field.mean_S_coh_psu:+5.3f} g/kg "
          f"(Soontiens range -0.29..-0.67, signed)")
    print(f"  systematic mean T offset = {field.mean_T_coh_c:+5.3f} °C "
          f"(Soontiens range +0.24..+0.48, signed)")
    print(f"  surface fluctuation RMS_S (σ around mean) = "
          f"{field.surface_rms_S_psu():5.3f} g/kg")
    print(f"  deep    fluctuation RMS_S = {field.deep_rms_S_psu():5.3f} g/kg")
    print(f"           fluctuation RMS_T = {field.rms_T_c():5.3f} °C")

    t_samples = np.arange(0, 72 * 3600, 600, dtype=float)
    print(f"\nsampling {len(STATIONS)} stations × {t_samples.size} "
          f"time-steps per depth:")
    print(f"{'depth':>7}  {'RMS_T':>8}  {'RMS_S':>8}  "
          f"{'expected_S':>12}")
    print("-" * 44)
    for z in DEPTHS_PROBE_M:
        Ts, Ss = [], []
        for la, lo in STATIONS:
            for t in t_samples:
                T_n, S_n = field.sample(la, lo, z, float(t))
                if np.isfinite(T_n) and np.isfinite(S_n):
                    Ts.append(T_n)
                    Ss.append(S_n)
        if not Ts:
            print(f"{z:7.1f}  (no finite samples)")
            continue
        Ts_arr = np.asarray(Ts)
        Ss_arr = np.asarray(Ss)
        # The full bias = DC offset + fluctuation; report mean and RMS-
        # of-deviation-from-mean separately so the diagnostic verifies
        # both pieces.
        mean_T = float(Ts_arr.mean())
        mean_S = float(Ss_arr.mean())
        rms_T = float(np.sqrt(np.mean((Ts_arr - mean_T) ** 2)))
        rms_S = float(np.sqrt(np.mean((Ss_arr - mean_S) ** 2)))
        plume = 0.5 * (1.0 - np.tanh(
            (z - field.plume_base_m) / max(field.plume_width_m, 0.1)))
        expected_S = float(np.sqrt(
            field.sigma_S_coh_psu ** 2
            + (plume * field.sigma_S_plume_psu) ** 2
            + field.sigma_S_white_psu ** 2
        ))
        print(f"{z:7.1f}  T:mean={mean_T:+5.2f} σ={rms_T:5.3f}  "
              f"S:mean={mean_S:+5.2f} σ={rms_S:5.3f}  "
              f"(expected σ_S={expected_S:5.3f})")

    # Component isolation at one station to verify the plume profile is
    # tanh-shaped, not exp. coh_* is now a 1-D time series (no spatial
    # arg), so the isolation reads its scalar value at this t.
    print("\ncomponent isolation (S, shallow station, t=24 h):")
    la, lo = STATIONS[0]
    t = 24.0 * 3600.0
    s_coh = field.coh_S.sample_at_time(t)
    print(f"  basin offset (mean) = {field.mean_S_coh_psu:+.3f}  "
          f"basin fluctuation coh_S(t) = {s_coh:+.3f} g/kg")
    for z in [0.5, 5.0, 10.0, 20.0, 50.0, 200.0]:
        s_plume, _ = field.plume_S.sample(la, lo, t)
        s_white, _ = field.white_S.sample(la, lo, t)
        plume = 0.5 * (1.0 - np.tanh(
            (z - field.plume_base_m) / field.plume_width_m))
        print(f"  z={z:5.1f}m  "
              f"plume_S·p(z)={s_plume * plume:+.3f}  "
              f"white_S={s_white:+.3f} g/kg  "
              f"(p(z)={plume:.3f})")


if __name__ == "__main__":
    main()
