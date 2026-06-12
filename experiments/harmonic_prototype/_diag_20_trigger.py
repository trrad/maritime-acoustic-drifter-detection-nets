"""Diagnostic: trace one station's PF spread + surface-trigger timeline
in both no_learn and field_learn modes, to explain why surf counts
diverge 60× between the two modes."""

from __future__ import annotations

import time
import numpy as np  # type: ignore[import-not-found]

import importlib
m = importlib.import_module("20_single_node_field_learning")


def main() -> None:
    from salishseacast_cache import bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months
    from submesoscale import build_multiscale_noise_field
    from truth_field import build_truth_field

    bbox = bbox_from_latlon(m.LAT_MIN, m.LAT_MAX, m.LON_MIN, m.LON_MAX)
    ds = fetch_bbox_months(bbox, m.MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    truth = build_truth_field(ds, lats_grid, lons_grid, m.DEFAULT_DEPTH_SET)

    sigma_slow = m.SIGMA_FORECAST_MS * np.sqrt(m.NOISE_SLOW_FRACTION)
    sigma_fast = m.SIGMA_FORECAST_MS * np.sqrt(1.0 - m.NOISE_SLOW_FRACTION)
    print("building noise...")
    t0 = time.time()
    noise_field = build_multiscale_noise_field(
        ds, lats_grid, lons_grid, m.DEFAULT_DEPTH_SET,
        sigma_fast_ms=sigma_fast, sigma_slow_ms=sigma_slow,
        spatial_sigma_cells_fast=m.NOISE_SPATIAL_CELLS_FAST,
        temporal_sigma_hours_fast=m.NOISE_TEMPORAL_HOURS_FAST,
        spatial_sigma_cells_slow=m.NOISE_SPATIAL_CELLS_SLOW,
        temporal_sigma_hours_slow=m.NOISE_TEMPORAL_HOURS_SLOW,
        seed=42,
    )
    print(f"  noise built in {time.time()-t0:.1f}s")

    # Pick a known-good station (station 2 from the aborted run).
    s_lat = float(truth.lat_axis[10])  # INTERIOR_MARGIN + 0
    s_lon = float(truth.lon_axis[45])  # roughly centre
    s_bathy = float(bathy_grid[10, 45])
    d_set = m.depth_set_for_bathy(s_bathy)
    print(f"station ({s_lat:.4f}, {s_lon:.4f}) bathy={s_bathy:.0f}m")

    # Trace a short run (24h) in no_learn vs field_learn at this station.
    # Instrument by monkey-patching run_station to print spread/depth/surface.
    for mode, learning_on in [("no_learn", False), ("field_learn", True)]:
        print(f"\n=== {mode} ===")
        prior = m.FieldLearningPrior.build(
            truth, noise_field, m.LAT_MIN, m.LAT_MAX, m.LON_MIN, m.LON_MAX,
            [interp.actual_depth_m for _, interp in sorted(truth.interps.items())],
            m.BIAS_GRID_SPACING_KM, learning_on=learning_on,
        )
        # Instrumented mini-run: 24h, identical structure but log every 30 min.
        from ballast_controller import StationKeeper
        from ballast_dynamics import BallastState, set_setpoint, step
        from truth_field import distance_m as dist_m_fn

        keeper = StationKeeper(
            station_lat=s_lat, station_lon=s_lon,
            available_depths_m=d_set,
            lookahead_sec=m.LOOKAHEAD_SEC,
            knowledge=prior,
        )
        rng = np.random.default_rng(2000)
        state = BallastState(lat=s_lat, lon=s_lon, depth_m=m.INITIAL_DEPTH_M,
                              depth_setpoint_m=m.INITIAL_DEPTH_M)
        pf = m.PF2D.init(s_lat, s_lon, m.PF_INIT_SIGMA_M, m.PF_N_PARTICLES, seed=2000)

        surface_events = 0
        last_surface_t = 0.0
        last_decision = -m.CONTROL_CADENCE_SEC
        last_lora = -m.LORA_CADENCE_SEC
        in_surface_dwell = False
        surface_dwell_end_t = -1.0
        anchor_latlons = [m.offsets_km_to_latlon(s_lat, s_lon, dn, de)
                          for (dn, de) in m.ANCHOR_OFFSETS_KM]

        def dyn_current(ts, la, lo, d):
            return truth.sample(la, lo, d, ts)

        n_steps = int(24 * 3600 / m.DT_SEC)
        t_sec = 0.0
        for i in range(n_steps):
            # Surface policy.
            if in_surface_dwell:
                if t_sec >= surface_dwell_end_t:
                    in_surface_dwell = False
                else:
                    state = set_setpoint(state, 0.5)
            if not in_surface_dwell:
                spread = pf.spread_m()
                tsu = t_sec - last_surface_t
                trigger = ((spread > m.SURFACE_UNCERTAINTY_THRESHOLD_M)
                           or (tsu >= m.SURFACE_MAX_INTERVAL_H * 3600.0))
                if trigger:
                    in_surface_dwell = True
                    surface_dwell_end_t = t_sec + m.SURFACE_DWELL_H * 3600.0
                    state = set_setpoint(state, 0.5)
                    surface_events += 1
                    last_surface_t = t_sec   # reset always, not just on field_learn
                elif t_sec - last_decision >= m.CONTROL_CADENCE_SEC - 1e-6:
                    pml, pmo = pf.mean()
                    chosen, _ = keeper.choose_depth(
                        state.lat, state.lon, t_sec,
                        perceived_lat=pml, perceived_lon=pmo,
                    )
                    state = set_setpoint(state, chosen)
                    last_decision = t_sec

            state = step(state, t_sec, m.DT_SEC, current_at=dyn_current,
                          w_z_max_ms=m.W_Z_MAX_MS)
            pf.predict(t_sec, m.DT_SEC, prior, state.depth_m)
            t_sec += m.DT_SEC

            # LoRa ranging.
            now_at_surface = state.depth_m <= m.LORA_MAX_DEPTH_M
            if now_at_surface and t_sec - last_lora >= m.LORA_CADENCE_SEC - 1e-6:
                for alat, alon in anchor_latlons:
                    tr = dist_m_fn(state.lat, state.lon, alat, alon)
                    z = tr + rng.normal(0, m.LORA_SIGMA_M)
                    pf.update_range(alat, alon, z, m.LORA_SIGMA_M)
                pf.maybe_resample(rng)
                last_lora = t_sec

            # Log every 30 min.
            if i % 3 == 0:
                ml, mo = pf.mean()
                pf_err = dist_m_fn(state.lat, state.lon, ml, mo)
                dist_station = dist_m_fn(state.lat, state.lon, s_lat, s_lon)
                print(f"  t={t_sec/3600:4.1f}h  depth={state.depth_m:5.1f}m  "
                      f"surf_dwell={'Y' if in_surface_dwell else ' '}  "
                      f"spread={pf.spread_m():6.0f}m  PFerr={pf_err:6.0f}m  "
                      f"dist_stn={dist_station:6.0f}m  "
                      f"surf_events={surface_events}")

        print(f"  TOTAL surface_events over 24h: {surface_events}")


if __name__ == "__main__":
    main()
