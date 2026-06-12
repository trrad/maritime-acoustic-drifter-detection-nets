"""Diagnostic: verify the 5-component LayeredNoiseField RMS profile
matches the design.

Samples the layered noise at the sim's operating conditions (all 8
stations × 72 h × 10-min cadence) and verifies:
  (a) surface per-component RMS ≈ 8 cm/s (√(σ_coh² + σ_plume² +
      σ_submeso² + σ_inertial² + σ_white²) at z=0).
  (b) Deep RMS ≈ √(σ_coh² + σ_white²) ≈ 4.3 cm/s (the barotropic +
      white floor that survives below all surface-trapped layers).
  (c) Plume profile falls off sharply by the base depth (tanh, not exp).
  (d) Submeso + inertial decay as exp(-z/L_z) with L_z = 20 m.

Print-only; no figure output. ~60 s runtime.
"""

from __future__ import annotations

import time

import numpy as np  # type: ignore[import-not-found]

from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
from submesoscale import build_layered_noise_field  # type: ignore[import-not-found]


LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
MONTHS = ["2023-04"]

DEPTHS_PROBE_M = [0.5, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0, 200.0]

# 8 hand-picked stations (same as the sim driver).
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
    field = build_layered_noise_field(
        ds, lats_grid, lons_grid,
        # Defaults match the driver; σ_fc = 0.08 m/s central-SoG April.
        seed=42,
    )
    print(f"built layered field in {time.time()-t0:.1f}s")
    print(f"  expected surface per-component RMS = "
          f"{field.surface_rms_ms()*100:5.2f} cm/s")
    print(f"  expected deep    per-component RMS = "
          f"{field.deep_rms_ms()*100:5.2f} cm/s")

    # Sample at sim operating conditions: 8 stations × t ∈ [0, 72 h] at
    # 10-min cadence × each probe depth.
    t_samples = np.arange(0, 72 * 3600, 600, dtype=float)
    print(f"\nsampling {len(STATIONS)} stations × {t_samples.size} "
          f"time-steps per depth:")
    print(f"{'depth':>7}  {'RMS_u':>8}  {'RMS_v':>8}  {'RMS_tot':>8}  "
          f"{'expected':>10}")
    print("-" * 52)
    for z in DEPTHS_PROBE_M:
        us, vs = [], []
        for la, lo in STATIONS:
            for t in t_samples:
                u, v = field.sample(la, lo, z, float(t))
                if np.isfinite(u) and np.isfinite(v):
                    us.append(u)
                    vs.append(v)
        if not us:
            print(f"{z:7.1f}  (no finite samples)")
            continue
        rms_u = float(np.sqrt(np.mean(np.asarray(us) ** 2)))
        rms_v = float(np.sqrt(np.mean(np.asarray(vs) ** 2)))
        rms_per = float(np.sqrt(0.5 * (rms_u ** 2 + rms_v ** 2)))
        # Analytic expectation (independent Gaussians, variance sum):
        plume = 0.5 * (1.0 - np.tanh(
            (z - field.plume_base_m) / max(field.plume_width_m, 0.1)))
        surf = np.exp(-max(z, 0.0) / field.L_z_surf_m)
        inr = np.exp(-max(z, 0.0) / field.L_z_inertial_m)
        expected = float(np.sqrt(
            field.sigma_coh_ms ** 2
            + (plume * field.sigma_plume_ms) ** 2
            + (surf * field.sigma_submeso_ms) ** 2
            + (inr * field.sigma_inertial_ms) ** 2
            + field.sigma_white_ms ** 2
        ))
        print(f"{z:7.1f}  {rms_u*100:6.2f}cm  {rms_v*100:6.2f}cm  "
              f"{rms_per*100:6.2f}cm  {expected*100:6.2f}cm")

    # Component isolation: coh alone, plume alone, etc.
    print("\ncomponent isolation (shallow station, t=24 h):")
    la, lo = STATIONS[0]
    t = 24.0 * 3600.0
    for z in [0.5, 5.0, 10.0, 20.0, 50.0, 200.0]:
        uc, vc = field.coh.sample(la, lo, t)
        up, vp = field.plume.sample(la, lo, t)
        us, vs = field.submeso_wind.sample(la, lo, t)
        ui, vi = field.inertial.sample(la, lo, z, t)
        uw, vw = field.white.sample(la, lo, t)
        plume = 0.5 * (1.0 - np.tanh(
            (z - field.plume_base_m) / field.plume_width_m))
        surf = np.exp(-z / field.L_z_surf_m)
        print(f"  z={z:5.1f}m  "
              f"coh=({uc*100:+5.2f},{vc*100:+5.2f})  "
              f"plume·p(z)=({up*plume*100:+5.2f},{vp*plume*100:+5.2f})  "
              f"submeso·s(z)=({us*surf*100:+5.2f},{vs*surf*100:+5.2f})  "
              f"inertial(z)=({ui*100:+5.2f},{vi*100:+5.2f})  "
              f"white=({uw*100:+5.2f},{vw*100:+5.2f}) cm/s")


if __name__ == "__main__":
    main()
