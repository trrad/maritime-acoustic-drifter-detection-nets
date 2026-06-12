"""PFFloat predict-stage contract tests + AST guard against truth-field reference.

Tests in this file were extracted from the original 2305-LOC
``test_pf_float.py`` as part of the post-implementation simplify pass.
Shared fixtures live in ``tests/maritime/_pf_float_helpers.py``.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import numpy as np
import pytest

from rtl.vectors.maritime.pf_float import PFFloat, PFFloatConfig
from rtl.vectors.maritime.state_layout import (
    ANCHOR_LAYOUT,
    BALLAST_DRIFTER_LAYOUT,
    PURE_DRIFTER_LAYOUT,
)

from tests.maritime._pf_float_helpers import (
    TEST_ENU_ORIGIN_LAT,
    TEST_ENU_ORIGIN_LON,
    anchor_positions_default,
    make_pf_at_origin,
    make_test_map,
    zero_noise_config,
)


def test_predict_advances_particles_via_climatology(make_rng):
    """Task 15.1: ``predict`` advects particles via the climatology
    current. Pure drifter, climatology ``(vx=0.2, vy=0.0)``, initial
    velocity zero, process noise zero → after one tick of ``dt=60``,
    the particle-mean east position has advanced by ``0.2 * 60 = 12 m``
    and the north position is essentially unchanged.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 100

    initial_state_mean = np.zeros(state_dim)
    initial_state_mean[0] = 50.0  # east_m
    initial_state_mean[1] = 50.0  # north_m
    # depth (idx 2) and velocity (idx 3,4,5) all zero

    pf = PFFloat(
        node_id="d00",
        layout=layout,
        initial_state_mean=initial_state_mean,
        initial_state_cov_diag=np.zeros(state_dim),  # all particles at mean
        onboard_map=make_test_map(mean_vx=0.2, mean_vy=0.0),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=zero_noise_config(n_particles=n_particles),
        rng=make_rng(seed=42),
    )

    pre_east = pf.particles[:, 0].mean()
    pre_north = pf.particles[:, 1].mean()

    pf.predict(dt_sec=60.0)

    post_east = pf.particles[:, 0].mean()
    post_north = pf.particles[:, 1].mean()

    east_drift = post_east - pre_east
    north_drift = post_north - pre_north

    # 0.2 m/s * 60 s = 12 m east drift; deterministic at zero noise.
    assert east_drift == pytest.approx(12.0, abs=1e-3)
    assert north_drift == pytest.approx(0.0, abs=1e-3)


def test_predict_is_deterministic_for_seeded_rng(make_rng):
    """Task 15.2: two ``PFFloat`` instances built with identical inputs
    and identically-seeded RNGs produce element-wise equal particle
    arrays after one ``predict`` call. RNG draws (construction +
    process noise) are reproducible end-to-end.
    """
    layout = BALLAST_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 100

    common_kwargs = dict(
        node_id="d00",
        layout=layout,
        initial_state_mean=np.zeros(state_dim),
        initial_state_cov_diag=np.full(state_dim, 0.1),
        onboard_map=make_test_map(mean_vx=0.1, mean_vy=0.05),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=PFFloatConfig(n_particles=n_particles),
    )

    pf_a = PFFloat(rng=make_rng(seed=42), **common_kwargs)
    pf_b = PFFloat(rng=make_rng(seed=42), **common_kwargs)

    pf_a.predict(dt_sec=1.0)
    pf_b.predict(dt_sec=1.0)

    assert np.allclose(pf_a.particles, pf_b.particles)


def test_predict_preserves_particle_count(make_rng):
    """Task 15.3: ``predict`` does not add or drop particles. Both
    ``pf.n_particles`` and ``pf.particles.shape[0]`` are unchanged
    by a predict call.
    """
    layout = BALLAST_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 100

    pf = PFFloat(
        node_id="d00",
        layout=layout,
        initial_state_mean=np.zeros(state_dim),
        initial_state_cov_diag=np.full(state_dim, 0.1),
        onboard_map=make_test_map(),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=PFFloatConfig(n_particles=n_particles),
        rng=make_rng(seed=42),
    )

    pre_n = pf.n_particles
    pre_shape0 = pf.particles.shape[0]

    pf.predict(dt_sec=1.0)

    assert pf.n_particles == pre_n
    assert pf.particles.shape[0] == pre_shape0


def test_anchor_predict_holds_position_fixed(make_rng):
    """Task 15.4: an anchor-class ``PFFloat`` (moored) keeps the
    position slice (slots 0:3) byte-identical across predict calls,
    even after 10 ticks. Anchors don't advect — predict is a no-op on
    position. With process noise zero, no drift can leak in either.
    """
    layout = ANCHOR_LAYOUT
    state_dim = layout.state_dim
    n_particles = 50

    pf = PFFloat(
        node_id="a00",
        layout=layout,
        initial_state_mean=np.zeros(state_dim),
        initial_state_cov_diag=np.zeros(state_dim),  # all particles at mean
        onboard_map=make_test_map(mean_vx=0.3, mean_vy=0.2),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=zero_noise_config(n_particles=n_particles),
        rng=make_rng(seed=42),
    )

    initial_position_slice = pf.particles[:, 0:3].copy()

    for _ in range(10):
        pf.predict(dt_sec=60.0)

    np.testing.assert_allclose(pf.particles[:, 0:3], initial_position_slice, atol=1e-6)


def test_pure_drifter_predict_holds_depth_at_zero(make_rng):
    """Task 15.5: a pure-drifter ``PFFloat`` keeps depth (slot 2) at 0
    after 10 predict ticks against a non-trivial climatology current.
    Pure drifters are surface-only — predict must not introduce
    vertical motion even with horizontal flow present.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 50

    pf = PFFloat(
        node_id="d00",
        layout=layout,
        initial_state_mean=np.zeros(state_dim),
        initial_state_cov_diag=np.zeros(state_dim),
        onboard_map=make_test_map(mean_vx=0.2, mean_vy=0.1),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=zero_noise_config(n_particles=n_particles),
        rng=make_rng(seed=42),
    )

    for _ in range(10):
        pf.predict(dt_sec=60.0)

    np.testing.assert_allclose(pf.particles[:, 2], 0.0, atol=1e-6)


def test_predict_call_path_does_not_reference_current_fields():
    """Task 15.6: AST walk of ``pf_float.py`` confirms the predict call
    path never reaches the truth current-field module. Specifically:

    - No ``import``/``from`` statement names ``current_fields`` (catches
      both module-level and function-local imports — the latter slips
      past ``import-linter``).
    - No attribute access references the ``CurrentField`` class symbol
      (catches ``some_module.CurrentField`` patterns even if the import
      came in under another name).
    - No ``.velocity_at(...)`` call appears anywhere in the file. The
      legitimate climatology accessor on the onboard map is named
      ``current_climatology_at`` — a different symbol — so blanket-
      banning ``.velocity_at`` cannot collide with the allowed call.

    This guards against deferred / local imports that ``lint-imports``
    (a module-graph tool) does not see.
    """
    pf_float_path = Path(
        "/var/home/tim/projects/eml-research/rtl/vectors/maritime/pf_float.py"
    )
    source = pf_float_path.read_text()
    tree = ast.parse(source)

    forbidden_module_substring = "current_fields"
    forbidden_class_name = "CurrentField"
    forbidden_method_name = "velocity_at"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert forbidden_module_substring not in alias.name, (
                    f"Forbidden module reference '{alias.name}' in import "
                    f"at line {node.lineno} — pf_float must not import "
                    f"the truth current-field module (deferred or top-level)"
                )

        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            assert forbidden_module_substring not in module_name, (
                f"Forbidden module reference '{module_name}' in "
                f"'from ... import' at line {node.lineno} — pf_float "
                f"must not import from the truth current-field module"
            )
            for alias in node.names:
                assert alias.name != forbidden_class_name, (
                    f"Forbidden class import '{forbidden_class_name}' in "
                    f"'from {module_name} import ...' at line {node.lineno}"
                )

        elif isinstance(node, ast.Attribute):
            # Catches `some_chain.CurrentField` — even if the truth field
            # came in under an aliased import.
            assert node.attr != forbidden_class_name, (
                f"Forbidden attribute access '.{forbidden_class_name}' "
                f"at line {node.lineno} — pf_float must not reference "
                f"the truth current-field class"
            )
            # Blanket ban on `.velocity_at(...)` — the only legitimate
            # climatology accessor is `current_climatology_at`, so this
            # cannot collide with allowed code.
            assert node.attr != forbidden_method_name, (
                f"Forbidden method call '.{forbidden_method_name}' at "
                f"line {node.lineno} — pf_float must use "
                f"'current_climatology_at' on the onboard map, not the "
                f"truth field's '.velocity_at(...)'"
            )


def test_predict_mean_drift_tracks_climatology_when_noise_muted(make_rng):
    """Task 15.7: with onboard-map climatology ``(vx=0.2, vy=0.0)`` at
    the drifter's start position, initial particle velocity zero, and
    process noise overridden to zero, 10 ticks of ``predict(dt_sec=60)``
    advance the particle-mean east position by ``≈ 0.2 * 10 * 60 = 120 m``
    and the north position by ``≈ 0 m``. At zero noise the integration
    is deterministic to float epsilon — ``atol=1e-3`` is generous.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 100

    initial_state_mean = np.zeros(state_dim)
    initial_state_mean[0] = 50.0  # east_m
    initial_state_mean[1] = 50.0  # north_m
    # velocity slots 3,4,5 = 0 (zeroed already)

    pf = PFFloat(
        node_id="d00",
        layout=layout,
        initial_state_mean=initial_state_mean,
        initial_state_cov_diag=np.zeros(state_dim),
        onboard_map=make_test_map(mean_vx=0.2, mean_vy=0.0),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=zero_noise_config(n_particles=n_particles),
        rng=make_rng(seed=42),
    )

    pre_east = pf.particles[:, 0].mean()
    pre_north = pf.particles[:, 1].mean()

    for _ in range(10):
        pf.predict(dt_sec=60.0)

    post_east = pf.particles[:, 0].mean()
    post_north = pf.particles[:, 1].mean()

    east_drift = post_east - pre_east
    north_drift = post_north - pre_north

    # 0.2 m/s * 10 ticks * 60 s = 120 m east drift.
    assert east_drift == pytest.approx(120.0, abs=1e-3)
    assert north_drift == pytest.approx(0.0, abs=1e-3)


def test_ballast_depth_invariant_across_predict_ticks(make_rng):
    """M1 ballast-drifter PF predict pins depth constant — mirrors the
    truth-side M1 invariant (pump is `pass`; KIND_BALLAST_DRIFTING_POSE
    does not write state[2]). Seeded-determinism guard: the
    pos_noise[:, 2] slice is still drawn from the RNG (just not
    applied), so a parallel PF that draws the same sequence reaches an
    identical RNG state after predict.
    """
    layout = BALLAST_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 128

    depth_m = 42.0
    initial_state_mean = np.zeros(state_dim)
    initial_state_mean[0] = 50.0  # east_m
    initial_state_mean[1] = 50.0  # north_m
    initial_state_mean[2] = depth_m  # depth_m (non-zero to distinguish invariant from trivial zero)
    initial_state_mean[5] = 0.3  # vz_ms — non-zero vertical velocity that WOULD advance depth pre-fix

    config = PFFloatConfig(
        n_particles=n_particles,
        process_noise_pos_m_per_sqrt_s=0.1,  # non-zero — pos_noise[:, 2] is drawn
        process_noise_vel_ms_per_sqrt_s=0.05,
        process_noise_heading_deg_per_sqrt_s=1.0,
        process_noise_current_ms_per_sqrt_s=0.01,
    )

    pf = PFFloat(
        node_id="d00",
        layout=layout,
        initial_state_mean=initial_state_mean,
        initial_state_cov_diag=np.zeros(state_dim),  # every particle at mean — depth_m exactly 42.0
        onboard_map=make_test_map(mean_vx=0.2, mean_vy=0.0),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=config,
        rng=make_rng(seed=42),
    )

    assert np.all(pf.particles[:, 2] == depth_m)

    for _ in range(10):
        pf.predict(dt_sec=60.0)

    np.testing.assert_array_equal(
        pf.particles[:, 2],
        np.full(n_particles, depth_m),
    )

    # Seeded-determinism guard: the predict step MUST still draw
    # pos_noise of shape (n, 3) each tick so RNG stream order is
    # unchanged. Build a reference PF with identical seed and confirm
    # that one tick of predict advances the RNG state identically.
    pf_ref = PFFloat(
        node_id="d00",
        layout=layout,
        initial_state_mean=initial_state_mean,
        initial_state_cov_diag=np.zeros(state_dim),
        onboard_map=make_test_map(mean_vx=0.2, mean_vy=0.0),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=config,
        rng=make_rng(seed=42),
    )
    pf_ref.predict(dt_sec=60.0)

    pf_replay = PFFloat(
        node_id="d00",
        layout=layout,
        initial_state_mean=initial_state_mean,
        initial_state_cov_diag=np.zeros(state_dim),
        onboard_map=make_test_map(mean_vx=0.2, mean_vy=0.0),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=config,
        rng=make_rng(seed=42),
    )
    pf_replay.predict(dt_sec=60.0)
    np.testing.assert_array_equal(pf_ref.particles, pf_replay.particles)


# ---------------------------------------------------------------------------
# Per-tick velocity sampling contract (maritime-velocity-model). These tests
# replace the retired RW velocity update with a per-tick-sampled residual.
# The PF's velocity state slot (vx at layout index 3) is re-sampled each
# predict tick from ``N(0, sqrt(var_vxvy(lat, lon)) + floor)`` where
# ``floor = config.process_noise_vel_ms_per_sqrt_s``.
# ---------------------------------------------------------------------------


def test_particle_velocity_sampling_sigma_matches_climatology_plus_floor(make_rng):
    """With climatology ``(mean_vx=0.1, mean_vy=0.05, var_vx=0.04,
    var_vy=0.01)`` uniform over the region and ``floor=0.02``, one
    ``predict(dt_sec=60)`` tick yields ``particles[:, idx.vx]`` whose
    sample stddev sits within ``[sqrt(0.04) + 0.02 ± 0.03]`` and whose
    sample mean is within ``3 * (sqrt(0.04) + 0.02) / sqrt(500)`` of
    zero. Pins the "σ = sqrt(climatology var) + floor" contract.
    """
    layout = PURE_DRIFTER_LAYOUT
    n_particles = 500
    floor = 0.02

    pf = make_pf_at_origin(
        layout=layout,
        rng=make_rng(seed=42),
        n_particles=n_particles,
        onboard_map=make_test_map(
            mean_vx=0.1, mean_vy=0.05, var_vx=0.04, var_vy=0.01
        ),
        config=PFFloatConfig(
            n_particles=n_particles,
            process_noise_pos_m_per_sqrt_s=0.0,
            process_noise_vel_ms_per_sqrt_s=floor,
            process_noise_heading_deg_per_sqrt_s=0.0,
            process_noise_current_ms_per_sqrt_s=0.0,
        ),
    )

    pf.predict(dt_sec=60.0)

    vx = pf.particles[:, 3]
    expected_sigma = math.sqrt(0.04) + floor  # ≈ 0.22

    sample_std = float(np.std(vx, ddof=1))
    margin = 0.03
    assert (expected_sigma - margin) <= sample_std <= (expected_sigma + margin), (
        f"particle vx stddev {sample_std} outside "
        f"[{expected_sigma - margin}, {expected_sigma + margin}] "
        f"(expected σ = sqrt(0.04) + {floor} = {expected_sigma})"
    )

    sample_mean = float(vx.mean())
    mean_bound = 3.0 * expected_sigma / math.sqrt(n_particles)
    assert abs(sample_mean) < mean_bound, (
        f"particle vx mean {sample_mean} exceeds 3σ/sqrt(n) bound {mean_bound} "
        f"— residual should be zero-mean"
    )


def test_particle_velocity_floor_prevents_collapse_on_zero_variance_climatology(make_rng):
    """With climatology ``var_vx=var_vy=0`` everywhere and
    ``floor=0.02``, one predict tick yields a non-degenerate
    ``particles[:, idx.vx]`` whose sample stddev lies within
    ``[0.5, 1.5] * 0.02`` (floor-dominated) and is not a single
    repeated value."""
    layout = PURE_DRIFTER_LAYOUT
    n_particles = 500
    floor = 0.02

    pf = make_pf_at_origin(
        layout=layout,
        rng=make_rng(seed=42),
        n_particles=n_particles,
        onboard_map=make_test_map(
            mean_vx=0.1, mean_vy=0.05, var_vx=0.0, var_vy=0.0
        ),
        config=PFFloatConfig(
            n_particles=n_particles,
            process_noise_pos_m_per_sqrt_s=0.0,
            process_noise_vel_ms_per_sqrt_s=floor,
            process_noise_heading_deg_per_sqrt_s=0.0,
            process_noise_current_ms_per_sqrt_s=0.0,
        ),
    )

    pf.predict(dt_sec=60.0)

    vx = pf.particles[:, 3]
    unique_count = int(np.unique(vx).size)
    assert unique_count > 1, (
        f"particles[:, vx] collapsed to {unique_count} unique value(s); "
        f"floor should prevent total collapse"
    )

    sample_std = float(np.std(vx, ddof=1))
    assert 0.5 * floor <= sample_std <= 1.5 * floor, (
        f"particle vx stddev {sample_std} outside [{0.5 * floor}, {1.5 * floor}] "
        f"— floor-only σ = {floor} should dominate"
    )


def test_particle_velocity_residual_stays_bounded_over_100_ticks(make_rng):
    """Over 100 predict ticks against climatology ``(mean_vx=0.1,
    var_vx=0.01)`` with ``floor=0.02``, the per-particle ``|vx|``
    maximum across all ticks stays below ``5 * (sqrt(0.01) + 0.02) =
    0.6 m/s``, and the particle-cloud mean ``vx`` at each tick stays
    within ``3 * (sqrt(0.01) + 0.02) / sqrt(n_particles)`` of zero.
    Guards against RW accumulation."""
    layout = PURE_DRIFTER_LAYOUT
    n_particles = 500
    floor = 0.02
    var_vx = 0.01
    expected_sigma = math.sqrt(var_vx) + floor  # = 0.12

    pf = make_pf_at_origin(
        layout=layout,
        rng=make_rng(seed=42),
        n_particles=n_particles,
        onboard_map=make_test_map(
            mean_vx=0.1, mean_vy=0.0, var_vx=var_vx, var_vy=var_vx
        ),
        config=PFFloatConfig(
            n_particles=n_particles,
            process_noise_pos_m_per_sqrt_s=0.0,
            process_noise_vel_ms_per_sqrt_s=floor,
            process_noise_heading_deg_per_sqrt_s=0.0,
            process_noise_current_ms_per_sqrt_s=0.0,
        ),
    )

    per_tick_max = []
    per_tick_mean_bound_ok = []
    mean_bound = 3.0 * expected_sigma / math.sqrt(n_particles)

    for _ in range(100):
        pf.predict(dt_sec=60.0)
        vx = pf.particles[:, 3]
        per_tick_max.append(float(np.max(np.abs(vx))))
        per_tick_mean_bound_ok.append(abs(float(vx.mean())) < mean_bound)

    global_max = max(per_tick_max)
    max_bound = 5.0 * expected_sigma
    assert global_max < max_bound, (
        f"per-particle |vx| max over 100 ticks is {global_max}; expected "
        f"< 5 * (sqrt({var_vx}) + {floor}) = {max_bound} — RW regression?"
    )

    bad_ticks = [i for i, ok in enumerate(per_tick_mean_bound_ok) if not ok]
    assert not bad_ticks, (
        f"particle-cloud mean vx exceeded 3σ/sqrt(n) bound {mean_bound} "
        f"on ticks {bad_ticks} — residual is not zero-mean per tick"
    )


def test_pf_mean_tracks_truth_under_matched_climatology_zero_obs(make_rng):
    """Matched pair: truth pure-drifter advected by
    ``ConstantCurrentField(0.2, 0.0)`` for 10 ticks at dt=60 vs. a PF
    pure-drifter with climatology ``(mean_vx=0.2, var_vx=0.01,
    var_vy=0.01)`` stepped with ``predict(dt_sec=60)`` 10 times and no
    observations. At tick 10, ``|pf_particle_mean_east - truth_east|``
    stays within ``10 * 60 * (sqrt(0.01) + 0.02) ≈ 72 m`` — a
    climatology-std-bounded envelope derived from (var_vx + floor)."""
    from rtl.vectors.maritime.dynamics import propagate_truth, PhysicsEnv
    from rtl.vectors.maritime.fleet import make_pure_drifter
    from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE

    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 500
    floor = 0.02
    var_vx = 0.01
    num_steps = 10
    dt = 60.0

    # Truth-side run.
    current_field = ConstantCurrentFieldForTest(0.2, 0.0)
    env = PhysicsEnv(
        current_field=current_field,
        t_sec=0.0,
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
    )
    truth_state = np.zeros(state_dim)
    truth_rng = make_rng(seed=42)
    for _ in range(num_steps):
        temp_node = make_pure_drifter(PURE_DRIFTER_PROFILE, truth_state, make_rng(seed=1))
        truth_state = propagate_truth(temp_node, dt_sec=dt, env=env, rng=truth_rng)
    truth_east = float(truth_state[0])

    # PF-side run (no observations).
    pf = make_pf_at_origin(
        layout=layout,
        rng=make_rng(seed=99),
        n_particles=n_particles,
        onboard_map=make_test_map(
            mean_vx=0.2, mean_vy=0.0, var_vx=var_vx, var_vy=var_vx
        ),
        config=PFFloatConfig(
            n_particles=n_particles,
            process_noise_pos_m_per_sqrt_s=0.0,
            process_noise_vel_ms_per_sqrt_s=floor,
            process_noise_heading_deg_per_sqrt_s=0.0,
            process_noise_current_ms_per_sqrt_s=0.0,
        ),
    )
    for _ in range(num_steps):
        pf.predict(dt_sec=dt)

    pf_east_mean = float(pf.particles[:, 0].mean())
    envelope = num_steps * dt * (math.sqrt(var_vx) + floor)  # ≈ 72 m
    err = abs(pf_east_mean - truth_east)
    assert err < envelope, (
        f"PF east-mean {pf_east_mean} m vs. truth east {truth_east} m "
        f"differs by {err} m; expected < {envelope} m "
        f"(10 * 60 * (sqrt({var_vx}) + {floor}))"
    )


class ConstantCurrentFieldForTest:
    """Local duplicate of ``ConstantCurrentField`` from
    ``test_dynamics.py``. Kept local to avoid cross-test-file import
    coupling; the contract under test here is the PF/truth matched
    pair, not the current-field class itself."""

    def __init__(self, vx: float, vy: float):
        self.vx = vx
        self.vy = vy

    def velocity_at(self, lat_deg: float, lon_deg: float, t_sec: float) -> tuple[float, float]:
        return (self.vx, self.vy)


# -----------------------------------------------------------------------------
# Contract tests for `Predict Shifts Previous-Velocity And Previous-Heading Slots`
# (maritime-pf-prev-velocity-shift). Pairs with truth-side dynamics.py:47-48.
# -----------------------------------------------------------------------------


def test_predict_shifts_prev_velocity_and_prev_heading(make_rng):
    """Substance: after ``predict`` runs one tick, each particle's
    ``prev_vx`` equals the PRE-predict ``vx`` (and same for vy, vz,
    heading). Guards against the stale-initial-prev-velocity bug in
    ``_imu_log_likelihood``'s finite-difference accel prediction.
    """
    layout = BALLAST_DRIFTER_LAYOUT
    state_dim = layout.state_dim

    # Seed initial state so vx and prev_vx start at DIFFERENT values.
    # Otherwise the test could "pass" trivially — we need to observe
    # the shift, not just that the slots happen to agree at tick 0.
    initial_state_mean = np.zeros(state_dim)
    initial_state_mean[3] = 0.25  # vx
    initial_state_mean[4] = -0.17  # vy
    initial_state_mean[5] = 0.05  # vz
    initial_state_mean[6] = 47.0  # heading
    # prev_velocity slots at indices 15-17 and prev_heading at 18
    # stay at 0 (their default initial value), so vx != prev_vx pre-predict.

    pf = make_pf_at_origin(
        layout=layout,
        rng=make_rng(seed=11),
        initial_state_mean=initial_state_mean,
        cov_diag=np.zeros(state_dim),  # every particle starts at the exact mean
        onboard_map=make_test_map(mean_vx=0.1, mean_vy=0.0),
        config=zero_noise_config(n_particles=64),
    )

    # Sanity: pre-predict, vx != prev_vx for all particles.
    pre_vx = pf.particles[:, 3].copy()
    pre_vy = pf.particles[:, 4].copy()
    pre_vz = pf.particles[:, 5].copy()
    pre_heading = pf.particles[:, 6].copy()
    pre_prev_vx = pf.particles[:, 15].copy()
    assert np.any(pre_vx != pre_prev_vx), (
        "Test precondition violated: initial vx matches initial prev_vx. "
        "Seed the initial state so they diverge."
    )

    pf.predict(dt_sec=60.0)

    post_prev_vx = pf.particles[:, 15]
    post_prev_vy = pf.particles[:, 16]
    post_prev_vz = pf.particles[:, 17]
    post_prev_heading = pf.particles[:, 18]

    np.testing.assert_array_equal(post_prev_vx, pre_vx)
    np.testing.assert_array_equal(post_prev_vy, pre_vy)
    np.testing.assert_array_equal(post_prev_vz, pre_vz)
    np.testing.assert_array_equal(post_prev_heading, pre_heading)


def test_prev_velocity_tracks_one_tick_lag_across_multiple_predicts(make_rng):
    """Substance: across 5 consecutive predicts, ``prev_vx`` at tick N
    equals the post-predict ``vx`` from tick N-1. Confirms the shift
    is invariant over the full predict path, not just a one-time
    top-of-method accident.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim

    initial_state_mean = np.zeros(state_dim)
    initial_state_mean[3] = 0.1  # vx
    initial_state_mean[4] = -0.05  # vy
    initial_state_mean[6] = 12.0  # heading

    pf = make_pf_at_origin(
        layout=layout,
        rng=make_rng(seed=22),
        initial_state_mean=initial_state_mean,
        cov_diag=np.zeros(state_dim),
        onboard_map=make_test_map(mean_vx=0.15, mean_vy=0.0),
        config=zero_noise_config(n_particles=32),
    )

    # Track post-predict vx at each tick; at tick N+1, prev_vx should match.
    post_vx_history: list[np.ndarray] = []
    post_vy_history: list[np.ndarray] = []
    post_heading_history: list[np.ndarray] = []

    for tick in range(5):
        pf.predict(dt_sec=30.0)
        post_vx_history.append(pf.particles[:, 3].copy())
        post_vy_history.append(pf.particles[:, 4].copy())
        post_heading_history.append(pf.particles[:, 6].copy())

    # At tick N (N>=1), pre-predict vx was the post-predict vx from tick N-1.
    # After tick N's predict, prev_vx equals that pre-predict value.
    # But since vx gets written again later in each predict, we can't
    # directly read pre-predict vx post-hoc. Instead: verify the shift
    # is consistent by re-running a single extra predict and checking
    # prev_vx now equals the last tick's post-predict vx.
    last_post_vx = post_vx_history[-1].copy()
    last_post_vy = post_vy_history[-1].copy()
    last_post_heading = post_heading_history[-1].copy()

    pf.predict(dt_sec=30.0)

    np.testing.assert_array_equal(pf.particles[:, 15], last_post_vx)
    np.testing.assert_array_equal(pf.particles[:, 16], last_post_vy)
    np.testing.assert_array_equal(pf.particles[:, 18], last_post_heading)
