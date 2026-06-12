"""Merge cells from multiple sweep_runs npz files into one unified npz.

Combines per-cell arrays from several source `results.npz` files,
renaming the density component of each cell prefix per source so that
cells from different sources don't collide in the merged keyspace.

Use case: the cadence-sweep ran two driver invocations (one
`CAMPAIGN_MODE=redeploy`, one `single`) plus reused an existing
`single` baseline cell. All three sources keyed cells under
`D6_empirical__<policy>`; this script renames each source's density
to a unique tag (e.g. `D6_redep72h`, `D6_norep`) so the merged npz
has one cell per (cadence, policy) pair, consumable by the v2
analyzer with no further changes.

Usage:
    python _combine_sweep_runs.py <out_run_dir> \\
        <src1_run_dir>:<src_density>:<dst_density>[:<policy_filter>] \\
        <src2_run_dir>:<src_density>:<dst_density>[:<policy_filter>] \\
        [...]

src_density picks which density to extract from a multi-density
source. dst_density is the rename target in the merged keyspace.
policy_filter is optional; comma-separated list of policy names to
keep from that source (drop others). Density rename applies to ALL
matching cells from the source.
"""
from __future__ import annotations

import os
import sys

import numpy as np   # type: ignore[import-not-found]


def _detect_source_densities(npz_path: str) -> set[str]:
    data = np.load(npz_path, allow_pickle=False)
    return {k.split("__", 1)[0]
            for k in data.files if k.endswith("__n_drifters")}


def _rename_keys(
    src_npz_path: str, src_density: str, dst_density: str,
    policy_filter: set[str] | None,
) -> dict[str, np.ndarray]:
    data = np.load(src_npz_path, allow_pickle=False)
    out: dict[str, np.ndarray] = {}
    src_prefix = src_density + "__"
    for k in data.files:
        if not k.startswith(src_prefix):
            continue
        rest = k[len(src_prefix):]
        # rest looks like "<policy>__<remainder>". Match the policy
        # against the filter if any.
        policy = rest.split("__", 1)[0]
        if policy_filter is not None and policy not in policy_filter:
            continue
        new_key = f"{dst_density}__{rest}"
        out[new_key] = data[k]
    return out


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    out_run_dir = sys.argv[1]
    sources = sys.argv[2:]

    merged: dict[str, np.ndarray] = {}
    for spec in sources:
        parts = spec.split(":")
        if len(parts) < 3:
            raise ValueError(
                f"source spec must be "
                f"<run_dir>:<src_density>:<dst_density>[:<filter>]; "
                f"got {spec!r}"
            )
        src_dir = parts[0]
        src_density = parts[1]
        dst_density = parts[2]
        filt = (set(parts[3].split(","))
                if len(parts) >= 4 and parts[3] else None)
        npz_path = os.path.join(src_dir, "raw", "results.npz")
        if not os.path.exists(npz_path):
            raise FileNotFoundError(f"missing {npz_path}")
        src_densities = _detect_source_densities(npz_path)
        if src_density not in src_densities:
            raise ValueError(
                f"source {src_dir} has densities {src_densities}; "
                f"requested src_density {src_density!r} not present"
            )
        keys = _rename_keys(npz_path, src_density, dst_density, filt)
        # Collision check.
        for k in keys:
            if k in merged:
                raise ValueError(
                    f"cell key collision after rename: {k!r} from "
                    f"{src_dir} (rename target {dst_density}) — pick "
                    f"distinct dst_density per source."
                )
        merged.update(keys)
        print(f"  {src_dir}: {src_density!r} → {dst_density!r} "
              f"({len(keys)} arrays, filter={filt})", flush=True)

    raw_dir = os.path.join(out_run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    out_path = os.path.join(raw_dir, "results.npz")
    np.savez(out_path, **merged)   # type: ignore[arg-type]
    print(f"  wrote merged npz: {out_path} ({len(merged)} arrays)",
          flush=True)


if __name__ == "__main__":
    main()
