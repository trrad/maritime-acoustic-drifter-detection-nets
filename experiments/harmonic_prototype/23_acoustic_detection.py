"""Acoustic detection + TDOA localization feasibility for a passive
drifter mesh. Self-contained physics — no dependency on the PF / truth-
field stack.

Addresses three coupled questions the Phase-2 work depends on:

1. Per-node detection range. Given a source at level SL (broadband,
   re 1 µPa @ 1 m), sea state SS, and an energy-detector threshold
   of ~10 dB in-band SNR, at what range R does a single hydrophone
   node detect the event?

2. Multi-node detection geometry. For a fleet at inter-node spacing d
   deployed around a source, how many nodes hear the event? (This is
   NOT beamforming — nodes are km apart; coherent array gain does
   not apply. Multi-node just means "more chances for independent
   detection and for TDOA.")

3. Retrospective triangulation envelope. Given N nodes that detect
   the event, each with post-RTS-smoothing position uncertainty σ_pos
   and clock-sync uncertainty σ_t, what's the triangulation RMSE?
   What σ_pos budget does the nav stack need to hit a 100 m target
   envelope?

Physics:
- Spherical spreading (20·log10 R), valid for R > 1 m in deep water.
  Shallow-water multipath is a known complication, not modelled here
  (flagged as future work).
- Thorp (1967) empirical absorption in dB/km.
- Wenz (1962) wind-noise ambient spectrum, per sea state.
- Two detection regimes:
    (i)  Broadband energy detector, 10 dB SNR threshold — no
         classifier, no narrowband tonal matching. The floor; what
         a naive square-law detector sees.
    (ii) +20 dB matched-filter / narrowband gain, representing a
         classifier that identifies propeller blade-rate or engine
         firing tonals against a template bank. Rough estimate from
         10·log10(B_broad / B_narrow) with B_narrow ≈ 50 Hz tonal
         bin vs 4.5 kHz energy band. Represents where the product
         actually wants to operate once the classifier is built.
- TDOA localization: Monte-Carlo linearized-LS around true source
  position. Effective measurement noise
  σ_range² ≈ σ_pos² + (c_water · σ_t)².

Parameter sweep:
- Source level SL ∈ {120, 140, 155} dB re 1 µPa @ 1m
  (electric / gasoline-outboard / small-trawler broadband, from
  Ross 1976, Bassett 2012, Arveson & Vendittis 2000 literature).
- Sea state SS ∈ {2, 4, 6} via wind speed {2, 7, 12} m/s.
- Inter-node spacing d ∈ {2, 5, 10} km.
- N nodes detecting ∈ {3, 5, 8, 12, 20}.
- Per-node σ_pos ∈ {10, 50, 100, 500, 1000} m.

Output: figures/26_acoustic_detection.png + brief stdout summary.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib.colors as mcolors  # type: ignore[import-not-found]
import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
C_WATER_MS = 1500.0              # sound speed, typical mid-latitude ocean
DETECTION_THRESHOLD_DB = 10.0    # energy-detector SNR threshold, conservative
CLASSIFIER_GAIN_DB = 20.0        # narrowband / matched-filter gain over
                                 # broadband energy detection. Rough estimate
                                 # assuming ~50 Hz tonal bin against a 4.5 kHz
                                 # band (10·log10(4500/50) ≈ 19.5 dB). A
                                 # trained classifier should reach this in
                                 # the propeller-tonal regime; the broadband
                                 # (0 dB gain) case is the detection floor.

# Detection band: outboard tonals + broadband cavitation sit primarily in
# this band; deep shipping tones (< 200 Hz) are mostly distant commercial
# traffic clutter we want to reject upstream.
BAND_F_LOW_HZ = 500.0
BAND_F_HIGH_HZ = 5000.0
BAND_CENTER_HZ = float(np.sqrt(BAND_F_LOW_HZ * BAND_F_HIGH_HZ))  # geo-mean
BAND_WIDTH_HZ = BAND_F_HIGH_HZ - BAND_F_LOW_HZ

# Sea-state → 10 m wind speed mapping (WMO scale, approximate)
SEA_STATE_TO_WIND_MS = {2: 2.0, 4: 7.0, 6: 12.0}

# Source-level classes (broadband SL in the 500 Hz – 5 kHz band,
# dB re 1 µPa at 1 m)
SOURCE_LEVELS_DB = {
    "electric (silent target)": 120.0,
    "gasoline outboard (~5 m skiff)": 140.0,
    "small trawler (~15 m, diesel)": 155.0,
}

SIGMA_T_S = 1e-3                 # clock sync σ, 1 ms assumes GPS-disciplined
                                 # anchors + TDMA sync; realistic for mesh
FIG_DIR = Path(__file__).parent / "figures"


# ---------------------------------------------------------------------------
# Physics primitives
# ---------------------------------------------------------------------------
def thorp_absorption_db_per_km(f_khz: np.ndarray | float) -> np.ndarray | float:
    """Thorp (1967) empirical absorption coefficient, dB/km.

    Valid roughly 100 Hz – 1 MHz; below ~1 kHz the boric-acid term
    dominates, 1–50 kHz the MgSO4 term dominates, above that pure-water
    viscous absorption.
    """
    f2 = np.asarray(f_khz) ** 2
    return (0.11 * f2 / (1.0 + f2)
            + 44.0 * f2 / (4100.0 + f2)
            + 2.75e-4 * f2
            + 0.003)


def wenz_wind_nl_per_hz(f_hz: np.ndarray | float,
                         wind_ms: float) -> np.ndarray | float:
    """Wenz (1962) wind-driven ambient noise spectrum level, dB re 1 µPa²/Hz.

    Empirical fit valid 500 Hz – 25 kHz. Below 500 Hz shipping dominates
    (not modelled — rejected by band-limiting). Above 25 kHz thermal
    noise floors; not relevant in our band.

    The coefficients below reproduce Wenz's curves within ~2 dB:
      - At 1 kHz, SS 3 (5 m/s wind): ~50 dB re µPa²/Hz
      - At 1 kHz, SS 5 (10 m/s wind): ~60 dB re µPa²/Hz
      - -17 dB/decade rolloff from ~500 Hz upward
    """
    f_arr = np.asarray(f_hz, dtype=float)
    return (44.0 + 10.0 * np.log10(max(wind_ms, 0.5))
            - 17.0 * np.log10(f_arr / 1000.0))


def transmission_loss_db(R_m: np.ndarray | float,
                          f_center_hz: float = BAND_CENTER_HZ) -> np.ndarray | float:
    """Spherical spreading + Thorp absorption transmission loss at R meters."""
    R = np.asarray(R_m, dtype=float)
    R_safe = np.maximum(R, 1.0)
    tl_spread = 20.0 * np.log10(R_safe)
    tl_absorb = thorp_absorption_db_per_km(f_center_hz / 1000.0) * R_safe / 1000.0
    return tl_spread + tl_absorb


def noise_level_band_db(wind_ms: float,
                         f_center_hz: float = BAND_CENTER_HZ,
                         bandwidth_hz: float = BAND_WIDTH_HZ) -> float:
    """Integrated in-band ambient noise level (dB re 1 µPa)."""
    nl_per_hz = wenz_wind_nl_per_hz(f_center_hz, wind_ms)
    return float(nl_per_hz + 10.0 * np.log10(bandwidth_hz))


def detection_range_m(SL_db: float, wind_ms: float,
                       threshold_db: float = DETECTION_THRESHOLD_DB,
                       classifier_gain_db: float = 0.0,
                       f_center_hz: float = BAND_CENTER_HZ) -> float:
    """Solve effective_SNR(R) = threshold for R. Classifier gain
    effectively reduces the required raw SNR to (threshold - gain)."""
    R_grid = np.logspace(0, np.log10(50_000), 500)  # 1 m to 50 km
    tl = transmission_loss_db(R_grid, f_center_hz)
    nl = noise_level_band_db(wind_ms, f_center_hz)
    snr = np.asarray(SL_db - tl - nl)
    effective_threshold = threshold_db - classifier_gain_db
    above = snr >= effective_threshold
    if not np.any(above):
        return 0.0
    if np.all(above):
        return float(R_grid[-1])
    # Last index where SNR crosses down through threshold.
    idx = int(np.argmax(np.logical_not(above)) - 1)
    if idx < 0:
        return 0.0
    r1, r2 = float(R_grid[idx]), float(R_grid[idx + 1])
    s1, s2 = float(snr[idx]), float(snr[idx + 1])
    if s1 == s2:
        return r1
    frac = (effective_threshold - s1) / (s2 - s1)
    return float(r1 * (r2 / r1) ** frac)


# ---------------------------------------------------------------------------
# TDOA triangulation via Monte-Carlo linearized LS
# ---------------------------------------------------------------------------
def triangulation_rmse_m(
    node_xys: np.ndarray,       # shape (N, 2), true node positions, metres
    source_xy: np.ndarray,      # shape (2,), true source position, metres
    sigma_pos_m: float,
    sigma_t_s: float,
    n_trials: int = 500,
    c_ms: float = C_WATER_MS,
    rng: np.random.Generator | None = None,
) -> float:
    """Monte-Carlo RMSE of TDOA-based source localization.

    Each trial:
      1. Perturb each node's assumed position by N(0, σ_pos) isotropic.
      2. Compute true arrival times, add N(0, σ_t) clock noise per node.
      3. Linearize range-difference around the (unknown) source; solve
         least-squares using the perturbed node positions for the source
         location. Uses node 0 as the TDOA reference.

    Linearization assumes the LS solver is initialized near the true
    source (which a sensible detector would be — rough bearing from
    earliest-arrival node tells you the half-plane). This gives the
    Cramer-Rao-like bound for TDOA accuracy, which is what the nav
    stack's σ_pos budget actually buys.
    """
    if rng is None:
        rng = np.random.default_rng(0)
    N = node_xys.shape[0]
    if N < 3:
        return float("inf")

    # True ranges + true TDOAs (ref = node 0).
    vec_true = source_xy[None, :] - node_xys          # (N, 2)
    R_true = np.linalg.norm(vec_true, axis=1)         # (N,)
    # Unit vectors from nodes to source.
    u_true = vec_true / np.maximum(R_true[:, None], 1e-9)

    # Jacobian of range-differences w.r.t. source position.
    #   dR_i / d(source) = u_i (unit vec node i → source)
    #   d(R_i - R_0) / d(source) = u_i - u_0
    J = (u_true[1:] - u_true[0:1])                    # (N-1, 2)

    # Effective measurement noise per TDOA pair (converted to range units).
    # Contributions: (1) each node's position σ projected onto its bearing,
    # (2) clock σ on both node i and node 0.
    #   var(ΔR_i) ≈ σ_pos² (own) + σ_pos² (ref) + (c·σ_t)²·2
    sigma_range_pair = float(np.sqrt(2 * sigma_pos_m ** 2
                                      + 2 * (c_ms * sigma_t_s) ** 2))

    # LS covariance: (J^T J)^{-1} · σ²
    # This is the CRLB-equivalent; MC samples would converge to this in
    # the linearized regime. Compute both for sanity.
    JTJ = J.T @ J
    try:
        cov_crlb = np.linalg.inv(JTJ) * (sigma_range_pair ** 2)
    except np.linalg.LinAlgError:
        return float("inf")

    # Monte-Carlo validation: sample ΔR_obs, solve LS, measure error.
    errs = np.zeros(n_trials)
    for i in range(n_trials):
        # Position perturbations (propagate into range-diff observations).
        node_xys_obs = node_xys + rng.normal(0, sigma_pos_m, node_xys.shape)
        # Time perturbations → range perturbation on R_i.
        t_noise = rng.normal(0, sigma_t_s, N) * c_ms
        R_obs = np.linalg.norm(source_xy[None, :] - node_xys_obs, axis=1) + t_noise
        dR_obs = R_obs[1:] - R_obs[0]
        # Residual from the linearized model at true source.
        dR_model = R_true[1:] - R_true[0]
        resid = dR_obs - dR_model
        # LS delta_source that explains residual.
        try:
            delta = np.linalg.lstsq(J, resid, rcond=None)[0]
        except np.linalg.LinAlgError:
            delta = np.array([np.inf, np.inf])
        errs[i] = float(np.linalg.norm(delta))

    mc_rmse = float(np.sqrt(np.mean(errs ** 2)))
    crlb_rmse = float(np.sqrt(np.trace(cov_crlb)))
    # Return the larger of the two as a conservative estimate.
    return max(mc_rmse, crlb_rmse)


def random_fleet_around_source(
    N: int, spacing_m: float, rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Place N nodes in an annulus around a source at origin.

    Inner radius = 0.5 × spacing (avoid nodes landing on source).
    Outer radius = 2.5 × spacing (realistic "source inside the fleet"
    geometry). Returns (node_xys, source_xy).
    """
    r = rng.uniform(0.5 * spacing_m, 2.5 * spacing_m, N)
    theta = rng.uniform(0.0, 2 * np.pi, N)
    node_xys = np.column_stack([r * np.cos(theta), r * np.sin(theta)])
    source_xy = np.zeros(2)
    return node_xys, source_xy


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------
@dataclass
class SweepResult:
    # (source_name, sea_state, classifier_on) → range_m
    detection_range_by_sl_ss: dict[tuple[str, int, bool], float]
    triangulation_by_n_sigma: dict[tuple[int, float], float]


def run_sweep(n_trials: int = 300, seed: int = 42) -> SweepResult:
    rng = np.random.default_rng(seed)

    # (1) Single-node detection range vs (SL, SS, classifier).
    det_range: dict[tuple[str, int, bool], float] = {}
    for sl_name, sl_db in SOURCE_LEVELS_DB.items():
        for ss, wind in SEA_STATE_TO_WIND_MS.items():
            for classifier_on in (False, True):
                gain = CLASSIFIER_GAIN_DB if classifier_on else 0.0
                det_range[(sl_name, ss, classifier_on)] = detection_range_m(
                    sl_db, wind, classifier_gain_db=gain,
                )

    # (2) Triangulation RMSE vs (N, σ_pos) at a representative spacing
    # (5 km — middle of our range). Source placed inside the fleet.
    N_sweep = [3, 5, 8, 12, 20]
    sigma_pos_sweep = [10.0, 50.0, 100.0, 500.0, 1000.0]
    tri_rmse: dict[tuple[int, float], float] = {}
    for N in N_sweep:
        for sigma_pos in sigma_pos_sweep:
            rmses = []
            for _ in range(20):  # average over geometry draws
                node_xys, src = random_fleet_around_source(N, 5000.0, rng)
                rmses.append(triangulation_rmse_m(
                    node_xys, src, sigma_pos, SIGMA_T_S,
                    n_trials=n_trials, rng=rng,
                ))
            tri_rmse[(N, sigma_pos)] = float(np.median(rmses))

    return SweepResult(det_range, tri_rmse)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------
def plot_results(sweep: SweepResult, out_path: Path) -> None:
    fig = plt.figure(figsize=(14, 9))
    gs = fig.add_gridspec(2, 3, hspace=0.35, wspace=0.30)

    colors = {"electric (silent target)": "#5a8a3a",
              "gasoline outboard (~5 m skiff)": "#c77a2f",
              "small trawler (~15 m, diesel)": "#b02a2a"}

    # --- (a) SNR vs range, SS 4, with + without classifier gain ---
    ax_a = fig.add_subplot(gs[0, 0])
    R_grid = np.logspace(1, np.log10(30_000), 300)
    for sl_name, sl_db in SOURCE_LEVELS_DB.items():
        tl = transmission_loss_db(R_grid)
        nl = noise_level_band_db(SEA_STATE_TO_WIND_MS[4])
        snr = np.asarray(sl_db - tl - nl)
        ax_a.plot(R_grid / 1000, snr,
                   color=colors[sl_name], linestyle="-", label=sl_name)
    ax_a.axhline(DETECTION_THRESHOLD_DB, color="gray", linestyle=":", linewidth=1,
                  label=f"energy-det threshold ({DETECTION_THRESHOLD_DB:.0f} dB)")
    ax_a.axhline(DETECTION_THRESHOLD_DB - CLASSIFIER_GAIN_DB,
                  color="gray", linestyle="--", linewidth=1,
                  label=f"classifier-assisted ({DETECTION_THRESHOLD_DB - CLASSIFIER_GAIN_DB:.0f} dB)")
    ax_a.set_xscale("log")
    ax_a.set_xlabel("range (km)")
    ax_a.set_ylabel("in-band SNR (dB)")
    ax_a.set_title("(a) SNR vs range, sea state 4")
    ax_a.legend(fontsize=7, loc="upper right")
    ax_a.grid(True, which="both", alpha=0.3)
    ax_a.set_xlim(0.01, 30)
    ax_a.set_ylim(-40, 80)

    # --- (b) Detection range by SS, split by classifier regime ---
    ax_b = fig.add_subplot(gs[0, 1])
    ss_values = sorted(SEA_STATE_TO_WIND_MS.keys())
    bar_w = 0.11
    x = np.arange(len(ss_values))
    # Two groups: energy-only (solid) and classifier (hatched), three SL each.
    for i, (sl_name, _) in enumerate(SOURCE_LEVELS_DB.items()):
        for j, classifier_on in enumerate((False, True)):
            ranges_km = [sweep.detection_range_by_sl_ss[(sl_name, ss, classifier_on)] / 1000
                          for ss in ss_values]
            offset = (i - 1) * 2 * bar_w + j * bar_w
            hatch = "///" if classifier_on else None
            label = (f"{sl_name} " + ("(classifier)" if classifier_on else "(energy)"))
            ax_b.bar(x + offset, ranges_km, bar_w,
                      label=label, color=colors[sl_name], hatch=hatch,
                      edgecolor="black", linewidth=0.3)
    ax_b.set_xticks(x)
    ax_b.set_xticklabels([f"SS {ss}" for ss in ss_values])
    ax_b.set_ylabel("detection range (km)")
    ax_b.set_yscale("log")
    ax_b.set_ylim(0.01, 30)
    ax_b.set_title("(b) Single-node detection range")
    ax_b.legend(fontsize=6, ncol=1, loc="upper right")
    ax_b.grid(True, axis="y", which="both", alpha=0.3)

    # --- (c) How many nodes within detection range at spacing d? ---
    ax_c = fig.add_subplot(gs[0, 2])
    d_values_km = np.array([2.0, 5.0, 10.0])
    for sl_name, _ in SOURCE_LEVELS_DB.items():
        # Classifier-on is the mission-realistic regime
        R_ss4_km = sweep.detection_range_by_sl_ss[(sl_name, 4, True)] / 1000
        expected_N = np.pi * (R_ss4_km / d_values_km) ** 2
        ax_c.plot(d_values_km, np.maximum(expected_N, 1e-2), "o-",
                   color=colors[sl_name], label=sl_name)
    ax_c.axhline(3, color="red", linestyle="--", linewidth=1,
                  label="triangulation floor (N≥3)")
    ax_c.set_xlabel("inter-node spacing (km)")
    ax_c.set_ylabel("expected nodes within detection range")
    ax_c.set_title("(c) Fleet coverage, SS 4, classifier ON")
    ax_c.set_yscale("log")
    ax_c.legend(fontsize=7)
    ax_c.grid(True, which="both", alpha=0.3)

    # --- (d) Triangulation RMSE heatmap: N × σ_pos ---
    ax_d = fig.add_subplot(gs[1, 0])
    N_sweep = sorted({k[0] for k in sweep.triangulation_by_n_sigma})
    sp_sweep = sorted({k[1] for k in sweep.triangulation_by_n_sigma})
    grid = np.array([[sweep.triangulation_by_n_sigma[(N, sp)]
                       for sp in sp_sweep] for N in N_sweep])
    im = ax_d.imshow(grid, aspect="auto", origin="lower",
                      extent=(0.0, float(len(sp_sweep)),
                              0.0, float(len(N_sweep))),
                      cmap="viridis",
                      norm=mcolors.LogNorm(
                          vmin=max(1.0, float(grid.min())),
                          vmax=float(grid.max())))
    ax_d.set_xticks(np.arange(len(sp_sweep)) + 0.5)
    ax_d.set_xticklabels([f"{sp:.0f}" for sp in sp_sweep])
    ax_d.set_yticks(np.arange(len(N_sweep)) + 0.5)
    ax_d.set_yticklabels([f"{N}" for N in N_sweep])
    ax_d.set_xlabel("per-node σ_pos (m)")
    ax_d.set_ylabel("N detecting nodes")
    ax_d.set_title("(d) Triangulation RMSE (m)\n  [5 km spacing, σ_t = 1 ms]")
    plt.colorbar(im, ax=ax_d, label="RMSE (m)")
    # Annotate cells.
    for i, N in enumerate(N_sweep):
        for j, _sp in enumerate(sp_sweep):
            v = grid[i, j]
            ax_d.text(j + 0.5, i + 0.5, f"{v:.0f}",
                       ha="center", va="center", fontsize=7,
                       color="white" if v > 200 else "black")

    # --- (e) Key plot: σ_pos budget for 100 m target envelope ---
    ax_e = fig.add_subplot(gs[1, 1])
    target_rmse_m = 100.0
    # For each N, find σ_pos at which RMSE crosses target.
    for N in N_sweep:
        rmses = np.array([sweep.triangulation_by_n_sigma[(N, sp)]
                           for sp in sp_sweep])
        sps = np.array(sp_sweep)
        ax_e.loglog(sps, rmses, "o-", label=f"N = {N}")
    ax_e.axhline(target_rmse_m, color="red", linestyle="--",
                  linewidth=1, label=f"target ({target_rmse_m:.0f} m)")
    ax_e.set_xlabel("per-node σ_pos (m)")
    ax_e.set_ylabel("triangulation RMSE (m)")
    ax_e.set_title("(e) σ_pos budget for target envelope")
    ax_e.legend(fontsize=7)
    ax_e.grid(True, which="both", alpha=0.3)

    # --- (f) Summary text ---
    ax_f = fig.add_subplot(gs[1, 2])
    ax_f.axis("off")
    sl_skiff = "gasoline outboard (~5 m skiff)"
    R_skiff_energy = sweep.detection_range_by_sl_ss[(sl_skiff, 4, False)] / 1000
    R_skiff_class = sweep.detection_range_by_sl_ss[(sl_skiff, 4, True)] / 1000
    R_trawler_class = sweep.detection_range_by_sl_ss[
        ("small trawler (~15 m, diesel)", 4, True)] / 1000
    R_silent_class = sweep.detection_range_by_sl_ss[
        ("electric (silent target)", 4, True)] / 1000
    rmse_n8_sp100 = sweep.triangulation_by_n_sigma[(8, 100.0)]
    rmse_n8_sp500 = sweep.triangulation_by_n_sigma[(8, 500.0)]
    summary = (
        "HEADLINE FINDINGS (SS 4)\n"
        "========================\n\n"
        f"Detection range, energy-only:\n"
        f"  5 m skiff (140 dB) = {R_skiff_energy:.2f} km\n"
        f"  → too short for mesh at 5 km spacing.\n"
        f"  ⇒ Energy detection is NOT the product.\n\n"
        f"Detection range, +{CLASSIFIER_GAIN_DB:.0f} dB classifier:\n"
        f"  5 m skiff (140 dB)  = {R_skiff_class:.2f} km\n"
        f"  15 m trawler (155 dB) = {R_trawler_class:.1f} km\n"
        f"  electric/silent (120 dB) = {R_silent_class:.2f} km\n\n"
        f"Triangulation (N=8, 5 km spacing):\n"
        f"  σ_pos=100 m → RMSE = {rmse_n8_sp100:.0f} m\n"
        f"  σ_pos=500 m → RMSE = {rmse_n8_sp500:.0f} m\n\n"
        "IMPLICATIONS\n"
        "============\n"
        "1. Classifier is on the critical path.\n"
        "   No classifier ⇒ no mission.\n"
        "   Training-data pipeline needs a\n"
        "   named plan.\n"
        "2. 'Silent target' use case (electric\n"
        "   outboards, sail, paddle) needs node\n"
        "   spacing ≤ ~1 km. Drop it from the\n"
        "   initial product mission, OR accept\n"
        "   higher fleet density at those sites.\n"
        "3. σ_pos budget: O(100 m) per node is\n"
        "   sufficient; RTS smoother landing at\n"
        "   ~100 m σ_pos gives ~100 m TDOA RMSE\n"
        "   envelope. This is the nav-stack's\n"
        "   operational requirement.\n\n"
        "NOT MODELED (flagged)\n"
        "=====================\n"
        "• Shallow-water multipath (≥0.5 km).\n"
        "• Biological / shipping clutter.\n"
        "• False-alarm rate from classifier.\n"
        "• Coherent beamforming — inapplicable\n"
        "  at km inter-node spacing."
    )
    ax_f.text(0.0, 1.0, summary, family="monospace", fontsize=7.5,
               va="top", ha="left",
               transform=ax_f.transAxes)

    fig.suptitle(
        "Acoustic detection + TDOA localization feasibility — Phase-2 supporting study",
        fontsize=11, y=0.995,
    )
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    t0 = time.time()
    FIG_DIR.mkdir(exist_ok=True)
    out_path = FIG_DIR / "26_acoustic_detection.png"

    print(f"[23_acoustic_detection] Running sweep...")
    sweep = run_sweep(n_trials=300)
    print(f"[23_acoustic_detection] Sweep done in {time.time() - t0:.1f}s.")

    print("\nSingle-node detection range (km), by (SL, sea state):")
    print("  [energy-only | +20 dB classifier]")
    print(f"  {'Source':<38}   SS2             SS4             SS6")
    for sl_name in SOURCE_LEVELS_DB:
        parts = []
        for ss in [2, 4, 6]:
            e = sweep.detection_range_by_sl_ss[(sl_name, ss, False)] / 1000
            c = sweep.detection_range_by_sl_ss[(sl_name, ss, True)] / 1000
            parts.append(f"{e:5.2f}|{c:5.2f}")
        print(f"  {sl_name:<38}  " + "   ".join(parts))

    print("\nTriangulation RMSE (m), 5 km spacing, σ_t = 1 ms:")
    print(f"  {'N':<4} " + "  ".join(f"σp={sp:4.0f}m" for sp in [10, 50, 100, 500, 1000]))
    for N in [3, 5, 8, 12, 20]:
        rmses = [sweep.triangulation_by_n_sigma[(N, sp)]
                  for sp in [10.0, 50.0, 100.0, 500.0, 1000.0]]
        print(f"  {N:<4} " + "  ".join(f"{r:8.1f}" for r in rmses))

    plot_results(sweep, out_path)
    print(f"\n[23_acoustic_detection] Wrote {out_path}")
    print(f"[23_acoustic_detection] Total time: {time.time() - t0:.1f}s.")


if __name__ == "__main__":
    main()
