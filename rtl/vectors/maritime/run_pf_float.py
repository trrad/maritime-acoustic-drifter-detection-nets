"""M1 PF orchestrator CLI — wires ScenarioReader + ScenarioTruthReader to
per-node PFFloat instances and produces three outputs:

- ``--out``: main estimate stream (one ``PFEstimateRecord`` per
  ``(tick, node_id)``).
- ``--particles-out`` (or default): particle sidecar stream subject to
  thinning knobs (``--thin-ticks``, ``--thin-particles``,
  ``--thin-nodes``); disabled by ``--no-particles``.
- ``--summary-out`` (or default ``pf_summary.json``): per-class RMSE
  aggregates over the final 25% of ticks, per-node ESS stats, and a
  ``completed`` flag — measurement report only.

Truth separation: ``run_pf_float.py`` is intentionally exempt from the
PF-library-truth-separation import-linter contract. It imports
``ScenarioTruthReader`` to compute the summary's RMSE; truth data does
NOT flow into any ``PFFloat`` method (PFFloat's signatures accept only
observation types).

See ``openspec/changes/maritime-pf-float/`` (design D9-D13, spec
"Main Estimate Stream", "Particle Sidecar Emission", "PF Summary
Measurement Report", "CLI Invocation").
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from rtl.vectors.maritime.pf_estimates_schema import (
    PF_ESTIMATE_SCHEMA_VERSION,
    PFEstimateHeader_Particles,
    ParticleRecord,
    make_jsonl_particle_writer,
)
from rtl.vectors.maritime.pf_float import PFFloat, PFFloatConfig
from rtl.vectors.maritime.scenario_schema import ScenarioReader
from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader
from rtl.vectors.maritime.state_layout import (
    ANCHOR_LAYOUT,
    BALLAST_DRIFTER_LAYOUT,
    PURE_DRIFTER_LAYOUT,
    StateLayout,
)


# ---------------------------------------------------------------------------
# Layout dispatch
# ---------------------------------------------------------------------------


_LAYOUTS: dict[str, StateLayout] = {
    "anchor": ANCHOR_LAYOUT,
    "ballast_drifter": BALLAST_DRIFTER_LAYOUT,
    "pure_drifter": PURE_DRIFTER_LAYOUT,
}


def _make_initial_cov_diag(layout: StateLayout) -> np.ndarray:
    """Per-class initial covariance diagonal, keyed by named layout slices.

    The PF starts at the first truth tick (an M1 concession — the CLI
    is the reporting layer per design D12 and may seed from truth
    without the algorithm itself touching truth). cov_diag is wide
    enough that the cloud has room to be informed by observations,
    narrow enough that particles stay in plausible range. Anchor
    position cov is tightened (σ=1m) since the anchor is surveyed.
    """
    cov = np.empty(layout.state_dim, dtype=float)
    cov[layout.slice("position")] = 100.0       # σ=10 m
    cov[layout.slice("velocity")] = 1.0         # σ=1 m/s
    cov[layout.slice("heading")] = 100.0        # σ=10 deg
    cov[layout.slice("surface_current")] = 0.04  # σ=0.2 m/s
    cov[layout.slice("imu_bias")] = 0.04        # σ=0.2 (bias)
    cov[layout.slice("prev_velocity")] = 1.0    # σ=1 m/s
    cov[layout.slice("prev_heading")] = 100.0   # σ=10 deg
    if "deep_current" in layout.groups:
        cov[layout.slice("deep_current")] = 0.04  # σ=0.2 m/s

    if layout.class_name == "anchor":
        cov[layout.slice("position")] = 1.0     # σ=1 m (surveyed)

    return cov


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the M1 PF over a scenario JSONL file.",
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        required=True,
        help="Input scenario JSONL path",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output main estimate stream JSONL path",
    )
    parser.add_argument(
        "--particles-out",
        type=Path,
        default=None,
        help=(
            "Output particle sidecar JSONL path. If omitted (and "
            "--no-particles is not set), defaults to <out_stem>.particles.jsonl"
        ),
    )
    parser.add_argument(
        "--no-particles",
        action="store_true",
        help="Disable the particle sidecar entirely.",
    )
    parser.add_argument(
        "--thin-ticks",
        type=int,
        default=1,
        help="Sidecar tick stride: write only ticks where t %% thin_ticks == 0 (default 1).",
    )
    parser.add_argument(
        "--thin-particles",
        type=int,
        default=50,
        help="Per-record subsample count for the sidecar (default 50).",
    )
    parser.add_argument(
        "--thin-nodes",
        type=str,
        default=None,
        help="Comma-separated subset of node_ids for the sidecar (default: all nodes).",
    )
    parser.add_argument(
        "--n-particles",
        type=int,
        default=500,
        help="Underlying PF particle count per node (default 500).",
    )
    parser.add_argument(
        "--predict-noise-pos",
        type=float,
        default=None,
        help=(
            "Override predict-step position process noise (m / sqrt(s)). "
            "Default: PFFloatConfig.process_noise_pos_m_per_sqrt_s = 1.0."
        ),
    )
    parser.add_argument(
        "--predict-noise-vel",
        type=float,
        default=None,
        help=(
            "Override the per-tick velocity sampling σ floor (m/s). "
            "Default: PFFloatConfig.process_noise_vel_ms_per_sqrt_s = 0.02. "
            "Under ``maritime-velocity-model``, per-tick σ is "
            "``sqrt(climatology.var_vxvy(lat, lon)) + floor``; this flag "
            "sets the floor. Setting to 0.0 collapses particle velocity "
            "to the pure climatology-variance draw (degenerate in regions "
            "where the climatology reports zero variance)."
        ),
    )
    parser.add_argument(
        "--predict-noise-heading",
        type=float,
        default=None,
        help=(
            "Override predict-step heading process noise (deg / sqrt(s)). "
            "Default: PFFloatConfig.process_noise_heading_deg_per_sqrt_s = 1.0."
        ),
    )
    parser.add_argument(
        "--predict-noise-current",
        type=float,
        default=None,
        help=(
            "Override predict-step current-state process noise (m/s / sqrt(s)). "
            "Default: PFFloatConfig.process_noise_current_ms_per_sqrt_s = 0.01."
        ),
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=None,
        help=(
            "Output summary JSON path. If omitted, defaults to "
            "<out_dir>/pf_summary.json."
        ),
    )
    return parser.parse_args(argv)


def _build_pf_config(args: argparse.Namespace) -> PFFloatConfig:
    """Build a PFFloatConfig from CLI args, applying predict-noise overrides."""
    defaults = PFFloatConfig(n_particles=args.n_particles)
    return PFFloatConfig(
        n_particles=args.n_particles,
        process_noise_pos_m_per_sqrt_s=(
            args.predict_noise_pos
            if args.predict_noise_pos is not None
            else defaults.process_noise_pos_m_per_sqrt_s
        ),
        process_noise_vel_ms_per_sqrt_s=(
            args.predict_noise_vel
            if args.predict_noise_vel is not None
            else defaults.process_noise_vel_ms_per_sqrt_s
        ),
        process_noise_heading_deg_per_sqrt_s=(
            args.predict_noise_heading
            if args.predict_noise_heading is not None
            else defaults.process_noise_heading_deg_per_sqrt_s
        ),
        process_noise_current_ms_per_sqrt_s=(
            args.predict_noise_current
            if args.predict_noise_current is not None
            else defaults.process_noise_current_ms_per_sqrt_s
        ),
    )


def _derive_default_particles_path(out_path: Path) -> Path:
    """Default sidecar path: <out_dir>/<out_stem>.particles.jsonl."""
    return out_path.with_name(out_path.stem + ".particles.jsonl")


def _derive_default_summary_path(out_path: Path) -> Path:
    """Default summary path: <out_dir>/pf_summary.json."""
    return out_path.parent / "pf_summary.json"


# ---------------------------------------------------------------------------
# Main estimate stream writer (plain JSONL — the schema module only
# provides a typed writer for the sidecar).
# ---------------------------------------------------------------------------


def _write_main_header(
    f,
    *,
    scenario_path: str,
    scenario_seed: int,
    n_particles: int,
    node_ids: tuple[str, ...],
    created_at_utc: str,
) -> None:
    record = {
        "record_type": "header",
        "schema_version": PF_ESTIMATE_SCHEMA_VERSION,
        "scenario_path": scenario_path,
        "scenario_seed": scenario_seed,
        "pf_impl": "float64_bootstrap",
        "n_particles": n_particles,
        "node_ids": list(node_ids),
        "created_at_utc": created_at_utc,
    }
    f.write(json.dumps(record) + "\n")


def _write_main_record(f, *, t: int, t_sec: float, node_id: str,
                       mean: tuple[float, ...], cov_diag: tuple[float, ...],
                       n_effective: float) -> None:
    record = {
        "record_type": "estimate",
        "t": t,
        "t_sec": t_sec,
        "node_id": node_id,
        "mean": list(mean),
        "cov_diag": list(cov_diag),
        "n_effective": n_effective,
    }
    f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Tick loop
# ---------------------------------------------------------------------------


def _build_pfs(
    *,
    scenario_header,
    first_truth_tick,
    onboard_map,
    config: PFFloatConfig,
    seed: int,
) -> dict[str, PFFloat]:
    """One PFFloat per node, seeded from the first truth tick state."""
    parent_rng = np.random.default_rng(seed)
    pfs: dict[str, PFFloat] = {}
    for node_id in scenario_header.node_ids:
        class_name = scenario_header.node_classes[node_id]
        if class_name not in _LAYOUTS:
            raise ValueError(
                f"Unknown node class '{class_name}' for node {node_id!r}; "
                f"expected one of {sorted(_LAYOUTS)}"
            )
        layout = _LAYOUTS[class_name]
        truth_state = first_truth_tick.node_truth.get(node_id)
        if truth_state is None:
            raise ValueError(
                f"First truth tick missing node_truth for node {node_id!r}"
            )
        if truth_state.shape != (layout.state_dim,):
            raise ValueError(
                f"Truth state for node {node_id!r} has shape "
                f"{truth_state.shape}; expected ({layout.state_dim},) for "
                f"class '{class_name}'"
            )
        initial_mean = truth_state.copy()
        cov_diag = _make_initial_cov_diag(layout)
        node_seed = int(parent_rng.integers(0, 2**32))
        node_rng = np.random.default_rng(node_seed)
        pfs[node_id] = PFFloat(
            node_id=node_id,
            layout=layout,
            initial_state_mean=initial_mean,
            initial_state_cov_diag=cov_diag,
            onboard_map=onboard_map,
            anchor_positions=scenario_header.anchor_positions,
            enu_origin_lat_deg=scenario_header.bbox[0],
            enu_origin_lon_deg=scenario_header.bbox[1],
            config=config,
            rng=node_rng,
        )
    return pfs


def run(args: argparse.Namespace) -> None:
    """Top-level orchestration. Raises ``ValueError`` on schema mismatch."""
    scenario_path: Path = args.scenario
    out_path: Path = args.out

    # Resolve default paths.
    if args.no_particles:
        particles_path: Path | None = None
    elif args.particles_out is not None:
        particles_path = args.particles_out
    else:
        particles_path = _derive_default_particles_path(out_path)

    if args.summary_out is not None:
        summary_path: Path = args.summary_out
    else:
        summary_path = _derive_default_summary_path(out_path)

    # Open readers. ScenarioReader raises ValueError on unsupported
    # schema_version at construction; let it bubble up to main()'s
    # try/except for stderr formatting.
    obs_reader = ScenarioReader(scenario_path)
    truth_reader = ScenarioTruthReader(scenario_path)

    scenario_header = obs_reader.header()
    onboard_map = obs_reader.onboard_map()

    # Validate thinning args against the fleet.
    if args.thin_ticks < 1:
        raise ValueError(f"--thin-ticks must be >= 1, got {args.thin_ticks}")
    if args.thin_particles < 1:
        raise ValueError(
            f"--thin-particles must be >= 1, got {args.thin_particles}"
        )
    if args.thin_particles > args.n_particles:
        raise ValueError(
            f"--thin-particles ({args.thin_particles}) must not exceed "
            f"--n-particles ({args.n_particles})"
        )
    if args.n_particles < 1:
        raise ValueError(f"--n-particles must be >= 1, got {args.n_particles}")

    thin_nodes_tuple: tuple[str, ...] | None
    if args.thin_nodes is not None:
        thin_nodes_tuple = tuple(
            s.strip() for s in args.thin_nodes.split(",") if s.strip()
        )
        # Validate node_ids exist in the fleet — explicit error per AGENTS.md.
        unknown = set(thin_nodes_tuple) - set(scenario_header.node_ids)
        if unknown:
            raise ValueError(
                f"--thin-nodes references unknown node_ids: {sorted(unknown)}"
            )
    else:
        thin_nodes_tuple = None
    thin_nodes_set: set[str] | None = (
        set(thin_nodes_tuple) if thin_nodes_tuple is not None else None
    )

    # Pull first truth tick to seed each PF's initial mean.
    truth_iter = iter(truth_reader)
    try:
        first_truth_tick = next(truth_iter)
    except StopIteration as exc:
        raise ValueError(
            f"Scenario {scenario_path} has no truth tick records"
        ) from exc

    pf_config = _build_pf_config(args)
    pfs = _build_pfs(
        scenario_header=scenario_header,
        first_truth_tick=first_truth_tick,
        onboard_map=onboard_map,
        config=pf_config,
        seed=scenario_header.seed,
    )

    created_at_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Sidecar setup. Use a separate RNG for sidecar particle subsampling
    # so it's independent of per-PF RNGs.
    sidecar_rng = np.random.default_rng(0)
    sidecar_writer = None
    if particles_path is not None:
        sidecar_writer = make_jsonl_particle_writer(particles_path)
        sidecar_writer.write_header(
            PFEstimateHeader_Particles(
                schema_version=PF_ESTIMATE_SCHEMA_VERSION,
                parent_estimate_path=str(out_path),
                scenario_seed=scenario_header.seed,
                n_particles_full=args.n_particles,
                thin_ticks=args.thin_ticks,
                thin_particles=args.thin_particles,
                thin_nodes=thin_nodes_tuple,
                created_at_utc=created_at_utc,
            )
        )

    # Build the iteration: walk obs and truth in lockstep. We've already
    # consumed the first truth tick to seed PFs; walk both fresh from
    # the start now (the obs reader hasn't been consumed yet).
    truth_reader_2 = ScenarioTruthReader(scenario_path)
    truth_iter_2 = iter(truth_reader_2)
    obs_iter = iter(obs_reader)

    # Per-class position errors confined to the final-25% RMSE window.
    # Compute the cutoff up front from the scenario duration so we
    # never buffer ticks we'll discard at summary time. Falls back to
    # `0` (everything-in-window) if duration is unknown.
    total_ticks_expected = max(1, int(round(scenario_header.duration_sec / scenario_header.dt_sec)))
    rmse_window = max(1, (total_ticks_expected + 3) // 4)
    rmse_cutoff = total_ticks_expected - rmse_window

    position_errors_by_class: dict[str, list[float]] = defaultdict(list)
    # Per-node ESS — running stats; we only need (count, sum, min, max).
    ess_count: dict[str, int] = {nid: 0 for nid in scenario_header.node_ids}
    ess_sum: dict[str, float] = {nid: 0.0 for nid in scenario_header.node_ids}
    ess_min: dict[str, float] = {nid: float("inf") for nid in scenario_header.node_ids}
    ess_max: dict[str, float] = {nid: float("-inf") for nid in scenario_header.node_ids}

    # Per-node position slice — derived once per layout to avoid index drift.
    position_slice_by_node: dict[str, slice] = {
        nid: _LAYOUTS[scenario_header.node_classes[nid]].slice("position")
        for nid in scenario_header.node_ids
    }

    dt_sec = scenario_header.dt_sec
    try:
        with out_path.open("w") as main_f:
            _write_main_header(
                main_f,
                scenario_path=str(scenario_path),
                scenario_seed=scenario_header.seed,
                n_particles=args.n_particles,
                node_ids=scenario_header.node_ids,
                created_at_utc=created_at_utc,
            )

            for obs_tick, truth_tick in zip(obs_iter, truth_iter_2):
                if obs_tick.t != truth_tick.t:
                    raise ValueError(
                        f"Observation/truth tick mismatch: obs.t={obs_tick.t}, "
                        f"truth.t={truth_tick.t}"
                    )
                t = obs_tick.t
                t_sec = obs_tick.t_sec
                in_rmse_window = t >= rmse_cutoff

                obs_by_node: dict[str, list] = defaultdict(list)
                for obs in obs_tick.observations:
                    obs_by_node[obs.node_id].append(obs)

                for node_id, pf in pfs.items():
                    record = pf.step(
                        dt_sec=dt_sec,
                        observations=obs_by_node.get(node_id, []),
                        t=t,
                        t_sec=t_sec,
                    )
                    _write_main_record(
                        main_f,
                        t=record.t,
                        t_sec=record.t_sec,
                        node_id=record.node_id,
                        mean=record.mean,
                        cov_diag=record.cov_diag,
                        n_effective=record.n_effective,
                    )

                    n_eff = record.n_effective
                    ess_count[node_id] += 1
                    ess_sum[node_id] += n_eff
                    if n_eff < ess_min[node_id]:
                        ess_min[node_id] = n_eff
                    if n_eff > ess_max[node_id]:
                        ess_max[node_id] = n_eff

                    if in_rmse_window:
                        truth_state = truth_tick.node_truth.get(node_id)
                        if truth_state is not None:
                            pos_slc = position_slice_by_node[node_id]
                            truth_pos = truth_state[pos_slc]
                            est_pos = np.asarray(record.mean[pos_slc])
                            err = float(np.linalg.norm(est_pos - truth_pos))
                            class_name = scenario_header.node_classes[node_id]
                            position_errors_by_class[class_name].append(err)

                    if (
                        sidecar_writer is not None
                        and (t % args.thin_ticks == 0)
                        and (thin_nodes_set is None or node_id in thin_nodes_set)
                    ):
                        idx = sidecar_rng.choice(
                            args.n_particles,
                            size=args.thin_particles,
                            replace=False,
                        )
                        sub_particles = pf.particles[idx]
                        sub_weights = pf.weights[idx]
                        wsum = float(sub_weights.sum())
                        if wsum > 0:
                            sub_weights = sub_weights / wsum
                        else:
                            # All-zero subset — emit uniform weights
                            # so the ParticleRecord sum-to-1 invariant
                            # holds. Main-stream n_effective remains
                            # the authoritative health signal.
                            sub_weights = np.full(
                                args.thin_particles, 1.0 / args.thin_particles
                            )
                        sidecar_writer.write_record(
                            ParticleRecord(
                                t=t,
                                t_sec=t_sec,
                                node_id=node_id,
                                particles=tuple(
                                    tuple(p.tolist()) for p in sub_particles
                                ),
                                weights=tuple(sub_weights.tolist()),
                            )
                        )
    finally:
        if sidecar_writer is not None:
            sidecar_writer.close()

    summary = _compute_summary(
        position_errors_by_class=position_errors_by_class,
        ess_count=ess_count,
        ess_sum=ess_sum,
        ess_min=ess_min,
        ess_max=ess_max,
    )
    summary["completed"] = True

    with summary_path.open("w") as f:
        json.dump(summary, f, indent=2)


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------


def _compute_summary(
    *,
    position_errors_by_class: dict[str, list[float]],
    ess_count: dict[str, int],
    ess_sum: dict[str, float],
    ess_min: dict[str, float],
    ess_max: dict[str, float],
) -> dict:
    """Build the summary dict.

    Per-class RMSE: median / mean / p95 of the position-error magnitudes
    that the tick loop already filtered to the final-25% window. Always
    emits keys for the three M1 classes for stable downstream shape; an
    empty class reports 0.0 (measurement-only convention).

    Per-node ESS: mean / min / max from the running stats accumulated
    during the tick loop.
    """
    rmse_by_class: dict[str, dict[str, float]] = {}
    for class_name in ("anchor", "ballast_drifter", "pure_drifter"):
        errs = position_errors_by_class.get(class_name, [])
        if errs:
            sorted_errs = sorted(errs)
            median = statistics.median(sorted_errs)
            mean = statistics.fmean(sorted_errs)
            # Nearest-rank p95; degenerates to max for small samples,
            # acceptable for a measurement report.
            k = max(0, min(len(sorted_errs) - 1,
                           int(round(0.95 * (len(sorted_errs) - 1)))))
            p95 = sorted_errs[k]
        else:
            median = mean = p95 = 0.0
        rmse_by_class[class_name] = {
            "median": float(median),
            "mean": float(mean),
            "p95": float(p95),
        }

    ess_stats_by_node: dict[str, dict[str, float]] = {}
    for node_id, count in ess_count.items():
        if count > 0:
            ess_stats_by_node[node_id] = {
                "mean": ess_sum[node_id] / count,
                "min": ess_min[node_id],
                "max": ess_max[node_id],
            }
        else:
            ess_stats_by_node[node_id] = {
                "mean": 0.0,
                "min": 0.0,
                "max": 0.0,
            }

    return {
        "rmse_by_class": rmse_by_class,
        "ess_by_node": ess_stats_by_node,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        run(args)
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
