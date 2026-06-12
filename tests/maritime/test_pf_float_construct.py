"""PFFloat construction contract tests.

Tests in this file were extracted from the original 2305-LOC
``test_pf_float.py`` as part of the post-implementation simplify pass.
Shared fixtures live in ``tests/maritime/_pf_float_helpers.py``.
"""

from __future__ import annotations

import numpy as np
import pytest

from rtl.vectors.maritime.pf_float import PFFloat, PFFloatConfig
from rtl.vectors.maritime.state_layout import (
    BALLAST_DRIFTER_LAYOUT,
    StateField,
    StateLayout,
)

from tests.maritime._pf_float_helpers import (
    TEST_ENU_ORIGIN_LAT,
    TEST_ENU_ORIGIN_LON,
    anchor_positions_default,
    make_test_map,
)


def test_construct_with_valid_inputs_succeeds(make_rng):
    """Task 13.1: construction with valid layout, initial mean + cov_diag,
    onboard map, anchor positions, config, and rng succeeds without
    raising; ``n_particles`` round-trips from the config."""
    layout = BALLAST_DRIFTER_LAYOUT
    state_dim = layout.state_dim  # 21

    initial_state_mean = np.zeros(state_dim)
    initial_state_cov_diag = np.full(state_dim, 0.1)

    pf = PFFloat(
        node_id="d00",
        layout=layout,
        initial_state_mean=initial_state_mean,
        initial_state_cov_diag=initial_state_cov_diag,
        onboard_map=make_test_map(),
        anchor_positions=anchor_positions_default(),
        enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
        enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
        config=PFFloatConfig(n_particles=100),
        rng=make_rng(seed=42),
    )

    assert pf.n_particles == 100


def test_construct_initial_mean_shape_mismatch_raises(make_rng):
    """Task 13.2: an ``initial_state_mean`` whose shape does not match
    ``(layout.state_dim,)`` is rejected with ``ValueError``."""
    layout = BALLAST_DRIFTER_LAYOUT  # state_dim = 21

    bad_mean = np.zeros(5)  # wrong length
    cov_diag = np.full(layout.state_dim, 0.1)

    with pytest.raises(ValueError):
        PFFloat(
            node_id="d00",
            layout=layout,
            initial_state_mean=bad_mean,
            initial_state_cov_diag=cov_diag,
            onboard_map=make_test_map(),
            anchor_positions=anchor_positions_default(),
            enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
            enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
            config=PFFloatConfig(n_particles=100),
            rng=make_rng(seed=42),
        )


def test_construct_negative_cov_diag_raises(make_rng):
    """Task 13.3: an ``initial_state_cov_diag`` containing a negative
    entry is rejected with ``ValueError`` — variances must be
    non-negative."""
    layout = BALLAST_DRIFTER_LAYOUT
    state_dim = layout.state_dim

    cov_diag = np.full(state_dim, 0.1)
    cov_diag[3] = -0.01  # one negative entry

    with pytest.raises(ValueError):
        PFFloat(
            node_id="d00",
            layout=layout,
            initial_state_mean=np.zeros(state_dim),
            initial_state_cov_diag=cov_diag,
            onboard_map=make_test_map(),
            anchor_positions=anchor_positions_default(),
            enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
            enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
            config=PFFloatConfig(n_particles=100),
            rng=make_rng(seed=42),
        )


def test_particle_array_initialized_correctly(make_rng):
    """Task 13.4: after construction, ``particles`` has shape
    ``(n_particles, state_dim)`` with finite entries, and ``weights``
    is uniform at ``1 / n_particles``."""
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

    assert pf.particles.shape == (n_particles, state_dim)
    assert np.isfinite(pf.particles).all()

    assert pf.weights.shape == (n_particles,)
    assert np.allclose(pf.weights, 1.0 / n_particles)


def test_config_rejects_non_positive_n_particles():
    """``PFFloatConfig.__post_init__`` raises ``ValueError`` for
    ``n_particles=0`` (the bootstrap PF is undefined without particles)."""
    with pytest.raises(ValueError, match=r"n_particles"):
        PFFloatConfig(n_particles=0)


def test_config_rejects_negative_process_noise():
    """``PFFloatConfig.__post_init__`` raises ``ValueError`` when any
    process-noise scale is negative — variances must be non-negative."""
    with pytest.raises(ValueError, match=r"process_noise"):
        PFFloatConfig(process_noise_pos_m_per_sqrt_s=-1.0)


def test_construct_unsupported_layout_class_name_raises(make_rng):
    """``PFFloat.__init__`` raises ``ValueError`` when
    ``layout.class_name`` is not one of the three M1 classes (anchor,
    pure_drifter, ballast_drifter). Constructor-level rejection prevents
    the dispatch in ``predict`` from ever reaching its defensive fallback.
    """
    fake_layout = StateLayout(
        class_name="unknown",
        fields=(
            StateField("east_m", "m", "east position"),
            StateField("north_m", "m", "north position"),
            StateField("depth_m", "m", "depth"),
        ),
        groups={"position": slice(0, 3)},
    )
    state_dim = fake_layout.state_dim
    with pytest.raises(ValueError, match=r"Unsupported layout.class_name|unknown"):
        PFFloat(
            node_id="x00",
            layout=fake_layout,
            initial_state_mean=np.zeros(state_dim),
            initial_state_cov_diag=np.full(state_dim, 0.1),
            onboard_map=make_test_map(),
            anchor_positions=anchor_positions_default(),
            enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
            enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
            config=PFFloatConfig(n_particles=10),
            rng=make_rng(seed=42),
        )


def test_construct_cov_diag_shape_mismatch_raises(make_rng):
    """``PFFloat.__init__`` raises ``ValueError`` when
    ``initial_state_cov_diag`` has the wrong shape — separate branch
    from the analogous mean-shape check (covered by
    ``test_construct_initial_mean_shape_mismatch_raises``)."""
    layout = BALLAST_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    with pytest.raises(ValueError, match=r"initial_state_cov_diag"):
        PFFloat(
            node_id="d00",
            layout=layout,
            initial_state_mean=np.zeros(state_dim),
            initial_state_cov_diag=np.full(5, 0.1),  # wrong length
            onboard_map=make_test_map(),
            anchor_positions=anchor_positions_default(),
            enu_origin_lat_deg=TEST_ENU_ORIGIN_LAT,
            enu_origin_lon_deg=TEST_ENU_ORIGIN_LON,
            config=PFFloatConfig(n_particles=100),
            rng=make_rng(seed=42),
        )

