"""Pipeline integration tests for current field → truth → dashboard substance.

The testing-philosophy.md "Pipeline Tests" section calls out an exact
substance risk that no existing unit test catches: a truth-side pipeline
that silently produces ``surface_current == (0, 0)`` on every tick
because nothing actually wires the field into the state slot. The M1
history has already produced one variant of this bug (noted in
``AGENTS.md`` under "Integration pipelines: skeleton before spec chain").

These tests exercise the real producer chain — gen CLI subprocess →
JSONL → ``ScenarioTruthReader`` — against a *non-trivial* field
(mean flow + eddy + tide) for a short horizon and assert substance:

- Every node's truth ``surface_current`` slot equals the field's
  ``velocity_at`` evaluated at the node's lat/lon at that tick, within
  float tolerance.
- Two nodes at different positions produce materially different
  ``surface_current`` values (rules out "field is a single global
  number written to every node").
- When ``--tidal-amplitude-ms`` is non-zero, the same position's
  ``surface_current`` varies over time (rules out "field is computed
  once at t=0").

The scenarios generated here are self-contained and short (fractions
of an hour) so pipeline-test time stays bounded. They're also the
intended seed for future "ranging-interval vs tracking-accuracy"
measurement reports — the same field fixture will drive those.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

from rtl.vectors.maritime.coords import enu_to_latlon
from rtl.vectors.maritime.current_fields import EddySpec, FieldConfig, SyntheticEddyField
from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader


REPO_ROOT: Path = Path(__file__).resolve().parents[2]

# Tiny scenario for pipeline tests: 5x5km bbox (matches the known-good
# small-deployment size for LoRa coverage), dt=30s. Duration set per-test
# so we can assert tick counts deterministically.
FIXED_BBOX = "48.50,-123.50,48.55,-123.45"
FIXED_DT_SEC = 30.0
FIXED_SEED = 42
FIXED_NODE_COUNT = 10


def _gen(tmp_path: Path, *, duration_hours: float, extra_args: list[str]) -> Path:
    """Run gen_maritime_scenario in a subprocess; return the output JSONL path."""
    out = tmp_path / "scenario.jsonl"
    args = [
        sys.executable,
        "-m", "rtl.vectors.maritime.gen_maritime_scenario",
        "--seed", str(FIXED_SEED),
        "--bbox", FIXED_BBOX,
        "--duration-hours", str(duration_hours),
        "--dt-sec", str(FIXED_DT_SEC),
        "--nodes", str(FIXED_NODE_COUNT),
        "--out", str(out),
        "--created-at", "2026-04-23T00:00:00+00:00",
        *extra_args,
    ]
    subprocess.run(args, check=True, cwd=str(REPO_ROOT), capture_output=True)
    return out


def test_pipeline_surface_current_matches_field_for_every_node(tmp_path):
    """Substance: for every node and every tick ``t >= 1``, the truth
    state's ``surface_current`` slot at tick ``t`` equals
    ``field.velocity_at(position_at_tick_{t-1}, t_sec_of_tick_t)``
    within float tolerance.

    This is the "surface_current stays at zeros because nothing wrote to
    it" failure mode the testing philosophy calls out. Mean flow + eddy
    + tide all enabled so the expected value is position- AND
    time-varying; a stubbed implementation that writes (0, 0) or a
    constant would fail this test hard.

    Pairwise comparison across ticks: ``propagate_truth`` samples the
    field at the BEFORE-advection position, stores it in
    ``surface_current``, then advects. The stored sample at tick t
    reflects the node's position at the end of tick t-1 (= tick t's
    input state). Tick 0's input is the initial fleet state which the
    scenario file does not carry, so we skip t=0 in this test.
    """
    mean_vx = 0.15
    mean_vy = -0.05
    tidal_amp = 0.1
    eddy_lat, eddy_lon = 48.525, -123.475
    eddy_radius, eddy_peak = 1500.0, 0.3

    scn_path = _gen(
        tmp_path,
        duration_hours=0.05,  # 180 sec = 6 ticks at dt=30s
        extra_args=[
            "--mean-flow-east-ms", str(mean_vx),
            "--mean-flow-north-ms", str(mean_vy),
            "--tidal-amplitude-ms", str(tidal_amp),
            "--eddy", f"{eddy_lat},{eddy_lon},{eddy_radius},{eddy_peak},1",
        ],
    )

    # Rebuild the same field locally; gen is deterministic, so this
    # reproduces the exact velocities emitted into the truth state.
    expected_field = SyntheticEddyField(
        FieldConfig(
            mean_vx_ms=mean_vx,
            mean_vy_ms=mean_vy,
            eddies=[EddySpec(eddy_lat, eddy_lon, eddy_radius, eddy_peak, True)],
            tidal_amplitude_ms=tidal_amp,
            tidal_period_sec=44712.0,
            tidal_direction_deg=0.0,
        )
    )

    reader = ScenarioTruthReader(scn_path)
    hdr = reader.header()
    origin_lat, origin_lon = hdr.bbox[0], hdr.bbox[1]

    ticks = list(reader)
    assert len(ticks) >= 2, "need >=2 ticks for pairwise surface_current check"

    mismatches: list[str] = []
    compared = 0
    for prev, curr in zip(ticks, ticks[1:]):
        for nid in curr.node_truth:
            prev_state = prev.node_truth[nid]
            east, north = float(prev_state[0]), float(prev_state[1])
            lat_arr, lon_arr = enu_to_latlon(
                np.array([east]), np.array([north]), origin_lat, origin_lon
            )
            lat, lon = float(lat_arr[0]), float(lon_arr[0])

            expected_vx, expected_vy = expected_field.velocity_at(lat, lon, curr.t_sec)

            curr_state = curr.node_truth[nid]
            actual_vx = float(curr_state[7])
            actual_vy = float(curr_state[8])

            compared += 1
            if (abs(actual_vx - expected_vx) > 1e-9
                    or abs(actual_vy - expected_vy) > 1e-9):
                mismatches.append(
                    f"t={curr.t} node={nid} "
                    f"expected ({expected_vx:+.9f}, {expected_vy:+.9f}) "
                    f"got ({actual_vx:+.9f}, {actual_vy:+.9f})"
                )

    assert compared > 0, "no (node, tick) pairs were compared"
    assert not mismatches, (
        f"{len(mismatches)}/{compared} (node, tick) pairs had truth "
        f"surface_current diverging from the field. First 5:\n"
        + "\n".join(mismatches[:5])
    )


def test_pipeline_surface_current_varies_across_nodes(tmp_path):
    """Substance: under a non-trivial field (eddy near the bbox center),
    at least two nodes at distinct positions produce distinct
    ``surface_current`` values.

    Rules out the "field queried at (0, 0) for every node" bug class.
    An eddy makes the velocity field position-dependent; two nodes
    sampling from different distances to the eddy center MUST see
    different tangential velocities.
    """
    scn_path = _gen(
        tmp_path,
        duration_hours=0.01,  # 1 tick
        extra_args=[
            "--mean-flow-east-ms", "0.1",
            "--eddy", "48.525,-123.475,1500,0.3,1",
        ],
    )

    reader = ScenarioTruthReader(scn_path)
    tick0 = next(iter(reader))

    vxs = [float(state[7]) for state in tick0.node_truth.values()]
    vys = [float(state[8]) for state in tick0.node_truth.values()]

    assert max(vxs) - min(vxs) > 0.01 or max(vys) - min(vys) > 0.01, (
        f"node surface_current values are suspiciously uniform across the "
        f"fleet under a non-trivial eddy field — "
        f"vx range [{min(vxs):+.4f}, {max(vxs):+.4f}], "
        f"vy range [{min(vys):+.4f}, {max(vys):+.4f}]. "
        "Expected position-dependent variation."
    )


def test_pipeline_surface_current_time_varies_under_tide(tmp_path):
    """Substance: with tidal forcing enabled, a moored anchor's
    ``surface_current`` MUST vary across ticks (the tide is a time-
    dependent component of the field).

    Use the anchor (fixed position) to isolate time-dependence from
    position-dependence: the anchor's lat/lon is constant, so any
    variation in its surface_current across ticks can only come from
    the time-varying component of the field.
    """
    # duration short enough to fit many ticks but long enough that the
    # tidal sine has a measurable phase shift. With period 44712 s,
    # a span of a few minutes shifts sin by ~0.001 rad — the difference
    # tidal_amp * (sin(t_end) - sin(t_start)) should be > 1e-5 m/s.
    duration_hours = 0.5  # 1800s = 60 ticks at dt=30s
    tidal_amp = 0.1

    scn_path = _gen(
        tmp_path,
        duration_hours=duration_hours,
        extra_args=[
            "--tidal-amplitude-ms", str(tidal_amp),
            "--tidal-period-sec", "3600.0",  # 1-hour tide -> ~half cycle
            "--tidal-direction-deg", "0.0",  # eastward tide
        ],
    )

    reader = ScenarioTruthReader(scn_path)
    hdr = reader.header()
    anchor_id = next(
        nid for nid, cls in hdr.node_classes.items() if cls == "anchor"
    )

    vx_trace = []
    for view in reader:
        state = view.node_truth[anchor_id]
        vx_trace.append(float(state[7]))

    vx_trace_arr = np.array(vx_trace)
    # Expect an appreciable oscillation across the trace; the amplitude
    # should be close to the full tidal amp since we've spanned half a
    # period. Tight sanity bound: peak-to-peak > 0.1 * tidal_amp.
    assert vx_trace_arr.max() - vx_trace_arr.min() > 0.1 * tidal_amp, (
        f"anchor surface_current_vx did not vary under tide; "
        f"trace range [{vx_trace_arr.min():+.6f}, {vx_trace_arr.max():+.6f}], "
        f"expected peak-to-peak > {0.1 * tidal_amp}. "
        "Implies truth pipeline is not re-sampling the time-dependent field."
    )


def test_pipeline_surface_current_all_zero_when_field_default(tmp_path):
    """Negative baseline: with no CLI field flags, defaults are all zero
    and the truth ``surface_current`` IS identically zero at every node
    and tick. Documents the pre-plumbing behavior so the non-zero tests
    above have a contrasting case.
    """
    scn_path = _gen(
        tmp_path,
        duration_hours=0.01,
        extra_args=[],  # no field flags → zero defaults
    )

    reader = ScenarioTruthReader(scn_path)
    for view in reader:
        for nid, state in view.node_truth.items():
            assert state[7] == 0.0 and state[8] == 0.0, (
                f"default-field run should emit zero surface_current; "
                f"node {nid} at t={view.t} had ({state[7]}, {state[8]})"
            )
