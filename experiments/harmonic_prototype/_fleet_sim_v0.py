"""Minimal end-to-end fleet sim v0.

Mission: deploy N drifters at hand-picked stations, run independently in
parallel for a configurable mission length, RTS-smooth each trajectory,
generate acoustic events (Poisson point pings + boat tracks), simulate
per-drifter detection within range, reconstruct each event via TDOA
trilateration using the smoothed drifter positions, report.

The deployment metric is **σ_event at acoustic-event times** — per
`docs/maritime_buoy_design.md` the prototype's job is fleet coverage +
retrospective σ_pos at event timestamps for TDOA triangulation. Boats
are the realistic target; concatenating per-tick boat positions into a
sequence of point events lets us reuse the point-event reconstruction
pipeline and recover boat tracks as the connected reconstructions.

V0 simplifications (each gets its own follow-up iteration):
  - Trivial detection model: drifter detects an event iff distance to
    event < R_detect; TOA = distance / c_water + Gaussian jitter. No
    propagation loss, no SNR threshold, no multipath.
  - TDOA reconstruction is full TOA-based LSQ over (lat, lon, t_event)
    — 3 unknowns, ≥3 detections required. Drifter positions come from
    the RTS smoother (deployment-honest σ).
  - Drifters don't share information between each other during the
    mission. All cross-platform inference is offline post-process.
  - Static deployment, no dynamic redeployment.
  - 4 stations × 1 mission per station (single-seed) for v0; multi-seed
    is its own iteration once we trust the metric definitions.

What it answers: can the existing PF + bias + MPC stack support
fleet-level TDOA at honest σ_pos? What's the σ_event distribution
across event positions? How well are boat tracks reconstructed?

Saves charts + numerical tables. ~15-25 min wall after init.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from multiprocessing import Pool, current_process

import numpy as np  # type: ignore[import-not-found]


# ---------- Configuration ----------

LAT_MIN, LAT_MAX = 49.15, 49.45
LON_MIN, LON_MAX = -123.95, -123.50
DEFAULT_DEPTH_SET = [0.5, 5.0, 10.0, 20.0, 50.0]

# Hand-picked deployment positions — same 4 stations as the smoke harness.
# A real deployment-planning iteration would optimise these.
STATIONS = [
    (49.3533, -123.7411, 289),
    (49.3533, -123.6892, 188),
    (49.3924, -123.7411, 182),
    (49.3924, -123.6374,  92),
]

RUN_HOURS = int(os.environ.get("FLEET_RUN_HOURS", "72"))
SEED_BASE = 1000
N_PROCS = int(os.environ.get("FLEET_N_PROCS", "16"))

# LoRa fix σ_range — per-anchor ranging noise standard deviation in
# meters. Default 100m: realistic over-sea LoRa with surface multipath
# (Zhang et al. 2024 reports 50–200m σ for SX1262-class radios at 915
# MHz over open water; SX1262 has no hardware TOF ranging so the
# effective σ is dominated by RSSI/distance modelling residual).
# Override via env var to sweep the axis (e.g., LORA_SIGMA_M=20 for
# benchmark / lower-bound conditions, LORA_SIGMA_M=200 for worst-case
# multipath).
LORA_SIGMA_M = float(os.environ.get("LORA_SIGMA_M", "100.0"))


# ---------- Fixed-anchor (shared-buoy) defaults ----------

def _default_fixed_anchors(lat_min: float, lat_max: float,
                            lon_min: float, lon_max: float,
                            n_anchors: int = 4,
                            ) -> tuple[tuple[float, float], ...]:
    """Edge-biased default placement for shared LoRa buoys.

    `n_anchors` ∈ {4, 6}. Layout:
      n=4: 3 edge buoys (north-mid, southwest, southeast) + 1 center.
        Picks the long-axis edge corners + opposite-edge midpoint to
        give a non-degenerate polygon for any in-bbox drifter.
      n=6: 4 edge buoys (NE, NW, SE, SW corners) + 2 inner sites
        offset toward the center to keep PDOP reasonable mid-basin.

    Pure polygon-edge placements starve the interior at the polygon
    scale (~30 km diagonal) we run; this layout always includes at
    least one central anchor so a drifter at basin centre still has
    a non-edge-biased fix. Anchor positions are exact (assumed known
    to ~1 m); the LoRa σ_m sensor noise still drives the per-fix
    σ_pos via PDOP × σ_m.
    """
    cx = 0.5 * (lon_min + lon_max)
    cy = 0.5 * (lat_min + lat_max)
    # Inset edge anchors slightly so they aren't right on the polygon
    # boundary (handier for a real deployment + nicer PDOP).
    insx = 0.10 * (lon_max - lon_min)
    insy = 0.10 * (lat_max - lat_min)
    if n_anchors == 4:
        return (
            (lat_max - insy, cx),                # north-mid
            (lat_min + insy, lon_min + insx),    # SW
            (lat_min + insy, lon_max - insx),    # SE
            (cy, cx),                            # center
        )
    if n_anchors == 6:
        return (
            (lat_max - insy, lon_min + insx),    # NW
            (lat_max - insy, lon_max - insx),    # NE
            (lat_min + insy, lon_min + insx),    # SW
            (lat_min + insy, lon_max - insx),    # SE
            (cy, cx - 0.5 * insx),               # inner-W
            (cy, cx + 0.5 * insx),               # inner-E
        )
    raise ValueError(f"_default_fixed_anchors: n_anchors={n_anchors} "
                       f"unsupported (use 4 or 6)")


# Default anchor set for sweeps that don't override per density-config:
# 4 anchors over the SoG bbox.
DEFAULT_FIXED_ANCHORS: tuple[tuple[float, float], ...] = _default_fixed_anchors(
    LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, n_anchors=4,
)

# Surfacing policies to evaluate. Each policy is run as its own
# fleet-mission pass; events are shared across passes (same RNG seed)
# so σ_event distributions are directly comparable.
@dataclass(frozen=True)
class PolicySpec:
    name: str       # short id used to dispatch the factory in workers
    label: str      # human-readable label for prints + charts


POLICIES_TO_EVAL: tuple[PolicySpec, ...] = (
    PolicySpec(name="fixed_6h",
               label="Fixed cadence (6h)"),
    PolicySpec(name="post_event_30m_12h",
               label="Post-event (30 min delay, 12h cap)"),
)

# Per-drifter audible-event radius used to seed PostEventSurfacingPolicy's
# detector. The policy fires for events within this radius of the
# station — generous (2× detect range) so a drifter at the far edge of
# its envelope still trips on events it could plausibly hear.
AUDIBLE_EVENT_RADIUS_M = 2.0 * 5000.0   # = 2 × DETECT_RANGE_M (defined below)

# Acoustic / detection model.
C_WATER_MS = 1500.0    # speed of sound in seawater (typical coastal)
DETECT_RANGE_M = 5000.0
SIGMA_TOA_S = 0.005    # TOA jitter — 5 ms ≈ 7.5 m at c_water

# Point-event source.
POINT_EVENT_RATE_PER_H = 2.0    # mean events per hour basin-wide
POINT_EVENT_SEED = 42

# Boat tracks.
BOAT_COUNT = int(os.environ.get("FLEET_BOAT_COUNT", "4"))
BOAT_SPEED_MS = 5.0    # ≈ 10 knots typical maritime cruise
BOAT_PING_INTERVAL_S = 60.0   # boat emits a ping every minute
BOAT_SEED = 43

EARTH_R_M: float = 111_320.0   # meters per degree (matches truth_field)


# ---------- Per-worker world cache ----------

_W: dict = {}


def _init_worker():
    from salishseacast_cache import (  # type: ignore[import-not-found]
        bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
    )
    from submesoscale import (  # type: ignore[import-not-found]
        build_layered_noise_field,
        build_layered_tracer_noise_field,
    )
    from truth_field import (  # type: ignore[import-not-found]
        build_tracer_field, build_truth_field,
    )
    label = current_process().name
    t0 = time.time()
    bbox = bbox_from_latlon(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
    ds = fetch_bbox_months(bbox, ["2023-04"], verbose=False,
                            include_tracers=True)
    lats_grid, lons_grid, bathy_grid = bbox_latlon_arrays(bbox)
    nemo = build_truth_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    tracer = build_tracer_field(ds, lats_grid, lons_grid, DEFAULT_DEPTH_SET)
    noise = build_layered_noise_field(ds, lats_grid, lons_grid, seed=42)
    tracer_noise = build_layered_tracer_noise_field(
        ds, lats_grid, lons_grid, seed=42,
    )
    _W["nemo"] = nemo
    _W["tracer"] = tracer
    _W["noise"] = noise
    _W["tracer_noise"] = tracer_noise
    _W["bathy_grid"] = bathy_grid
    print(f"[{label}] init done ({time.time() - t0:.1f}s)", flush=True)


# ---------- Truth wrappers (same shape as smoke harness) ----------

class _RealCurrents:
    def __init__(self, n, no): self.nemo, self.noise = n, no

    def sample(self, lat, lon, d, t):
        ut, vt = self.nemo.sample(lat, lon, d, t)
        if not (np.isfinite(ut) and np.isfinite(vt)):
            return float("nan"), float("nan")
        un, vn = self.noise.sample(lat, lon, d, t)
        return ut + un, vt + vn

    def sample_batched(self, lats, lons, depths, t):
        ut, vt = self.nemo.sample_batched(lats, lons, depths, t)
        un, vn = self.noise.sample_batched(lats, lons, depths, t)
        u = np.where(np.isfinite(ut), ut + un, np.nan)
        v = np.where(np.isfinite(vt), vt + vn, np.nan)
        return u, v

    def get_current_at(self, *a):
        return self.sample(*a)

    def get_current_at_batched(self, *a):
        return self.sample_batched(*a)


class _RealTracer:
    def __init__(self, t, tn): self.tracer, self.tn = t, tn

    def sample(self, lat, lon, d, t):
        Tt, St = self.tracer.sample(lat, lon, d, t)
        if not (np.isfinite(Tt) and np.isfinite(St)):
            return float("nan"), float("nan")
        Tn, Sn = self.tn.sample(lat, lon, d, t)
        return Tt + Tn, St + Sn


class _NemoPrior:
    def __init__(self, n): self.nemo = n
    def sample(self, l, lo, d, t): return self.nemo.sample(l, lo, d, t)
    def sample_batched(self, ls, los, ds, t):
        return self.nemo.sample_batched(ls, los, ds, t)
    def get_current_at(self, *a): return self.sample(*a)
    def get_current_at_batched(self, *a): return self.sample_batched(*a)


def _make_bias():
    from rbpf_prototype import BiasConfig  # type: ignore[import-not-found]
    return BiasConfig(
        n_cells=8, cell_size_m=2000.0,
        sigma_bias_init_ms=float(np.sqrt(
            0.04**2 + 0.02**2 + 0.05**2)),
    )


# ---------- Event sources ----------

@dataclass(frozen=True)
class AcousticEvent:
    """One ping at known (lat, lon, t_sec)."""
    lat: float
    lon: float
    t_sec: float
    src: str = "point"        # "point" or "boat:<id>"


@dataclass(frozen=True)
class BoatTrack:
    """One boat: spawn (lat, lon, t_start), heading_rad, speed_ms,
    duration_h. Discretised into a series of pings at fixed cadence."""
    boat_id: int
    spawn_lat: float
    spawn_lon: float
    t_start_sec: float
    heading_rad: float
    speed_ms: float
    duration_h: float
    ping_interval_s: float

    def positions(self) -> list[AcousticEvent]:
        cos_lat = float(np.cos(np.deg2rad(self.spawn_lat)))
        n = int(self.duration_h * 3600.0 / self.ping_interval_s) + 1
        events = []
        # Heading 0 = north (+lat), π/2 = east (+lon).
        v_north = self.speed_ms * float(np.cos(self.heading_rad))
        v_east = self.speed_ms * float(np.sin(self.heading_rad))
        for k in range(n):
            dt = k * self.ping_interval_s
            d_lat = (v_north * dt) / EARTH_R_M
            d_lon = (v_east * dt) / (EARTH_R_M * cos_lat)
            lat = self.spawn_lat + d_lat
            lon = self.spawn_lon + d_lon
            # Drop pings that have left the bbox.
            if not (LAT_MIN <= lat <= LAT_MAX
                    and LON_MIN <= lon <= LON_MAX):
                break
            events.append(AcousticEvent(
                lat=lat, lon=lon,
                t_sec=self.t_start_sec + dt,
                src=f"boat:{self.boat_id}",
            ))
        return events


def _generate_point_events(
    rate_per_h: float, mission_dur_sec: float, seed: int,
) -> list[AcousticEvent]:
    """Poisson point-event source over the basin bbox."""
    rng = np.random.default_rng(seed)
    n_expected = rate_per_h * mission_dur_sec / 3600.0
    n = int(rng.poisson(n_expected))
    times = np.sort(rng.uniform(0, mission_dur_sec, size=n))
    lats = rng.uniform(LAT_MIN, LAT_MAX, size=n)
    lons = rng.uniform(LON_MIN, LON_MAX, size=n)
    return [
        AcousticEvent(lat=float(lats[i]), lon=float(lons[i]),
                       t_sec=float(times[i]), src="point")
        for i in range(n)
    ]


def _generate_boat_tracks(
    n_boats: int, mission_dur_sec: float, speed_ms: float,
    ping_interval_s: float, seed: int,
) -> tuple[list[BoatTrack], list[AcousticEvent]]:
    """Spawn N boats at random bbox edge points with random outward
    headings and random spawn times. Each lives until it exits the bbox
    or the mission ends."""
    rng = np.random.default_rng(seed)
    tracks: list[BoatTrack] = []
    all_events: list[AcousticEvent] = []
    for k in range(n_boats):
        # Spawn on a random edge of the bbox.
        edge = rng.integers(4)   # 0=south, 1=north, 2=west, 3=east
        if edge == 0:    # south edge, heading north
            spawn_lat, spawn_lon = LAT_MIN, rng.uniform(LON_MIN, LON_MAX)
            heading = 0.0 + rng.uniform(-0.5, 0.5)        # ≈ north
        elif edge == 1:  # north edge, heading south
            spawn_lat, spawn_lon = LAT_MAX, rng.uniform(LON_MIN, LON_MAX)
            heading = float(np.pi) + rng.uniform(-0.5, 0.5)
        elif edge == 2:  # west edge, heading east
            spawn_lat, spawn_lon = rng.uniform(LAT_MIN, LAT_MAX), LON_MIN
            heading = float(np.pi / 2) + rng.uniform(-0.5, 0.5)
        else:            # east edge, heading west
            spawn_lat, spawn_lon = rng.uniform(LAT_MIN, LAT_MAX), LON_MAX
            heading = float(-np.pi / 2) + rng.uniform(-0.5, 0.5)
        t_start = float(rng.uniform(0, mission_dur_sec * 0.5))
        # Boat lifespan in basin: roughly the shorter of (cross-bbox
        # transit at speed) or (remaining mission time).
        lat_ext_m = (LAT_MAX - LAT_MIN) * EARTH_R_M
        lon_ext_m = (LON_MAX - LON_MIN) * EARTH_R_M * float(
            np.cos(np.deg2rad(0.5 * (LAT_MIN + LAT_MAX)))
        )
        bbox_diag_m = float(np.sqrt(lat_ext_m ** 2 + lon_ext_m ** 2))
        max_lifespan_h = min(
            bbox_diag_m / max(speed_ms, 0.1) / 3600.0,
            (mission_dur_sec - t_start) / 3600.0,
        )
        track = BoatTrack(
            boat_id=k, spawn_lat=float(spawn_lat),
            spawn_lon=float(spawn_lon), t_start_sec=t_start,
            heading_rad=heading, speed_ms=speed_ms,
            duration_h=max_lifespan_h, ping_interval_s=ping_interval_s,
        )
        tracks.append(track)
        all_events.extend(track.positions())
    return tracks, all_events


# ---------- TDOA reconstruction ----------

def _distance_m_latlon(lat1, lon1, lat2, lon2) -> float:
    """Local-flat-earth distance in meters.

    Delegates to `truth_field.distance_m` (the canonical equirectangular
    formula that uses the meters-per-degree constant). The module-local
    `EARTH_R_M = 6,378,137` here is the actual Earth radius, used by the
    smoother's internal storage convention and by `_smoothed_at_t`'s
    matched round-trip — it is NOT the right constant for a true
    distance, which needs ~111,320 m/°.
    """
    from truth_field import distance_m  # type: ignore[import-not-found]
    return distance_m(lat1, lon1, lat2, lon2)


@dataclass
class Detection:
    """One drifter d detecting one event e."""
    event_idx: int
    drifter_id: int
    drifter_lat: float
    drifter_lon: float
    drifter_sigma_m: float
    distance_m: float
    toa_s: float       # absolute TOA at the drifter (event_t + dist/c)


@dataclass
class Reconstruction:
    """TDOA reconstruction of one event."""
    event_idx: int
    src: str
    n_detectors: int
    truth_lat: float
    truth_lon: float
    truth_t_sec: float
    recon_lat: float = float("nan")
    recon_lon: float = float("nan")
    recon_t_sec: float = float("nan")
    sigma_m: float = float("nan")     # sqrt of mean residual² × c_water
    error_m: float = float("nan")     # truth-vs-recon distance
    # Time at which all detecting drifters had surfaced and could
    # exfil their TOAs, allowing reconstruction. Mode-(b) only —
    # for mode (a) all events are reconstructed at end-of-mission.
    t_detect_sec: float = float("nan")
    time_to_detect_sec: float = float("nan")
    # Distance from event to the centroid of detecting drifter stations
    # (geometry property for downstream bucketing).
    dist_to_detector_centroid_m: float = float("nan")
    # Posterior covariance from the LSQ in local-ENU + time units:
    #   Σ_post[:2,:2] is 2×2 position cov (m²);
    #   Σ_post[2,2]   is t_event variance (s²).
    # NaN-filled when LSQ fails (singular J^T W J or <3 detectors).
    # Persisted per event so post-hoc analyzers can compute
    # Mahalanobis residuals m² = Δxᵀ Σ_post[:2,:2]⁻¹ Δx without
    # re-running the LSQ pipeline.
    sigma_post_3x3: np.ndarray = field(
        default_factory=lambda: np.full((3, 3), np.nan)
    )
    # Drifter ids that detected this event (and therefore participated
    # in the LSQ if N >= 3). Empty tuple if no detections; subset of
    # the fleet for any given event. Persisted per event so the
    # analyzer can look up per-detector trajectory state at event
    # time (PDOP, anisotropy diagnostic, σ-scaling counterfactual).
    detector_ids: tuple[int, ...] = field(default_factory=tuple)
    # Per-detector σ_pos that was ACTUALLY USED in this LSQ (matches
    # `detector_ids` order). Mode-a uses full-mission smoother σ_pos;
    # mode-b uses windowed-RTS σ_pos (conditioned only through each
    # drifter's next own LoRa fix). Saving these explicitly lets the
    # analyzer rebuild Σ_post under counterfactual scaling without
    # ambiguity about which cov source the LSQ saw.
    detector_sigma_pos_used: tuple[float, ...] = field(default_factory=tuple)


def _trilaterate_tdoa(
    detections: list[Detection], event: AcousticEvent,
) -> Reconstruction:
    """Inverse-variance-weighted Gauss-Newton over (lat, lon, t_event)
    given absolute TOAs at drifters with known posterior σ_pos.

    Observation model:
        t_d_obs = t_event + ||drifter_d − event_pos|| / c + ε_d
    Each drifter's TOA-noise variance is the sum of intrinsic acoustic
    noise and the propagated drifter-position uncertainty:
        σ_TOA_d² = SIGMA_TOA_S² + (drifter_sigma_m / C_WATER_MS)²
    Drifter σ_pos dominates: 250 m / 1500 m/s = 167 ms vs intrinsic 5 ms,
    so this term is load-bearing for any honest σ_event.

    Σ_obs = diag(σ_TOA_d²). Weighted Gauss-Newton step:
        dx = (J^T Σ⁻¹ J)⁻¹ J^T Σ⁻¹ r
    Posterior covariance at the optimum:
        Σ_post = (J^T Σ⁻¹ J)⁻¹    (3×3, in (m, m, s))
    Reported `sigma_m` = sqrt(0.5 (Σ_post[0,0] + Σ_post[1,1])) — the
    per-axis position uncertainty implied by drifter σ_pos + acoustic
    noise + geometry. This IS the deployment metric for σ_event.

    Returns σ_m = posterior position σ; `error_m` = truth-vs-recon.
    """
    rec = Reconstruction(
        event_idx=detections[0].event_idx, src=event.src,
        n_detectors=len(detections),
        truth_lat=event.lat, truth_lon=event.lon, truth_t_sec=event.t_sec,
        detector_ids=tuple(d.drifter_id for d in detections),
        detector_sigma_pos_used=tuple(
            float(d.drifter_sigma_m) for d in detections
        ),
    )
    if len(detections) < 3:
        return rec

    # Reference for local-ENU: centroid of drifter positions.
    ref_lat = float(np.mean([d.drifter_lat for d in detections]))
    ref_lon = float(np.mean([d.drifter_lon for d in detections]))
    cos_lat = float(np.cos(np.deg2rad(ref_lat)))

    def latlon_to_enu(lat, lon):
        return (
            (lon - ref_lon) * EARTH_R_M * cos_lat,   # x = east
            (lat - ref_lat) * EARTH_R_M,             # y = north
        )

    drifter_enu = np.array([latlon_to_enu(d.drifter_lat, d.drifter_lon)
                              for d in detections])    # (N, 2)
    toa_obs = np.array([d.toa_s for d in detections])  # (N,)
    sigma_pos = np.array([d.drifter_sigma_m for d in detections])  # (N,)

    # Per-detector TOA noise variance (s²). Drifter σ_pos enters as
    # propagated σ_TOA = σ_pos / c (the rate at which TOA changes as
    # the assumed-known drifter position moves along the event-drifter
    # line).
    sigma_toa_total_s = np.sqrt(SIGMA_TOA_S ** 2
                                  + (sigma_pos / C_WATER_MS) ** 2)
    inv_var = 1.0 / np.maximum(sigma_toa_total_s ** 2, 1e-12)
    W_sqrt = np.sqrt(inv_var)    # diag of Σ^(-1/2) for the lstsq form

    # Initial guess: centroid of detected drifters at earliest TOA.
    x0 = np.array([
        float(np.mean(drifter_enu[:, 0])),
        float(np.mean(drifter_enu[:, 1])),
        float(toa_obs.min()) - 1.0,    # t guess
    ])
    x = x0.copy()
    for _ in range(50):
        # Predicted TOA: t + ||drifter - x[:2]|| / c
        diff = drifter_enu - x[:2]    # (N, 2)
        dist = np.linalg.norm(diff, axis=1)   # (N,)
        toa_pred = x[2] + dist / C_WATER_MS
        r = toa_obs - toa_pred        # (N,)
        # Jacobian (unweighted): ∂r/∂x = diff_x / dist / c, ∂y similar,
        # ∂r/∂t = -1.
        with np.errstate(divide="ignore", invalid="ignore"):
            J = np.column_stack([
                diff[:, 0] / np.maximum(dist, 1e-3) / C_WATER_MS,
                diff[:, 1] / np.maximum(dist, 1e-3) / C_WATER_MS,
                -np.ones_like(dist),
            ])
        # Whiten: row-scale J and r by 1/σ_TOA_d so plain lstsq solves
        # the inverse-variance-weighted normal equations.
        Jw = J * W_sqrt[:, None]
        rw = r * W_sqrt
        try:
            dx, *_ = np.linalg.lstsq(Jw, rw, rcond=None)
        except np.linalg.LinAlgError:
            break
        x = x - dx
        if np.linalg.norm(dx[:2]) < 0.1 and abs(dx[2]) < 1e-4:
            break

    recon_lat = ref_lat + x[1] / EARTH_R_M
    recon_lon = ref_lon + x[0] / (EARTH_R_M * cos_lat)
    rec.recon_lat = float(recon_lat)
    rec.recon_lon = float(recon_lon)
    rec.recon_t_sec = float(x[2])

    # Posterior covariance: Σ_post = (J^T Σ⁻¹ J)⁻¹ at the optimum.
    # `sigma_m` is the per-axis position σ implied by drifter σ_pos,
    # acoustic σ_TOA, and the local geometry. This is the deployment
    # metric — moves when σ_pos moves.
    diff = drifter_enu - x[:2]
    dist = np.linalg.norm(diff, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        J = np.column_stack([
            diff[:, 0] / np.maximum(dist, 1e-3) / C_WATER_MS,
            diff[:, 1] / np.maximum(dist, 1e-3) / C_WATER_MS,
            -np.ones_like(dist),
        ])
    JtWJ = J.T @ (J * inv_var[:, None])
    try:
        Sigma_post = np.linalg.inv(JtWJ)
        sigma_pos_per_axis = float(np.sqrt(
            0.5 * (Sigma_post[0, 0] + Sigma_post[1, 1])
        ))
        rec.sigma_post_3x3 = np.asarray(Sigma_post, dtype=float).copy()
    except np.linalg.LinAlgError:
        sigma_pos_per_axis = float("nan")
    rec.sigma_m = sigma_pos_per_axis
    rec.error_m = _distance_m_latlon(event.lat, event.lon,
                                       recon_lat, recon_lon)
    return rec


# ---------- Per-drifter mission ----------

def _build_surfacing_policy(name: str, audible_events: list):
    """Construct a SurfacingPolicy for a given drifter from the policy
    name + a per-drifter pre-filtered list of audible AcousticEvent.

    The audible-event filter is set at job-creation time in `main()`
    using the station's target lat/lon ± `AUDIBLE_EVENT_RADIUS_M`.
    """
    from rbpf_prototype import (  # type: ignore[import-not-found]
        EventInfo, EventScheduleDetector, FixedIntervalPolicy,
    )
    from rbpf_prototype.surfacing import (  # type: ignore[import-not-found]
        PostEventSurfacingPolicy,
    )
    if name == "fixed_6h":
        return FixedIntervalPolicy(period_h=6.0)
    if name == "post_event_30m_12h":
        events = tuple(
            EventInfo(lat=float(e.lat), lon=float(e.lon),
                       t_sec=float(e.t_sec), src=str(e.src))
            for e in sorted(audible_events, key=lambda e: e.t_sec)
        )
        det = EventScheduleDetector(events=events)
        return PostEventSurfacingPolicy(
            event_detector=det,
            post_event_delay_min=30.0,
            max_interval_h=12.0,
            # Track-divergence threshold: pings within 500 m of the
            # projection from the last-exfiltrated track are dropped.
            # Larger than expected per-tick drift (~300 m at 5 m/s
            # boat speed × 60 s ping interval) so a uniformly-moving
            # boat does NOT re-trigger; smaller than the operational
            # σ_event target so genuine course changes do.
            track_divergence_threshold_m=500.0,
        )
    raise ValueError(f"unknown surfacing policy: {name!r}")


def _run_one_drifter(args: tuple) -> dict:
    # Args may have 3..8 elements (positional, optional tail):
    #   (s_idx, policy_name, audible_events)
    #   (..., station_target)
    #   (..., run_hours)
    #   (..., lora_sigma_m)        # per-cell LoRa σ_m override
    #   (..., control_cadence_sec) # per-cell MPC decision interval override
    #   (..., anchors)             # per-cell fixed-buoy anchor list,
    #                              #   tuple[tuple[lat, lon], ...]; if
    #                              #   None falls back to DEFAULT_FIXED_ANCHORS
    # `station_target` overrides module-level STATIONS; `run_hours`
    # overrides module-level RUN_HOURS; `lora_sigma_m` overrides module
    # LORA_SIGMA_M; `control_cadence_sec` overrides the SimConfig default
    # (1800 → e.g. 7200 for a 2-hour decision interval / 24-hour
    # MPC plan when paired with horizon_n=12). `anchors` is the shared
    # fixed-buoy set for this density-config; every drifter in the fleet
    # sees the same anchors.
    s_idx = args[0]
    policy_name = args[1]
    audible_events = args[2]
    station_target = args[3] if len(args) >= 4 else None
    run_hours = args[4] if len(args) >= 5 else None
    lora_sigma_m = args[5] if len(args) >= 6 and args[5] is not None else LORA_SIGMA_M
    control_cadence_sec = args[6] if len(args) >= 7 and args[6] is not None else 1800.0
    anchors_in = args[7] if len(args) >= 8 and args[7] is not None else None
    from rbpf_prototype import (  # type: ignore[import-not-found]
        BiasConfig, CTDSensor, Experiment,
        LoRaRangeSensor, PFConfig, ProcessNoiseConfig, SensorConfig,
        SimConfig, StationConfig, rts_smooth_trajectory, run_one_station,
    )

    nemo = _W["nemo"]; tracer = _W["tracer"]
    noise = _W["noise"]; tracer_noise = _W["tracer_noise"]
    bathy_grid = _W["bathy_grid"]

    if station_target is not None:
        s_lat_target, s_lon_target, _depth_hint = station_target
    else:
        s_lat_target, s_lon_target, _depth_hint = STATIONS[s_idx]
    gy = int(np.argmin(np.abs(nemo.lat_axis - s_lat_target)))
    gx = int(np.argmin(np.abs(nemo.lon_axis - s_lon_target)))
    s_lat = float(nemo.lat_axis[gy])
    s_lon = float(nemo.lon_axis[gx])
    s_bathy = float(bathy_grid[gy, gx])
    max_d = min(50.0, s_bathy * 0.8)
    d_set = [d for d in DEFAULT_DEPTH_SET if d <= max_d]
    station = StationConfig(lat=s_lat, lon=s_lon, envelope_m=3000.0,
                              available_depths_m=d_set)
    # Every drifter in the fleet shares the same anchor buoy set for
    # this density-config; caller passes the cell's anchor list and
    # the module default fills in if unspecified.
    anchors = list(anchors_in) if anchors_in is not None \
        else list(DEFAULT_FIXED_ANCHORS)
    sim = SimConfig(
        run_hours=int(run_hours) if run_hours is not None else RUN_HOURS,
        dt_sec=600.0,
        control_cadence_sec=float(control_cadence_sec),
        # Lookahead matches decision interval — only used by the
        # TrajectoryStationKeeper fallback. MPC uses horizon_n × interval
        # for total horizon and ignores this knob.
        lookahead_sec=float(control_cadence_sec),
        w_z_max_ms=0.1, initial_depth_m=10.0,
        surface_dwell_h=0.5, lora_cadence_sec=60.0,
        process_noise_model="ou_integrated",
        # Honest σ + posterior_cvar default per recent fixes — fleet sim
        # uses our most-advanced controller setup.
    )
    pf_cfg = PFConfig(n_particles=500, init_sigma_m=20.0,
                       process_noise_ms=0.08)
    sensor_cfg = SensorConfig(
        lora=LoRaRangeSensor(anchors=anchors, sigma_m=float(lora_sigma_m),
                              max_depth_m=1.0),
        flow=None, ctd=CTDSensor(),
    )
    bias_cfg = _make_bias()
    real = _RealCurrents(nemo, noise)
    nemo_prior = _NemoPrior(nemo)
    real_tracer = _RealTracer(tracer, tracer_noise)
    seed = SEED_BASE + s_idx * 100
    t0 = time.time()
    surfacing = _build_surfacing_policy(policy_name, audible_events)
    exp = Experiment(
        station=station, sim=sim, sensor=sensor_cfg, pf_cfg=pf_cfg,
        truth=real, prior=nemo_prior,
        surfacing=surfacing,
        bias_cfg=bias_cfg,
        tracer_truth=real_tracer, tracer_prior=tracer,
    )
    r = run_one_station(exp, seed=seed)
    pn_cfg = ProcessNoiseConfig()
    smoothed = rts_smooth_trajectory(
        pf_mean_lats=r.pf_mean_lats,
        pf_mean_lons=r.pf_mean_lons,
        pf_cov_m=r.pf_cov_m,
        depths=r.depths,
        lora_fix_mask=r.lora_fix_mask,
        dt_sec=sim.dt_sec,
        process_noise_cfg=pn_cfg,
    )
    dt = time.time() - t0
    # Also compute smoothed-vs-truth distance per tick (the deployment
    # metric proxy — it is what TDOA reconstruction will consume).
    smooth_lats, smooth_lons = smoothed.to_latlon()
    smooth_err_m = np.array([
        _distance_m_latlon(float(r.lats[i]), float(r.lons[i]),
                            float(smooth_lats[i]), float(smooth_lons[i]))
        for i in range(len(r.lats))
    ])
    n_surfacings = int(r.surface_events)
    n_lora_fix_ticks = int(np.sum(r.lora_fix_mask))
    print(
        f"  [{policy_name}] drifter {s_idx+1} (S{s_idx+1}): "
        f"station_keeping={r.ctrl_mean_m():.0f}m  "
        f"pf_err_mean={float(np.nanmean(r.pf_err_m)):.0f}m  "
        f"smooth_err_mean={float(np.nanmean(smooth_err_m)):.0f}m  "
        f"surfacings={n_surfacings} (fix-ticks={n_lora_fix_ticks})  "
        f"({dt:.0f}s)",
        flush=True,
    )
    return {
        "s_idx": s_idx,
        "drifter_id": s_idx,
        "policy_name": policy_name,
        "seed": seed,
        "dt_sec": sim.dt_sec,
        "station_lat": s_lat,
        "station_lon": s_lon,
        "n_surfacings": n_surfacings,
        "n_lora_fix_ticks": n_lora_fix_ticks,
        # Truth trajectory (for diagnostics — NOT used by TDOA).
        "truth_lats": r.lats,
        "truth_lons": r.lons,
        # Forward-filter outputs (needed for mode-b windowed smoothing,
        # which conditions on observations only through each event's
        # next-surface tick).
        "pf_mean_lats": r.pf_mean_lats,
        "pf_mean_lons": r.pf_mean_lons,
        "pf_cov_m": r.pf_cov_m,
        "lora_fix_mask": r.lora_fix_mask,
        "depths": r.depths,
        # Mode-(a) full-mission smoother output (deployment-relevant
        # for end-of-mission recovery operational mode).
        "t_sec": smoothed.t_sec,
        "smooth_means_local_m": smoothed.means_local_m,
        "smooth_covs_m": smoothed.covs_m,
        "smooth_ref_lat": smoothed.ref_lat,
        "smooth_ref_lon": smoothed.ref_lon,
        "ctrl_mean_m": r.ctrl_mean_m(),
        "pf_err_mean": float(np.nanmean(r.pf_err_m)),
        "smooth_err_mean": float(np.nanmean(smooth_err_m)),
        # Per-tick diagnostic arrays — saved so the analyzer can read
        # them straight from npz without parsing the driver log.
        # `dists_m` is the per-tick station-keeping distance (drifter
        # truth → station target). `pf_err_m` is per-tick PF error
        # (truth → PF mean). `smooth_err_per_tick` is per-tick
        # full-mission smoother error.
        "station_keeping_per_tick": np.asarray(r.dists_m, dtype=float),
        "pf_err_per_tick": np.asarray(r.pf_err_m, dtype=float),
        "smooth_err_per_tick": np.asarray(smooth_err_m, dtype=float),
    }


def _smoothed_at_t(row: dict, t_query_sec: float) -> tuple[float, float, float]:
    """Query a drifter's smoothed (lat, lon, sigma_m) at t_query."""
    t = row["t_sec"]
    if t_query_sec <= t[0]:
        i = 0; a = 0.0
    elif t_query_sec >= t[-1]:
        i = t.size - 2; a = 1.0
    else:
        i = int(np.searchsorted(t, t_query_sec, side="right") - 1)
        a = (t_query_sec - t[i]) / max(t[i + 1] - t[i], 1e-9)
    m = (1 - a) * row["smooth_means_local_m"][i] \
        + a * row["smooth_means_local_m"][i + 1]
    c = (1 - a) * row["smooth_covs_m"][i] + a * row["smooth_covs_m"][i + 1]
    cos_lat = float(np.cos(np.deg2rad(row["smooth_ref_lat"])))
    lat = row["smooth_ref_lat"] + m[1] / EARTH_R_M
    lon = row["smooth_ref_lon"] + m[0] / (EARTH_R_M * cos_lat)
    sigma_m = float(np.sqrt(0.5 * (c[0, 0] + c[1, 1])))
    return lat, lon, sigma_m


def _truth_at_t(row: dict, t_query_sec: float) -> tuple[float, float]:
    """Query a drifter's TRUTH (lat, lon) at t_query via linear interp.

    Used for the acoustic-physics TOA computation (drifters don't know
    their own positions; the propagation distance is set by where the
    drifter actually IS, not where the smoother estimates it to be).
    """
    t = row["t_sec"]
    truth_lats = row["truth_lats"]
    truth_lons = row["truth_lons"]
    if t_query_sec <= t[0]:
        return float(truth_lats[0]), float(truth_lons[0])
    if t_query_sec >= t[-1]:
        return float(truth_lats[-1]), float(truth_lons[-1])
    i = int(np.searchsorted(t, t_query_sec, side="right") - 1)
    a = (t_query_sec - t[i]) / max(t[i + 1] - t[i], 1e-9)
    lat = (1 - a) * float(truth_lats[i]) + a * float(truth_lats[i + 1])
    lon = (1 - a) * float(truth_lons[i]) + a * float(truth_lons[i + 1])
    return lat, lon


def _surface_event_start_times(row: dict) -> np.ndarray:
    """Extract per-drifter surface-event START times from `lora_fix_mask`.

    Each contiguous run of True in the mask corresponds to a surface
    dwell; the run's first tick is the surface-event start. Returns
    an array of times in seconds.
    """
    mask = row["lora_fix_mask"]
    t_sec = row["t_sec"]
    starts: list[float] = []
    in_block = False
    for i in range(len(mask)):
        if mask[i] and not in_block:
            starts.append(float(t_sec[i]))
            in_block = True
        elif not mask[i]:
            in_block = False
    return np.asarray(starts, dtype=float)


def _first_surface_at_or_after(row: dict, t: float) -> float:
    """First surface-event start time at or after `t`. Returns NaN if
    none. Computed from cached starts to avoid recomputing per event.

    In campaign mode the row carries `cycle_idx_per_tick` (int per tick).
    We then constrain the search to surface events within the SAME cycle
    as `t` — the next-cycle drifter is a fresh deployment with no
    knowledge of this cycle's TOAs, so it cannot be the exfil mechanism
    for an event that happened in this cycle.
    """
    if "_surface_starts" not in row:
        row["_surface_starts"] = _surface_event_start_times(row)
    starts = row["_surface_starts"]
    if starts.size == 0:
        return float("nan")
    idx = int(np.searchsorted(starts, t, side="left"))
    if idx >= starts.size:
        return float("nan")
    candidate = float(starts[idx])
    # Cycle-boundary constraint (campaign mode). Compare cycle indices
    # at t and at the candidate surface time; if they differ, the
    # surface belongs to a later deployment — drop it.
    cycle_idx = row.get("cycle_idx_per_tick")
    if cycle_idx is not None:
        t_arr = row["t_sec"]
        i_t = int(np.searchsorted(t_arr, t, side="right") - 1)
        i_t = max(0, min(i_t, len(cycle_idx) - 1))
        i_c = int(np.searchsorted(t_arr, candidate, side="right") - 1)
        i_c = max(0, min(i_c, len(cycle_idx) - 1))
        if int(cycle_idx[i_t]) != int(cycle_idx[i_c]):
            return float("nan")
    return candidate


def _smoothed_mode_b_at_t(
    row: dict, t_query_sec: float,
) -> tuple[float, float, float] | None:
    """Mode-(b) "next-surface-conditioned" smoothed (lat, lon, sigma_m).

    For event time t_query, find the FIRST LoRa-fix tick after t_query
    in this drifter's mission. Run a *windowed* RTS backward pass over
    the forward-filter outputs in [query_tick, next_fix_tick]. Return
    the smoothed (lat, lon, sigma_m) at t_query.

    This is the deployment-honest σ for "drifter computes its own σ_pos
    at event time at its next surface dwell" — i.e., it conditions on
    observations only through that single next-surface fix, not on
    LoRa fixes farther in the future.

    Returns None if the drifter has no LoRa fix anywhere after t_query
    (mission ended before the next surface; the drifter has no anchor
    to back-project from and cannot honestly publish a σ_pos at t_query).
    """
    from rbpf_prototype import (  # type: ignore[import-not-found]
        ProcessNoiseConfig, rts_smooth_trajectory,
    )

    t_sec = row["t_sec"]
    lora_fix_mask = row["lora_fix_mask"]
    pf_mean_lats = row["pf_mean_lats"]
    pf_mean_lons = row["pf_mean_lons"]
    pf_cov_m = row["pf_cov_m"]
    depths = row["depths"]
    dt_sec = float(row["dt_sec"])

    # Find query_tick = LAST tick at-or-BEFORE t_query, so we can
    # interpolate forward (between query_tick and query_tick+1) to land
    # exactly on t_query in the smoother window.
    if t_query_sec < t_sec[0]:
        # Before mission start: use forward-filter post-deployment state
        # at tick 0 (no smoother window before the start).
        m_lat = float(pf_mean_lats[0])
        m_lon = float(pf_mean_lons[0])
        c = pf_cov_m[0]
        sigma_m = float(np.sqrt(0.5 * (c[0, 0] + c[1, 1])))
        return m_lat, m_lon, sigma_m
    if t_query_sec >= t_sec[-1]:
        return None
    query_tick = int(np.searchsorted(t_sec, t_query_sec, side="right") - 1)
    if query_tick < 0:
        query_tick = 0

    # Find first LoRa fix STRICTLY after query_tick. A fix at query_tick
    # itself is already absorbed into pf_cov_m[query_tick] (post-update);
    # we need a FUTURE anchor to back-propagate from.
    #
    # Campaign mode: clip the search window to the END of query_tick's
    # cycle. A LoRa fix in a later cycle belongs to a fresh drifter that
    # never observed the trajectory we're trying to back-propagate, so
    # the windowed RTS would be incoherent across the cycle boundary.
    cycle_idx = row.get("cycle_idx_per_tick")
    end_search = lora_fix_mask.size
    if cycle_idx is not None:
        my_cycle = int(cycle_idx[query_tick])
        # Last tick belonging to my_cycle.
        same_cycle = (cycle_idx == my_cycle)
        # last index where same_cycle is True (cycles are contiguous).
        last_in_cycle = int(np.where(same_cycle)[0].max())
        end_search = last_in_cycle + 1
    if query_tick + 1 >= end_search:
        return None
    remaining = lora_fix_mask[query_tick + 1:end_search]
    if not bool(np.any(remaining)):
        return None
    next_fix_tick = int(query_tick + 1 + np.argmax(remaining))

    # Windowed RTS over [query_tick, next_fix_tick] inclusive. The slice's
    # last tick is the LoRa-fix tick → backward pass treats it as the
    # anchor; smoothed values at earlier ticks pull toward that anchor.
    end_excl = next_fix_tick + 1
    sliced = rts_smooth_trajectory(
        pf_mean_lats=pf_mean_lats[query_tick:end_excl],
        pf_mean_lons=pf_mean_lons[query_tick:end_excl],
        pf_cov_m=pf_cov_m[query_tick:end_excl],
        depths=depths[query_tick:end_excl],
        lora_fix_mask=lora_fix_mask[query_tick:end_excl],
        dt_sec=dt_sec,
        process_noise_cfg=ProcessNoiseConfig(),
    )

    # Linearly interp the smoothed (mean, cov) between query_tick and
    # query_tick+1 to land on t_query. If only one tick in the window
    # (next_fix_tick == query_tick, but we excluded that above), fall
    # back to that tick's value.
    s_lats, s_lons = sliced.to_latlon()
    if sliced.covs_m.shape[0] >= 2:
        dt_local = t_sec[query_tick + 1] - t_sec[query_tick]
        a = float((t_query_sec - t_sec[query_tick]) / max(dt_local, 1e-9))
        a = max(0.0, min(1.0, a))
        lat = float((1 - a) * s_lats[0] + a * s_lats[1])
        lon = float((1 - a) * s_lons[0] + a * s_lons[1])
        c_interp = (1 - a) * sliced.covs_m[0] + a * sliced.covs_m[1]
    else:
        lat = float(s_lats[0])
        lon = float(s_lons[0])
        c_interp = sliced.covs_m[0]
    sigma_m = float(np.sqrt(0.5 * (c_interp[0, 0] + c_interp[1, 1])))
    return lat, lon, sigma_m


# ---------- Top-level orchestrator ----------

def _do_detect_and_reconstruct(
    drifters: list[dict], all_events: list,
) -> tuple[list, list, np.ndarray]:
    """Detect each event at each drifter (range-gated by truth distance)
    and run weighted-LSQ TDOA in TWO modes:

      - MODE (a) full-retro: drifter posterior comes from the full-
        mission RTS smoother. Conditions on ALL LoRa fixes including
        ones in the future of t_event. Operationally relevant for
        end-of-mission recovery analysis.
      - MODE (b) next-surface-conditioned: drifter posterior comes from
        a windowed RTS smoother that conditions only on observations
        through THIS drifter's first LoRa fix at-or-after t_event.
        Operationally relevant for "drifter publishes σ_pos at next
        surface dwell" deployment story.

    Both modes share the same detection set (range-gated by truth
    drifter position), but a drifter may be excluded from mode (b) if
    it has no LoRa fix after t_event in its mission (no anchor to
    back-project from).

    Returns (reconstructions_a, reconstructions_b, detect_counts).
    """
    rng_toa = np.random.default_rng(SEED_BASE + 9999)
    recons_a: list[Reconstruction] = []
    recons_b: list[Reconstruction] = []
    detect_counts = np.zeros(len(all_events), dtype=int)
    for e_idx, event in enumerate(all_events):
        dets_a: list[Detection] = []
        dets_b: list[Detection] = []
        for d_idx, drifter in enumerate(drifters):
            # Acoustic-physics observation: TOA from event truth to
            # drifter TRUTH position.
            t_lat, t_lon = _truth_at_t(drifter, event.t_sec)
            true_dist = _distance_m_latlon(t_lat, t_lon,
                                            event.lat, event.lon)
            if true_dist > DETECT_RANGE_M:
                continue
            toa = event.t_sec + true_dist / C_WATER_MS \
                  + float(rng_toa.normal(0.0, SIGMA_TOA_S))

            # Mode (a): full-mission smoothed posterior at event time.
            s_lat_a, s_lon_a, s_sigma_a = _smoothed_at_t(drifter,
                                                          event.t_sec)
            dets_a.append(Detection(
                event_idx=e_idx, drifter_id=d_idx,
                drifter_lat=s_lat_a, drifter_lon=s_lon_a,
                drifter_sigma_m=s_sigma_a,
                distance_m=true_dist, toa_s=toa,
            ))

            # Mode (b): next-surface-conditioned posterior. May return
            # None if drifter has no LoRa fix after t_event.
            res_b = _smoothed_mode_b_at_t(drifter, event.t_sec)
            if res_b is not None:
                s_lat_b, s_lon_b, s_sigma_b = res_b
                dets_b.append(Detection(
                    event_idx=e_idx, drifter_id=d_idx,
                    drifter_lat=s_lat_b, drifter_lon=s_lon_b,
                    drifter_sigma_m=s_sigma_b,
                    distance_m=true_dist, toa_s=toa,
                ))
        detect_counts[e_idx] = len(dets_a)

        if len(dets_a) >= 3:
            recons_a.append(_trilaterate_tdoa(dets_a, event))
        else:
            recons_a.append(Reconstruction(
                event_idx=e_idx, src=event.src, n_detectors=len(dets_a),
                truth_lat=event.lat, truth_lon=event.lon,
                truth_t_sec=event.t_sec,
                detector_ids=tuple(d.drifter_id for d in dets_a),
                detector_sigma_pos_used=tuple(
                    float(d.drifter_sigma_m) for d in dets_a
                ),
            ))
        if len(dets_b) >= 3:
            rec_b = _trilaterate_tdoa(dets_b, event)
            # Time-to-detection (mode b only): the latest surface event
            # among detecting drifters; reconstruction can't happen until
            # then (each drifter exfils its TOA + smoothed σ_pos at its
            # next surface dwell).
            t_surf_per_det = []
            for det in dets_b:
                d_row = drifters[det.drifter_id]
                t_surf_per_det.append(_first_surface_at_or_after(
                    d_row, event.t_sec,
                ))
            t_surf_arr = np.array(t_surf_per_det)
            if np.all(np.isfinite(t_surf_arr)):
                rec_b.t_detect_sec = float(np.max(t_surf_arr))
                rec_b.time_to_detect_sec = float(
                    rec_b.t_detect_sec - event.t_sec
                )
            # Centroid distance for downstream bucketing.
            cx = float(np.mean([d.drifter_lat for d in dets_b]))
            cy = float(np.mean([d.drifter_lon for d in dets_b]))
            rec_b.dist_to_detector_centroid_m = _distance_m_latlon(
                cx, cy, event.lat, event.lon,
            )
            recons_b.append(rec_b)
        else:
            recons_b.append(Reconstruction(
                event_idx=e_idx, src=event.src, n_detectors=len(dets_b),
                truth_lat=event.lat, truth_lon=event.lon,
                truth_t_sec=event.t_sec,
                detector_ids=tuple(d.drifter_id for d in dets_b),
                detector_sigma_pos_used=tuple(
                    float(d.drifter_sigma_m) for d in dets_b
                ),
            ))
    return recons_a, recons_b, detect_counts


def _print_policy_report(
    policy: PolicySpec, drifters: list[dict],
    recons_a: list, recons_b: list,
    detect_counts: np.ndarray, all_events: list,
) -> None:
    def _summarize(label: str, recons: list) -> None:
        n = sum(1 for r in recons if np.isfinite(r.error_m))
        print(f"  [{label}] events reconstructed: {n} / {len(all_events)} "
              f"({100 * n / max(len(all_events), 1):.1f}%)", flush=True)
        if n > 0:
            errs = np.array([r.error_m for r in recons
                              if np.isfinite(r.error_m)])
            sigmas = np.array([r.sigma_m for r in recons
                                if np.isfinite(r.sigma_m)])
            print(f"    recon error:   mean={errs.mean():.0f}m  "
                  f"median={np.median(errs):.0f}m  "
                  f"p95={np.percentile(errs, 95):.0f}m", flush=True)
            print(f"    σ_event post:  mean={sigmas.mean():.0f}m  "
                  f"median={np.median(sigmas):.0f}m  "
                  f"p95={np.percentile(sigmas, 95):.0f}m", flush=True)

    _summarize("mode-a full-retro          ", recons_a)
    _summarize("mode-b next-surface-cond   ", recons_b)
    n_surf_total = sum(d["n_surfacings"] for d in drifters)
    print(f"  fleet surface events: {n_surf_total} "
          f"({n_surf_total / max(len(drifters), 1):.1f}/drifter)", flush=True)


def main() -> None:
    print(f"=== fleet sim v0 ({len(STATIONS)} drifters × "
          f"{RUN_HOURS}h, N_PROCS={N_PROCS}) ===", flush=True)
    print(f"  policies to evaluate: "
          f"{[p.name for p in POLICIES_TO_EVAL]}", flush=True)

    mission_dur = RUN_HOURS * 3600.0

    # --- Generate events FIRST so PostEventSurfacingPolicy can wire them
    # into per-drifter detectors. Same RNG seeds across policies → same
    # event set → directly comparable σ_event distributions.
    print(f"\n--- generating events (shared across policies) ---", flush=True)
    point_events = _generate_point_events(
        rate_per_h=POINT_EVENT_RATE_PER_H,
        mission_dur_sec=mission_dur, seed=POINT_EVENT_SEED,
    )
    boat_tracks, boat_events = _generate_boat_tracks(
        n_boats=BOAT_COUNT, mission_dur_sec=mission_dur,
        speed_ms=BOAT_SPEED_MS, ping_interval_s=BOAT_PING_INTERVAL_S,
        seed=BOAT_SEED,
    )
    all_events = sorted(point_events + boat_events,
                         key=lambda e: e.t_sec)
    print(f"  point events: {len(point_events)} "
          f"(rate={POINT_EVENT_RATE_PER_H}/h)", flush=True)
    print(f"  boats: {len(boat_tracks)} → {len(boat_events)} pings",
          flush=True)
    print(f"  total events: {len(all_events)}", flush=True)

    # --- Loop over policies, sharing the worker pool so init (~4 min)
    # is paid only once across all policies.
    results_by_policy: dict[str, dict] = {}
    with Pool(processes=min(N_PROCS, len(STATIONS)),
              initializer=_init_worker) as pool:
        for policy in POLICIES_TO_EVAL:
            print(f"\n=== policy: {policy.label} "
                  f"({policy.name}) ===", flush=True)
            # Per-drifter pre-filter: events within AUDIBLE_EVENT_RADIUS_M
            # of the station target (used by PostEventSurfacingPolicy's
            # detector; ignored by FixedIntervalPolicy).
            jobs = []
            for s_idx in range(len(STATIONS)):
                s_lat_t, s_lon_t, _ = STATIONS[s_idx]
                audible = [
                    e for e in all_events
                    if _distance_m_latlon(e.lat, e.lon, s_lat_t, s_lon_t)
                       <= AUDIBLE_EVENT_RADIUS_M
                ]
                jobs.append((s_idx, policy.name, audible))

            t_pol = time.time()
            drifters = pool.map(_run_one_drifter, jobs)
            print(f"  {len(drifters)} missions done "
                  f"(wall {time.time() - t_pol:.0f}s)", flush=True)

            recons_a, recons_b, detect_counts = \
                _do_detect_and_reconstruct(drifters, all_events)
            _print_policy_report(policy, drifters, recons_a, recons_b,
                                  detect_counts, all_events)

            results_by_policy[policy.name] = {
                "policy": policy,
                "drifters": drifters,
                # Mode (a) = full-mission RTS smoother (end-of-mission
                # operational story).
                "reconstructions_a": recons_a,
                # Mode (b) = each drifter's next-surface-conditioned
                # windowed smoother (deployment "alert" story).
                "reconstructions_b": recons_b,
                # Default `reconstructions` alias = mode (a) for backward
                # compatibility with the trailing chart code.
                "reconstructions": recons_a,
                "detect_counts": detect_counts,
            }

    # The trailing diagnostic + chart code below uses the LAST policy's
    # results for the per-policy panel views; the comparison chart at
    # the very end shows σ_event distributions across all policies.
    last_policy_name = POLICIES_TO_EVAL[-1].name
    drifters = results_by_policy[last_policy_name]["drifters"]
    reconstructions = results_by_policy[last_policy_name]["reconstructions"]
    detect_counts = results_by_policy[last_policy_name]["detect_counts"]

    # --- Diagnostic: where are drifters vs. events? ---
    print(f"\n--- diagnostic: drifter/event geometry ---", flush=True)
    for d in drifters:
        positions = []
        for t_q in [0.0, mission_dur * 0.5, mission_dur * 0.99]:
            l, o, s = _smoothed_at_t(d, t_q)
            positions.append((l, o, s))
        print(f"  drifter {d['s_idx']} station=({d['station_lat']:.4f},"
              f"{d['station_lon']:.4f})", flush=True)
        for tag, (l, o, s) in zip(["t=0   ", "t=mid ", "t=end "], positions):
            print(f"    {tag} smoothed=({l:.4f},{o:.4f}) σ={s:.0f}m", flush=True)
    if all_events:
        ev_lats = np.array([e.lat for e in all_events])
        ev_lons = np.array([e.lon for e in all_events])
        print(f"  events lat range: [{ev_lats.min():.4f}, {ev_lats.max():.4f}]",
              flush=True)
        print(f"  events lon range: [{ev_lons.min():.4f}, {ev_lons.max():.4f}]",
              flush=True)
        # For each drifter, find closest event using mid-mission position.
        for d in drifters:
            l, o, _ = _smoothed_at_t(d, mission_dur * 0.5)
            dists = np.array([_distance_m_latlon(l, o, e.lat, e.lon)
                              for e in all_events])
            print(f"  drifter {d['s_idx']} mid-mission: closest event at "
                  f"{dists.min():.0f}m, n_within_5km={int((dists < 5000).sum())}",
                  flush=True)

    # --- Cross-policy comparison (mode a + mode b side by side) ---
    print(f"\n=== cross-policy comparison ===", flush=True)
    cols = []
    for p in POLICIES_TO_EVAL:
        cols.append(f"{p.name}/a")
        cols.append(f"{p.name}/b")
    print(f"  {'metric':<32}" + "  ".join(f"{c:>22}" for c in cols),
          flush=True)
    metrics = [
        ("σ_event mean (m)",
         lambda recs: float(np.nanmean(
             [r.sigma_m for r in recs if np.isfinite(r.sigma_m)] or [np.nan]
         ))),
        ("σ_event p50 (m)",
         lambda recs: float(np.nanmedian(
             [r.sigma_m for r in recs if np.isfinite(r.sigma_m)] or [np.nan]
         ))),
        ("σ_event p95 (m)",
         lambda recs: float(np.nanpercentile(
             [r.sigma_m for r in recs if np.isfinite(r.sigma_m)] or [np.nan],
             95,
         ))),
        ("recon error mean (m)",
         lambda recs: float(np.nanmean(
             [r.error_m for r in recs if np.isfinite(r.error_m)] or [np.nan]
         ))),
        ("recon error p95 (m)",
         lambda recs: float(np.nanpercentile(
             [r.error_m for r in recs if np.isfinite(r.error_m)] or [np.nan],
             95,
         ))),
        ("events reconstructed",
         lambda recs: sum(1 for r in recs if np.isfinite(r.error_m))),
    ]
    for label, getter in metrics:
        cells = []
        for p in POLICIES_TO_EVAL:
            for mode_key in ["reconstructions_a", "reconstructions_b"]:
                v = getter(results_by_policy[p.name][mode_key])
                if isinstance(v, float):
                    cells.append(f"{v:>22.1f}")
                else:
                    cells.append(f"{v:>22}")
        print(f"  {label:<32}" + "  ".join(cells), flush=True)
    # Final per-policy rows for surfacings + station-keeping (mode-agnostic).
    surf_row = []
    sk_row = []
    for p in POLICIES_TO_EVAL:
        d = results_by_policy[p.name]["drifters"]
        s_total = sum(dr["n_surfacings"] for dr in d)
        sk_mean = float(np.mean([dr["ctrl_mean_m"] for dr in d]))
        # Each policy occupies two columns; both show the same value.
        surf_row.append(f"{s_total:>22}")
        surf_row.append(f"{s_total:>22}")
        sk_row.append(f"{sk_mean:>22.0f}")
        sk_row.append(f"{sk_mean:>22.0f}")
    print(f"  {'fleet surface events':<32}" + "  ".join(surf_row),
          flush=True)
    print(f"  {'station_keeping mean (m)':<32}" + "  ".join(sk_row),
          flush=True)

    # --- The remaining sections + chart use the last policy's results
    # for the per-source breakdown and trajectory panels. ---
    print(f"\n--- last-policy detail ({last_policy_name}) ---", flush=True)

    # By source class.
    point_recs = [r for r in reconstructions
                   if r.src == "point" and np.isfinite(r.error_m)]
    boat_recs = [r for r in reconstructions
                  if r.src.startswith("boat:") and np.isfinite(r.error_m)]
    if point_recs:
        e = np.array([r.error_m for r in point_recs])
        print(f"  point events: {len(point_recs)} reconstructed; "
              f"mean err = {e.mean():.0f}m, p95 = {np.percentile(e, 95):.0f}m",
              flush=True)
    if boat_recs:
        e = np.array([r.error_m for r in boat_recs])
        print(f"  boat pings:   {len(boat_recs)} reconstructed; "
              f"mean err = {e.mean():.0f}m, p95 = {np.percentile(e, 95):.0f}m",
              flush=True)

    # Per-boat track reconstruction quality.
    print(f"\n--- per-boat track reconstruction ---", flush=True)
    per_boat: dict[int, list[Reconstruction]] = {b.boat_id: []
                                                   for b in boat_tracks}
    for r in reconstructions:
        if r.src.startswith("boat:") and np.isfinite(r.error_m):
            bid = int(r.src.split(":")[1])
            per_boat.setdefault(bid, []).append(r)
    for b in boat_tracks:
        recs_b = per_boat.get(b.boat_id, [])
        if not recs_b:
            print(f"  boat {b.boat_id}: 0 reconstructed pings", flush=True)
            continue
        e = np.array([r.error_m for r in recs_b])
        print(f"  boat {b.boat_id}: {len(recs_b)} pings reconstructed; "
              f"mean err = {e.mean():.0f}m, p95 = {np.percentile(e, 95):.0f}m",
              flush=True)

    # --- Build chart ---
    print(f"\n--- building chart ---", flush=True)
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(15, 13))

    # Panel 1: drifter trajectories + detection-radius circles + events.
    ax = axes[0, 0]
    for d_idx, dr in enumerate(drifters):
        ax.plot(dr["truth_lons"], dr["truth_lats"],
                 color=f"C{d_idx}", lw=0.8, alpha=0.6,
                 label=f"D{d_idx + 1} truth")
        ax.scatter([dr["station_lon"]], [dr["station_lat"]],
                    s=40, marker="x", color=f"C{d_idx}")
    # Boat tracks (truth).
    for b in boat_tracks:
        events_b = b.positions()
        if events_b:
            xs = [e.lon for e in events_b]
            ys = [e.lat for e in events_b]
            ax.plot(xs, ys, color="grey", lw=0.5, alpha=0.6)
            ax.scatter([xs[0]], [ys[0]], s=20, marker=">",
                        color="grey", alpha=0.8)
    # Reconstructed boat positions.
    boat_truth_lats = [r.truth_lat for r in reconstructions
                        if r.src.startswith("boat:") and np.isfinite(r.error_m)]
    boat_truth_lons = [r.truth_lon for r in reconstructions
                        if r.src.startswith("boat:") and np.isfinite(r.error_m)]
    boat_recon_lats = [r.recon_lat for r in reconstructions
                        if r.src.startswith("boat:") and np.isfinite(r.error_m)]
    boat_recon_lons = [r.recon_lon for r in reconstructions
                        if r.src.startswith("boat:") and np.isfinite(r.error_m)]
    if boat_truth_lats:
        ax.scatter(boat_recon_lons, boat_recon_lats, s=8,
                    color="red", alpha=0.5, label="reconstructed boat")
    # Point events.
    p_lats = [e.lat for e in point_events]
    p_lons = [e.lon for e in point_events]
    if p_lats:
        ax.scatter(p_lons, p_lats, s=30, marker="*",
                    color="orange", alpha=0.6, label="point event")
    ax.set_xlabel("lon"); ax.set_ylabel("lat")
    ax.set_title(f"Fleet sim — {len(drifters)} drifters × {RUN_HOURS}h, "
                  f"{len(point_events)} point events + {len(boat_tracks)} boats")
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.grid(alpha=0.3)

    # Panel 2: detection count distribution.
    ax = axes[0, 1]
    ax.hist(detect_counts, bins=range(0, len(drifters) + 2),
             align="left", color="C0", alpha=0.7)
    ax.set_xlabel("# drifters detecting event")
    ax.set_ylabel("# events")
    ax.set_title(f"Detection count per event "
                  f"(R_detect={DETECT_RANGE_M:.0f}m)")
    ax.axvline(3, color="red", linestyle="--", lw=1.0,
                label="≥3 needed for TDOA")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, axis="y")

    # Panel 3: error distribution by source.
    ax = axes[1, 0]
    if point_recs:
        ax.hist([r.error_m for r in point_recs], bins=30,
                 alpha=0.6, color="C1", label=f"point ({len(point_recs)})")
    if boat_recs:
        ax.hist([r.error_m for r in boat_recs], bins=30,
                 alpha=0.6, color="C2", label=f"boat ({len(boat_recs)})")
    ax.set_xlabel("reconstruction error (m)")
    ax.set_ylabel("# events")
    ax.set_title("σ_event = |truth − reconstructed|, by source")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, axis="y")

    # Panel 4: σ vs # detectors scatter.
    ax = axes[1, 1]
    valid = [r for r in reconstructions if np.isfinite(r.error_m)]
    ax.scatter([r.n_detectors for r in valid],
                [r.error_m for r in valid],
                s=10, alpha=0.5, color="C0")
    ax.set_xlabel("# detectors")
    ax.set_ylabel("recon error (m)")
    ax.set_title("Reconstruction error vs detector count")
    ax.grid(alpha=0.3)
    ax.set_yscale("log")

    plt.tight_layout()
    fig.suptitle(f"Last policy: {last_policy_name}", y=1.00, fontsize=11)
    out = os.path.join(os.path.dirname(__file__), "figures",
                        f"fleet_sim_v0_{last_policy_name}.png")
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print(f"  saved {out}", flush=True)
    plt.close(fig)

    # --- Cross-policy σ_event comparison chart (mode a + mode b) ---
    print(f"\n--- building cross-policy comparison chart ---", flush=True)
    fig2, axes2 = plt.subplots(1, 3, figsize=(16, 5))
    LINE_STYLES = {"a": "-", "b": "--"}
    # Panel A: σ_event posterior CDF per (policy, mode)
    ax = axes2[0]
    for p_idx, p in enumerate(POLICIES_TO_EVAL):
        for mode_letter, mode_key in [("a", "reconstructions_a"),
                                        ("b", "reconstructions_b")]:
            recs = results_by_policy[p.name][mode_key]
            s = np.array([r.sigma_m for r in recs
                          if np.isfinite(r.sigma_m)])
            if s.size == 0:
                continue
            ax.plot(np.sort(s), np.linspace(0, 1, s.size),
                     lw=1.5, color=f"C{p_idx}",
                     linestyle=LINE_STYLES[mode_letter],
                     label=f"{p.name}/{mode_letter} (n={s.size})")
    ax.set_xlabel("σ_event posterior (m)")
    ax.set_ylabel("CDF")
    ax.set_title("σ_event by policy + mode\n(a=full-retro solid; b=next-surf dashed)")
    ax.set_xscale("log")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)

    # Panel B: recon error CDF per (policy, mode)
    ax = axes2[1]
    for p_idx, p in enumerate(POLICIES_TO_EVAL):
        for mode_letter, mode_key in [("a", "reconstructions_a"),
                                        ("b", "reconstructions_b")]:
            recs = results_by_policy[p.name][mode_key]
            e = np.array([r.error_m for r in recs
                          if np.isfinite(r.error_m)])
            if e.size == 0:
                continue
            ax.plot(np.sort(e), np.linspace(0, 1, e.size),
                     lw=1.5, color=f"C{p_idx}",
                     linestyle=LINE_STYLES[mode_letter],
                     label=f"{p.name}/{mode_letter} (n={e.size})")
    ax.set_xlabel("recon error |truth − reconstructed| (m)")
    ax.set_ylabel("CDF")
    ax.set_title("Recon error by policy + mode")
    ax.set_xscale("log")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)

    # Panel C: surfacing-count vs σ_event-median per (policy, mode)
    ax = axes2[2]
    for p_idx, p in enumerate(POLICIES_TO_EVAL):
        d = results_by_policy[p.name]["drifters"]
        n_surf = sum(dr["n_surfacings"] for dr in d)
        sk = float(np.mean([dr["ctrl_mean_m"] for dr in d]))
        for mode_letter, mode_key, marker in [
            ("a", "reconstructions_a", "o"),
            ("b", "reconstructions_b", "s"),
        ]:
            recs = results_by_policy[p.name][mode_key]
            s = np.array([r.sigma_m for r in recs
                          if np.isfinite(r.sigma_m)])
            if s.size == 0:
                continue
            ax.scatter(n_surf, np.median(s), s=80,
                       color=f"C{p_idx}", marker=marker,
                       label=f"{p.name}/{mode_letter}")
            ax.annotate(f"{p.name}/{mode_letter}\nsk={sk:.0f}m",
                        (n_surf, np.median(s)),
                         fontsize=7, alpha=0.7,
                         xytext=(5, 5), textcoords="offset points")
    ax.set_xlabel("fleet surface events (power proxy)")
    ax.set_ylabel("σ_event posterior median (m)")
    ax.set_title("Power vs σ_event tradeoff\n(a=○ full-retro; b=□ next-surf)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)

    plt.tight_layout()
    out2 = os.path.join(os.path.dirname(__file__), "figures",
                         "fleet_sim_v0_policy_comparison.png")
    fig2.savefig(out2, dpi=110, bbox_inches="tight")
    print(f"  saved {out2}", flush=True)
    plt.close(fig2)


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
