"""Bootstrap-validate the analytical σ_event formula in
`_fleet_sim_v0._trilaterate_tdoa`.

Setup: place K drifters in a synthetic triangular/square geometry, an
event at a known position, and compute σ_event two ways:

  1. ANALYTICAL — the formula we use in the sim:
       Σ_post = (J^T W J)^(-1)
       σ_event = sqrt(0.5 * (Σ_post[0,0] + Σ_post[1,1]))
     where W_ii = 1 / (σ_TOA² + σ_pos_d²/c²) and the σ_pos_d is the
     per-axis scalar from the (assumed-isotropic) drifter posterior.

  2. BOOTSTRAP — sample many realisations of:
       (a) drifter TRUE position from N(μ_d, Σ_d)
       (b) intrinsic TOA noise ε_d ~ N(0, σ_TOA²)
     For each sample, compute the OBSERVED TOAs from the *truth* drifter
     positions (drifters don't know their own positions when emitting
     TOAs). Run the same weighted LSQ used in the sim against the
     posterior-mean drifter positions (μ_d) — i.e., the LSQ uses our
     belief, not truth. Empirical std of the (lat, lon) point estimates
     across bootstrap samples = "true" σ_event.

Compare the two. Discrepancy reveals which approximation is failing:
  - Isotropic Σ_d (we use scalar σ_pos vs full anisotropic cov)
  - Linearisation error (geometry where Gauss-Newton is poor)
  - Independence of drifter posteriors (covered: the bootstrap
    samples drifters independently, matching the sim's current
    assumption — adding cross-drifter correlation would test
    that separately)

Three test geometries:
  - 4-drifter symmetric square, event at centroid
  - 3-drifter equilateral, event at centroid
  - 3-drifter colinear-ish, event off-axis (poor geometry)

Each at σ_pos = 100, 250, 500 m (representative of mid-leg / end-leg
sigma seen in the fleet sim).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import numpy as np


# Same constants as fleet sim.
C_WATER_MS = 1500.0
SIGMA_TOA_S = 0.005
EARTH_R_M = 111_320.0   # meters per degree


def _latlon_to_enu(lat: float, lon: float, ref_lat: float, ref_lon: float
                    ) -> tuple[float, float]:
    cos_lat = float(np.cos(np.deg2rad(ref_lat)))
    return ((lon - ref_lon) * EARTH_R_M * cos_lat,
            (lat - ref_lat) * EARTH_R_M)


def _enu_to_latlon(x: float, y: float, ref_lat: float, ref_lon: float
                    ) -> tuple[float, float]:
    cos_lat = float(np.cos(np.deg2rad(ref_lat)))
    return (ref_lat + y / EARTH_R_M, ref_lon + x / (EARTH_R_M * cos_lat))


def _gn_lsq(drifter_enu: np.ndarray, toa_obs: np.ndarray,
             sigma_pos: np.ndarray, x0: np.ndarray) -> np.ndarray:
    """Weighted Gauss-Newton over (x, y, t). Returns the converged 3-vec."""
    sigma_eff = np.sqrt(SIGMA_TOA_S ** 2 + (sigma_pos / C_WATER_MS) ** 2)
    inv_var = 1.0 / np.maximum(sigma_eff ** 2, 1e-12)
    W_sqrt = np.sqrt(inv_var)
    x = x0.copy()
    for _ in range(50):
        diff = drifter_enu - x[:2]
        dist = np.linalg.norm(diff, axis=1)
        toa_pred = x[2] + dist / C_WATER_MS
        r = toa_obs - toa_pred
        with np.errstate(divide="ignore", invalid="ignore"):
            J = np.column_stack([
                diff[:, 0] / np.maximum(dist, 1e-3) / C_WATER_MS,
                diff[:, 1] / np.maximum(dist, 1e-3) / C_WATER_MS,
                -np.ones_like(dist),
            ])
        Jw = J * W_sqrt[:, None]
        rw = r * W_sqrt
        try:
            dx, *_ = np.linalg.lstsq(Jw, rw, rcond=None)
        except np.linalg.LinAlgError:
            break
        x = x - dx
        if np.linalg.norm(dx[:2]) < 0.01 and abs(dx[2]) < 1e-5:
            break
    return x


def _analytical_sigma_event(drifter_enu: np.ndarray, x_opt: np.ndarray,
                              sigma_pos: np.ndarray) -> float:
    """Σ_post = (J^T W J)^(-1) at the optimum; sigma_event = sqrt(½ tr_xy)."""
    sigma_eff = np.sqrt(SIGMA_TOA_S ** 2 + (sigma_pos / C_WATER_MS) ** 2)
    inv_var = 1.0 / np.maximum(sigma_eff ** 2, 1e-12)
    diff = drifter_enu - x_opt[:2]
    dist = np.linalg.norm(diff, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        J = np.column_stack([
            diff[:, 0] / np.maximum(dist, 1e-3) / C_WATER_MS,
            diff[:, 1] / np.maximum(dist, 1e-3) / C_WATER_MS,
            -np.ones_like(dist),
        ])
    JtWJ = J.T @ (J * inv_var[:, None])
    Sigma_post = np.linalg.inv(JtWJ)
    return float(np.sqrt(0.5 * (Sigma_post[0, 0] + Sigma_post[1, 1])))


def _bootstrap_sigma_event(drifter_enu_mean: np.ndarray,
                             event_enu: np.ndarray, t_event: float,
                             sigma_pos: np.ndarray, n_boot: int,
                             seed: int = 0) -> dict:
    """Sample (drifter truth pos, TOA noise) and run LSQ against the
    posterior-mean drifter positions. Returns a dict with both std-based
    and IQR-based (outlier-robust) empirical σ_event, plus bias and
    failure-rate diagnostics."""
    rng = np.random.default_rng(seed)
    n_d = drifter_enu_mean.shape[0]
    estimates = np.zeros((n_boot, 2))
    for b in range(n_boot):
        eta = rng.normal(0.0, 1.0, size=(n_d, 2)) * sigma_pos[:, None]
        drifter_truth = drifter_enu_mean + eta
        true_dist = np.linalg.norm(drifter_truth - event_enu, axis=1)
        eps = rng.normal(0.0, SIGMA_TOA_S, size=n_d)
        toa_obs = t_event + true_dist / C_WATER_MS + eps
        x0 = np.array([drifter_enu_mean[:, 0].mean(),
                        drifter_enu_mean[:, 1].mean(),
                        toa_obs.min() - 1.0])
        x_est = _gn_lsq(drifter_enu_mean, toa_obs, sigma_pos, x0)
        estimates[b] = x_est[:2]
    # Distance from event truth — used for failure detection.
    err = np.linalg.norm(estimates - event_enu, axis=1)
    # Std-based σ (around empirical mean; sensitive to outliers).
    sigma_std = float(np.sqrt(
        0.5 * (estimates[:, 0].var() + estimates[:, 1].var())
    ))
    # IQR-based σ per axis, scaled to Gaussian-equivalent (σ = IQR/1.349).
    # Robust to catastrophic LSQ failures.
    iqr_x = float(np.subtract(*np.percentile(estimates[:, 0], [75, 25])))
    iqr_y = float(np.subtract(*np.percentile(estimates[:, 1], [75, 25])))
    sigma_iqr = float(np.sqrt(0.5 * ((iqr_x / 1.349) ** 2
                                       + (iqr_y / 1.349) ** 2)))
    bias = float(np.linalg.norm(estimates.mean(axis=0) - event_enu))
    median_pos_err = float(np.median(err))
    # Failure rate: estimates more than 10× the analytical σ-equivalent
    # would be from the truth. Using the spread of "good" samples to
    # set the threshold dynamically — compare 99th percentile to median.
    p99 = float(np.percentile(err, 99))
    p50 = float(np.percentile(err, 50))
    failure_rate = float(np.mean(err > 100.0 * (p50 + 1.0)))
    return {
        "sigma_std": sigma_std,
        "sigma_iqr": sigma_iqr,
        "bias": bias,
        "median_err": median_pos_err,
        "p99_err": p99,
        "failure_rate": failure_rate,
    }


@dataclass(frozen=True)
class Geometry:
    name: str
    drifter_enu: np.ndarray   # (K, 2)
    event_enu: np.ndarray     # (2,)


GEOMETRIES = [
    Geometry(
        name="4-drifter symmetric square (2km side), event at centroid",
        drifter_enu=np.array([
            [-1000, -1000], [+1000, -1000],
            [-1000, +1000], [+1000, +1000],
        ], dtype=float),
        event_enu=np.array([0.0, 0.0]),
    ),
    Geometry(
        name="3-drifter equilateral (2km side), event at centroid",
        drifter_enu=np.array([
            [+1000, -578], [-1000, -578], [0, +1155],
        ], dtype=float),
        event_enu=np.array([0.0, 0.0]),
    ),
    Geometry(
        name="3-drifter colinear-ish, event 2km off-axis",
        drifter_enu=np.array([
            [-1500, 0], [0, 100], [+1500, 0],
        ], dtype=float),
        event_enu=np.array([0.0, 2000.0]),
    ),
    Geometry(
        name="3-drifter equilateral (2km side), event 2km outside cluster",
        drifter_enu=np.array([
            [+1000, -578], [-1000, -578], [0, +1155],
        ], dtype=float),
        event_enu=np.array([2500.0, 0.0]),
    ),
]

SIGMA_POS_VALUES = [50.0, 100.0, 250.0, 500.0, 1000.0]
N_BOOTSTRAP = 5000


def main() -> None:
    print(f"Bootstrap validation of σ_event analytical formula", flush=True)
    print(f"  N_BOOTSTRAP = {N_BOOTSTRAP}", flush=True)
    print(f"  σ_TOA = {SIGMA_TOA_S * 1000:.1f} ms", flush=True)
    print(flush=True)
    for geom in GEOMETRIES:
        print(f"=== {geom.name} ===", flush=True)
        print(
            f"{'σ_pos':>8}  "
            f"{'σ_an':>8}  "
            f"{'σ_iqr':>8}  "
            f"{'σ_std':>10}  "
            f"{'ratio_iqr':>10}  "
            f"{'med_err':>8}  "
            f"{'p99_err':>10}  "
            f"{'bias':>8}",
            flush=True,
        )
        for sigma_pos in SIGMA_POS_VALUES:
            sigma_pos_arr = np.full(geom.drifter_enu.shape[0], sigma_pos)
            true_dist = np.linalg.norm(
                geom.drifter_enu - geom.event_enu, axis=1
            )
            t_event = 0.0
            toa_clean = t_event + true_dist / C_WATER_MS
            x0 = np.array([geom.drifter_enu[:, 0].mean(),
                            geom.drifter_enu[:, 1].mean(),
                            toa_clean.min() - 1.0])
            x_opt = _gn_lsq(geom.drifter_enu, toa_clean,
                             sigma_pos_arr, x0)
            sigma_an = _analytical_sigma_event(
                geom.drifter_enu, x_opt, sigma_pos_arr,
            )
            bs = _bootstrap_sigma_event(
                geom.drifter_enu, geom.event_enu, t_event,
                sigma_pos_arr, N_BOOTSTRAP, seed=42,
            )
            ratio_iqr = sigma_an / max(bs["sigma_iqr"], 1e-9)
            print(
                f"{sigma_pos:>8.0f}  "
                f"{sigma_an:>8.0f}  "
                f"{bs['sigma_iqr']:>8.0f}  "
                f"{bs['sigma_std']:>10.1e}  "
                f"{ratio_iqr:>10.3f}  "
                f"{bs['median_err']:>8.0f}  "
                f"{bs['p99_err']:>10.1e}  "
                f"{bs['bias']:>8.1e}",
                flush=True,
            )
        print(flush=True)


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
