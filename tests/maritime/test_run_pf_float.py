"""Contract tests for the M1 ``run_pf_float`` CLI — Batch F.

Batch F covers the orchestrator CLI at
``rtl/vectors/maritime/run_pf_float.py`` (tasks 26.4, 27.1, 28.1, 28.2,
29.*, 30.*, 31.*, 32.*, 7.6, 1.6) — the binary that wires
``ScenarioReader`` + ``ScenarioTruthReader`` (for the summary only) to
per-node ``PFFloat`` instances and produces:

- the main estimate stream (``--out``),
- the optional particle sidecar (``--particles-out`` / ``--no-particles``),
- the per-class summary report (``--summary-out`` / default
  ``pf_summary.json``).

These tests intentionally fail with a "no such module" / "no such file"
error until ``rtl/vectors/maritime/run_pf_float.py`` lands. The
implementer makes every assertion here pass without modifying these
tests; the file is the "done" definition for Batch F.

Design notes:

- Tests invoke the CLI as a subprocess via ``sys.executable -m
  rtl.vectors.maritime.run_pf_float`` — same pattern as
  ``test_scenario_gen.py``. Subprocess calls give the test the same
  argv / file I/O surface a human operator sees.
- ``small_scenario`` is a module-scoped fixture: a 60 s, 10-node M1
  scenario produced by the real ``gen_maritime_scenario`` CLI. Two
  ticks (``t=0``, ``t=1``) keep generation + PF runtime small while
  exercising the multi-tick path. Reusing the fixture across tests
  amortizes the ~0.3 s scenario-gen cost.
- ``cli_run_default`` is a module-scoped fixture: one PF run with
  default flags whose three output paths are passed explicitly so each
  test knows where to look without coupling to the implementer's
  default-derivation convention. Passing ``--particles-out`` and
  ``--summary-out`` explicitly is on purpose — see fixture docstring.
- Node IDs in the M1 fleet are class-derived (``anchor_<hash>``,
  ``ballast_drifter_<hash>``, ``pure_drifter_<hash>``). Tests that
  filter by node_id pull the actual IDs from the scenario header at
  runtime; they do NOT hardcode ``n00`` / ``n05``.
- No RMSE / convergence thresholds are asserted — only sanity (finite,
  non-negative, ESS > 0). This matches the spec's "PF Summary
  Measurement Report" requirement and AGENTS.md "No unprincipled
  numeric thresholds in specs."
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from rtl.vectors.maritime.pf_estimates_schema import (
    PFEstimateReader,
    ParticleStreamReader,
)
from rtl.vectors.maritime.scenario_schema import ScenarioReader


# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------


PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# Pinned timestamp so the small_scenario fixture is byte-stable across
# pytest invocations — also matches the pattern in ``test_scenario_gen.py``.
FIXED_CREATED_AT: str = "2026-04-22T00:00:00+00:00"

# 60 s of scenario time at dt=60s gives ceil(60.12 / 60.0) = 2 ticks
# (t=0, t=1). The M1 fleet size is fixed at 10 by the generator.
SCENARIO_DURATION_HOURS: float = 0.0167
SCENARIO_DT_SEC: float = 60.0
SCENARIO_BBOX: str = "20.0,-160.0,20.5,-159.5"
SCENARIO_SEED: int = 42
SCENARIO_NODE_COUNT: int = 10
EXPECTED_TICK_COUNT: int = 2

# Smaller particle count than the production default keeps PF runtime
# fast in the test suite. Must be >= --thin-particles (default 50).
TEST_N_PARTICLES: int = 100


def _run_pf_cli(
    *,
    scenario_path: Path,
    out_path: Path,
    summary_path: Path | None = None,
    particles_out_path: Path | None = None,
    no_particles: bool = False,
    n_particles: int = TEST_N_PARTICLES,
    thin_ticks: int | None = None,
    thin_particles: int | None = None,
    thin_nodes: str | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Invoke ``run_pf_float`` as a subprocess. Returns the completed process.

    Mirrors ``test_scenario_gen.py``'s ``run_cli`` pattern: invoke via
    ``sys.executable -m`` so the test runs against the project's resolved
    Python interpreter (the venv when invoked under ``uv run pytest``).
    """
    cmd: list[str] = [
        sys.executable,
        "-m",
        "rtl.vectors.maritime.run_pf_float",
        "--scenario",
        str(scenario_path),
        "--out",
        str(out_path),
        "--n-particles",
        str(n_particles),
    ]
    if summary_path is not None:
        cmd.extend(["--summary-out", str(summary_path)])
    if no_particles:
        cmd.append("--no-particles")
    elif particles_out_path is not None:
        cmd.extend(["--particles-out", str(particles_out_path)])
    if thin_ticks is not None:
        cmd.extend(["--thin-ticks", str(thin_ticks)])
    if thin_particles is not None:
        cmd.extend(["--thin-particles", str(thin_particles)])
    if thin_nodes is not None:
        cmd.extend(["--thin-nodes", thin_nodes])
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, cwd=str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_scenario(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate a small 10-node, 2-tick M1 scenario via the real CLI.

    M1 requires exactly 10 nodes. A 60 s duration with dt=60s yields
    2 ticks (t=0, t=1) — enough to exercise multi-tick aggregation
    while keeping fixture cost low.

    Module-scoped so every test in this file shares the same scenario
    file (and the same onboard_map.pkl sidecar in its parent dir).
    """
    scenario_dir = tmp_path_factory.mktemp("scenario")
    scenario_path = scenario_dir / "scenario.jsonl"
    cmd = [
        sys.executable,
        "-m",
        "rtl.vectors.maritime.gen_maritime_scenario",
        "--seed",
        str(SCENARIO_SEED),
        "--bbox",
        SCENARIO_BBOX,
        "--duration-hours",
        str(SCENARIO_DURATION_HOURS),
        "--dt-sec",
        str(SCENARIO_DT_SEC),
        "--nodes",
        str(SCENARIO_NODE_COUNT),
        "--out",
        str(scenario_path),
        "--created-at",
        FIXED_CREATED_AT,
    ]
    result = subprocess.run(cmd, capture_output=True, cwd=str(PROJECT_ROOT))
    assert result.returncode == 0, (
        f"Scenario generator failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout.decode(errors='replace')}\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )
    assert scenario_path.exists(), "Generator did not produce scenario file"
    return scenario_path


@pytest.fixture(scope="module")
def scenario_node_ids(small_scenario: Path) -> tuple[str, ...]:
    """Return the actual node_ids the generator produced for ``small_scenario``.

    The M1 generator emits class-prefixed IDs (``anchor_<hash>``,
    ``ballast_drifter_<hash>``, ``pure_drifter_<hash>``) — they are NOT
    ``n00`` / ``n01``. Tests that need to filter by node_id pull the
    real IDs from this fixture.
    """
    return ScenarioReader(small_scenario).header().node_ids


@pytest.fixture(scope="module")
def cli_run_default(
    small_scenario: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Path]:
    """Run the PF CLI with default flags. Returns the three output paths.

    Passes ``--particles-out`` and ``--summary-out`` explicitly so the
    test knows where to look without coupling to the implementer's
    default-derivation convention (see spec "Particle Sidecar Emission
    with Thinning" — the default sidecar path is derived from ``--out``,
    but the convention isn't pinned by the spec). Tests that exercise
    the default-path derivation specifically use a separate CLI call.
    """
    out_dir = tmp_path_factory.mktemp("pf_default")
    main_path = out_dir / "estimates.jsonl"
    sidecar_path = out_dir / "particles.jsonl"
    summary_path = out_dir / "pf_summary.json"
    result = _run_pf_cli(
        scenario_path=small_scenario,
        out_path=main_path,
        summary_path=summary_path,
        particles_out_path=sidecar_path,
    )
    assert result.returncode == 0, (
        f"PF CLI failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout.decode(errors='replace')}\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )
    return {
        "main": main_path,
        "particles": sidecar_path,
        "summary": summary_path,
    }


# ---------------------------------------------------------------------------
# Section A — Main stream contract (tasks 28.1, 28.2, 31.1-31.4, 1.6)
# ---------------------------------------------------------------------------


def test_cli_runs_with_defaults_and_exits_zero(
    cli_run_default: dict[str, Path],
) -> None:
    """Tasks 31.4, 32.1: Default-flag invocation produces a non-empty
    main estimate file and exits 0.

    The fixture has already enforced ``returncode == 0``; this test
    reasserts the file artifact survives that exit.
    """
    main_path = cli_run_default["main"]
    assert main_path.exists(), f"Main estimate file missing: {main_path}"
    assert main_path.stat().st_size > 0, (
        f"Main estimate file is empty: {main_path}"
    )


def test_main_stream_one_record_per_tick_per_node(
    cli_run_default: dict[str, Path],
    scenario_node_ids: tuple[str, ...],
) -> None:
    """Task 28.1: Main stream emits one ``PFEstimateRecord`` per
    ``(t, node_id)`` for every node — no privileged subset.

    With 10 nodes and 2 ticks the stream contains exactly 20 records,
    each ``(t, node_id)`` pair appearing exactly once.
    """
    reader = PFEstimateReader(cli_run_default["main"])
    records = list(reader)
    expected_total = SCENARIO_NODE_COUNT * EXPECTED_TICK_COUNT
    assert len(records) == expected_total, (
        f"Expected {expected_total} estimate records "
        f"({SCENARIO_NODE_COUNT} nodes x {EXPECTED_TICK_COUNT} ticks); "
        f"got {len(records)}"
    )

    # Every (t, node_id) pair appears exactly once.
    pairs: list[tuple[int, str]] = [(r.t, r.node_id) for r in records]
    assert len(pairs) == len(set(pairs)), (
        f"Duplicate (t, node_id) pairs in main stream: "
        f"{[p for p in pairs if pairs.count(p) > 1]}"
    )

    # And the (t, node_id) pairs cover every (tick, node) combination.
    expected_pairs = {
        (t, node_id)
        for t in range(EXPECTED_TICK_COUNT)
        for node_id in scenario_node_ids
    }
    assert set(pairs) == expected_pairs, (
        f"Main stream (t, node_id) coverage mismatch.\n"
        f"Missing: {sorted(expected_pairs - set(pairs))}\n"
        f"Unexpected: {sorted(set(pairs) - expected_pairs)}"
    )


def test_main_stream_records_have_no_particles_or_weights(
    cli_run_default: dict[str, Path],
) -> None:
    """Task 28.2: Main stream records have no ``particles`` or
    ``weights`` field.

    The ``PFEstimateRecord`` dataclass is a frozen, slotted shape with
    only ``t / t_sec / node_id / mean / cov_diag / n_effective``;
    ``particles`` and ``weights`` belong in the sidecar, never the main
    stream.
    """
    reader = PFEstimateReader(cli_run_default["main"])
    for record in reader:
        assert not hasattr(record, "particles"), (
            f"Main-stream record carries forbidden 'particles' attr: "
            f"(t={record.t}, node_id={record.node_id})"
        )
        assert not hasattr(record, "weights"), (
            f"Main-stream record carries forbidden 'weights' attr: "
            f"(t={record.t}, node_id={record.node_id})"
        )


def test_main_stream_means_finite(
    cli_run_default: dict[str, Path],
) -> None:
    """Task 31.1: every ``record.mean`` entry is finite (no NaN, no inf).

    The dataclass already rejects non-finite means at construction —
    this test exercises the post-CLI artifact and would catch a
    regression where the implementer bypasses the dataclass
    validation (e.g., writing JSONL by hand without round-tripping).
    """
    import math

    reader = PFEstimateReader(cli_run_default["main"])
    for record in reader:
        for i, value in enumerate(record.mean):
            assert math.isfinite(value), (
                f"Non-finite mean[{i}]={value} at t={record.t}, "
                f"node_id={record.node_id}"
            )


def test_main_stream_cov_diag_non_negative_finite(
    cli_run_default: dict[str, Path],
) -> None:
    """Task 31.2: every ``record.cov_diag`` entry is non-negative and finite.

    Variance can never be negative; an inf would silently propagate
    into downstream estimators. The dataclass enforces both at
    construction — this is a post-CLI artifact check.
    """
    import math

    reader = PFEstimateReader(cli_run_default["main"])
    for record in reader:
        for i, value in enumerate(record.cov_diag):
            assert math.isfinite(value), (
                f"Non-finite cov_diag[{i}]={value} at t={record.t}, "
                f"node_id={record.node_id}"
            )
            assert value >= 0, (
                f"Negative cov_diag[{i}]={value} at t={record.t}, "
                f"node_id={record.node_id}"
            )


def test_main_stream_n_effective_positive(
    cli_run_default: dict[str, Path],
) -> None:
    """Task 31.3: every ``record.n_effective`` is strictly positive.

    ``n_effective = 1 / sum(w_i^2)`` is positive for any normalized
    weight vector. A zero ESS would mean the bootstrap PF degenerated
    silently — the spec's "ESS never zero" sanity invariant catches it.
    """
    reader = PFEstimateReader(cli_run_default["main"])
    for record in reader:
        assert record.n_effective > 0, (
            f"Non-positive n_effective={record.n_effective} at "
            f"t={record.t}, node_id={record.node_id}"
        )


def test_main_stream_header_echoes_cli_inputs(
    cli_run_default: dict[str, Path],
    small_scenario: Path,
    scenario_node_ids: tuple[str, ...],
) -> None:
    """Task 1.6: Main stream header echoes CLI inputs + PF configuration.

    After invoking the CLI with ``--scenario <path> --out <path>
    --n-particles <N>``, the stream's header carries:

    - ``scenario_path`` identifying the source scenario (verbatim
      string OR resolved absolute path — the spec doesn't pin which);
    - ``scenario_seed`` matching the source scenario's header seed;
    - ``n_particles`` matching the ``--n-particles`` argument;
    - ``pf_impl == "float64_bootstrap"``;
    - ``node_ids`` matching the source scenario's fleet.
    """
    reader = PFEstimateReader(cli_run_default["main"])
    header = reader.header()

    # scenario_path: accept either the verbatim arg or the resolved path,
    # to avoid pinning the implementer to one normalization choice.
    expected_paths = {
        str(small_scenario),
        str(small_scenario.resolve()),
    }
    assert header.scenario_path in expected_paths, (
        f"header.scenario_path={header.scenario_path!r} does not match "
        f"the input scenario path. Expected one of {expected_paths!r}."
    )

    assert header.scenario_seed == SCENARIO_SEED, (
        f"header.scenario_seed={header.scenario_seed}; "
        f"expected {SCENARIO_SEED} (from the source scenario)"
    )
    assert header.n_particles == TEST_N_PARTICLES, (
        f"header.n_particles={header.n_particles}; "
        f"expected {TEST_N_PARTICLES} (from --n-particles)"
    )
    assert header.pf_impl == "float64_bootstrap", (
        f"header.pf_impl={header.pf_impl!r}; expected 'float64_bootstrap'"
    )
    assert set(header.node_ids) == set(scenario_node_ids), (
        f"header.node_ids does not match the scenario fleet.\n"
        f"Missing: {sorted(set(scenario_node_ids) - set(header.node_ids))}\n"
        f"Extraneous: {sorted(set(header.node_ids) - set(scenario_node_ids))}"
    )


# ---------------------------------------------------------------------------
# Section B — Sidecar thinning (tasks 29.1-29.5, 7.6, 32.4-32.6)
# ---------------------------------------------------------------------------


def test_sidecar_default_thinning_record_count(
    cli_run_default: dict[str, Path],
) -> None:
    """Task 29.1: Default thinning (``thin_ticks=1``,
    ``thin_particles=50``, ``thin_nodes=all``) writes every tick for
    every node with 50 particles each.

    With 10 nodes x 2 ticks the sidecar carries 20 records. Each
    record carries 50 particles (the default ``thin_particles``).
    """
    reader = ParticleStreamReader(cli_run_default["particles"])
    header = reader.header()
    assert header.thin_ticks == 1, (
        f"Default header.thin_ticks={header.thin_ticks}; expected 1"
    )
    assert header.thin_particles == 50, (
        f"Default header.thin_particles={header.thin_particles}; "
        f"expected 50"
    )
    assert header.thin_nodes is None, (
        f"Default header.thin_nodes={header.thin_nodes!r}; expected "
        f"None (no node restriction)"
    )

    records = list(reader)
    expected_total = SCENARIO_NODE_COUNT * EXPECTED_TICK_COUNT
    assert len(records) == expected_total, (
        f"Default thinning: expected {expected_total} sidecar records "
        f"({SCENARIO_NODE_COUNT} nodes x {EXPECTED_TICK_COUNT} ticks); "
        f"got {len(records)}"
    )
    for record in records:
        assert len(record.particles) == 50, (
            f"Record (t={record.t}, node_id={record.node_id}) carries "
            f"{len(record.particles)} particles; expected 50"
        )


def test_sidecar_thin_ticks_reduces_cadence(
    small_scenario: Path,
    tmp_path: Path,
) -> None:
    """Task 29.2: ``--thin-ticks 2`` keeps only ticks where
    ``t % 2 == 0``; the sidecar header records ``thin_ticks == 2``.

    With 2 ticks (t=0, t=1) only t=0 passes the filter, leaving 10
    records (one per node).
    """
    main_path = tmp_path / "estimates.jsonl"
    sidecar_path = tmp_path / "particles.jsonl"
    summary_path = tmp_path / "pf_summary.json"
    result = _run_pf_cli(
        scenario_path=small_scenario,
        out_path=main_path,
        summary_path=summary_path,
        particles_out_path=sidecar_path,
        thin_ticks=2,
    )
    assert result.returncode == 0, (
        f"PF CLI failed (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )

    reader = ParticleStreamReader(sidecar_path)
    assert reader.header().thin_ticks == 2, (
        f"header.thin_ticks={reader.header().thin_ticks}; expected 2"
    )

    records = list(reader)
    expected_count = SCENARIO_NODE_COUNT  # one passing tick (t=0)
    assert len(records) == expected_count, (
        f"--thin-ticks 2: expected {expected_count} records "
        f"(only t=0 passes for a 2-tick run); got {len(records)}"
    )
    for record in records:
        assert record.t % 2 == 0, (
            f"Sidecar record at t={record.t} fails thin_ticks=2 filter "
            f"(t % 2 != 0)"
        )


def test_sidecar_thin_nodes_restricts_subset(
    small_scenario: Path,
    scenario_node_ids: tuple[str, ...],
    tmp_path: Path,
) -> None:
    """Task 29.3: ``--thin-nodes id1,id2`` restricts sidecar records to
    the listed subset; the header records the tuple.

    Picks the first two node_ids from the actual fleet (M1 IDs are
    class-prefixed ``anchor_<hash>`` etc., not ``n00``/``n05``).
    """
    selected = (scenario_node_ids[0], scenario_node_ids[1])
    thin_nodes_arg = ",".join(selected)

    main_path = tmp_path / "estimates.jsonl"
    sidecar_path = tmp_path / "particles.jsonl"
    summary_path = tmp_path / "pf_summary.json"
    result = _run_pf_cli(
        scenario_path=small_scenario,
        out_path=main_path,
        summary_path=summary_path,
        particles_out_path=sidecar_path,
        thin_nodes=thin_nodes_arg,
    )
    assert result.returncode == 0, (
        f"PF CLI failed (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )

    reader = ParticleStreamReader(sidecar_path)
    assert reader.header().thin_nodes == selected, (
        f"header.thin_nodes={reader.header().thin_nodes!r}; "
        f"expected {selected!r}"
    )

    selected_set = set(selected)
    for record in reader:
        assert record.node_id in selected_set, (
            f"Sidecar record carries node_id={record.node_id!r} "
            f"outside --thin-nodes subset {selected!r}"
        )


def test_no_particles_skips_sidecar(
    small_scenario: Path,
    tmp_path: Path,
) -> None:
    """Tasks 29.4, 32.4: ``--no-particles`` writes no sidecar.

    The specified sidecar path does NOT exist after the run. The main
    file and summary file are still written.
    """
    main_path = tmp_path / "estimates.jsonl"
    sidecar_path = tmp_path / "particles.jsonl"
    summary_path = tmp_path / "pf_summary.json"
    result = _run_pf_cli(
        scenario_path=small_scenario,
        out_path=main_path,
        summary_path=summary_path,
        # particles_out_path intentionally omitted; --no-particles wins
        no_particles=True,
    )
    assert result.returncode == 0, (
        f"PF CLI failed (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )

    assert not sidecar_path.exists(), (
        f"--no-particles: sidecar file should not exist, but found: "
        f"{sidecar_path}"
    )
    assert main_path.exists(), (
        f"--no-particles: main estimate file should still be written: "
        f"{main_path}"
    )
    assert summary_path.exists(), (
        f"--no-particles: summary file should still be written: "
        f"{summary_path}"
    )


def test_thinning_composes_with_and(
    small_scenario: Path,
    scenario_node_ids: tuple[str, ...],
    tmp_path: Path,
) -> None:
    """Tasks 29.5, 32.6: thinning knobs compose with AND.

    ``--thin-ticks 2 --thin-nodes <single_id>`` produces records only
    where ``(t % 2 == 0) AND (node_id == <single_id>)``. With 2 ticks
    only t=0 passes the tick filter, and only one node passes the node
    filter, leaving exactly 1 record.
    """
    selected_node = scenario_node_ids[0]

    main_path = tmp_path / "estimates.jsonl"
    sidecar_path = tmp_path / "particles.jsonl"
    summary_path = tmp_path / "pf_summary.json"
    result = _run_pf_cli(
        scenario_path=small_scenario,
        out_path=main_path,
        summary_path=summary_path,
        particles_out_path=sidecar_path,
        thin_ticks=2,
        thin_nodes=selected_node,
    )
    assert result.returncode == 0, (
        f"PF CLI failed (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )

    reader = ParticleStreamReader(sidecar_path)
    records = list(reader)
    assert len(records) == 1, (
        f"Composed thinning (--thin-ticks 2 --thin-nodes {selected_node}): "
        f"expected 1 record (t=0 AND node_id={selected_node!r}); "
        f"got {len(records)}"
    )
    only_record = records[0]
    assert only_record.t == 0 and only_record.node_id == selected_node, (
        f"Composed-thinning record mismatch: "
        f"got (t={only_record.t}, node_id={only_record.node_id!r}); "
        f"expected (t=0, node_id={selected_node!r})"
    )


def test_sidecar_header_echoes_cli_config(
    small_scenario: Path,
    scenario_node_ids: tuple[str, ...],
    tmp_path: Path,
) -> None:
    """Task 7.6: After invoking with ``--thin-ticks 2 --thin-particles
    30 --thin-nodes id1,id2 --n-particles 100``, the sidecar header
    carries ``parent_estimate_path``, ``scenario_seed``,
    ``n_particles_full``, ``thin_ticks``, ``thin_particles``, and
    ``thin_nodes`` matching the CLI invocation.
    """
    selected = (scenario_node_ids[0], scenario_node_ids[1])
    thin_nodes_arg = ",".join(selected)

    main_path = tmp_path / "estimates.jsonl"
    sidecar_path = tmp_path / "particles.jsonl"
    summary_path = tmp_path / "pf_summary.json"
    result = _run_pf_cli(
        scenario_path=small_scenario,
        out_path=main_path,
        summary_path=summary_path,
        particles_out_path=sidecar_path,
        n_particles=100,
        thin_ticks=2,
        thin_particles=30,
        thin_nodes=thin_nodes_arg,
    )
    assert result.returncode == 0, (
        f"PF CLI failed (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )

    header = ParticleStreamReader(sidecar_path).header()
    expected_parent_paths = {str(main_path), str(main_path.resolve())}
    assert header.parent_estimate_path in expected_parent_paths, (
        f"header.parent_estimate_path={header.parent_estimate_path!r}; "
        f"expected one of {expected_parent_paths!r}"
    )
    assert header.scenario_seed == SCENARIO_SEED, (
        f"header.scenario_seed={header.scenario_seed}; "
        f"expected {SCENARIO_SEED}"
    )
    assert header.n_particles_full == 100, (
        f"header.n_particles_full={header.n_particles_full}; expected 100"
    )
    assert header.thin_ticks == 2, (
        f"header.thin_ticks={header.thin_ticks}; expected 2"
    )
    assert header.thin_particles == 30, (
        f"header.thin_particles={header.thin_particles}; expected 30"
    )
    assert header.thin_nodes == selected, (
        f"header.thin_nodes={header.thin_nodes!r}; expected {selected!r}"
    )


def test_particles_out_custom_path_writes_sidecar_there(
    small_scenario: Path,
    tmp_path: Path,
) -> None:
    """Task 32.5: ``--particles-out <custom>`` writes the sidecar at
    the specified path.

    A non-default sidecar path is chosen (a file under ``tmp_path``
    with a unique name) and the test asserts the file exists at that
    exact location after the CLI completes.
    """
    main_path = tmp_path / "estimates.jsonl"
    summary_path = tmp_path / "pf_summary.json"
    custom_sidecar = tmp_path / "custom_named_particles_sidecar.jsonl"

    result = _run_pf_cli(
        scenario_path=small_scenario,
        out_path=main_path,
        summary_path=summary_path,
        particles_out_path=custom_sidecar,
    )
    assert result.returncode == 0, (
        f"PF CLI failed (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )
    assert custom_sidecar.exists(), (
        f"--particles-out {custom_sidecar}: sidecar not written at the "
        f"specified path"
    )
    assert custom_sidecar.stat().st_size > 0, (
        f"--particles-out {custom_sidecar}: sidecar file is empty"
    )


# ---------------------------------------------------------------------------
# Section C — Summary report (tasks 30.1-30.5)
# ---------------------------------------------------------------------------


def test_summary_file_written_by_default(
    cli_run_default: dict[str, Path],
) -> None:
    """Task 30.1: Summary file is written alongside the main stream
    (default invocation).

    The fixture passes ``--summary-out`` explicitly so the test knows
    where to look. The file must exist and be valid JSON.
    """
    summary_path = cli_run_default["summary"]
    assert summary_path.exists(), (
        f"Summary file missing: {summary_path}"
    )
    # Must parse as JSON (the spec says "is valid JSON").
    with summary_path.open("r") as f:
        summary = json.load(f)
    assert isinstance(summary, dict), (
        f"Summary file root is not a JSON object: {type(summary).__name__}"
    )


def test_summary_contains_per_class_rmse_aggregates(
    cli_run_default: dict[str, Path],
) -> None:
    """Task 30.2: Summary contains per-class RMSE aggregates (median,
    mean, p95) for ``anchor``, ``ballast_drifter``, ``pure_drifter``.

    Each class entry includes ``median``, ``mean``, ``p95`` numeric
    values, all finite. The summary's exact key for the per-class
    section is not pinned by the spec — the test discovers it by
    looking for any dict-valued key whose entries match the expected
    class names.
    """
    import math

    with cli_run_default["summary"].open("r") as f:
        summary = json.load(f)

    expected_classes = {"anchor", "ballast_drifter", "pure_drifter"}

    # Locate the per-class RMSE block. Accept any top-level key whose
    # value is a dict containing exactly the three class names; if
    # multiple such keys exist, accept the first one found.
    rmse_block: dict | None = None
    rmse_block_key: str | None = None
    for key, value in summary.items():
        if isinstance(value, dict) and expected_classes.issubset(value.keys()):
            rmse_block = value
            rmse_block_key = key
            break
    assert rmse_block is not None, (
        f"Summary has no per-class RMSE block keyed by "
        f"{sorted(expected_classes)}; top-level keys are "
        f"{sorted(summary.keys())}"
    )

    for class_name in expected_classes:
        entry = rmse_block[class_name]
        assert isinstance(entry, dict), (
            f"Summary[{rmse_block_key!r}][{class_name!r}] is not a dict; "
            f"got {type(entry).__name__}"
        )
        for stat in ("median", "mean", "p95"):
            assert stat in entry, (
                f"Summary[{rmse_block_key!r}][{class_name!r}] missing "
                f"'{stat}' key; got keys {sorted(entry.keys())}"
            )
            value = entry[stat]
            assert isinstance(value, (int, float)), (
                f"Summary[{rmse_block_key!r}][{class_name!r}][{stat!r}] "
                f"is not numeric: {value!r} ({type(value).__name__})"
            )
            assert math.isfinite(value), (
                f"Summary[{rmse_block_key!r}][{class_name!r}][{stat!r}] "
                f"is not finite: {value}"
            )


def test_summary_contains_per_node_ess_stats(
    cli_run_default: dict[str, Path],
    scenario_node_ids: tuple[str, ...],
) -> None:
    """Task 30.3: Summary contains per-node ESS stats (mean, min, max).

    Each node has a numeric ``mean``, ``min``, ``max``. The exact
    key for the per-node ESS block is not pinned by the spec — the
    test discovers it by looking for any dict-valued key whose entries
    contain at least one scenario node_id whose value is a dict with
    ``mean``, ``min``, ``max`` keys.
    """
    import math

    with cli_run_default["summary"].open("r") as f:
        summary = json.load(f)

    node_ids_set = set(scenario_node_ids)

    ess_block: dict | None = None
    ess_block_key: str | None = None
    for key, value in summary.items():
        if not isinstance(value, dict):
            continue
        # Accept blocks whose keys overlap with the scenario node_ids.
        if not (set(value.keys()) & node_ids_set):
            continue
        # Sample one entry; if it has mean/min/max keys, accept the block.
        sample_node = next(iter(set(value.keys()) & node_ids_set))
        sample_entry = value[sample_node]
        if (
            isinstance(sample_entry, dict)
            and {"mean", "min", "max"}.issubset(sample_entry.keys())
        ):
            ess_block = value
            ess_block_key = key
            break
    assert ess_block is not None, (
        f"Summary has no per-node ESS stats block (dict keyed by "
        f"node_id with mean/min/max entries); top-level keys are "
        f"{sorted(summary.keys())}"
    )

    # Every node in the scenario must have an entry.
    missing_nodes = node_ids_set - set(ess_block.keys())
    assert not missing_nodes, (
        f"Summary[{ess_block_key!r}] missing ESS stats for nodes: "
        f"{sorted(missing_nodes)}"
    )
    for node_id in scenario_node_ids:
        entry = ess_block[node_id]
        for stat in ("mean", "min", "max"):
            assert stat in entry, (
                f"Summary[{ess_block_key!r}][{node_id!r}] missing "
                f"'{stat}' key; got keys {sorted(entry.keys())}"
            )
            value = entry[stat]
            assert isinstance(value, (int, float)), (
                f"Summary[{ess_block_key!r}][{node_id!r}][{stat!r}] "
                f"is not numeric: {value!r} ({type(value).__name__})"
            )
            assert math.isfinite(value), (
                f"Summary[{ess_block_key!r}][{node_id!r}][{stat!r}] "
                f"is not finite: {value}"
            )


def test_summary_contains_completed_true(
    cli_run_default: dict[str, Path],
) -> None:
    """Task 30.4: Summary contains ``completed: true`` when the run
    completes cleanly.

    The CLI ran to completion (returncode 0 in the fixture); the
    summary's ``completed`` flag must be the JSON literal ``true``.
    """
    with cli_run_default["summary"].open("r") as f:
        summary = json.load(f)
    assert "completed" in summary, (
        f"Summary missing 'completed' key; top-level keys: "
        f"{sorted(summary.keys())}"
    )
    assert summary["completed"] is True, (
        f"Summary['completed']={summary['completed']!r}; "
        f"expected True after a clean run"
    )


def test_summary_values_finite_no_threshold_assertion(
    cli_run_default: dict[str, Path],
) -> None:
    """Task 30.5: Summary numeric values are finite (no NaN, no inf).

    NO threshold assertion against any specific RMSE / ESS bound — the
    summary is a measurement report, not a spec assertion target. This
    test walks every nested numeric value (excluding the ``completed``
    bool) and asserts finiteness.
    """
    import math

    with cli_run_default["summary"].open("r") as f:
        summary = json.load(f)

    def _walk(prefix: str, value: object) -> None:
        if isinstance(value, bool):
            # Booleans are explicitly excluded — ``completed`` is not
            # a numeric value to be range-checked.
            return
        if isinstance(value, (int, float)):
            assert math.isfinite(value), (
                f"Non-finite numeric value at {prefix}: {value}"
            )
            return
        if isinstance(value, dict):
            for k, v in value.items():
                _walk(f"{prefix}.{k}", v)
            return
        if isinstance(value, list):
            for i, v in enumerate(value):
                _walk(f"{prefix}[{i}]", v)
            return
        # Strings and None are fine — the summary may carry e.g. a
        # ``schema_version`` string or null placeholders.

    _walk("summary", summary)


# ---------------------------------------------------------------------------
# Section D — CLI flags & validation (tasks 32.2, 32.3, 26.4, 27.1)
# ---------------------------------------------------------------------------


def test_cli_rejects_unsupported_schema_version(
    small_scenario: Path,
    tmp_path: Path,
) -> None:
    """Task 32.2: CLI rejects scenarios whose header declares an
    unsupported ``schema_version`` — exits nonzero with stderr naming
    the version mismatch.

    Builds a scenario file with header ``schema_version="2.0"`` (not
    in the supported set) by hand-rewriting the first line of a
    valid generator-produced file, then runs the CLI on it.
    """
    # Read the valid scenario, swap the schema_version on the header line.
    with small_scenario.open("r") as f:
        lines = f.readlines()
    header = json.loads(lines[0])
    header["schema_version"] = "2.0"  # unsupported
    lines[0] = json.dumps(header) + "\n"

    bad_scenario = tmp_path / "bad_version.jsonl"
    with bad_scenario.open("w") as f:
        f.writelines(lines)

    # Copy the onboard_map sidecar over so the CLI can find it (the
    # generator places it next to the scenario file under the name
    # declared in the header).
    sidecar_src = small_scenario.parent / header["onboard_map_path"]
    sidecar_dst = bad_scenario.parent / header["onboard_map_path"]
    sidecar_dst.write_bytes(sidecar_src.read_bytes())

    main_path = tmp_path / "estimates.jsonl"
    summary_path = tmp_path / "pf_summary.json"
    result = _run_pf_cli(
        scenario_path=bad_scenario,
        out_path=main_path,
        summary_path=summary_path,
        particles_out_path=tmp_path / "particles.jsonl",
    )
    assert result.returncode != 0, (
        f"CLI accepted unsupported schema_version='2.0' (exit "
        f"{result.returncode}); expected nonzero exit.\n"
        f"stdout:\n{result.stdout.decode(errors='replace')}\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )
    stderr_text = result.stderr.decode(errors="replace").lower()
    assert "schema_version" in stderr_text or "version" in stderr_text, (
        f"CLI rejected the bad scenario but stderr does not name the "
        f"version mismatch.\nstderr:\n{stderr_text}"
    )


def test_cli_rejects_focus_nodes_flag(
    small_scenario: Path,
    scenario_node_ids: tuple[str, ...],
    tmp_path: Path,
) -> None:
    """Task 32.3: Legacy ``--focus-nodes`` flag is not accepted — CLI
    exits nonzero with an "unrecognized argument" error naming
    ``--focus-nodes``.

    The privileged-subset ``--focus-nodes`` flag from earlier drafts
    has been replaced by the orthogonal ``--thin-nodes`` thinning
    knob; the legacy spelling must be rejected.
    """
    main_path = tmp_path / "estimates.jsonl"
    summary_path = tmp_path / "pf_summary.json"
    sidecar_path = tmp_path / "particles.jsonl"
    bogus_arg = ",".join(scenario_node_ids[:2])
    result = _run_pf_cli(
        scenario_path=small_scenario,
        out_path=main_path,
        summary_path=summary_path,
        particles_out_path=sidecar_path,
        extra_args=["--focus-nodes", bogus_arg],
    )
    assert result.returncode != 0, (
        f"CLI accepted legacy --focus-nodes flag (exit "
        f"{result.returncode}); expected nonzero exit."
    )
    stderr_text = result.stderr.decode(errors="replace").lower()
    assert "unrecognized" in stderr_text or "focus-nodes" in stderr_text, (
        f"CLI rejected --focus-nodes but stderr does not name the flag "
        f"or describe it as 'unrecognized'.\nstderr:\n{stderr_text}"
    )


def test_run_pf_float_imports_scenario_truth_reader() -> None:
    """Task 26.4: ``run_pf_float.py`` is allowed (and required) to
    import ``ScenarioTruthReader`` for the summary's RMSE computation.

    Two-part assertion:

    1. AST walk of ``run_pf_float.py`` finds at least one
       ``ImportFrom`` referencing ``scenario_truth_schema`` AND
       importing the ``ScenarioTruthReader`` symbol (the import is
       present for summary computation per spec "PF Summary
       Measurement Report").
    2. ``uv run lint-imports`` exits zero — ``run_pf_float`` is
       intentionally exempt from the PF-library-truth-separation
       contract, so the truth import does not violate the linter.

    The complementary "no truth flows into PFFloat methods" check is
    enforced on the ``PFFloat`` side by 26.3 (signature-level type
    rejection); this test does not duplicate that check.
    """
    run_pf_float_path = (
        PROJECT_ROOT / "rtl" / "vectors" / "maritime" / "run_pf_float.py"
    )
    source = run_pf_float_path.read_text()
    tree = ast.parse(source)

    found_truth_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "scenario_truth_schema" in module:
                imported = [alias.name for alias in node.names]
                if "ScenarioTruthReader" in imported:
                    found_truth_import = True
                    break

    assert found_truth_import, (
        f"run_pf_float.py does not import ScenarioTruthReader from "
        f"scenario_truth_schema. The summary-report RMSE computation "
        f"requires reading truth (see spec 'PF Summary Measurement "
        f"Report'); without this import the summary cannot consume "
        f"truth and the 'Reported RMSE actually consumes truth' "
        f"scenario cannot pass."
    )

    # The import must not violate the import-linter contract — run_pf_float
    # is deliberately exempt from the PF-library-truth-separation contract.
    lint_result = subprocess.run(
        ["uv", "run", "lint-imports"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
    )
    assert lint_result.returncode == 0, (
        f"`uv run lint-imports` exited {lint_result.returncode}; "
        f"expected 0. The truth import in run_pf_float.py must not "
        f"trip the PF-library-truth-separation contract.\n"
        f"stdout:\n{lint_result.stdout.decode(errors='replace')}\n"
        f"stderr:\n{lint_result.stderr.decode(errors='replace')}"
    )


def test_pf_uses_sidecar_onboard_map_for_bathy_likelihood(
    small_scenario: Path,
) -> None:
    """Task 27.1: The PF receives its onboard map from
    ``ScenarioReader.onboard_map()`` and uses that map (not a
    PF-internal reconstruction, not the truth map).

    Identity check: construct a ``PFFloat`` with the reader-provided
    onboard map and assert ``pf._onboard_map is onboard_map``. This
    proves the constructor stored the passed-in instance unmodified —
    if the implementer secretly rebuilt the map (e.g., via
    ``make_onboard_map``), the identity check would fail. The bathy
    likelihood downstream then necessarily uses this stored map (the
    PFFloat code reads ``self._onboard_map.depth_at(...)`` — see
    ``_bathy_log_likelihood``).

    This is the simplest assertion that verifies the binding without
    depending on the bathy likelihood's exact numerical surface or
    requiring a parallel truth-map vs. onboard-map differential probe.
    """
    import numpy as np

    from rtl.vectors.maritime.pf_float import PFFloat, PFFloatConfig
    from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

    reader = ScenarioReader(small_scenario)
    onboard_map = reader.onboard_map()
    header = reader.header()

    state_dim = ANCHOR_LAYOUT.state_dim
    initial_mean = np.zeros(state_dim)
    initial_cov_diag = np.ones(state_dim)

    pf = PFFloat(
        node_id="probe_node",
        layout=ANCHOR_LAYOUT,
        initial_state_mean=initial_mean,
        initial_state_cov_diag=initial_cov_diag,
        onboard_map=onboard_map,
        anchor_positions=header.anchor_positions,
        enu_origin_lat_deg=header.bbox[0],
        enu_origin_lon_deg=header.bbox[1],
        config=PFFloatConfig(n_particles=10),
        rng=np.random.default_rng(0),
    )

    # Identity assertion: the PF stores the passed-in onboard map
    # without copying or rebuilding. This is the binding verified
    # for the bathy likelihood — the handler reads from
    # ``self._onboard_map`` (see PFFloat._bathy_log_likelihood), so
    # whatever map flows into the constructor flows into the
    # likelihood unchanged.
    assert pf._onboard_map is onboard_map, (
        "PFFloat did not store the passed-in onboard map by reference. "
        "The bathy likelihood must query the reader-provided onboard "
        "map (loaded via ScenarioReader.onboard_map()), not a "
        "PF-internal reconstruction. See spec 'Onboard Map From "
        "Scenario Reader' / design D12."
    )


def test_build_pf_config_applies_noise_overrides():
    """``_build_pf_config`` plumbs each predict-noise flag into the
    matching ``PFFloatConfig`` field; ``None`` means "use the bundled
    default."
    """
    import argparse

    from rtl.vectors.maritime.pf_float import PFFloatConfig
    from rtl.vectors.maritime.run_pf_float import _build_pf_config

    args = argparse.Namespace(
        n_particles=500,
        predict_noise_pos=0.1,
        predict_noise_vel=0.0,
        predict_noise_heading=0.5,
        predict_noise_current=0.02,
    )
    cfg = _build_pf_config(args)
    assert cfg.n_particles == 500
    assert cfg.process_noise_pos_m_per_sqrt_s == 0.1
    assert cfg.process_noise_vel_ms_per_sqrt_s == 0.0
    assert cfg.process_noise_heading_deg_per_sqrt_s == 0.5
    assert cfg.process_noise_current_ms_per_sqrt_s == 0.02


def test_build_pf_config_defaults_when_overrides_none():
    """All four ``predict_noise_*`` = ``None`` → the returned config has
    the bundled ``PFFloatConfig`` defaults for the four noise fields.
    """
    import argparse

    from rtl.vectors.maritime.pf_float import PFFloatConfig
    from rtl.vectors.maritime.run_pf_float import _build_pf_config

    args = argparse.Namespace(
        n_particles=500,
        predict_noise_pos=None,
        predict_noise_vel=None,
        predict_noise_heading=None,
        predict_noise_current=None,
    )
    cfg = _build_pf_config(args)
    defaults = PFFloatConfig(n_particles=500)
    assert cfg.process_noise_pos_m_per_sqrt_s == defaults.process_noise_pos_m_per_sqrt_s
    assert cfg.process_noise_vel_ms_per_sqrt_s == defaults.process_noise_vel_ms_per_sqrt_s
    assert cfg.process_noise_heading_deg_per_sqrt_s == defaults.process_noise_heading_deg_per_sqrt_s
    assert cfg.process_noise_current_ms_per_sqrt_s == defaults.process_noise_current_ms_per_sqrt_s
