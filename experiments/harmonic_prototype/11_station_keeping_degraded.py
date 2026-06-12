"""Phase B (Q2): degraded-knowledge station-keeping sweep.

Reuses Phase A's ballast dynamics + controller + bbox + station. The
only variable that changes across tiers is the KnowledgeSource handed to
the controller (and, for B4, the perceived position the controller sees).

Tiers:
  B0 truth                     — perfect knowledge (matches Phase A output)
  B1 spatially smoothed truth  — 2D Gaussian blur, effective ~2 km resolution
  B2 temporally smoothed truth — 6h centered rolling mean
  B3 historical prior          — 2020 Apr–Jun at the same calendar date
                                 (2022 is only partially cached; 2020 has
                                 full Apr/May/Jun coverage in local cache)
  B4 PF-estimated belief       — controller sees PF mean, not truth position;
                                 PF uses prior (B3) for predict + noisy GPS
                                 (σ=3m every 30 min) + LoRa-to-anchor range
                                 (σ=20m every 10 min)

Output: figures/13_station_keeping_degradation.png with:
  - degradation curve (% within 500m vs tier)
  - 5-subplot trajectory map (one per tier) showing where the node
    ended up versus station

Also prints a summary table + honest-limitations note.

Run: uv run --with xarray,netCDF4,numpy,matplotlib,scipy \\
     python experiments/harmonic_prototype/11_station_keeping_degraded.py
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from ballast_controller import KnowledgeSource, StationKeeper  # type: ignore[import-not-found]
from ballast_dynamics import BallastState, set_setpoint, step  # type: ignore[import-not-found]
from knowledge_sources import (  # type: ignore[import-not-found]
    HistoricalPriorKnowledge,
    SpatiallySmoothedTruth,
    TemporallySmoothedTruth,
    TruthKnowledge,
    build_spatially_smoothed,
    build_temporally_smoothed,
)
from salishseacast_cache import (  # type: ignore[import-not-found]
    _cache_path,  # type: ignore[attr-defined]
    U_DATASET,  # type: ignore[attr-defined]
    bbox_from_latlon,
    bbox_latlon_arrays,
    fetch_bbox_months,
)
from truth_field import (  # type: ignore[import-not-found]
    EARTH_R_M,
    build_truth_field,
    distance_m,
    lat_lon_step_from_velocity,
)

LAT_MIN, LAT_MAX = 49.25, 49.35
LON_MIN, LON_MAX = -123.78, -123.62
TRUTH_MONTHS = ["2023-04", "2023-05", "2023-06"]
PRIOR_MONTHS = ["2020-04", "2020-05", "2020-06"]

STATION_ENVELOPE_M = 500.0
RUN_HOURS = 24
DT_SEC = 3600.0
CONTROL_CADENCE_SEC = 1800.0
LOOKAHEAD_SEC = 1800.0
W_Z_MAX_MS = 0.1
AVAILABLE_DEPTHS_M = [0.5, 5.0, 10.0, 20.0, 50.0]
INITIAL_DEPTH_M = 10.0

# Phase B PF (B4) settings.
PF_N_PARTICLES = 200
PF_INIT_SIGMA_M = 10.0
PF_PROCESS_NOISE_MS = 0.05          # extra m/s noise per axis per step
GPS_SIGMA_M = 3.0
GPS_PERIOD_SEC = 1800.0             # every 30 min
LORA_SIGMA_M = 20.0
LORA_PERIOD_SEC = 600.0             # every 10 min
LORA_ANCHOR_OFFSET_LAT_DEG = 0.02   # ~2.2 km north
LORA_ANCHOR_OFFSET_LON_DEG = 0.03   # ~2.2 km east at 49.3°N

FIG_DIR = Path(__file__).parent / "figures"


# ---------------------------------------------------------------------------
# Simulation runner (shared across tiers B0–B3)
# ---------------------------------------------------------------------------

def current_for_dynamics_factory(truth_field):
    """Truth-tied dynamics callback — the node's *actual* motion always
    uses true currents regardless of what the controller believes."""

    def current_at(t_sec: float, lat: float, lon: float, depth_m: float
                   ) -> tuple[float, float]:
        return truth_field.sample(lat, lon, depth_m, t_sec)
    return current_at


def run_tier(
    tier_label: str,
    truth_field,
    knowledge: KnowledgeSource,
    station_lat: float, station_lon: float,
    start_lat: float, start_lon: float, start_depth: float,
    run_hours: int, dt_sec: float, control_cadence_sec: float,
) -> dict:
    """Run a single tier's sim. No PF — perceived position = truth."""
    keeper = StationKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=AVAILABLE_DEPTHS_M,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=knowledge,
    )
    dyn_current = current_for_dynamics_factory(truth_field)
    state = BallastState(
        lat=start_lat, lon=start_lon,
        depth_m=start_depth, depth_setpoint_m=start_depth,
    )
    n_steps = int(run_hours * 3600 / dt_sec)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    depths = np.zeros(n_steps + 1)
    lats[0], lons[0], depths[0] = state.lat, state.lon, state.depth_m
    t_sec = 0.0
    last_decision = -control_cadence_sec
    for i in range(n_steps):
        if t_sec - last_decision >= control_cadence_sec - 1e-6:
            chosen, _ = keeper.choose_depth(state.lat, state.lon, t_sec)
            state = set_setpoint(state, chosen)
            last_decision = t_sec
        state = step(state, t_sec, dt_sec,
                     current_at=dyn_current, w_z_max_ms=W_Z_MAX_MS)
        t_sec += dt_sec
        lats[i + 1], lons[i + 1], depths[i + 1] = state.lat, state.lon, state.depth_m
    dists = np.array([
        distance_m(la, lo, station_lat, station_lon) for la, lo in zip(lats, lons)
    ])
    frac_within = float((dists <= STATION_ENVELOPE_M).mean())
    return {
        "label": tier_label,
        "lats": lats, "lons": lons, "depths": depths, "dists_m": dists,
        "frac_within": frac_within,
        "max_excursion_m": float(np.nanmax(dists)),
        "mean_m": float(np.nanmean(dists)),
        "final_m": float(dists[-1]),
    }


# ---------------------------------------------------------------------------
# Tier B4: simple 2D particle filter
# ---------------------------------------------------------------------------

@dataclass
class ParticleFilter2D:
    """Minimal Gaussian-resample PF over (lat, lon).

    n_particles ~200. State is (lat, lon). Each predict step advects
    particles using the knowledge source's current estimate at the
    node's depth at the particle's position, plus Gaussian process
    noise. Updates on GPS (direct lat/lon) and LoRa (range to anchor).
    Stratified resampling when ESS < n/2.
    """

    lats: np.ndarray
    lons: np.ndarray
    weights: np.ndarray

    @staticmethod
    def init(mean_lat: float, mean_lon: float, sigma_m: float, n: int
             ) -> "ParticleFilter2D":
        rng = np.random.default_rng(42)
        dlat = rng.normal(0, sigma_m / EARTH_R_M, n)
        dlon = rng.normal(0, sigma_m / (EARTH_R_M * np.cos(np.deg2rad(mean_lat))), n)
        return ParticleFilter2D(
            lats=mean_lat + dlat,
            lons=mean_lon + dlon,
            weights=np.full(n, 1.0 / n),
        )

    def mean_position(self) -> tuple[float, float]:
        return float(np.sum(self.lats * self.weights)), float(
            np.sum(self.lons * self.weights)
        )

    def ess(self) -> float:
        return float(1.0 / np.sum(self.weights**2))

    def predict(
        self,
        t_sec: float, dt_sec: float,
        knowledge: KnowledgeSource, depth_m: float,
        process_noise_ms: float, rng: np.random.Generator,
    ) -> None:
        for i in range(len(self.lats)):
            u, v = knowledge.get_current_at(self.lats[i], self.lons[i], depth_m, t_sec)
            if not (np.isfinite(u) and np.isfinite(v)):
                u, v = 0.0, 0.0
            u += rng.normal(0, process_noise_ms)
            v += rng.normal(0, process_noise_ms)
            dlat, dlon = lat_lon_step_from_velocity(u, v, self.lats[i], dt_sec)
            self.lats[i] += dlat
            self.lons[i] += dlon

    def update_gps(self, z_lat: float, z_lon: float, sigma_m: float) -> None:
        cos_lat = np.cos(np.deg2rad(z_lat))
        dlat_m = (self.lats - z_lat) * EARTH_R_M
        dlon_m = (self.lons - z_lon) * EARTH_R_M * cos_lat
        d2 = dlat_m**2 + dlon_m**2
        log_w = -0.5 * d2 / sigma_m**2
        log_w = log_w - log_w.max()
        w = np.exp(log_w) * self.weights
        self.weights = w / w.sum()

    def update_range(
        self, anchor_lat: float, anchor_lon: float,
        z_range_m: float, sigma_m: float,
    ) -> None:
        cos_lat = np.cos(np.deg2rad(anchor_lat))
        dlat_m = (self.lats - anchor_lat) * EARTH_R_M
        dlon_m = (self.lons - anchor_lon) * EARTH_R_M * cos_lat
        dist = np.sqrt(dlat_m**2 + dlon_m**2)
        resid = dist - z_range_m
        log_w = -0.5 * (resid**2) / sigma_m**2
        log_w = log_w - log_w.max()
        w = np.exp(log_w) * self.weights
        self.weights = w / w.sum()

    def maybe_resample(self, rng: np.random.Generator) -> None:
        n = len(self.lats)
        if self.ess() >= n / 2:
            return
        # Stratified resampling.
        positions = (np.arange(n) + rng.uniform(0, 1, n)) / n
        cum = np.cumsum(self.weights)
        idx = np.searchsorted(cum, positions)
        idx = np.clip(idx, 0, n - 1)
        self.lats = self.lats[idx].copy()
        self.lons = self.lons[idx].copy()
        self.weights = np.full(n, 1.0 / n)


def run_tier_b4(
    truth_field,
    prior_knowledge: KnowledgeSource,
    station_lat: float, station_lon: float,
    start_lat: float, start_lon: float, start_depth: float,
    run_hours: int, dt_sec: float, control_cadence_sec: float,
) -> dict:
    """B4: PF maintains belief using prior for predict + noisy sensors for update.
    Controller decides on PF-mean position."""
    rng = np.random.default_rng(0)
    anchor_lat = station_lat + LORA_ANCHOR_OFFSET_LAT_DEG
    anchor_lon = station_lon + LORA_ANCHOR_OFFSET_LON_DEG

    keeper = StationKeeper(
        station_lat=station_lat, station_lon=station_lon,
        available_depths_m=AVAILABLE_DEPTHS_M,
        lookahead_sec=LOOKAHEAD_SEC,
        knowledge=prior_knowledge,
    )
    dyn_current = current_for_dynamics_factory(truth_field)
    state = BallastState(
        lat=start_lat, lon=start_lon,
        depth_m=start_depth, depth_setpoint_m=start_depth,
    )
    pf = ParticleFilter2D.init(start_lat, start_lon, PF_INIT_SIGMA_M, PF_N_PARTICLES)

    n_steps = int(run_hours * 3600 / dt_sec)
    lats = np.zeros(n_steps + 1)
    lons = np.zeros(n_steps + 1)
    depths = np.zeros(n_steps + 1)
    pf_mean_lats = np.zeros(n_steps + 1)
    pf_mean_lons = np.zeros(n_steps + 1)
    pf_ess = np.zeros(n_steps + 1)
    lats[0], lons[0], depths[0] = state.lat, state.lon, state.depth_m
    pf_mean_lats[0], pf_mean_lons[0] = pf.mean_position()
    pf_ess[0] = pf.ess()

    t_sec = 0.0
    last_decision = -control_cadence_sec
    last_gps = -GPS_PERIOD_SEC
    last_lora = -LORA_PERIOD_SEC

    for i in range(n_steps):
        # --- control: decide on PF-mean position ---
        if t_sec - last_decision >= control_cadence_sec - 1e-6:
            pmlat, pmlon = pf.mean_position()
            chosen, _ = keeper.choose_depth(
                state.lat, state.lon, t_sec,
                perceived_lat=pmlat, perceived_lon=pmlon,
            )
            state = set_setpoint(state, chosen)
            last_decision = t_sec

        # --- truth step ---
        state = step(state, t_sec, dt_sec,
                     current_at=dyn_current, w_z_max_ms=W_Z_MAX_MS)

        # --- PF predict using prior at the node's actual depth ---
        pf.predict(
            t_sec, dt_sec, prior_knowledge, state.depth_m,
            PF_PROCESS_NOISE_MS, rng,
        )

        t_sec += dt_sec

        # --- sensor updates ---
        if t_sec - last_gps >= GPS_PERIOD_SEC - 1e-6:
            z_lat = state.lat + rng.normal(0, GPS_SIGMA_M / EARTH_R_M)
            z_lon = state.lon + rng.normal(
                0, GPS_SIGMA_M / (EARTH_R_M * np.cos(np.deg2rad(state.lat))),
            )
            pf.update_gps(z_lat, z_lon, GPS_SIGMA_M)
            last_gps = t_sec
        if t_sec - last_lora >= LORA_PERIOD_SEC - 1e-6:
            true_range = distance_m(state.lat, state.lon, anchor_lat, anchor_lon)
            z_range = true_range + rng.normal(0, LORA_SIGMA_M)
            pf.update_range(anchor_lat, anchor_lon, z_range, LORA_SIGMA_M)
            last_lora = t_sec
        pf.maybe_resample(rng)

        lats[i + 1], lons[i + 1], depths[i + 1] = state.lat, state.lon, state.depth_m
        pml, pmo = pf.mean_position()
        pf_mean_lats[i + 1], pf_mean_lons[i + 1] = pml, pmo
        pf_ess[i + 1] = pf.ess()

    dists = np.array([
        distance_m(la, lo, station_lat, station_lon) for la, lo in zip(lats, lons)
    ])
    pf_err = np.array([
        distance_m(la, lo, pla, plo)
        for la, lo, pla, plo in zip(lats, lons, pf_mean_lats, pf_mean_lons)
    ])
    return {
        "label": "B4 PF+prior",
        "lats": lats, "lons": lons, "depths": depths, "dists_m": dists,
        "pf_mean_lats": pf_mean_lats, "pf_mean_lons": pf_mean_lons,
        "pf_ess": pf_ess, "pf_err_m": pf_err,
        "frac_within": float((dists <= STATION_ENVELOPE_M).mean()),
        "max_excursion_m": float(np.nanmax(dists)),
        "mean_m": float(np.nanmean(dists)),
        "final_m": float(dists[-1]),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Phase B: degraded-knowledge station-keeping sweep ===")
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    print(f"bbox: {bbox}")

    # Truth window.
    ds_truth = fetch_bbox_months(bbox, TRUTH_MONTHS, verbose=False)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    n_y, n_x = ds_truth.sizes["gridY"], ds_truth.sizes["gridX"]
    cy, cx = n_y // 2, n_x // 2
    station_lat = float(lats_grid[cy, cx])
    station_lon = float(lons_grid[cy, cx])
    print(f"station: ({station_lat:.4f}, {station_lon:.4f})")

    # Build knowledge sources.
    print()
    print("building knowledge sources ...")
    t0 = time.time()
    truth_field = build_truth_field(ds_truth, lats_grid, lons_grid, AVAILABLE_DEPTHS_M)
    b0 = TruthKnowledge(truth=truth_field)
    print(f"  B0 truth           ... {time.time() - t0:.1f}s")

    t0 = time.time()
    b1_field = build_spatially_smoothed(
        ds_truth, lats_grid, lons_grid, AVAILABLE_DEPTHS_M, blur_sigma_m=1000.0,
    )
    b1 = SpatiallySmoothedTruth(field=b1_field)
    print(f"  B1 spatial blur    ... {time.time() - t0:.1f}s")

    t0 = time.time()
    b2_field = build_temporally_smoothed(
        ds_truth, lats_grid, lons_grid, AVAILABLE_DEPTHS_M, window_hours=6,
    )
    b2 = TemporallySmoothedTruth(field=b2_field)
    print(f"  B2 temporal blur   ... {time.time() - t0:.1f}s")

    # B3 prior — confirm cache coverage, fall back if needed.
    prior_available = [
        m for m in PRIOR_MONTHS
        if _cache_path(U_DATASET, bbox.key(), m).exists()
    ]
    if len(prior_available) != len(PRIOR_MONTHS):
        print(f"  B3 prior: {PRIOR_MONTHS} has missing months; cached = {prior_available}")
        raise SystemExit("B3 prior requires full Apr–Jun coverage for the chosen year.")
    t0 = time.time()
    ds_prior = fetch_bbox_months(bbox, PRIOR_MONTHS, verbose=False)
    b3 = HistoricalPriorKnowledge.from_datasets(
        ds_prior, truth_t0=truth_field.t0,
        bbox_lats_grid=lats_grid, bbox_lons_grid=lons_grid,
        target_depths_m=AVAILABLE_DEPTHS_M,
    )
    print(f"  B3 historical prior ({PRIOR_MONTHS[0]}–{PRIOR_MONTHS[-1]}) "
          f"year_gap {b3.year_gap_sec / 86400:.1f}d ... {time.time() - t0:.1f}s")

    # Run tiers.
    print()
    print("running tiers ...")
    results = []
    for label, k in [
        ("B0 truth", b0),
        ("B1 spatial σ=1km", b1),
        ("B2 temporal 6h", b2),
        ("B3 prior 2020", b3),
    ]:
        t0 = time.time()
        r = run_tier(
            label, truth_field, k,
            station_lat, station_lon,
            station_lat, station_lon, INITIAL_DEPTH_M,
            RUN_HOURS, DT_SEC, CONTROL_CADENCE_SEC,
        )
        r["wallclock_s"] = time.time() - t0
        results.append(r)
        print(f"  {label:<20} {r['frac_within']*100:5.1f}% within {STATION_ENVELOPE_M:.0f}m  "
              f"max {r['max_excursion_m']:5.0f}m  mean {r['mean_m']:5.0f}m  "
              f"({r['wallclock_s']:.1f}s)")

    t0 = time.time()
    r_b4 = run_tier_b4(
        truth_field, b3,
        station_lat, station_lon,
        station_lat, station_lon, INITIAL_DEPTH_M,
        RUN_HOURS, DT_SEC, CONTROL_CADENCE_SEC,
    )
    r_b4["wallclock_s"] = time.time() - t0
    results.append(r_b4)
    print(f"  {r_b4['label']:<20} {r_b4['frac_within']*100:5.1f}% within {STATION_ENVELOPE_M:.0f}m  "
          f"max {r_b4['max_excursion_m']:5.0f}m  mean {r_b4['mean_m']:5.0f}m  "
          f"(PF ESS min {r_b4['pf_ess'].min():.0f}/ {PF_N_PARTICLES}, "
          f"mean PF err {r_b4['pf_err_m'].mean():.0f}m) "
          f"({r_b4['wallclock_s']:.1f}s)")

    # --- Monotonicity check ---
    pct = [r["frac_within"] for r in results]
    monotone = all(pct[i] >= pct[i + 1] - 1e-9 for i in range(len(pct) - 1))
    print()
    if monotone:
        print("degradation curve is monotonic (% within envelope decreases as knowledge degrades)")
    else:
        print("NON-MONOTONIC degradation — call out:")
        for i in range(len(pct) - 1):
            if pct[i] < pct[i + 1]:
                print(f"  {results[i]['label']} ({pct[i]*100:.0f}%) < "
                      f"{results[i+1]['label']} ({pct[i+1]*100:.0f}%)")

    # --- Plot ---
    labels = [r["label"] for r in results]
    xs = np.arange(len(results))
    colors = ["C2", "C0", "C4", "C1", "C3"]
    masked = np.where(bathy_grid > 0, bathy_grid, np.nan)

    fig = plt.figure(figsize=(18, 10))
    gs = fig.add_gridspec(3, 5, height_ratios=[0.9, 1.0, 1.0], hspace=0.45, wspace=0.35)

    # Row 0: degradation bar chart spanning full width.
    ax_curve = fig.add_subplot(gs[0, :])
    ax_curve.bar(xs, [r["frac_within"] * 100 for r in results],
                 color=colors, alpha=0.75)
    for i, r in enumerate(results):
        ax_curve.text(
            i, r["frac_within"] * 100 + 1.5,
            f"{r['frac_within']*100:.0f}%\nmax {r['max_excursion_m']:.0f}m",
            ha="center", fontsize=9,
        )
    ax_curve.set_xticks(xs)
    ax_curve.set_xticklabels(labels)
    ax_curve.set_ylabel(f"% of {RUN_HOURS}h within {STATION_ENVELOPE_M:.0f}m")
    ax_curve.set_ylim(0, max(50, max(r["frac_within"] * 100 for r in results) + 15))
    ax_curve.set_title(
        f"Phase B: station-keeping degradation curve "
        f"(station at {station_lat:.3f}°N {station_lon:.3f}°E, "
        f"{RUN_HOURS}h from {TRUTH_MONTHS[0]}-01)"
    )
    ax_curve.grid(alpha=0.3, axis="y")

    # Row 1: trajectory map per tier.
    theta = np.linspace(0, 2 * np.pi, 200)
    env_dlat = (STATION_ENVELOPE_M / 111_320.0) * np.sin(theta)
    env_dlon = (STATION_ENVELOPE_M /
                (111_320.0 * np.cos(np.deg2rad(station_lat)))) * np.cos(theta)
    for i, (r, c) in enumerate(zip(results, colors)):
        ax = fig.add_subplot(gs[1, i])
        ax.imshow(masked, origin="lower", cmap="Blues", alpha=0.35,
                  extent=(LON_MIN, LON_MAX, LAT_MIN, LAT_MAX), aspect="auto")
        ax.plot(r["lons"], r["lats"], "-", lw=1.4, color=c)
        ax.plot(station_lon, station_lat, "*", color="black", markersize=12)
        ax.plot(station_lon + env_dlon, station_lat + env_dlat, "--",
                color="black", alpha=0.4, lw=0.8)
        ax.plot(r["lons"][0], r["lats"][0], "o", color=c,
                markeredgecolor="black", markersize=6)
        ax.plot(r["lons"][-1], r["lats"][-1], "s", color=c,
                markeredgecolor="black", markersize=6)
        ax.set_title(f"{r['label']}: {r['frac_within']*100:.0f}% in {STATION_ENVELOPE_M:.0f}m",
                     fontsize=10)
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=7)

    # Row 2: distance timeseries overlaid.
    ax_dist = fig.add_subplot(gs[2, :])
    hrs = np.arange(results[0]["lats"].shape[0]) * (DT_SEC / 3600.0)
    for r, c in zip(results, colors):
        ax_dist.plot(hrs, r["dists_m"], "-", color=c, lw=1.4, label=r["label"])
    ax_dist.axhline(STATION_ENVELOPE_M, ls="--", color="black", alpha=0.5,
                    label=f"{STATION_ENVELOPE_M:.0f}m envelope")
    ax_dist.set_xlabel("hours since start")
    ax_dist.set_ylabel("distance from station (m)")
    ax_dist.set_title("distance-from-station vs time, per knowledge tier")
    ax_dist.legend(loc="best", fontsize=8)
    ax_dist.grid(alpha=0.3)

    fig.tight_layout()
    out = FIG_DIR / "13_station_keeping_degradation.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[viz] wrote {out}")

    # Save NPZ for later comparison.
    npz_out = FIG_DIR / "13_station_keeping_degradation.npz"
    save_dict = {
        "station_lat": station_lat, "station_lon": station_lon,
        "envelope_m": STATION_ENVELOPE_M,
        "run_hours": RUN_HOURS, "dt_sec": DT_SEC,
    }
    for r in results:
        tag = r["label"].split()[0].lower()
        save_dict[f"{tag}_lats"] = r["lats"]
        save_dict[f"{tag}_lons"] = r["lons"]
        save_dict[f"{tag}_dists_m"] = r["dists_m"]
        save_dict[f"{tag}_frac_within"] = r["frac_within"]
        save_dict[f"{tag}_max_excursion_m"] = r["max_excursion_m"]
    save_dict["b4_pf_ess"] = r_b4["pf_ess"]
    save_dict["b4_pf_err_m"] = r_b4["pf_err_m"]
    np.savez(npz_out, **save_dict)
    print(f"[data] wrote {npz_out}")


if __name__ == "__main__":
    main()
