"""Aggregate JSON outputs from the Phase 2 parallel (σ, seed) sweep.

Reads `figures/25_rbpf_v2_bias_learning_sigmaXX_seedYYY.json` files and
builds:

  1. %<500 m by σ_fc, paired no_learn vs grid, per policy (4 panels).
  2. PFerr mean by σ_fc, per policy. Log-y because it spans 10 m → 1 km.
  3. Δ (%<500m)_grid − (%<500m)_no_learn by σ, per policy. Positive = bias
     learning helps; error bars = ±1 std across (seed × station) samples.
  4. Station-authority heatmap: baseline_real %<500 m per (station, σ),
     averaged across seeds — shows which stations have controller
     authority at which σ levels.
  5. Learned bias magnitude vs expected σ_slow, scatter coloured by σ_fc.

Output: `figures/25_rbpf_v2_sweep_summary.png`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib.pyplot as plt  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

FIG_DIR = Path(__file__).parent / "figures"

PATTERN = re.compile(r"25_rbpf_v2_bias_learning_sigma(\d+)_seed(\d+)\.json")


def load_all() -> tuple[list[dict], list[dict]]:
    pf_runs: list[dict] = []
    baselines: list[dict] = []
    for p in sorted(FIG_DIR.glob("25_rbpf_v2_bias_learning_*.json")):
        m = PATTERN.match(p.name)
        if m is None:
            continue
        with open(p) as fh:
            payload = json.load(fh)
        pf_runs.extend(payload.get("pf_runs", []))
        baselines.extend(payload.get("baselines", []))
    return pf_runs, baselines


def main() -> None:
    pf_runs, baselines = load_all()
    if not pf_runs:
        print("No JSON files found in figures/.")
        return

    sigmas = sorted({round(r["sigma_fc_ms"], 3) for r in pf_runs})
    policies = sorted({r["policy"] for r in pf_runs})
    seeds = sorted({r["noise_seed"] for r in pf_runs})
    stations = sorted({(round(r["station_lat"], 4), round(r["station_lon"], 4))
                        for r in pf_runs})
    print(f"loaded {len(pf_runs)} pf_runs + {len(baselines)} baseline rows "
          f"| σ={sigmas} | policies={policies} | seeds={seeds} | "
          f"stations={len(stations)}")

    # Index for fast access
    def pf_at(sigma, policy, cfg, stat=None, seed=None):
        """Filter pf_runs by any combination of axes."""
        out = [r for r in pf_runs
               if round(r["sigma_fc_ms"], 3) == sigma
               and r["policy"] == policy
               and r["config"] == cfg]
        if stat is not None:
            out = [r for r in out
                   if round(r["station_lat"], 4) == stat[0]
                   and round(r["station_lon"], 4) == stat[1]]
        if seed is not None:
            out = [r for r in out if r["noise_seed"] == seed]
        return out

    def baseline_at(kind, sigma, stat=None, seed=None):
        out = [b for b in baselines
               if round(b["sigma_fc_ms"], 3) == sigma]
        if stat is not None:
            out = [b for b in out
                   if round(b["station_lat"], 4) == stat[0]
                   and round(b["station_lon"], 4) == stat[1]]
        if seed is not None:
            out = [b for b in out if b["noise_seed"] == seed]
        return [b[kind] for b in out]

    fig, axes = plt.subplots(2, 3, figsize=(22, 12))

    # --- Panel (0,0): %<500 m vs σ, per policy. no_learn / grid / grid+ctd.
    ax = axes[0, 0]
    colors = {"fixed_3h": "tab:orange", "fixed_6h": "tab:blue",
              "fixed_12h": "tab:cyan", "geometric": "tab:purple"}
    for policy in policies:
        no_learn = []
        grid = []
        grid_ctd = []
        for s in sigmas:
            nl = [r["envelope_fracs"]["500.0"] for r in pf_at(s, policy, "no_learn")]
            gr = [r["envelope_fracs"]["500.0"] for r in pf_at(s, policy, "grid")]
            gc = [r["envelope_fracs"]["500.0"] for r in pf_at(s, policy, "grid+ctd")]
            no_learn.append(100 * float(np.mean(nl)) if nl else float("nan"))
            grid.append(100 * float(np.mean(gr)) if gr else float("nan"))
            grid_ctd.append(100 * float(np.mean(gc)) if gc else float("nan"))
        c = colors.get(policy, "k")
        ax.plot(sigmas, no_learn, "o:", color=c, alpha=0.4, label=f"{policy} no_learn")
        ax.plot(sigmas, grid, "o--", color=c, alpha=0.7, label=f"{policy} grid")
        ax.plot(sigmas, grid_ctd, "o-", color=c, label=f"{policy} grid+ctd")
    # Baselines
    bl_real = [100 * float(np.mean(
                    [b["envelope_fracs"]["500.0"]
                     for b in baseline_at("baseline_real", s)] or [np.nan]))
               for s in sigmas]
    bl_nemo = [100 * float(np.mean(
                    [b["envelope_fracs"]["500.0"]
                     for b in baseline_at("baseline_nemo", s)] or [np.nan]))
               for s in sigmas]
    ax.plot(sigmas, bl_real, "s-", color="tab:green", alpha=0.8,
             label="baseline_real")
    ax.plot(sigmas, bl_nemo, "s--", color="tab:olive", alpha=0.8,
             label="baseline_nemo")
    ax.set_xlabel("σ_fc (m/s)")
    ax.set_ylabel("%<500 m (mean over stations × seeds)")
    ax.set_title("Station-keeping fraction vs forecast-error σ")
    ax.legend(fontsize=7, loc="upper right")
    ax.grid(alpha=0.3)

    # --- Panel (0,1): PFerr mean vs σ, per policy. CTD's tightening
    # of submerged-leg PF position should show as a clear separation
    # between `grid` and `grid+ctd` here.
    ax = axes[0, 1]
    for policy in policies:
        vals_nl = []
        vals_gr = []
        vals_gc = []
        for s in sigmas:
            nl = [r["pf_err_mean_m"] for r in pf_at(s, policy, "no_learn")]
            gr = [r["pf_err_mean_m"] for r in pf_at(s, policy, "grid")]
            gc = [r["pf_err_mean_m"] for r in pf_at(s, policy, "grid+ctd")]
            vals_nl.append(float(np.mean(nl)) if nl else float("nan"))
            vals_gr.append(float(np.mean(gr)) if gr else float("nan"))
            vals_gc.append(float(np.mean(gc)) if gc else float("nan"))
        c = colors.get(policy, "k")
        ax.plot(sigmas, vals_nl, "o:", color=c, alpha=0.4, label=f"{policy} no_learn")
        ax.plot(sigmas, vals_gr, "o--", color=c, alpha=0.7, label=f"{policy} grid")
        ax.plot(sigmas, vals_gc, "o-", color=c, label=f"{policy} grid+ctd")
    ax.set_xlabel("σ_fc (m/s)")
    ax.set_ylabel("mean PF position error (m)")
    ax.set_title("Retrospective-localisation error vs σ")
    ax.set_yscale("log")
    ax.legend(fontsize=6, loc="upper left", ncol=2)
    ax.grid(alpha=0.3, which="both")

    # --- Panel (0,2): Δ(%<500m) vs no_learn, for `grid` and `grid+ctd`.
    # Two stacked box columns per (σ, policy) — the gap between them
    # measures the CTD-only tightening contribution.
    ax = axes[0, 2]
    delta_data = []
    delta_labels = []
    for s in sigmas:
        for policy in policies:
            nl = [100 * r["envelope_fracs"]["500.0"]
                  for r in pf_at(s, policy, "no_learn")]
            gr = [100 * r["envelope_fracs"]["500.0"]
                  for r in pf_at(s, policy, "grid")]
            gc = [100 * r["envelope_fracs"]["500.0"]
                  for r in pf_at(s, policy, "grid+ctd")]
            paired_g = min(len(nl), len(gr))
            paired_c = min(len(nl), len(gc))
            if paired_g > 0:
                delta_data.append(list(np.array(gr[:paired_g]) - np.array(nl[:paired_g])))
                delta_labels.append(f"σ{int(s*100)} {policy} grid")
            if paired_c > 0:
                delta_data.append(list(np.array(gc[:paired_c]) - np.array(nl[:paired_c])))
                delta_labels.append(f"σ{int(s*100)} {policy} +ctd")
    if delta_data:
        ax.boxplot(delta_data, labels=delta_labels, showmeans=True)
    ax.axhline(0, color="k", ls="--", alpha=0.5)
    ax.set_ylabel("Δ(%<500m) vs no_learn")
    ax.set_title("Bias-learning + CTD benefit distribution")
    ax.tick_params(axis="x", rotation=70, labelsize=6)
    ax.grid(alpha=0.3, axis="y")

    # --- Panel (1,0): baseline_real heatmap per (station, σ).
    ax = axes[1, 0]
    heat = np.full((len(stations), len(sigmas)), np.nan)
    for i, stat in enumerate(stations):
        for j, s in enumerate(sigmas):
            vals = [b["envelope_fracs"]["500.0"]
                    for b in baseline_at("baseline_real", s, stat=stat)]
            if vals:
                heat[i, j] = 100 * float(np.mean(vals))
    im = ax.imshow(heat, aspect="auto", cmap="viridis", origin="lower",
                    vmin=0, vmax=100)
    ax.set_xticks(range(len(sigmas)))
    ax.set_xticklabels([f"{s*100:.0f}" for s in sigmas])
    ax.set_yticks(range(len(stations)))
    ax.set_yticklabels([f"S{i+1}" for i in range(len(stations))], fontsize=8)
    ax.set_xlabel("σ_fc (cm/s)")
    ax.set_ylabel("station")
    ax.set_title("baseline_real %<500 m per station × σ\n"
                  "(controller authority with oracle knowledge)")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    # --- Panel (1,1): learned |b|_max vs expected σ_learnable.
    # In the 5-component layered model the PF bias learner can in
    # principle recover everything except the white residual (coh +
    # plume + submeso + inertial). σ_learnable is computed from the
    # ref amplitudes scaled linearly with σ_fc (σ_fc_ref = 0.08 m/s).
    SIGMA_FC_REF = 0.08
    SIGMA_COH_REF = 0.04
    SIGMA_PLUME_REF = 0.02
    SIGMA_SUBMESO_REF = 0.05
    SIGMA_INERTIAL_REF = 0.04
    sigma_learnable_ref = float(np.sqrt(
        SIGMA_COH_REF ** 2 + SIGMA_PLUME_REF ** 2
        + SIGMA_SUBMESO_REF ** 2 + SIGMA_INERTIAL_REF ** 2
    ))
    ax = axes[1, 1]
    for policy in policies:
        xs, ys = [], []
        for s in sigmas:
            gr = [r["bias_max_learned_mag_ms"]
                  for r in pf_at(s, policy, "grid")]
            if gr:
                xs.append(s * 100 * sigma_learnable_ref / SIGMA_FC_REF)
                ys.append(float(np.mean(gr)) * 100)
        c = colors.get(policy, "k")
        ax.plot(xs, ys, "o-", color=c, label=policy)
    max_sig = max(sigmas) * 100 * sigma_learnable_ref / SIGMA_FC_REF
    ref = np.linspace(0, max_sig, 50)
    ax.plot(ref, ref, "k--", alpha=0.4, label="y=x (unbiased learner)")
    ax.set_xlabel("expected σ_learnable (cm/s)")
    ax.set_ylabel("mean learned |b|_max (cm/s)")
    ax.set_title("Bias-learner calibration")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # --- Panel (1,2): mean distance from station vs σ, per policy.
    ax = axes[1, 2]
    for policy in policies:
        vals_nl = []
        vals_gr = []
        vals_gc = []
        for s in sigmas:
            nl = [r["ctrl_mean_m"] for r in pf_at(s, policy, "no_learn")]
            gr = [r["ctrl_mean_m"] for r in pf_at(s, policy, "grid")]
            gc = [r["ctrl_mean_m"] for r in pf_at(s, policy, "grid+ctd")]
            vals_nl.append(float(np.mean(nl)) if nl else float("nan"))
            vals_gr.append(float(np.mean(gr)) if gr else float("nan"))
            vals_gc.append(float(np.mean(gc)) if gc else float("nan"))
        c = colors.get(policy, "k")
        ax.plot(sigmas, vals_nl, "o:", color=c, alpha=0.4, label=f"{policy} no_learn")
        ax.plot(sigmas, vals_gr, "o--", color=c, alpha=0.7, label=f"{policy} grid")
        ax.plot(sigmas, vals_gc, "o-", color=c, label=f"{policy} grid+ctd")
    bl_real_mean = [float(np.mean([b["ctrl_mean_m"]
                                     for b in baseline_at("baseline_real", s)] or [np.nan]))
                    for s in sigmas]
    ax.plot(sigmas, bl_real_mean, "s-", color="tab:green", alpha=0.8,
             label="baseline_real")
    ax.set_xlabel("σ_fc (m/s)")
    ax.set_ylabel("mean distance from station (m)")
    ax.set_title("Station-keeping distance vs σ")
    ax.legend(fontsize=6, loc="upper left", ncol=2)
    ax.grid(alpha=0.3)

    fig.suptitle(
        f"Phase 2 RBPF bias-learning — parallel sweep across "
        f"{len(sigmas)} σ × {len(seeds)} seeds × {len(stations)} stations × "
        f"{len(policies)} policies × {{no_learn, grid, grid+ctd}}",
        fontsize=12, y=1.0,
    )
    fig.tight_layout()
    out = FIG_DIR / "25_rbpf_v2_sweep_summary.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"[viz] wrote {out}")


if __name__ == "__main__":
    main()
