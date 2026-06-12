"""Per-cell summary for axis-keyed sweep results.

Reads `figures/sweep_runs/<run_id>/raw/results.npz` (saved by
`_fleet_sweep_v0.py` with the per-cell axis-extension prefix) and
prints a compact per-cell table grouped by (density, policy, σ_m,
cadence, mission_h).

Usage:
    python _summarize_sweep_axes.py figures/sweep_runs/<run_id>
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np


def _parse_cell_keys(npz_keys: list[str]) -> list[tuple[str, dict]]:
    """Find every distinct cell prefix and parse its axis values.

    Returns list of (prefix, axes_dict) where axes_dict has keys
    'density', 'policy', and optionally 's', 'c', 'h'.
    """
    cell_prefixes: set[str] = set()
    for k in npz_keys:
        if k.startswith("__"):
            continue
        # Per-cell keys have form: <density>__<policy>[__s<σ>][__c<cad>][__h<rh>]__<rest>
        # Strip everything after '__a__' / '__b__' / '__drifter_' / '__event_'
        # and keep the cell-prefix portion.
        for suffix_marker in ("__a__", "__b__", "__drifter_", "__event_",
                               "__n_drifters", "__n_events"):
            idx = k.find(suffix_marker)
            if idx >= 0:
                cell_prefixes.add(k[:idx])
                break

    cells = []
    for prefix in sorted(cell_prefixes):
        parts = prefix.split("__")
        density = parts[0]
        policy = parts[1]
        axes = {"density": density, "policy": policy, "prefix": prefix}
        for part in parts[2:]:
            if part.startswith("s") and part[1:].replace(".", "").isdigit():
                axes["s"] = float(part[1:])
            elif part.startswith("c") and part[1:].replace(".", "").isdigit():
                axes["c"] = float(part[1:])
            elif part.startswith("h") and part[1:].isdigit():
                axes["h"] = int(part[1:])
        cells.append((prefix, axes))
    return cells


def _cell_metrics(d: dict, prefix: str) -> dict:
    """Extract per-cell summary metrics from the npz dict.

    Reports four rates so consumers can distinguish system effectiveness
    from coverage geometry:
      - heard_pct: events with ≥1 drifter in detection range
      - triang_pct: events with ≥3 drifters (necessary for LSQ)
      - recon_pct: ≥3 detectors AND finite LSQ result (current "recon%")
      - recon_of_triang_pct: of triangulable events, % that reconstructed
        (LSQ-success rate; isolates numerical/geometry failure mode)
      - recon_of_heard_pct: of heard events, % that reconstructed
        (system effectiveness given coverage)
    """
    out = {}
    for mode in ("a", "b"):
        err_key = f"{prefix}__{mode}__error_m"
        sig_key = f"{prefix}__{mode}__sigma_m"
        ndet_key = f"{prefix}__{mode}__n_detectors"
        if err_key not in d:
            continue
        err = d[err_key]
        sig = d[sig_key]
        ndet = d[ndet_key]
        n_total = len(err)
        heard_mask = ndet >= 1
        triang_mask = ndet >= 3
        recon_mask = triang_mask & np.isfinite(err)
        n_heard = int(heard_mask.sum())
        n_triang = int(triang_mask.sum())
        n_recon = int(recon_mask.sum())
        base = dict(
            n_total=n_total,
            n_heard=n_heard,
            n_triang=n_triang,
            n_recon=n_recon,
            heard_pct=100.0 * n_heard / max(n_total, 1),
            triang_pct=100.0 * n_triang / max(n_total, 1),
            recon_pct=100.0 * n_recon / max(n_total, 1),
            recon_of_triang_pct=(100.0 * n_recon / n_triang) if n_triang > 0 else 0.0,
            recon_of_heard_pct=(100.0 * n_recon / n_heard) if n_heard > 0 else 0.0,
        )
        if n_recon == 0:
            out[mode] = base
            continue
        err_r = err[recon_mask]
        sig_r = sig[recon_mask]
        out[mode] = {**base,
            "err_p50": float(np.nanmedian(err_r)),
            "err_p95": float(np.nanpercentile(err_r, 95)),
            "err_mean_capped": float(np.nanmean(np.minimum(err_r, 1e6))),
            "sigma_p50": float(np.nanmedian(sig_r)) if np.any(np.isfinite(sig_r)) else float("nan"),
            "sigma_p95": float(np.nanpercentile(sig_r, 95)) if np.any(np.isfinite(sig_r)) else float("nan"),
        }
    # Per-drifter station-keeping aggregate.
    sk = []
    pf_err = []
    surfacings = []
    di = 0
    while True:
        sk_key = f"{prefix}__drifter_{di}__ctrl_mean_m"
        if sk_key not in d:
            break
        sk.append(float(d[sk_key]))
        pf_err.append(float(d[f"{prefix}__drifter_{di}__pf_err_per_tick"].mean()))
        surfacings.append(int(d[f"{prefix}__drifter_{di}__n_surfacings"]))
        di += 1
    out["n_drifters"] = di
    if sk:
        out["sk_mean"] = float(np.mean(sk))
        out["pf_err_mean"] = float(np.mean(pf_err))
        out["surfacings_mean"] = float(np.mean(surfacings))
    return out


def _load_one(run_dir: str) -> tuple[dict, list[str]]:
    npz_path = os.path.join(run_dir, "raw", "results.npz")
    if not os.path.exists(npz_path):
        return {}, []
    d = np.load(npz_path, allow_pickle=True)
    return d, list(d.files)


def _chunk_axes_from_run_dir(run_dir: str) -> dict:
    """Pull (σ_m, policy) from a chunked-sweep run_dir name.

    Run-id format from `_run_chunked_sweep.sh`:
      science_v1__<density>__s<sigma>__<policy>__<timestamp>
    The σ_m and policy aren't in the cell prefix when only one value
    is present in the chunk (axis-variation logic in npz key prefix
    skips constant axes). Reading them from the directory name lets
    us tag cells correctly when merging chunks.
    """
    name = os.path.basename(os.path.normpath(run_dir))
    parts = name.split("__")
    out: dict = {}
    for p in parts:
        if p.startswith("s") and len(p) > 1 and p[1:].replace(".", "").isdigit():
            out["chunk_sigma"] = float(p[1:])
        elif p.startswith("c") and len(p) > 1 and p[1:].replace(".", "").isdigit():
            out["chunk_cadence"] = float(p[1:])
        elif p.startswith("h") and len(p) > 1 and p[1:].isdigit():
            out["chunk_run_hours"] = int(p[1:])
        elif p in ("fixed_6h", "fixed_3h", "fixed_12h", "fixed_2h",
                   "post_event_30m_12h", "post_event_30m_6h",
                   "uncertainty_gated"):
            out["chunk_policy"] = p
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: python _summarize_sweep_axes.py <run_dir> [<run_dir> ...]")
        print("       python _summarize_sweep_axes.py --glob <pattern>")
        sys.exit(1)

    run_dirs: list[str] = []
    if sys.argv[1] == "--glob":
        import glob
        for p in sys.argv[2:]:
            run_dirs.extend(sorted(glob.glob(p)))
    else:
        run_dirs = sys.argv[1:]

    print(f"loading {len(run_dirs)} run_dir(s)")
    # Per-chunk processing. Each chunk's npz has cell prefixes that
    # may collide with other chunks' prefixes (when σ_m or policy is
    # the only thing varying across chunks but isn't in the prefix
    # because it's constant within a chunk). So we process each chunk
    # SEPARATELY rather than merging into a single dict.
    cells: list[tuple[dict, dict, str]] = []  # (axes, npz_dict, prefix)
    for rd in run_dirs:
        di, ki = _load_one(rd)
        if not ki:
            print(f"  skip (no npz): {rd}")
            continue
        chunk_axes = _chunk_axes_from_run_dir(rd)
        # Discover this chunk's cell prefixes.
        chunk_prefixes = _parse_cell_keys(ki)
        for prefix, axes in chunk_prefixes:
            # Backfill chunk-level σ_m / cadence / policy / run_hours
            # for axes that weren't varied within the chunk.
            if "s" not in axes and "chunk_sigma" in chunk_axes:
                axes["s"] = chunk_axes["chunk_sigma"]
            if "c" not in axes and "chunk_cadence" in chunk_axes:
                axes["c"] = chunk_axes["chunk_cadence"]
            if "h" not in axes and "chunk_run_hours" in chunk_axes:
                axes["h"] = chunk_axes["chunk_run_hours"]
            if "chunk_policy" in chunk_axes:
                axes["policy"] = chunk_axes["chunk_policy"]
            cells.append((axes, di, prefix))
        print(f"  + {rd}  ({len(chunk_prefixes)} cells)")

    print(f"\n=== {len(cells)} cells total ===")

    # Compact per-cell table.
    # heard% = events with ≥1 drifter in detect range (coverage)
    # triang% = events with ≥3 drifters (LSQ-eligible)
    # recon% = of-total reconstructed (system × coverage)
    # r/heard% = of-heard reconstructed (system effectiveness given coverage)
    print(f"\n{'density':<16} {'policy':<22} {'σ_m':>6} "
          f"{'cad':>7} {'h':>5} {'mode':>5} "
          f"{'heard%':>7} {'triang%':>8} {'recon%':>7} {'r/heard%':>9} "
          f"{'p50err':>8} {'p50σ':>7} "
          f"{'sk_mean':>8} {'pf_err':>7} {'surf':>5}")
    print("-" * 155)
    # Sort cells by (density, policy, σ, cad, h) for stable output.
    def _sort_key(t):
        a = t[0]
        return (
            a.get("density", ""),
            a.get("policy", ""),
            a.get("s", float("inf")),
            a.get("c", float("inf")),
            a.get("h", 0),
        )
    cells.sort(key=_sort_key)

    for axes, d, prefix in cells:
        m = _cell_metrics(d, prefix)
        for mode in ("a", "b"):
            if mode not in m:
                continue
            mm = m[mode]
            n_recon = mm.get("n_recon", 0)
            if n_recon == 0:
                continue
            s_str = (f"{axes['s']:.0f}" if "s" in axes else "—")
            c_str = (f"{axes['c']:.0f}" if "c" in axes else "—")
            h_str = (f"{axes['h']}" if "h" in axes else "—")
            print(f"{axes['density']:<16} {axes['policy']:<22} "
                  f"{s_str:>6} "
                  f"{c_str:>7} "
                  f"{h_str:>5} "
                  f"{mode:>5} "
                  f"{mm.get('heard_pct', 0):>6.1f}% "
                  f"{mm.get('triang_pct', 0):>7.1f}% "
                  f"{mm.get('recon_pct', 0):>6.1f}% "
                  f"{mm.get('recon_of_heard_pct', 0):>8.1f}% "
                  f"{mm.get('err_p50', float('nan')):>7.0f}m "
                  f"{mm.get('sigma_p50', float('nan')):>6.0f}m "
                  f"{m.get('sk_mean', float('nan')):>7.0f}m "
                  f"{m.get('pf_err_mean', float('nan')):>6.0f}m "
                  f"{m.get('surfacings_mean', 0):>4.1f}")


if __name__ == "__main__":
    main()
