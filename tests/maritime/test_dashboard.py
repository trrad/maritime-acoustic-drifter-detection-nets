"""Contract tests for the ``maritime-dashboard`` OpenSpec change.

These tests define "done" for the dashboard CLI at
``experiments/12_maritime_dashboard.py``. The implementer makes every
assertion here pass without modifying this file — this file is the
observable-behavior contract.

Design notes:

- Tests launch the dashboard as a subprocess via
  ``python experiments/12_maritime_dashboard.py ...``. The subprocess
  prints a URL (e.g. ``http://localhost:<port>``) on startup; tests
  parse stdout line-by-line to discover the chosen port (``--port 0``
  is used so test runs don't collide on a fixed port).

- A single session-scoped fixture builds the scenario + PF estimate +
  particle sidecar via the real ``gen_maritime_scenario`` and
  ``run_pf_float`` CLIs (10 nodes, 36 ticks at 1 Hz, all nodes in
  the sidecar). A separate function-scoped fixture re-runs
  ``run_pf_float`` with ``--thin-nodes`` to produce a "2-node sidecar"
  variant needed by the drill-down gating scenario.

- Assertions target observable behavior: HTTP response codes / bodies,
  the inlined JSON blob's content (parsed via ``json.loads`` out of
  the ``<script type="application/json" id="scenarioData">`` tag), and
  the truth / estimate values that the dashboard claims to show.
  Substance-level tests compare the inlined trails against what the
  typed readers (``ScenarioTruthReader``, ``PFEstimateReader``) yield
  from the same files, ruling out stubbed / zeroed implementations.

- The implementer contract on the inlined blob: it is a JSON object
  whose top-level keys include ``header``, ``truth_ticks``,
  ``pf_estimates``, and ``coastline``. When a particle sidecar is
  loaded, a ``particles`` key is also present. Nested shapes inside
  each top-level key are at the implementer's discretion as long as
  the substance tests pass. The ``data-particle-node="<node_id>"``
  HTML attribute is a test-design contract — the implementer MUST use
  that attribute on drill-down toggle elements so the gating tests
  (``test_drill_down_*``) can verify behavior deterministically.
"""

from __future__ import annotations

import ast
import json
import math
import re
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

from rtl.vectors.maritime.pf_estimates_schema import (
    PFEstimateReader,
    ParticleStreamReader,
)
from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader


# ---------------------------------------------------------------------------
# Constants and paths
# ---------------------------------------------------------------------------


REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DASHBOARD_SCRIPT: Path = REPO_ROOT / "experiments" / "12_maritime_dashboard.py"

# Pinned scenario parameters — chosen so:
# - 0.01 hours = 36 seconds → 36 ticks at dt=1.0s (tick indices 0..35)
# - 10 nodes (M1 fleet size is fixed)
# - bbox intersects the bundled BC / Strait of Georgia coastline
FIXED_SEED: int = 7
FIXED_BBOX: str = "48.6,-123.5,48.603,-123.497"
FIXED_DURATION_HOURS: float = 0.01
FIXED_DT_SEC: float = 1.0
FIXED_NODE_COUNT: int = 10
EXPECTED_TICK_COUNT: int = 36
FIXED_CREATED_AT: str = "2026-04-23T00:00:00+00:00"

PF_N_PARTICLES: int = 100
PF_THIN_TICKS: int = 2
PF_THIN_PARTICLES: int = 25

URL_REGEX = re.compile(r"http://(?:localhost|127\.0\.0\.1):(\d+)")
SCENARIO_DATA_REGEX = re.compile(
    r'<script[^>]*type="application/json"[^>]*id="scenarioData"[^>]*>(.*?)</script>',
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _gen_full_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Generate scenario + main estimate + particle sidecar (all nodes)."""
    scn = tmp_path / "scn.jsonl"
    est = tmp_path / "est.jsonl"
    part = tmp_path / "part.jsonl"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl.vectors.maritime.gen_maritime_scenario",
            "--seed",
            str(FIXED_SEED),
            "--bbox",
            FIXED_BBOX,
            "--duration-hours",
            str(FIXED_DURATION_HOURS),
            "--dt-sec",
            str(FIXED_DT_SEC),
            "--nodes",
            str(FIXED_NODE_COUNT),
            "--out",
            str(scn),
            "--created-at",
            FIXED_CREATED_AT,
        ],
        check=True,
        cwd=str(REPO_ROOT),
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl.vectors.maritime.run_pf_float",
            "--scenario",
            str(scn),
            "--out",
            str(est),
            "--particles-out",
            str(part),
            "--n-particles",
            str(PF_N_PARTICLES),
            "--thin-ticks",
            str(PF_THIN_TICKS),
            "--thin-particles",
            str(PF_THIN_PARTICLES),
        ],
        check=True,
        cwd=str(REPO_ROOT),
    )
    return scn, est, part


@pytest.fixture(scope="session")
def full_fixture(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """Session-scoped: scenario + PF main + particle sidecar for all 10 nodes."""
    tmp = tmp_path_factory.mktemp("dash-fixture")
    return _gen_full_fixture(tmp)


@pytest.fixture(scope="function")
def two_node_sidecar_fixture(
    tmp_path: Path, full_fixture: tuple[Path, Path, Path]
) -> tuple[Path, Path, Path, tuple[str, str]]:
    """Re-run the PF with ``--thin-nodes`` restricting the sidecar to 2 nodes.

    Returns ``(scenario_path, estimates_path, particles_path,
    (node_a, node_b))`` — the two node IDs that appear in the sidecar.
    Reuses the session-level scenario JSONL; writes fresh estimate +
    particle files alongside it so the thin-nodes variant is isolated.
    """
    scn_src, _, _ = full_fixture
    # Copy is unnecessary — run_pf_float only reads the scenario.
    scn = scn_src

    # Pick two node_ids from the scenario header deterministically.
    reader = ScenarioTruthReader(scn)
    header = reader.header()
    node_ids = list(header.node_ids)
    assert len(node_ids) >= 2, "fixture scenario must have >=2 nodes"
    node_a, node_b = node_ids[0], node_ids[1]

    est = tmp_path / "est_thinned.jsonl"
    part = tmp_path / "part_thinned.jsonl"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl.vectors.maritime.run_pf_float",
            "--scenario",
            str(scn),
            "--out",
            str(est),
            "--particles-out",
            str(part),
            "--n-particles",
            str(PF_N_PARTICLES),
            "--thin-ticks",
            str(PF_THIN_TICKS),
            "--thin-particles",
            str(PF_THIN_PARTICLES),
            "--thin-nodes",
            f"{node_a},{node_b}",
        ],
        check=True,
        cwd=str(REPO_ROOT),
    )
    return scn, est, part, (node_a, node_b)


# ---------------------------------------------------------------------------
# Dashboard launcher helper
# ---------------------------------------------------------------------------


@contextmanager
def launch_dashboard(
    *,
    scenario: Path | str,
    estimates: Path | str,
    particles: Path | str | None = None,
    extra_args: tuple[str, ...] = (),
):
    """Launch the dashboard CLI on --port 0 and yield (proc, url).

    Reads stdout line-by-line until a URL line like
    ``http://localhost:<port>`` is seen, then yields control. The
    subprocess is terminated on context-manager exit.

    If the subprocess exits before printing a URL, an AssertionError
    carrying the subprocess's stderr tail is raised — makes "dashboard
    failed to boot" failures obvious instead of hanging.
    """
    args = [
        sys.executable,
        str(DASHBOARD_SCRIPT),
        "--scenario",
        str(scenario),
        "--estimates",
        str(estimates),
        "--port",
        "0",
        "--no-open",
    ]
    if particles is not None:
        args.extend(["--particles", str(particles)])
    args.extend(extra_args)

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
    )

    url: str | None = None
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            if proc.poll() is not None:
                break
            continue
        m = URL_REGEX.search(line)
        if m:
            url = m.group(0)
            break

    try:
        if url is None:
            stderr_tail = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(
                f"dashboard did not print URL (exit={proc.poll()}); stderr:\n{stderr_tail}"
            )
        yield proc, url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def _fetch_body(url: str) -> tuple[int, str, str]:
    """GET ``url``, return (status, content_type, body)."""
    with urllib.request.urlopen(url, timeout=5) as resp:
        status = resp.status
        content_type = resp.headers.get("Content-Type", "")
        body = resp.read().decode("utf-8")
    return status, content_type, body


def _parse_inlined_json(body: str) -> dict:
    """Extract and parse the ``#scenarioData`` JSON blob from the HTML body."""
    m = SCENARIO_DATA_REGEX.search(body)
    assert m is not None, (
        "did not find <script type=\"application/json\" id=\"scenarioData\"> tag "
        "in dashboard HTML"
    )
    return json.loads(m.group(1))


def _close(a: float, b: float, atol: float = 1e-9) -> bool:
    return math.isclose(a, b, abs_tol=atol, rel_tol=0.0)


# ---------------------------------------------------------------------------
# Section 1 — CLI contract
# ---------------------------------------------------------------------------


def test_cli_serves_html_on_chosen_port(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 1.1: GET / returns 200 with text/html on the port-0-chosen port."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        status, content_type, _ = _fetch_body(url)
    assert status == 200
    assert content_type.startswith("text/html"), (
        f"Content-Type should start with 'text/html', got {content_type!r}"
    )


def test_cli_prints_localhost_url_to_stdout(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 1.2: stdout contains a URL with localhost/127.0.0.1 + chosen port."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        assert URL_REGEX.search(url) is not None
        assert ("localhost" in url) or ("127.0.0.1" in url), (
            f"URL must contain 'localhost' or '127.0.0.1', got {url!r}"
        )


def test_cli_exits_nonzero_on_missing_scenario(tmp_path: Path) -> None:
    """Task 1.3: missing --scenario path exits non-zero, stderr names the path."""
    bogus = tmp_path / "nope" / "missing.jsonl"  # does not exist
    # Also need a valid-looking estimates path; use a path that doesn't exist —
    # the scenario check should fire first (or both do; either way non-zero
    # exit and the scenario substring must be reported).
    fake_est = tmp_path / "also_missing.jsonl"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(DASHBOARD_SCRIPT),
            "--scenario",
            str(bogus),
            "--estimates",
            str(fake_est),
            "--port",
            "0",
            "--no-open",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
    )
    try:
        _, stderr = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, stderr = proc.communicate()
        raise AssertionError("CLI hung on missing scenario instead of exiting")
    assert proc.returncode != 0, (
        f"expected non-zero exit on missing scenario; got {proc.returncode} "
        f"stderr:\n{stderr}"
    )
    assert str(bogus) in stderr or "missing.jsonl" in stderr, (
        f"stderr should name the missing scenario path; got:\n{stderr}"
    )


def test_cli_exits_nonzero_on_missing_estimates(
    full_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """Task 1.4: missing --estimates path exits non-zero, stderr names the path."""
    scn, _, _ = full_fixture
    bogus = tmp_path / "nope" / "missing_est.jsonl"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(DASHBOARD_SCRIPT),
            "--scenario",
            str(scn),
            "--estimates",
            str(bogus),
            "--port",
            "0",
            "--no-open",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
    )
    try:
        _, stderr = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, stderr = proc.communicate()
        raise AssertionError("CLI hung on missing estimates instead of exiting")
    assert proc.returncode != 0, (
        f"expected non-zero exit on missing estimates; got {proc.returncode} "
        f"stderr:\n{stderr}"
    )
    assert str(bogus) in stderr or "missing_est.jsonl" in stderr, (
        f"stderr should name the missing estimates path; got:\n{stderr}"
    )


def test_cli_loads_particle_sidecar_when_provided(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 1.5: --particles sidecar → every sidecar node_id appears in blob."""
    scn, est, part = full_fixture
    sidecar_node_ids = ParticleStreamReader(part).node_ids_present()
    assert len(sidecar_node_ids) > 0, "sidecar must contain at least one node"

    with launch_dashboard(scenario=scn, estimates=est, particles=part) as (_, url):
        _, _, body = _fetch_body(url)
    parsed = _parse_inlined_json(body)

    assert "particles" in parsed, (
        "inlined JSON must expose a 'particles' key when a sidecar is loaded"
    )
    particles_blob = parsed["particles"]
    # Implementer may pick any nested shape; check each node_id appears somewhere
    # inside the particles substructure (as a dict key or a string inside a
    # nested record). Serializing the particles_blob to JSON and substring-
    # matching is an implementer-pattern-agnostic check.
    particles_text = json.dumps(particles_blob)
    for node_id in sidecar_node_ids:
        assert node_id in particles_text, (
            f"node_id {node_id!r} is in sidecar node_ids_present() "
            f"but not in the inlined particles substructure"
        )


def test_cli_warns_on_missing_particles_but_still_serves(
    full_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """Task 1.6: missing --particles path warns on stderr + still serves 200."""
    scn, est, _ = full_fixture
    bogus = tmp_path / "definitely_not_here.jsonl"  # does NOT exist

    with launch_dashboard(scenario=scn, estimates=est, particles=bogus) as (proc, url):
        status, content_type, body = _fetch_body(url)

        # Drain some stderr so we can inspect the warning. The subprocess
        # is still alive (serving); read without blocking by using a
        # short communicate after sending a terminate signal below.
    # After exit, read remaining stderr (context manager terminated the proc).
    stderr_tail = proc.stderr.read() if proc.stderr else ""

    assert status == 200
    assert content_type.startswith("text/html")

    lower_stderr = stderr_tail.lower()
    assert "particles" in lower_stderr, (
        f"stderr should warn about missing particles; got:\n{stderr_tail}"
    )
    assert ("not found" in lower_stderr) or ("missing" in lower_stderr), (
        f"stderr should say 'not found' or 'missing' for particles; got:\n{stderr_tail}"
    )

    # No per-node particle records in the inlined JSON.
    parsed = _parse_inlined_json(body)
    if "particles" in parsed:
        particles_blob = parsed["particles"]
        assert particles_blob in (None, {}, []), (
            f"particles key should be null/empty when sidecar is missing, "
            f"got {type(particles_blob).__name__} with "
            f"{len(particles_blob) if hasattr(particles_blob, '__len__') else '?'} entries"
        )


# ---------------------------------------------------------------------------
# Section 2 — HTML inlining (substance, not shape)
# ---------------------------------------------------------------------------


def test_body_contains_schema_version_string(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 2.1: raw body contains '"schema_version": "1.0"' (from scenario header)."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    assert '"schema_version": "1.0"' in body, (
        "body must contain the scenario header's schema_version literal"
    )


def test_body_contains_every_node_id_from_scenario(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 2.2: every scenario node_id appears in the response body."""
    scn, est, _ = full_fixture
    header = ScenarioTruthReader(scn).header()
    assert len(header.node_ids) == FIXED_NODE_COUNT

    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    for node_id in header.node_ids:
        assert node_id in body, (
            f"scenario node_id {node_id!r} missing from dashboard body"
        )


def test_inlined_json_has_N_tick_entries_with_correct_spacing(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 2.3: truth_ticks has N entries and t_sec == t * dt_sec (exact)."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    parsed = _parse_inlined_json(body)
    header = ScenarioTruthReader(scn).header()

    truth_ticks = parsed["truth_ticks"]
    assert isinstance(truth_ticks, list), "truth_ticks must be a list"
    assert len(truth_ticks) == EXPECTED_TICK_COUNT, (
        f"expected {EXPECTED_TICK_COUNT} truth_ticks entries, got {len(truth_ticks)}"
    )

    for t, entry in enumerate(truth_ticks):
        assert isinstance(entry, dict), f"truth_ticks[{t}] must be a dict"
        assert "t_sec" in entry, f"truth_ticks[{t}] missing 't_sec'"
        expected = t * header.dt_sec
        assert entry["t_sec"] == expected, (
            f"truth_ticks[{t}].t_sec == {entry['t_sec']!r}, expected {expected!r} "
            f"(t * dt_sec = {t} * {header.dt_sec})"
        )


def test_inlined_truth_trail_matches_scenario_truth(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 2.4 (substance): inlined truth trails equal scenario truth positions.

    Rules out zeroed / constant / placeholder trail implementations:
    - Each (node_id, t) entry in the inlined blob matches the scenario
      truth state's position slice (slice(0, 3) = east_m, north_m, depth_m).
    - Two nodes with differing truth positions produce differing trail
      entries at the same tick.
    """
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    parsed = _parse_inlined_json(body)
    truth_ticks = parsed["truth_ticks"]

    # Collect scenario truth and perform per-(node,t) comparisons.
    scenario_truth: dict[int, dict[str, tuple[float, float, float]]] = {}
    for view in ScenarioTruthReader(scn):
        per_node: dict[str, tuple[float, float, float]] = {}
        for node_id, state in view.node_truth.items():
            per_node[node_id] = (float(state[0]), float(state[1]), float(state[2]))
        scenario_truth[view.t] = per_node

    # Sanity: exactly EXPECTED_TICK_COUNT ticks.
    assert len(scenario_truth) == EXPECTED_TICK_COUNT

    # Walk each tick, each node, and assert the inlined entry matches
    # the scenario within 1e-9. The implementer's JSON shape for
    # "inlined entry for (node_id, t)" is up to them; we search
    # truth_ticks[t] for the three floats in order.
    mismatches: list[str] = []
    for t in range(EXPECTED_TICK_COUNT):
        tick_entry = truth_ticks[t]
        entry_text = json.dumps(tick_entry)
        for node_id, (ex, ny, dp) in scenario_truth[t].items():
            # The inlined entry for this node must contain these three
            # floats. Match as JSON-encoded substrings (python's json
            # encoder uses the same formatting as javascript, so
            # comparing as a JSON-serialized triple within the tick
            # entry's JSON text is robust).
            # We require the three values to appear within 1e-9 of some
            # triple present in the tick entry. Check via the parsed
            # substructure.
            found = _find_triple_for_node(tick_entry, node_id, ex, ny, dp)
            if not found:
                mismatches.append(
                    f"t={t} node={node_id} expected pos=({ex:.6f},{ny:.6f},{dp:.6f}) "
                    f"not found within 1e-9 in truth_ticks[{t}]"
                )
    assert not mismatches, (
        "inlined truth trail diverges from scenario truth:\n"
        + "\n".join(mismatches[:10])
        + (f"\n... ({len(mismatches)} total)" if len(mismatches) > 10 else "")
    )

    # Non-degeneracy check: find two nodes whose positions differ at
    # the same tick by > 1 m somewhere in the scenario, and confirm
    # their inlined trail entries differ at that tick.
    differ_found = False
    node_ids = list(scenario_truth[0].keys())
    for t in range(EXPECTED_TICK_COUNT):
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                a = scenario_truth[t][node_ids[i]]
                b = scenario_truth[t][node_ids[j]]
                dist = math.hypot(a[0] - b[0], a[1] - b[1])
                if dist > 1.0:
                    # Their inlined entries must encode different positions.
                    tick_entry = truth_ticks[t]
                    a_found = _find_triple_for_node(tick_entry, node_ids[i], *a)
                    b_found = _find_triple_for_node(tick_entry, node_ids[j], *b)
                    assert a_found and b_found
                    differ_found = True
                    break
            if differ_found:
                break
        if differ_found:
            break
    assert differ_found, (
        "scenario has no two nodes >1 m apart at any tick — fixture is too "
        "degenerate to rule out a zeroed-trail implementation"
    )


def _find_triple_for_node(
    tick_entry: dict,
    node_id: str,
    ex: float,
    ny: float,
    dp: float,
    atol: float = 1e-9,
) -> bool:
    """Recursively scan ``tick_entry`` for a place where ``node_id`` maps to
    a 3-tuple/list of floats close to (ex, ny, dp)."""
    # Common shape 1: tick_entry["nodes"][node_id] == [ex, ny, dp, ...]
    # Common shape 2: tick_entry["truth"][node_id] == [ex, ny, dp]
    # Common shape 3: tick_entry[node_id] == [ex, ny, dp]
    # Common shape 4: tick_entry has a per-node list
    # We walk everything and look for a value associated with node_id.

    def _triple_matches(v) -> bool:
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            try:
                return (
                    _close(float(v[0]), ex, atol)
                    and _close(float(v[1]), ny, atol)
                    and _close(float(v[2]), dp, atol)
                )
            except (TypeError, ValueError):
                return False
        if isinstance(v, dict):
            # e.g. {"pos": [ex, ny, dp]} or {"east_m": ex, "north_m": ny, "depth_m": dp}
            if all(k in v for k in ("east_m", "north_m", "depth_m")):
                try:
                    return (
                        _close(float(v["east_m"]), ex, atol)
                        and _close(float(v["north_m"]), ny, atol)
                        and _close(float(v["depth_m"]), dp, atol)
                    )
                except (TypeError, ValueError):
                    return False
            for sub in v.values():
                if _triple_matches(sub):
                    return True
        return False

    # Direct lookup forms first.
    def _scan(obj) -> bool:
        if isinstance(obj, dict):
            if node_id in obj and _triple_matches(obj[node_id]):
                return True
            for v in obj.values():
                if _scan(v):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                # Records-of-dicts style: {"node_id": ..., "pos": [...]}.
                if isinstance(item, dict) and item.get("node_id") == node_id:
                    if _triple_matches(item):
                        return True
                    # Maybe the triple is nested inside the record.
                    for v in item.values():
                        if v is node_id or v == node_id:
                            continue
                        if _triple_matches(v):
                            return True
                if _scan(item):
                    return True
        return False

    return _scan(tick_entry)


def test_inlined_estimate_trail_matches_pf_mean(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 2.5 (substance): each inlined estimate trail entry == PFEstimate.mean[0:3]."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    parsed = _parse_inlined_json(body)

    assert "pf_estimates" in parsed, "inlined blob must expose 'pf_estimates' key"
    pf_blob = parsed["pf_estimates"]

    mismatches: list[str] = []
    any_compared = False
    for rec in PFEstimateReader(est):
        ex, ny, dp = float(rec.mean[0]), float(rec.mean[1]), float(rec.mean[2])
        found = _scan_pf_blob_for_estimate(pf_blob, rec.node_id, rec.t, ex, ny, dp)
        any_compared = True
        if not found:
            mismatches.append(
                f"t={rec.t} node={rec.node_id} mean[0:3]=({ex:.6f},{ny:.6f},{dp:.6f}) "
                f"not found in inlined pf_estimates blob"
            )
    assert any_compared, "PF estimate stream is empty"
    assert not mismatches, (
        "inlined estimate trail diverges from PFEstimateReader:\n"
        + "\n".join(mismatches[:10])
        + (f"\n... ({len(mismatches)} total)" if len(mismatches) > 10 else "")
    )


def _scan_pf_blob_for_estimate(
    blob,
    node_id: str,
    t: int,
    ex: float,
    ny: float,
    dp: float,
    atol: float = 1e-9,
) -> bool:
    """Walk any nested pf_estimates shape for a (node_id, t) entry whose
    first three mean-slice floats match (ex, ny, dp) within atol."""

    def _triple_close(v) -> bool:
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            try:
                return (
                    _close(float(v[0]), ex, atol)
                    and _close(float(v[1]), ny, atol)
                    and _close(float(v[2]), dp, atol)
                )
            except (TypeError, ValueError):
                return False
        return False

    def _scan(obj, seen_node: bool, seen_tick: bool) -> bool:
        if isinstance(obj, dict):
            # Record-style: {"node_id":..., "t":..., "mean":[...]}
            if (
                obj.get("node_id") == node_id
                and obj.get("t") == t
                and _triple_close(obj.get("mean"))
            ):
                return True
            # Nested dict indexed by node_id then tick (or vice versa).
            for k, v in obj.items():
                n2 = seen_node or (k == node_id)
                t2 = seen_tick or (k == t) or (k == str(t))
                if n2 and t2 and _triple_close(v):
                    return True
                if _scan(v, n2, t2):
                    return True
        elif isinstance(obj, list):
            # Position-indexed per-tick: the value at index t for node node_id.
            if seen_node and not seen_tick and 0 <= t < len(obj):
                if _triple_close(obj[t]):
                    return True
                if _scan(obj[t], seen_node, True):
                    return True
            for item in obj:
                if _scan(item, seen_node, seen_tick):
                    return True
        return False

    return _scan(blob, False, False)


def test_inlined_blob_structure(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 2.6: top-level keys include header/truth_ticks/pf_estimates/coastline."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    parsed = _parse_inlined_json(body)

    required_keys = {"header", "truth_ticks", "pf_estimates", "coastline"}
    missing = required_keys - set(parsed.keys())
    assert not missing, (
        f"inlined JSON top-level missing keys: {sorted(missing)}; "
        f"got keys: {sorted(parsed.keys())}"
    )

    header_obj = parsed["header"]
    assert header_obj["schema_version"] == "1.0"
    assert "node_ids" in header_obj
    assert len(header_obj["node_ids"]) == FIXED_NODE_COUNT


# ---------------------------------------------------------------------------
# Section 3 — Truth reader usage
# ---------------------------------------------------------------------------


def test_dashboard_source_imports_scenariotruthreader_from_truth_module() -> None:
    """Task 3.1: module imports ScenarioTruthReader from scenario_truth_schema."""
    assert DASHBOARD_SCRIPT.exists(), (
        f"{DASHBOARD_SCRIPT} missing — implementer must create this file"
    )
    src = DASHBOARD_SCRIPT.read_text(encoding="utf-8")
    # Correct import must be present.
    assert "from rtl.vectors.maritime.scenario_truth_schema import" in src, (
        "dashboard must import from rtl.vectors.maritime.scenario_truth_schema "
        "(the dedicated truth module)"
    )
    # And ScenarioTruthReader must be referenced near that import block.
    # Walk the AST to find an explicit `from scenario_truth_schema import`
    # statement that names ScenarioTruthReader.
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (
                node.module == "rtl.vectors.maritime.scenario_truth_schema"
                and any(alias.name == "ScenarioTruthReader" for alias in node.names)
            ):
                found = True
                break
    assert found, (
        "no `from rtl.vectors.maritime.scenario_truth_schema import "
        "ScenarioTruthReader` statement found in dashboard module"
    )
    # Negative: the wrong-module form must NOT appear.
    assert "from rtl.vectors.maritime.scenario_schema import ScenarioTruthReader" not in src, (
        "dashboard imports ScenarioTruthReader from the wrong module "
        "(scenario_schema instead of scenario_truth_schema)"
    )


def test_module_docstring_documents_charter_allowance() -> None:
    """Task 3.2: module docstring documents the truth import as charter allowance."""
    assert DASHBOARD_SCRIPT.exists(), (
        f"{DASHBOARD_SCRIPT} missing — implementer must create this file"
    )
    src = DASHBOARD_SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    docstring = ast.get_docstring(tree)
    assert docstring, "dashboard module must have a non-empty docstring"
    lowered = docstring.lower()
    assert ("charter" in lowered) or ("allowance" in lowered), (
        "dashboard module docstring must document the import as an explicit "
        f"charter allowance; got docstring:\n{docstring}"
    )


@pytest.mark.slow
def test_lint_imports_passes() -> None:
    """Task 3.3: `uv run lint-imports` exits zero — dashboard is not in any
    PF-truth-separation contract's source_modules."""
    proc = subprocess.run(
        ["uv", "run", "lint-imports"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"lint-imports exited {proc.returncode}:\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# Section 4 — No external JS/CSS deps
# ---------------------------------------------------------------------------


_EXTERNAL_SCRIPT_SRC = re.compile(
    r"<script[^>]*\bsrc\s*=\s*[\"']https?://", re.IGNORECASE
)
_EXTERNAL_LINK_HREF = re.compile(
    r"<link[^>]*\bhref\s*=\s*[\"']https?://", re.IGNORECASE
)


def test_no_external_script_src_urls(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 4.1: body has no <script src="http(s)://..."> external refs."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    m = _EXTERNAL_SCRIPT_SRC.search(body)
    assert m is None, (
        f"dashboard body contains an external <script src=...> URL: "
        f"{m.group(0)!r}"
    )


def test_no_external_link_href_urls(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 4.2: body has no <link href="http(s)://..."> external refs."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    m = _EXTERNAL_LINK_HREF.search(body)
    assert m is None, (
        f"dashboard body contains an external <link href=...> URL: "
        f"{m.group(0)!r}"
    )


# ---------------------------------------------------------------------------
# Section 5 — Rendering code structure (JS string inspection)
# ---------------------------------------------------------------------------


def test_js_renders_coastline_polygons(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 5.1: body contains Canvas primitives used by coastline rendering."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    for token in ("beginPath", "closePath", "fill"):
        assert token in body, (
            f"expected Canvas primitive {token!r} in dashboard JS "
            "(coastline polygons use beginPath/closePath/fill)"
        )


def test_js_branches_on_three_node_classes(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 5.2: body mentions all three class-name strings for icon dispatch."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    for class_name in ("anchor", "ballast_drifter", "pure_drifter"):
        assert class_name in body, (
            f"expected class-name {class_name!r} in dashboard body "
            "(icon-rendering dispatch must branch on each node class)"
        )


def test_js_branches_on_three_lora_statuses(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 5.3: body mentions all three LoRa status strings."""
    scn, est, _ = full_fixture
    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)
    for status in ("success", "dropped", "out_of_range"):
        assert status in body, (
            f"expected LoRa status {status!r} in dashboard body "
            "(link-rendering dispatch must branch on each status)"
        )


def test_drill_down_populated_from_sidecar_node_ids_present(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 5.4 (substance): particles substructure covers exactly the sidecar
    node_ids_present() — no privileged 'focus_node_ids' concept."""
    scn, est, part = full_fixture
    sidecar_node_ids = ParticleStreamReader(part).node_ids_present()
    assert len(sidecar_node_ids) == FIXED_NODE_COUNT, (
        "fixture sidecar should contain all 10 nodes (no --thin-nodes)"
    )

    with launch_dashboard(scenario=scn, estimates=est, particles=part) as (_, url):
        _, _, body = _fetch_body(url)
    parsed = _parse_inlined_json(body)
    particles_blob = parsed.get("particles")
    assert particles_blob is not None, (
        "particles substructure must be present when sidecar is loaded"
    )
    particles_text = json.dumps(particles_blob)
    for node_id in sidecar_node_ids:
        assert node_id in particles_text, (
            f"sidecar node_id {node_id!r} missing from inlined particles blob"
        )

    # Design D9: no privileged-subset concept — ensure the literal token
    # "focus_node_ids" does not appear anywhere in the response body.
    assert "focus_node_ids" not in body, (
        "dashboard must not carry a 'focus_node_ids' concept "
        "(per design D9 — drill-down is driven by sidecar presence)"
    )


def test_drill_down_toggle_only_for_particle_nodes(
    two_node_sidecar_fixture: tuple[Path, Path, Path, tuple[str, str]],
) -> None:
    """Task 5.5 (substance): drill-down toggles appear only for sidecar nodes.

    The implementer MUST use an HTML attribute of the form
    ``data-particle-node="<node_id>"`` on drill-down toggle elements so
    this test can verify gating deterministically. For every node NOT
    in the sidecar, no such attribute should appear in the body.
    """
    scn, est, part, (node_a, node_b) = two_node_sidecar_fixture

    sidecar_node_ids = ParticleStreamReader(part).node_ids_present()
    assert sidecar_node_ids == frozenset({node_a, node_b}), (
        f"sidecar should contain exactly {{ {node_a!r}, {node_b!r} }}; "
        f"got {sorted(sidecar_node_ids)}"
    )

    # Full fleet to discover the absent nodes.
    header = ScenarioTruthReader(scn).header()
    all_node_ids = set(header.node_ids)
    absent_node_ids = all_node_ids - sidecar_node_ids
    assert len(absent_node_ids) == FIXED_NODE_COUNT - 2

    with launch_dashboard(scenario=scn, estimates=est, particles=part) as (_, url):
        _, _, body = _fetch_body(url)

    # (a) Inlined particles structure has records for exactly these 2 nodes.
    parsed = _parse_inlined_json(body)
    particles_blob = parsed.get("particles")
    assert particles_blob is not None
    particles_text = json.dumps(particles_blob)
    for node_id in sidecar_node_ids:
        assert node_id in particles_text, (
            f"sidecar node_id {node_id!r} missing from inlined particles blob"
        )

    # (b) No drill-down toggle for nodes absent from the sidecar.
    for node_id in absent_node_ids:
        needle = f'data-particle-node="{node_id}"'
        assert needle not in body, (
            f"node {node_id!r} is NOT in the sidecar but the body still "
            f"contains a drill-down toggle ({needle!r}). Drill-down must be "
            "gated on sidecar presence."
        )

    # Positive check — the two sidecar nodes DO have drill-down toggles.
    for node_id in sidecar_node_ids:
        needle = f'data-particle-node="{node_id}"'
        assert needle in body, (
            f"node {node_id!r} is in the sidecar but has no drill-down "
            f"toggle ({needle!r}) in the body"
        )


def test_drill_down_hidden_when_no_sidecar(
    full_fixture: tuple[Path, Path, Path],
) -> None:
    """Task 5.6: without --particles, no data-particle-node attributes and
    no populated particles substructure; truth_ticks still renders normally."""
    scn, est, _ = full_fixture

    with launch_dashboard(scenario=scn, estimates=est) as (_, url):
        _, _, body = _fetch_body(url)

    assert "data-particle-node=" not in body, (
        "no drill-down toggle attributes should appear when --particles is "
        "omitted"
    )

    parsed = _parse_inlined_json(body)
    # Either the key is absent, or it is null / empty.
    if "particles" in parsed:
        pb = parsed["particles"]
        assert pb in (None, {}, []), (
            f"particles key should be null/empty without sidecar, got {pb!r}"
        )

    # Main rendering still works — truth_ticks is fully populated.
    assert "truth_ticks" in parsed
    assert len(parsed["truth_ticks"]) == EXPECTED_TICK_COUNT


# ---------------------------------------------------------------------------
# Section 9 — Smoke harness
# ---------------------------------------------------------------------------


def test_boot_failure_surfaces_stderr(
    full_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """Task 9.2: when the dashboard cannot boot (bad scenario file), the
    subprocess exits non-zero and stderr carries a message that identifies
    the problem — so pytest assertions carry both exit code and stderr."""
    _scn, est, _ = full_fixture

    # Create an existing file that is NOT a valid scenario JSONL (empty).
    bad_scn = tmp_path / "empty_scenario.jsonl"
    bad_scn.write_text("", encoding="utf-8")

    proc = subprocess.Popen(
        [
            sys.executable,
            str(DASHBOARD_SCRIPT),
            "--scenario",
            str(bad_scn),
            "--estimates",
            str(est),
            "--port",
            "0",
            "--no-open",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
    )
    try:
        _, stderr = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, stderr = proc.communicate()
        raise AssertionError(
            "dashboard hung on empty scenario file instead of exiting cleanly"
        )

    exit_code = proc.returncode
    # An AssertionError that captures both the exit code AND the stderr —
    # matches task 9.2's requirement that boot failure messages name the
    # subprocess exit code AND captured stderr.
    assert exit_code != 0, (
        f"expected non-zero exit on empty scenario file; got exit={exit_code}; "
        f"stderr:\n{stderr}"
    )
    assert stderr.strip() != "", (
        f"expected non-empty stderr on boot failure; exit={exit_code}; "
        "stderr was empty"
    )
