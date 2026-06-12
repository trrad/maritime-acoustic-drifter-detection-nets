"""PFFloat resample / estimate / step + per-node independence contract tests.

Tests in this file were extracted from the original 2305-LOC
``test_pf_float.py`` as part of the post-implementation simplify pass.
Shared fixtures live in ``tests/maritime/_pf_float_helpers.py``.
"""

from __future__ import annotations

import numpy as np
import pytest

from rtl.vectors.maritime.coords import enu_to_latlon
from rtl.vectors.maritime.pf_estimates_schema import PFEstimateRecord
from rtl.vectors.maritime.pf_float import PFFloat, PFFloatConfig
from rtl.vectors.maritime.scenario_schema import GPSObservation
from rtl.vectors.maritime.state_layout import (
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


# ---------------------------------------------------------------------------
# Section 21 — Resample
# ---------------------------------------------------------------------------


def test_resample_weights_become_uniform(make_rng):
    """Task 21.1 / Spec scenario "Systematic resample resets weights".

    After ``resample``, the weight vector MUST be exactly uniform at
    ``1 / n_particles`` regardless of the pre-resample distribution.
    Set up a deliberately non-uniform 5-particle weight vector and
    confirm uniformity after one resample call.
    """
    layout = PURE_DRIFTER_LAYOUT
    n_particles = 5

    pf = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )

    # Manually install a non-uniform weight distribution. Underscore
    # access is the documented test-fixture boundary.
    pf._weights = np.array([0.5, 0.3, 0.1, 0.05, 0.05])

    pf.resample()

    assert np.allclose(pf.weights, 1.0 / n_particles), (
        f"weights must be uniform at 1/{n_particles} after resample; "
        f"got {pf.weights}"
    )


def test_resample_preserves_particle_count(make_rng):
    """Task 21.2 / Spec requirement "Particle count preserved by
    resample".

    Systematic resampling MUST NOT add or drop particles —
    ``pf.particles.shape[0]`` and ``pf.n_particles`` are unchanged.
    """
    layout = BALLAST_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 100

    pf = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )

    pf.resample()

    assert pf.particles.shape == (n_particles, state_dim)
    assert pf.n_particles == n_particles


def test_systematic_resample_is_deterministic_for_seeded_rng(make_rng):
    """Task 21.3 / Spec requirement "Resample stage deterministic given
    seeded RNG".

    Two PFs built with identical inputs and identically-seeded RNGs,
    given identical pre-resample weight vectors, MUST produce
    element-wise equal particle arrays after ``resample`` — the
    systematic-resampling routine consumes the RNG via ``u0 ~ U(0, 1/n)``
    and that draw is reproducible given the seed.
    """
    layout = PURE_DRIFTER_LAYOUT
    n_particles = 5

    pf1 = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        rng=make_rng(seed=99),
    )
    pf2 = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        rng=make_rng(seed=99),
    )

    # Make particle arrays distinguishable (they all start at zero by
    # default — without per-particle distinctness, "equal after
    # resample" would be vacuous). Identical mutation on both.
    distinct_state = np.arange(n_particles * pf1.particles.shape[1]).reshape(
        n_particles, pf1.particles.shape[1]
    ).astype(float)
    pf1._particles = distinct_state.copy()
    pf2._particles = distinct_state.copy()

    common_weights = np.array([0.4, 0.3, 0.2, 0.05, 0.05])
    pf1._weights = common_weights.copy()
    pf2._weights = common_weights.copy()

    pf1.resample()
    pf2.resample()

    assert np.array_equal(pf1.particles, pf2.particles), (
        "resample with identical seed + identical inputs must produce "
        "byte-identical particle arrays"
    )


# ---------------------------------------------------------------------------
# Section 23 — Estimate
# ---------------------------------------------------------------------------


def test_estimate_mean_is_weighted_average(make_rng):
    """Task 23.1 (mean facet) / Spec scenario "Estimate returns weighted
    mean".

    ``estimate.mean[d]`` MUST equal ``sum_i w_i * particles[i, d]`` for
    each state dimension ``d`` — the standard importance-weighted
    posterior mean. We pin a known 4-particle posterior and check the
    returned mean against the hand-computed weighted average.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 4

    pf = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )

    # Known particle matrix and known (normalized) weights.
    particles = np.zeros((n_particles, state_dim))
    particles[0, 0] = 10.0  # east
    particles[1, 0] = 20.0
    particles[2, 0] = 30.0
    particles[3, 0] = 40.0
    particles[0, 1] = 1.0  # north
    particles[1, 1] = 2.0
    particles[2, 1] = 3.0
    particles[3, 1] = 4.0
    weights = np.array([0.4, 0.3, 0.2, 0.1])

    pf._particles = particles.copy()
    pf._weights = weights.copy()

    record = pf.estimate(t=0, t_sec=0.0)

    expected_mean = (weights[:, None] * particles).sum(axis=0)
    actual_mean = np.asarray(record.mean)

    assert actual_mean.shape == (state_dim,)
    np.testing.assert_allclose(
        actual_mean,
        expected_mean,
        atol=1e-12,
        err_msg="estimate.mean must equal sum_i w_i * particles[i, :]",
    )


def test_estimate_cov_diag_is_weighted_variance(make_rng):
    """Task 23.1 (cov_diag facet) / Spec scenario "Estimate returns
    diagonal weighted covariance".

    ``estimate.cov_diag[d]`` MUST equal
    ``sum_i w_i * (particles[i, d] - mean[d])^2`` per dim — the biased
    (population) weighted variance. The spec says "weighted variance"
    without specifying biased vs. unbiased; we pin biased here because
    it is the standard PF formulation and matches the natural
    formulation of importance-weighted second moment. The implementer
    must use the same convention.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 4

    pf = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )

    particles = np.zeros((n_particles, state_dim))
    particles[0, 0] = 10.0
    particles[1, 0] = 20.0
    particles[2, 0] = 30.0
    particles[3, 0] = 40.0
    particles[0, 1] = 1.0
    particles[1, 1] = 2.0
    particles[2, 1] = 3.0
    particles[3, 1] = 4.0
    weights = np.array([0.4, 0.3, 0.2, 0.1])

    pf._particles = particles.copy()
    pf._weights = weights.copy()

    record = pf.estimate(t=0, t_sec=0.0)

    expected_mean = (weights[:, None] * particles).sum(axis=0)
    expected_cov_diag = (weights[:, None] * (particles - expected_mean) ** 2).sum(axis=0)

    actual_cov_diag = np.asarray(record.cov_diag)

    assert actual_cov_diag.shape == (state_dim,)
    np.testing.assert_allclose(
        actual_cov_diag,
        expected_cov_diag,
        atol=1e-12,
        err_msg=(
            "estimate.cov_diag[d] must equal sum_i w_i * (particles[i,d] - mean[d])^2 "
            "(biased / population weighted variance)"
        ),
    )


def test_estimate_n_effective_formula(make_rng):
    """Task 23.2 / Spec scenario "Estimate reports effective sample size".

    ``estimate.n_effective`` MUST equal ``1 / sum_i w_i^2``. Two
    sub-cases:

    1. Non-uniform weights — value matches the closed-form ESS.
    2. Uniform weights (fresh PF) — value is exactly ``n_particles``
       (the maximum, as expected when no sample carries disproportionate
       weight).
    """
    layout = PURE_DRIFTER_LAYOUT
    n_particles = 5

    pf = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )

    # Non-uniform case. (Already normalized: 0.5 + 0.3 + 0.1 + 0.1 + 0.0.)
    weights = np.array([0.5, 0.3, 0.1, 0.1, 0.0])
    pf._weights = weights.copy()

    record = pf.estimate(t=0, t_sec=0.0)

    expected_ess = 1.0 / float(np.sum(weights**2))
    assert record.n_effective == pytest.approx(expected_ess, abs=1e-12), (
        f"n_effective must equal 1 / sum_i w_i^2; "
        f"expected {expected_ess:.6f}, got {record.n_effective:.6f}"
    )

    # Uniform case — exact equality with n_particles.
    pf_uniform = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )
    record_uniform = pf_uniform.estimate(t=0, t_sec=0.0)
    assert record_uniform.n_effective == pytest.approx(float(n_particles), abs=1e-12), (
        f"uniform-weights ESS must equal n_particles ({n_particles}); "
        f"got {record_uniform.n_effective}"
    )


def test_step_n_effective_is_pre_resample(make_rng):
    """Regression: ``step()`` returns ``n_effective`` computed BEFORE
    resample, so the value reflects observation informativeness.

    After resample, weights are uniform by construction and ``1/Σw²``
    trivially equals ``n_particles``; capturing that would make the
    summary's ESS column a meaningless tautology. This test injects a
    single informative GPS observation (noise σ=1 m, truth at origin,
    initial position σ=100 m) and asserts the returned record's
    ``n_effective`` is strictly less than ``n_particles`` — i.e. the
    weight step actually concentrated weight on some particles and the
    estimate captured that concentration.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 200

    # Wide initial position prior so GPS has discriminatory power.
    cov_diag = np.zeros(state_dim)
    cov_diag[0:2] = 10000.0  # σ ≈ 100 m on each of east, north

    pf = make_pf_at_origin(
        layout=layout,
        cov_diag=cov_diag,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )

    # GPS observation at origin (matches the initial mean) with tight noise.
    obs_lat, obs_lon = enu_to_latlon(0.0, 0.0, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON)
    gps_obs = GPSObservation(
        t_sec=0.0,
        node_id="d00",
        lat_deg=float(obs_lat),
        lon_deg=float(obs_lon),
        noise_sigma_m=1.0,
    )

    record = pf.step(dt_sec=1.0, observations=[gps_obs], t=0, t_sec=0.0)

    assert record.n_effective < n_particles, (
        f"n_effective must reflect pre-resample weight informativeness; "
        f"got {record.n_effective} == n_particles ({n_particles}), "
        "which means estimate is reading post-resample uniform weights "
        "(a tautology)."
    )
    # Sanity band: with σ_prior=100 m vs σ_obs=1 m, ESS should drop substantially.
    assert record.n_effective < 0.5 * n_particles, (
        f"informative GPS obs against wide prior should drop ESS well below "
        f"n_particles/2; got n_effective={record.n_effective} / n_particles={n_particles}"
    )


def test_step_returns_pf_estimate_record_with_correct_t_t_sec_node_id(make_rng):
    """Task 23.3 / Spec scenario "Step returns PFEstimateRecord stamped
    with tick + node".

    A single ``step`` call MUST return a ``PFEstimateRecord`` whose
    ``t``, ``t_sec``, and ``node_id`` fields match the arguments /
    construction-time node identity. With an empty observation list
    and a known node_id, all three fields are deterministic.
    """
    layout = PURE_DRIFTER_LAYOUT

    pf = PFFloat(
        node_id="d05",
        layout=layout,
        initial_state_mean=np.zeros(layout.state_dim),
        initial_state_cov_diag=np.full(layout.state_dim, 0.1),
        onboard_map=make_test_map(),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=PFFloatConfig(n_particles=50),
        rng=make_rng(seed=42),
    )

    record = pf.step(dt_sec=1.0, observations=[], t=42, t_sec=42.0)

    assert isinstance(record, PFEstimateRecord)
    assert record.t == 42
    assert record.t_sec == 42.0
    assert record.node_id == "d05"


def test_estimate_record_has_no_particles_or_weights_attribute(make_rng):
    """Task 23.4 / Spec scenario "Estimate record carries summary, not
    raw particles".

    The Batch A ``PFEstimateRecord`` is a summary-only record —
    particle-level state lives in the sidecar ``ParticleRecord``. A
    consumer reading the main estimate stream MUST NOT find
    ``particles`` or ``weights`` on the returned object (those names
    would imply the summary stream carries the cloud and confuse
    downstream code about which stream to read).
    """
    layout = PURE_DRIFTER_LAYOUT

    pf = make_pf_at_origin(
        layout=layout,
        n_particles=20,
        rng=make_rng(seed=42),
    )

    record = pf.estimate(t=0, t_sec=0.0)

    assert isinstance(record, PFEstimateRecord)
    assert not hasattr(record, "particles"), (
        "PFEstimateRecord must not expose 'particles' — particle-level "
        "data belongs in the sidecar ParticleRecord stream"
    )
    assert not hasattr(record, "weights"), (
        "PFEstimateRecord must not expose 'weights' — particle-level "
        "data belongs in the sidecar ParticleRecord stream"
    )


# ---------------------------------------------------------------------------
# Section 25 — Per-node independence
# ---------------------------------------------------------------------------


def test_two_pf_instances_with_identical_init_and_rng_produce_identical_state(make_rng):
    """Task 25.1 / Spec scenario "Two PF instances with identical init
    + RNG remain identical after one step".

    Per-node PF independence is the contract that makes the M1 fleet
    safe to scale: each node owns its own RNG, particle array, and
    weight vector — no cross-node aliasing. The strongest observable
    form of that property is determinism: two PFs with identical
    construction inputs and identically-seeded RNGs, given the same
    observations, produce byte-identical state through one full
    ``step``.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 100

    common_kwargs = dict(
        node_id="d00",
        layout=layout,
        initial_state_mean=np.zeros(state_dim),
        initial_state_cov_diag=np.full(state_dim, 0.1),
        onboard_map=make_test_map(),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=PFFloatConfig(n_particles=n_particles),
    )

    pf1 = PFFloat(rng=make_rng(seed=999), **common_kwargs)
    pf2 = PFFloat(rng=make_rng(seed=999), **common_kwargs)

    gps_lat, gps_lon = enu_to_latlon(
        0.0, 0.0, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON
    )
    gps_obs = GPSObservation(
        t_sec=0.0,
        node_id="d00",
        lat_deg=float(gps_lat),
        lon_deg=float(gps_lon),
        noise_sigma_m=5.0,
    )

    pf1.step(dt_sec=1.0, observations=[gps_obs], t=0, t_sec=0.0)
    pf2.step(dt_sec=1.0, observations=[gps_obs], t=0, t_sec=0.0)

    assert np.array_equal(pf1.particles, pf2.particles), (
        "two PFs with identical init + RNG seed + observations must "
        "produce byte-identical particle arrays after one step"
    )
    assert np.array_equal(pf1.weights, pf2.weights), (
        "two PFs with identical init + RNG seed + observations must "
        "produce byte-identical weight vectors after one step"
    )


def test_modifying_one_pf_does_not_affect_another(make_rng):
    """Task 25.2 / Spec scenario "Per-node particle arrays are not
    aliased".

    Per-node independence requires that two ``PFFloat`` instances do
    NOT share underlying numpy buffers — even when constructed with
    identical inputs. Two failure modes this test catches:

    1. Direct buffer aliasing: mutating ``pf1._particles[i, j]``
       changes ``pf2._particles[i, j]`` (would happen if the
       constructor caches a class-level array, or if ``np.tile`` /
       ``np.broadcast_to`` is used to construct the cloud without an
       explicit copy).
    2. Indirect coupling through the predict path: calling
       ``pf1.predict`` mutates ``pf2._particles``. Same root cause —
       any shared buffer that ``predict`` writes through.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 50

    common_kwargs = dict(
        node_id="d00",
        layout=layout,
        initial_state_mean=np.zeros(state_dim),
        initial_state_cov_diag=np.full(state_dim, 0.1),
        onboard_map=make_test_map(),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=PFFloatConfig(n_particles=n_particles),
    )

    pf1 = PFFloat(rng=make_rng(seed=999), **common_kwargs)
    pf2 = PFFloat(rng=make_rng(seed=999), **common_kwargs)

    # Sanity: pre-mutation, the two are equal (same RNG + init).
    assert np.array_equal(pf1.particles, pf2.particles)

    # Direct mutation — must not propagate.
    pf1._particles[0, 0] = 9999.0
    assert pf2._particles[0, 0] != 9999.0, (
        "mutating pf1._particles must not change pf2._particles — "
        "the two arrays share an underlying numpy buffer"
    )

    # Capture pf2 state before pf1.predict — must be unchanged after.
    pf2_particles_snapshot = pf2.particles.copy()

    pf1.predict(dt_sec=60.0)

    assert np.array_equal(pf2.particles, pf2_particles_snapshot), (
        "calling pf1.predict must not mutate pf2.particles — the "
        "predict path writes through a buffer aliased to pf2"
    )
