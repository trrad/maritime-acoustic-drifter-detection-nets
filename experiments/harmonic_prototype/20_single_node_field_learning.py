"""Single-node real-physics sim, built on 17's foundation, with:

  * Multi-timescale forecast-error prior (fast chop + slow persistent
    bias) calibrated to published regional-forecast skill (~20 cm/s RMS
    at 24–66h lead).
  * Field-resolved bias learning — correction is a 2D grid per depth,
    not a scalar. Updated from observed drift residuals at each surface
    event, distributed across the cells the node actually visited.
  * Dynamic surfacing policy — surface when PF particle spread exceeds
    a threshold OR a max-interval cap hits, not a fixed schedule. The
    node decides when a position fix is worth the surfacing cost.
  * Grid sweep across many stations, not just 6.

Compared at each station:
  - BASELINE: perfect-knowledge truth controller (Phase A ceiling).
  - NO_LEARN: PF runs with noisy prior; no bias learning.
  - FIELD_LEARN: PF runs with noisy prior + field-resolved bias learning.

Output: figures/23_field_learning_single_node.png

σ_pf emerges from the physics (LoRa σ=20m per anchor × 3 anchors × many
pings at surface + dead-reckon error growth between). Not a free knob.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from ballast_controller import PerfectKnowledge, StationKeeper  # type: ignore[import-not-found]
from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
from salishseacast_cache import (  # type: ignore[import-not-found]
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
from submesoscale import build_multiscale_noise_field  # type: ignore[import-not-found]
from truth_field import (  # type: ignore[import-not-found]
    EARTH_R_M, build_truth_field, distance_m, lat_lon_step_from_velocity,
)


# --- Domain: expanded 60x60 km bbox with 2023-04 cached ---
LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
MONTHS = ["2023-04"]

# --- Grid sweep ---
STRIDE_Y = 15
STRIDE_X = 15
INTERIOR_MARGIN_Y = 10
INTERIOR_MARGIN_X = 10
MIN_BATHY_M = 60.0
CAP_DEPTH_MARGIN = 0.8
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]

# --- Sim ---
RUN_HOURS = 72
DT_SEC = 600.0                    # 10-min tick (finer than 1h so bias paths are well-resolved)
CONTROL_CADENCE_SEC = 1800.0
LOOKAHEAD_SEC = 1800.0
W_Z_MAX_MS = 0.1
INITIAL_DEPTH_M = 10.0

# --- Forecast-error prior (calibrated to published regional-forecast skill) ---
# References: Yang et al. 2020 (PNNL-30448, Salish Sea FVCOM vs 135 ADCPs),
# Idžanović et al. 2023 (Barents-2.5 EPS, coastal operational forecast),
# Oldford et al. 2025 (HOTSSea NEMO). Summary in
# docs/reference/forecast_error_validation_notes.md.
SIGMA_FORECAST_MS = 0.20          # 20 cm/s — matches PNNL Salish Sea hindcast + Idžanović
NOISE_SLOW_FRACTION = 0.75
# Slow component: wind bias, freshet, mesoscale — very long decorrelation.
# Idžanović: error grows only 0.3 cm/s from +24h to +66h → τ ≳ 36h.
NOISE_SPATIAL_CELLS_SLOW = 10.0    # ~5 km at 500m grid
NOISE_TEMPORAL_HOURS_SLOW = 36.0
# Fast component: submesoscale / unresolved chop — short everywhere.
NOISE_SPATIAL_CELLS_FAST = 2.0     # ~1 km
NOISE_TEMPORAL_HOURS_FAST = 3.0

# --- Dynamic surfacing policy ---
SURFACE_UNCERTAINTY_THRESHOLD_M = 500.0   # surface when PF spread > this
SURFACE_MAX_INTERVAL_H = 12.0              # hard cap: never go this long without fix
SURFACE_DWELL_H = 0.5                      # how long to stay up once there
LORA_MAX_DEPTH_M = 1.0
LORA_CADENCE_SEC = 60.0
LORA_SIGMA_M = 20.0

# --- Anchors ---
ANCHOR_OFFSETS_KM = [(+5.0, +5.0), (-5.0, +5.0), (0.0, -6.0)]

# --- PF ---
PF_N_PARTICLES = 400
PF_INIT_SIGMA_M = 20.0

# --- Field-learning ---
BIAS_GRID_SPACING_KM = 3.0        # ~3 km cells for the learned bias field
BIAS_EMA_ALPHA = 0.3              # per-observation EMA rate

# --- Metrics ---
ROUGH_ENVELOPE_M = 3000.0
ENVELOPES_M = [500.0, 1000.0, 2000.0, 4000.0, 6000.0]

FIG_DIR = Path(__file__).parent / "figures"


# ---------------------------------------------------------------------------
# Noisy-prior with field-resolved bias learning
# ---------------------------------------------------------------------------

@dataclass
class FieldLearningPrior:
    """Prior = truth + multiscale noise + learned-bias-field.

    The learned bias is a 3D array indexed by coarse (lat, lon, depth)
    bins. Updated by distributing drift residuals to visited cells.
    """
    truth: "object"
    noise: "object"
    lat_edges: np.ndarray       # ascending
    lon_edges: np.ndarray       # ascending
    depth_values: list[float]   # snapped depth levels
    bias_u: np.ndarray          # (n_lat_bins, n_lon_bins, n_depths)
    bias_v: np.ndarray
    learning_on: bool = True

    @staticmethod
    def build(truth, noise, lat_min, lat_max, lon_min, lon_max,
              depth_values, spacing_km, learning_on=True):
        cos_lat_mean = np.cos(np.deg2rad(0.5 * (lat_min + lat_max)))
        dlat = spacing_km * 1000.0 / EARTH_R_M
        dlon = spacing_km * 1000.0 / (EARTH_R_M * cos_lat_mean)
        lat_edges = np.arange(lat_min, lat_max + dlat, dlat)
        lon_edges = np.arange(lon_min, lon_max + dlon, dlon)
        n_lat_bins = len(lat_edges) - 1
        n_lon_bins = len(lon_edges) - 1
        n_depths = len(depth_values)
        return FieldLearningPrior(
            truth=truth, noise=noise,
            lat_edges=lat_edges, lon_edges=lon_edges,
            depth_values=list(depth_values),
            bias_u=np.zeros((n_lat_bins, n_lon_bins, n_depths)),
            bias_v=np.zeros((n_lat_bins, n_lon_bins, n_depths)),
            learning_on=learning_on,
        )

    def _cell(self, lat, lon, depth_m):
        i = int(np.clip(np.searchsorted(self.lat_edges, lat) - 1,
                         0, self.bias_u.shape[0] - 1))
        j = int(np.clip(np.searchsorted(self.lon_edges, lon) - 1,
                         0, self.bias_u.shape[1] - 1))
        k = int(np.argmin([abs(d - depth_m) for d in self.depth_values]))
        return i, j, k

    def get_current_at(
        self, lat: float, lon: float, depth_m: float, t_sec: float,
    ) -> tuple[float, float]:
        ut, vt = self.truth.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        i, j, k = self._cell(lat, lon, depth_m)
        return ut + un + self.bias_u[i, j, k], vt + vn + self.bias_v[i, j, k]

    def prior_without_learned_bias(self, lat, lon, depth_m, t_sec):
        ut, vt = self.truth.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, depth_m, t_sec)  # type: ignore[attr-defined]
        return ut + un, vt + vn

    def update_from_leg(
        self,
        pos_start_latlon: tuple[float, float],
        pos_end_latlon: tuple[float, float],
        t_start: float, t_end: float,
        path_samples: list[tuple[float, float, float]],  # (t_sec, lat, lon) *approximate*
        depth_samples: list[tuple[float, float]],         # (t_sec, depth_m)
    ) -> None:
        """Given observed start / end positions of a submerged leg, update
        the learned-bias field in each cell the node visited, weighted
        by time spent."""
        if not self.learning_on:
            return
        dt_total = t_end - t_start
        if dt_total <= 0:
            return

        # Observed displacement (m).
        p0_lat, p0_lon = pos_start_latlon
        p1_lat, p1_lon = pos_end_latlon
        cos_lat = np.cos(np.deg2rad(0.5 * (p0_lat + p1_lat)))
        obs_dy_m = (p1_lat - p0_lat) * EARTH_R_M
        obs_dx_m = (p1_lon - p0_lon) * EARTH_R_M * cos_lat

        # Predicted displacement from current prior+bias, integrating along
        # the approximate path the node took. Uses dead-reckoned positions
        # from path_samples (which are PF-mean-approximate, not truth).
        pred_dx_m = 0.0
        pred_dy_m = 0.0
        cell_time: dict[tuple[int, int, int], float] = {}
        for n in range(len(path_samples) - 1):
            ti, lat_i, lon_i = path_samples[n]
            tj, _, _ = path_samples[n + 1]
            dt = tj - ti
            # Find depth at this step (nearest in time).
            di = _nearest_depth_at(depth_samples, ti)
            u, v = self.get_current_at(lat_i, lon_i, di, ti)
            if not (np.isfinite(u) and np.isfinite(v)):
                continue
            pred_dx_m += u * dt
            pred_dy_m += v * dt
            cell = self._cell(lat_i, lon_i, di)
            cell_time[cell] = cell_time.get(cell, 0.0) + dt

        resid_u = (obs_dx_m - pred_dx_m) / dt_total
        resid_v = (obs_dy_m - pred_dy_m) / dt_total

        # Distribute residual velocity to visited cells, weighted by fraction
        # of leg-time spent in each.
        for cell, t_in in cell_time.items():
            w = t_in / dt_total
            i, j, k = cell
            self.bias_u[i, j, k] += BIAS_EMA_ALPHA * w * resid_u
            self.bias_v[i, j, k] += BIAS_EMA_ALPHA * w * resid_v


def _nearest_depth_at(depth_samples: list[tuple[float, float]], t: float) -> float:
    best = min(depth_samples, key=lambda d: abs(d[0] - t))
    return best[1]


# ---------------------------------------------------------------------------
# PF (same structure as 17 but slightly cleaner)
# ---------------------------------------------------------------------------

@dataclass
class PF2D:
    lats: np.ndarray
    lons: np.ndarray
    weights: np.ndarray

    @staticmethod
    def init(mean_lat, mean_lon, sigma_m, n, seed=0):
        rng = np.random.default_rng(seed)
        dlat = rng.normal(0, sigma_m / EARTH_R_M, n)
        dlon = rng.normal(0, sigma_m / (EARTH_R_M * np.cos(np.deg2rad(mean_lat))), n)
        return PF2D(lats=mean_lat + dlat, lons=mean_lon + dlon,
                    weights=np.full(n, 1.0 / n))

    def mean(self):
        return float(np.sum(self.lats * self.weights)), float(
            np.sum(self.lons * self.weights))

    def spread_m(self):
        """Weighted stdev of particle positions in meters."""
        ml, mo = self.mean()
        cos_lat = np.cos(np.deg2rad(ml))
        dy = (self.lats - ml) * EARTH_R_M
        dx = (self.lons - mo) * EARTH_R_M * cos_lat
        var = float(np.sum(self.weights * (dx**2 + dy**2)))
        return float(np.sqrt(var))

    def ess(self):
        return float(1.0 / np.sum(self.weights**2))

    def predict(self, t_sec, dt_sec, prior, depth_m):
        for i in range(len(self.lats)):
            u, v = prior.get_current_at(self.lats[i], self.lons[i], depth_m, t_sec)
            if not (np.isfinite(u) and np.isfinite(v)):
                u, v = 0.0, 0.0
            dlat, dlon = lat_lon_step_from_velocity(u, v, self.lats[i], dt_sec)
            self.lats[i] += dlat
            self.lons[i] += dlon

    def update_range(self, alat, alon, z_range, sigma_m):
        cos_lat = np.cos(np.deg2rad(alat))
        dlat_m = (self.lats - alat) * EARTH_R_M
        dlon_m = (self.lons - alon) * EARTH_R_M * cos_lat
        dist = np.sqrt(dlat_m**2 + dlon_m**2)
        resid = dist - z_range
        log_w = -0.5 * (resid**2) / sigma_m**2
        log_w -= log_w.max()
        w = np.exp(log_w) * self.weights
        s = w.sum()
        if s > 0:
            self.weights = w / s

    def maybe_resample(self, rng):
        n = len(self.lats)
        if self.ess() >= n / 2:
            return
        pos = (np.arange(n) + rng.uniform(0, 1, n)) / n
        cum = np.cumsum(self.weights)
        idx = np.clip(np.searchsorted(cum, pos), 0, n - 1)
        self.lats = self.lats[idx].copy()
        self.lons = self.lons[idx].copy()
        self.weights = np.full(n, 1.0 / n)


# ---------------------------------------------------------------------------
# Run a station
# ---------------------------------------------------------------------------

def offsets_km_to_latlon(ref_lat, ref_lon, dn_km, de_km):
    cos_lat = np.cos(np.deg2rad(ref_lat))
    return (ref_lat + dn_km * 1000.0 / EARTH_R_M,
            ref_lon + de_km * 1000.0 / (EARTH_R_M * cos_lat))


def depth_set_for_bathy(bathy_m):
    max_allowed = min(50.0, bathy_m * CAP_DEPTH_MARGIN)
    return [d for d in DEFAULT_DEPTH_SET if d <= max_allowed]


def run_station(
    truth, prior_source,             # object with .get_current_at; may be FieldLearningPrior
    station_lat, station_lon,
    depth_set,
    mode: str,                       # "baseline" | "no_learn" | "field_learn"
    seed: int,
) -> dict:
    rng = np.random.default_rng(seed)
    anchor_latlons = [offsets_km_to_latlon(station_lat, station_lon, dn, de)
                      for (dn, de) in ANCHOR_OFFSETS_KM]

    keeper = StationKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=depth_set,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=prior_source,
    )

    def dyn_current(t_sec, lat, lon, depth_m):
        return truth.sample(lat, lon, depth_m, t_sec)

    state = BallastState(
        lat=station_lat, lon=station_lon,
        depth_m=INITIAL_DEPTH_M, depth_setpoint_m=INITIAL_DEPTH_M,
    )

    if mode == "baseline":
        pf = None
    else:
        pf = PF2D.init(station_lat, station_lon, PF_INIT_SIGMA_M,
                        PF_N_PARTICLES, seed=seed)

    n_steps = int(RUN_HOURS * 3600 / DT_SEC)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    depths = np.zeros(n_steps + 1)
    pf_err = np.zeros(n_steps + 1)
    pf_spread = np.zeros(n_steps + 1)
    at_surface_mask = np.zeros(n_steps + 1, dtype=bool)
    surface_events = 0

    lats[0], lons[0], depths[0] = state.lat, state.lon, state.depth_m
    t_sec = 0.0
    last_decision = -CONTROL_CADENCE_SEC
    last_lora = -LORA_CADENCE_SEC

    # Surface-event state.
    in_surface_dwell = False
    surface_dwell_end_t = -1.0
    last_surface_t = 0.0  # "just had a surface event" at t=0 (start)

    # Path tracking for field-learning updates.
    leg_start = (state.lat, state.lon)
    leg_start_t = 0.0
    leg_path_samples: list[tuple[float, float, float]] = [(0.0, state.lat, state.lon)]
    leg_depth_samples: list[tuple[float, float]] = [(0.0, state.depth_m)]
    was_at_surface = True  # start "at surface" (init PF / got fix implicitly)

    for i in range(n_steps):
        # --- Surfacing policy ---
        if in_surface_dwell:
            # Continue at 0.5m until dwell ends.
            if t_sec >= surface_dwell_end_t:
                in_surface_dwell = False
            else:
                state = set_setpoint(state, 0.5)

        if not in_surface_dwell:
            # Decide whether to trigger a surface event.
            if pf is not None:
                spread = pf.spread_m()
            else:
                spread = 0.0
            time_since_surface = t_sec - last_surface_t
            trigger = (
                (pf is not None and spread > SURFACE_UNCERTAINTY_THRESHOLD_M)
                or (time_since_surface >= SURFACE_MAX_INTERVAL_H * 3600.0)
            )
            if trigger and pf is not None:
                in_surface_dwell = True
                surface_dwell_end_t = t_sec + SURFACE_DWELL_H * 3600.0
                state = set_setpoint(state, 0.5)
                surface_events += 1
            elif t_sec - last_decision >= CONTROL_CADENCE_SEC - 1e-6:
                # Normal control decision.
                if pf is not None:
                    pml, pmo = pf.mean()
                    perceived = (pml, pmo)
                else:
                    perceived = (state.lat, state.lon)
                chosen, _ = keeper.choose_depth(
                    state.lat, state.lon, t_sec,
                    perceived_lat=perceived[0], perceived_lon=perceived[1],
                )
                state = set_setpoint(state, chosen)
                last_decision = t_sec

        # Advance truth dynamics.
        state = step(state, t_sec, DT_SEC,
                     current_at=dyn_current, w_z_max_ms=W_Z_MAX_MS)

        # PF predict based on the prior (noisy + learned bias, as applicable).
        if pf is not None:
            pf.predict(t_sec, DT_SEC, prior_source, state.depth_m)

        t_sec += DT_SEC
        lats[i + 1], lons[i + 1], depths[i + 1] = state.lat, state.lon, state.depth_m
        leg_path_samples.append((t_sec, state.lat, state.lon))
        leg_depth_samples.append((t_sec, state.depth_m))

        # LoRa ranging: only when actually at surface.
        now_at_surface = state.depth_m <= LORA_MAX_DEPTH_M
        at_surface_mask[i + 1] = now_at_surface
        if (pf is not None and now_at_surface
            and t_sec - last_lora >= LORA_CADENCE_SEC - 1e-6):
            for alat, alon in anchor_latlons:
                true_range = distance_m(state.lat, state.lon, alat, alon)
                z = true_range + rng.normal(0, LORA_SIGMA_M)
                pf.update_range(alat, alon, z, LORA_SIGMA_M)
            pf.maybe_resample(rng)
            last_lora = t_sec

        # Field-learning update on transition to surface.
        if (mode == "field_learn"
            and isinstance(prior_source, FieldLearningPrior)
            and now_at_surface and not was_at_surface
            and t_sec - leg_start_t > 1800.0
            and pf is not None):
            pml, pmo = pf.mean()  # use PF mean as observed end position
            prior_source.update_from_leg(
                pos_start_latlon=leg_start,
                pos_end_latlon=(pml, pmo),
                t_start=leg_start_t, t_end=t_sec,
                path_samples=leg_path_samples,
                depth_samples=leg_depth_samples,
            )
            last_surface_t = t_sec
        elif was_at_surface and not now_at_surface:
            # Starting a new submerged leg.
            if pf is not None:
                pml, pmo = pf.mean()
                leg_start = (pml, pmo)
            else:
                leg_start = (state.lat, state.lon)
            leg_start_t = t_sec
            leg_path_samples = [(t_sec, leg_start[0], leg_start[1])]
            leg_depth_samples = [(t_sec, state.depth_m)]
        was_at_surface = now_at_surface

        # Record PF stats.
        if pf is not None:
            ml, mo = pf.mean()
            pf_err[i + 1] = distance_m(state.lat, state.lon, ml, mo)
            pf_spread[i + 1] = pf.spread_m()

    dists = np.array([distance_m(la, lo, station_lat, station_lon)
                       for la, lo in zip(lats, lons)])
    valid = np.isfinite(dists)
    if not valid.all():
        last = np.where(valid)[0]
        dists = (np.where(valid, dists, dists[last[-1]]) if len(last) > 0
                  else np.full_like(dists, np.inf))

    envelope_fracs = {e: float((dists <= e).mean()) for e in ENVELOPES_M}
    return {
        "mode": mode,
        "station_lat": station_lat, "station_lon": station_lon,
        "lats": lats, "lons": lons, "depths": depths,
        "dists_m": dists,
        "pf_err_m": pf_err, "pf_spread_m": pf_spread,
        "ctrl_mean_m": float(np.nanmean(dists)),
        "ctrl_max_m": float(np.nanmax(dists)),
        "envelope_fracs": envelope_fracs,
        "surface_events": surface_events,
        "mean_pf_err_m": float(np.nanmean(pf_err)) if pf is not None else 0.0,
    }


def main() -> None:
    print("=== Single-node field-learning sim ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    n_y, n_x = ds.sizes["gridY"], ds.sizes["gridX"]

    print("building truth ...", flush=True)
    t0 = time.time()
    truth = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    print(f"  {time.time() - t0:.1f}s", flush=True)

    # One multiscale noise field shared across all stations (one "weather day").
    sigma_slow = SIGMA_FORECAST_MS * np.sqrt(NOISE_SLOW_FRACTION)
    sigma_fast = SIGMA_FORECAST_MS * np.sqrt(1.0 - NOISE_SLOW_FRACTION)
    print(f"building multiscale noise σ_fc={SIGMA_FORECAST_MS*100:.0f} "
          f"(slow={sigma_slow*100:.1f}, fast={sigma_fast*100:.1f}) ...", flush=True)
    t0 = time.time()
    noise_field = build_multiscale_noise_field(
        ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET,
        sigma_fast_ms=sigma_fast, sigma_slow_ms=sigma_slow,
        spatial_sigma_cells_fast=NOISE_SPATIAL_CELLS_FAST,
        temporal_sigma_hours_fast=NOISE_TEMPORAL_HOURS_FAST,
        spatial_sigma_cells_slow=NOISE_SPATIAL_CELLS_SLOW,
        temporal_sigma_hours_slow=NOISE_TEMPORAL_HOURS_SLOW,
        seed=42,
    )
    print(f"  built in {time.time() - t0:.1f}s", flush=True)

    # Candidate stations.
    candidates = []
    for gy in range(INTERIOR_MARGIN_Y, n_y - INTERIOR_MARGIN_Y, STRIDE_Y):
        for gx in range(INTERIOR_MARGIN_X, n_x - INTERIOR_MARGIN_X, STRIDE_X):
            if bathy_grid[gy, gx] >= MIN_BATHY_M:
                candidates.append((gy, gx))
    print(f"stations: {len(candidates)}", flush=True)

    results: dict[str, list[dict]] = {"baseline": [], "no_learn": [], "field_learn": []}
    for idx, (gy, gx) in enumerate(candidates):
        s_lat = float(truth.lat_axis[gy])
        s_lon = float(truth.lon_axis[gx])
        s_bathy = float(bathy_grid[gy, gx])
        d_set = depth_set_for_bathy(s_bathy)
        if len(d_set) < 2:
            continue
        u0, v0 = truth.sample(s_lat, s_lon, INITIAL_DEPTH_M, 0.0)
        if not (np.isfinite(u0) and np.isfinite(v0)):
            continue

        print(f"\nstation {idx+1}/{len(candidates)}: "
              f"({s_lat:.4f}, {s_lon:.4f}) bathy={s_bathy:.0f}m", flush=True)

        # (a) Baseline: perfect prior.
        t0 = time.time()
        r_b = run_station(truth, PerfectKnowledge(truth=truth),
                           s_lat, s_lon, d_set, mode="baseline",
                           seed=1000 + idx)
        print(f"  baseline    ctrl_max={r_b['ctrl_max_m']:.0f}m mean={r_b['ctrl_mean_m']:.0f}m "
              f"%<500m={r_b['envelope_fracs'][500.0]*100:.0f}% ({time.time()-t0:.1f}s)",
              flush=True)

        # (b) No learn: PF with noisy prior, no bias learning.
        prior_b = FieldLearningPrior.build(
            truth, noise_field, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX,
            [interp.actual_depth_m for _, interp in sorted(truth.interps.items())],
            BIAS_GRID_SPACING_KM, learning_on=False,
        )
        t0 = time.time()
        r_n = run_station(truth, prior_b, s_lat, s_lon, d_set, mode="no_learn",
                           seed=1000 + idx)
        print(f"  no_learn    ctrl_max={r_n['ctrl_max_m']:.0f}m mean={r_n['ctrl_mean_m']:.0f}m "
              f"%<500m={r_n['envelope_fracs'][500.0]*100:.0f}%  "
              f"PFerr={r_n['mean_pf_err_m']:.0f}m  surf={r_n['surface_events']}  "
              f"({time.time()-t0:.1f}s)", flush=True)

        # (c) Field learn: PF + bias-field learning.
        prior_l = FieldLearningPrior.build(
            truth, noise_field, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX,
            [interp.actual_depth_m for _, interp in sorted(truth.interps.items())],
            BIAS_GRID_SPACING_KM, learning_on=True,
        )
        t0 = time.time()
        r_l = run_station(truth, prior_l, s_lat, s_lon, d_set, mode="field_learn",
                           seed=1000 + idx)
        nz_cells = int((np.abs(prior_l.bias_u) + np.abs(prior_l.bias_v) > 0).sum())
        mean_bias = float(np.sqrt(prior_l.bias_u**2 + prior_l.bias_v**2).mean())
        print(f"  field_learn ctrl_max={r_l['ctrl_max_m']:.0f}m mean={r_l['ctrl_mean_m']:.0f}m "
              f"%<500m={r_l['envelope_fracs'][500.0]*100:.0f}%  "
              f"PFerr={r_l['mean_pf_err_m']:.0f}m  surf={r_l['surface_events']}  "
              f"nz_bias_cells={nz_cells}  mean|bias|={mean_bias*100:.1f}cm/s  "
              f"({time.time()-t0:.1f}s)", flush=True)

        results["baseline"].append(r_b)
        results["no_learn"].append(r_n)
        results["field_learn"].append(r_l)

    # --- Aggregate ---
    print()
    print("=== aggregate ===", flush=True)
    n_total = len(results["baseline"])
    for label, rs in results.items():
        rough = sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        env = {e: float(np.mean([r["envelope_fracs"][e] for r in rs]))
               for e in ENVELOPES_M}
        cm = float(np.mean([r["ctrl_mean_m"] for r in rs]))
        pf_e = float(np.mean([r["mean_pf_err_m"] for r in rs]))
        surf = float(np.mean([r["surface_events"] for r in rs]))
        env_str = "  ".join(f"{'%<'+str(int(e))+'m':>8}: {env[e]*100:>3.0f}%"
                             for e in ENVELOPES_M)
        print(f"  {label:<12} rough={rough:>2}/{n_total}  {env_str}  "
              f"mean_d={cm:>4.0f}m  PFerr={pf_e:>4.0f}m  surf_events={surf:.1f}",
              flush=True)

    # --- Plot ---
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    ax = axes[0]
    xs = np.arange(len(ENVELOPES_M))
    w = 0.27
    for k, (label, rs, color) in enumerate([
        ("baseline", results["baseline"], "tab:green"),
        ("no_learn", results["no_learn"], "tab:gray"),
        ("field_learn", results["field_learn"], "tab:blue"),
    ]):
        pct = [np.mean([r["envelope_fracs"][e] for r in rs]) * 100 for e in ENVELOPES_M]
        ax.bar(xs + (k - 1) * w, pct, w, label=label, color=color, alpha=0.85)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"≤{int(e)}m" for e in ENVELOPES_M])
    ax.set_ylabel(f"mean %-of-run within envelope  ({n_total} stations)")
    ax.set_title(f"Envelope success  (σ_fc={SIGMA_FORECAST_MS*100:.0f}cm/s calibrated prior)")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, axis="y")

    ax = axes[1]
    rough_counts = {
        label: sum(1 for r in rs if r["ctrl_max_m"] <= ROUGH_ENVELOPE_M)
        for label, rs in results.items()
    }
    ax.bar(range(3),
           [rough_counts["baseline"], rough_counts["no_learn"],
            rough_counts["field_learn"]],
           color=["tab:green", "tab:gray", "tab:blue"], alpha=0.85)
    for i, c in enumerate([rough_counts["baseline"], rough_counts["no_learn"],
                            rough_counts["field_learn"]]):
        ax.text(i, c + 0.3, str(c), ha="center", fontsize=11)
    ax.set_xticks(range(3))
    ax.set_xticklabels(["baseline\n(truth)", "no_learn\n(noisy prior)",
                         "field_learn\n(noisy prior + field bias)"])
    ax.set_ylabel(f"# stations with ctrl_max ≤ {int(ROUGH_ENVELOPE_M)}m (of {n_total})")
    ax.set_title(f"Rough station-keeping count")
    ax.grid(alpha=0.3, axis="y")

    fig.suptitle(
        f"Single-node real-physics PF: does field-resolved bias learning rescue "
        f"station-keeping at σ_fc={SIGMA_FORECAST_MS*100:.0f}cm/s?  "
        f"({RUN_HOURS}h, dynamic surfacing ≤{SURFACE_MAX_INTERVAL_H:.0f}h or "
        f"{SURFACE_UNCERTAINTY_THRESHOLD_M:.0f}m spread)",
        fontsize=11, y=1.02,
    )
    fig.tight_layout()
    out = FIG_DIR / "23_field_learning_single_node.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}", flush=True)


if __name__ == "__main__":
    main()
