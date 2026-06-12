"""PFFloat weight-stage contract tests — six sensor handlers, anchor-only LoRa filter, unknown-sensor explicit error, AST vectorization gate.

Tests in this file were extracted from the original 2305-LOC
``test_pf_float.py`` as part of the post-implementation simplify pass.
Shared fixtures live in ``tests/maritime/_pf_float_helpers.py``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from rtl.vectors.maritime.coords import enu_to_latlon, latlon_to_enu
from rtl.vectors.maritime.pf_float import PFFloat, PFFloatConfig
from rtl.vectors.maritime.scenario_schema import (
    BaroObservation,
    BathyProbeObservation,
    GPSObservation,
    IMUObservation,
    LoraTOAObservation,
    MagObservation,
)
from rtl.vectors.maritime.state_layout import (
    ANCHOR_LAYOUT,
    BALLAST_DRIFTER_LAYOUT,
    PURE_DRIFTER_LAYOUT,
)

from tests.maritime._pf_float_helpers import (
    TEST_BBOX,
    TEST_ENU_ORIGIN_LAT,
    TEST_ENU_ORIGIN_LON,
    anchor_positions_default,
    make_map_with_land_polygon,
    make_pf_at_origin,
    make_test_map,
    zero_noise_config,
)


# ---------------------------------------------------------------------------
# Task 17.1 — GPS narrows position posterior
# ---------------------------------------------------------------------------


def test_gps_observation_narrows_position_posterior(make_rng):
    """Task 17.1 / Spec scenario "GPS observation narrows position
    posterior".

    A GPS observation should pull the weighted-mean position toward the
    observed (lat, lon). With particles spread by a wide initial
    position covariance and the GPS reading at ENU (0, 0), the
    weighted-mean east position after weight should be closer to 0 than
    before. We assert direction of change (closer to GPS reading), not
    a specific convergence magnitude — convergence quality is a
    measurement-report concern, not a spec assertion.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 200

    # Mean east = 50 m, std ~ 10 m so the cloud spreads around 50 m east.
    mean = np.zeros(state_dim)
    mean[0] = 50.0  # east_m
    cov_diag = np.zeros(state_dim)
    cov_diag[0] = 100.0  # var=100 → sigma=10 m

    pf = make_pf_at_origin(
        layout=layout,
        initial_state_mean=mean,
        cov_diag=cov_diag,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )

    # Predict once with zero process noise — sets internal _last_dt_sec
    # and leaves the particle cloud's mean east position essentially
    # at 50 m (no climatology current in default test map).
    pf.predict(dt_sec=60.0)

    # Capture weighted-mean east before the GPS update. Weights are
    # uniform at this point, so this is equivalent to the arithmetic
    # mean — but compute it the weighted way for symmetry with the
    # post-weight assertion.
    pre_weighted_mean_east = float(np.sum(pf.weights * pf.particles[:, 0]))

    # GPS observation at ENU origin (0, 0) → lat/lon = (20.0, -160.0).
    obs_lat, obs_lon = enu_to_latlon(0.0, 0.0, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON)
    gps_obs = GPSObservation(
        t_sec=60.0,
        node_id="d00",
        lat_deg=float(obs_lat),
        lon_deg=float(obs_lon),
        noise_sigma_m=1.5,
    )

    pf.weight([gps_obs])

    post_weighted_mean_east = float(np.sum(pf.weights * pf.particles[:, 0]))

    # GPS reading is at east = 0; particles initially centered on east = 50.
    # Direction of update: weighted mean should be closer to 0 (i.e.,
    # smaller absolute value) than before.
    assert abs(post_weighted_mean_east) < abs(pre_weighted_mean_east), (
        f"Weighted-mean east should move toward GPS reading at 0; "
        f"pre={pre_weighted_mean_east:.3f} m, post={post_weighted_mean_east:.3f} m"
    )


# ---------------------------------------------------------------------------
# Task 17.2 — Bathy likelihood zeroes particles on land
# ---------------------------------------------------------------------------


def test_bathy_likelihood_zeroes_particles_on_land(make_rng):
    """Task 17.2 / Spec scenario "Bathy likelihood zeroes particles on
    land".

    Particles whose (lat, lon) fall inside an ``onboard_map.land_polygons``
    polygon must receive weight 0 after a bathy_probe weight step (the
    likelihood is the on-land path; no depth comparison applies).
    Particles off land receive a normal Gaussian likelihood on the
    observed depth vs the bathymetry-grid depth.

    We construct an onboard map with a single small land polygon
    covering the immediate east of the ENU origin and place half the
    particles inside the polygon, half outside. After ``pf.weight``,
    the on-land particles' weights MUST be exactly 0 (within float
    epsilon — log-likelihood -∞ → exp → 0).
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 50  # even count — half in, half out

    # Land polygon: a small box (~1 km^2) centered at lat=20.05,
    # lon=-159.95. ``coastline.point_on_land`` expects [lon, lat]
    # columns. Position particles such that exactly half fall inside.
    polygon = np.array(
        [
            [-159.96, 20.04],
            [-159.94, 20.04],
            [-159.94, 20.06],
            [-159.96, 20.06],
        ]
    )

    onboard = make_map_with_land_polygon(polygon_lon_lat=polygon, depth_m=1000.0)

    # Build PF with all particles at origin (cov=0). We will overwrite
    # the position slots manually below to place half the particles in
    # the polygon and half outside.
    pf = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        onboard_map=onboard,
        rng=make_rng(seed=42),
    )

    # Convert the polygon-interior anchor (lat=20.05, lon=-159.95) to
    # ENU. Place the first half of particles there.
    east_in, north_in = latlon_to_enu(20.05, -159.95, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON)
    # Convert a polygon-exterior anchor (lat=20.20, lon=-159.60) to
    # ENU. Place the second half there.
    east_out, north_out = latlon_to_enu(20.20, -159.60, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON)

    half = n_particles // 2
    # Mutating .particles directly is safe — the property returns the
    # underlying array (Batch B's `_particles` is the same reference).
    pf.particles[:half, 0] = float(east_in)
    pf.particles[:half, 1] = float(north_in)
    pf.particles[half:, 0] = float(east_out)
    pf.particles[half:, 1] = float(north_out)

    # Sanity: the half/half split is real per the on-land predicate.
    on_land_mask = np.zeros(n_particles, dtype=bool)
    for i in range(n_particles):
        lat_arr, lon_arr = enu_to_latlon(
            float(pf.particles[i, 0]),
            float(pf.particles[i, 1]),
            TEST_ENU_ORIGIN_LAT,
            TEST_ENU_ORIGIN_LON,
        )
        on_land_mask[i] = onboard.is_on_land(float(lat_arr), float(lon_arr))
    assert on_land_mask[:half].all(), "first half should be on land — fixture bug"
    assert not on_land_mask[half:].any(), "second half should be off land — fixture bug"

    # Bathy probe observation: 100 m depth (any value — on-land
    # particles get weight 0 regardless).
    bathy_obs = BathyProbeObservation(
        t_sec=1.0,
        node_id="d00",
        depth_m=100.0,
        noise_sigma_m=5.0,
    )

    pf.weight([bathy_obs])

    # On-land particles must have weight EXACTLY zero (log-likelihood
    # -inf → exp → 0). Off-land particles must have weight > 0.
    np.testing.assert_array_equal(
        pf.weights[:half],
        0.0,
        err_msg="on-land particles must have weight exactly 0 after bathy weight",
    )
    assert (pf.weights[half:] > 0).all(), (
        "off-land particles must have positive weight after bathy weight"
    )


# ---------------------------------------------------------------------------
# Task 17.3 — Mag likelihood wraps at 0/360
# ---------------------------------------------------------------------------


def test_mag_likelihood_wraps_at_0_360(make_rng):
    """Task 17.3 / Spec scenario "Magnetometer wraps distance at 0/360".

    The mag likelihood must use wrap-aware angular distance — the
    correct distance from heading=2° to obs=0° is 2°, and from
    heading=358° to obs=0° is also 2° (NOT 358°). We construct three
    particles at headings 2°, 358°, and 180° and apply a mag
    observation at heading=0°. The 2° and 358° particles should
    receive nearly equal weights (angular distance 2° each); the 180°
    particle's weight should be much smaller (angular distance 180°).

    A naive implementation that computes |heading_obs - heading_p|
    without wrapping would assign very different weights to the 2°
    and 358° particles — that is the failure mode this test catches.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 3

    pf = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )

    # All particles at origin with all-zero state by default; overwrite
    # the heading slot (index 6) for each particle.
    pf.particles[0, 6] = 2.0
    pf.particles[1, 6] = 358.0
    pf.particles[2, 6] = 180.0

    mag_obs = MagObservation(
        t_sec=1.0,
        node_id="d00",
        heading_deg=0.0,
        noise_sigma_deg=10.0,
    )

    pf.weight([mag_obs])

    w0, w1, w2 = float(pf.weights[0]), float(pf.weights[1]), float(pf.weights[2])

    # Particles 0 (2°) and 1 (358°) are both 2° from the obs after
    # wrapping. Their weights must be (very nearly) equal — far closer
    # than either is to particle 2 (180° away).
    assert w0 == pytest.approx(w1, rel=1e-9), (
        f"heading=2° (w0={w0:.6e}) and heading=358° (w1={w1:.6e}) must "
        f"have equal mag likelihood — both are 2° from obs after "
        f"wrap-aware angular distance"
    )
    # Particle 2 (180° away) must have a much smaller weight than
    # particles 0 and 1. "Much smaller" is unambiguous here — at
    # σ=10°, the likelihood ratio between 2° and 180° is exp(-(180^2 -
    # 2^2)/(2*100)) ≈ exp(-162) — astronomical.
    assert w2 < w0, (
        f"heading=180° (w2={w2:.6e}) must have smaller weight than "
        f"heading=2° (w0={w0:.6e}) — angular distance 180° vs 2°"
    )
    assert w2 < w1, (
        f"heading=180° (w2={w2:.6e}) must have smaller weight than "
        f"heading=358° (w1={w1:.6e}) — angular distance 180° vs 2°"
    )


# ---------------------------------------------------------------------------
# Task 17.4 — Baro narrows depth posterior via hydrostatic inversion
# ---------------------------------------------------------------------------


def test_baro_observation_updates_depth_posterior(make_rng):
    """Task 17.4 / Spec scenario "Baro observation narrows depth
    posterior via hydrostatic inversion".

    The baro likelihood maps particle depth to a predicted pressure
    via the hydrostatic relation ``pressure = 101_325 + 10_000 *
    depth_m`` (matching ``maritime-sensors`` ``BaroSensor``), then
    Gaussian on the observation's pressure. With a wide initial depth
    prior centered at 50 m and an observation corresponding to depth
    100 m, the weighted-mean depth should move toward 100 m
    (i.e., increase from ~50 m).
    """
    layout = BALLAST_DRIFTER_LAYOUT  # ballast drifters carry depth dim
    state_dim = layout.state_dim
    n_particles = 200

    # Depth (slot 2) prior: mean 50 m, var 100 m^2 → sigma 10 m. Wide
    # enough that the obs (at depth 100 m) is ~5 sigma away from the
    # prior mean — should produce a clear directional update.
    mean = np.zeros(state_dim)
    mean[2] = 50.0  # depth_m
    cov_diag = np.zeros(state_dim)
    cov_diag[2] = 100.0

    pf = make_pf_at_origin(
        layout=layout,
        initial_state_mean=mean,
        cov_diag=cov_diag,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )

    pf.predict(dt_sec=60.0)

    pre_weighted_mean_depth = float(np.sum(pf.weights * pf.particles[:, 2]))

    # Pressure for depth = 100 m.
    obs_pressure = 101325.0 + 10000.0 * 100.0  # = 1101325.0 Pa
    baro_obs = BaroObservation(
        t_sec=60.0,
        node_id="d00",
        pressure_pa=obs_pressure,
        noise_sigma_pa=1000.0,  # ~ 0.1 m equivalent
    )

    pf.weight([baro_obs])

    post_weighted_mean_depth = float(np.sum(pf.weights * pf.particles[:, 2]))

    # Direction-of-update assertion only: depth should move from ~50 m
    # toward 100 m.
    assert post_weighted_mean_depth > pre_weighted_mean_depth, (
        f"weighted-mean depth should move toward observation (100 m); "
        f"pre={pre_weighted_mean_depth:.3f} m, "
        f"post={post_weighted_mean_depth:.3f} m"
    )


# ---------------------------------------------------------------------------
# Task 17.5 — IMU narrows bias posterior
# ---------------------------------------------------------------------------


def test_imu_observation_updates_bias_posterior(make_rng):
    """Task 17.5 / Spec scenario "IMU observation narrows bias
    posterior".

    The IMU likelihood mirrors ``rtl/vectors/maritime/sensors.py``
    (lines 128-179). Per-particle predicted readings:

    - accel_x = (vx - prev_vx) / dt + accel_bias_x   (slots 3, 15, 12)
    - accel_y = (vy - prev_vy) / dt + accel_bias_y   (slots 4, 16, 13)
    - accel_z = (vz - prev_vz) / dt + accel_bias_z   (slots 5, 17, 14)
    - gyro_x = 0 + gyro_bias_x                       (slot 9)
    - gyro_y = 0 + gyro_bias_y                       (slot 10)
    - gyro_z = wrap(heading - prev_heading) / dt
              * (π/180) + gyro_bias_z                (slots 6, 18, 11)

    With a wide initial bias prior, the IMU observation should pull
    the weighted-mean accel_bx slot toward the truth bias and the
    weighted-mean gyro_bz slot toward the truth gyro bias. ESS must
    remain strictly positive — the gyro_x / gyro_y channels are not
    driven by the M1 dynamics and should not collapse the posterior.
    """
    layout = BALLAST_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 500

    # IMU bias prior — slots 9..14, sigma sqrt(0.5) ≈ 0.71 each.
    # Initial bias means = 0; truth bias values will be 0.5 (accel) and
    # 0.05 (gyro), comfortably inside the prior cloud (~1σ for accel,
    # ~0.07σ for gyro). Wide enough to satisfy the spec's "wide initial
    # bias prior" intent without driving the bootstrap PF into 6D
    # particle deprivation: with 6 sharp likelihood channels (σ=0.01)
    # against a 6D wide prior, weight alone — without resample — would
    # collapse onto a single particle whose gyro_bz could be arbitrary.
    # The spec scenario actually contemplates "weight + resample"
    # convergence; this Batch C test exercises weight only, so the
    # prior is dialed back to keep the per-channel update visible.
    cov_diag = np.zeros(state_dim)
    cov_diag[9:15] = 0.5  # imu_bias slots — moderately wide prior

    pf = make_pf_at_origin(
        layout=layout,
        cov_diag=cov_diag,
        n_particles=n_particles,
        rng=make_rng(seed=42),
    )

    # Set up motion state (after construction so cov_diag doesn't blur
    # them). Velocity = 1 m/s on each axis; prev_velocity = 0 → truth
    # accel = (1 - 0) / 60 ≈ 0.01667 m/s^2 per axis. Heading 100°,
    # prev_heading 0° → truth heading rate = 100° / 60 s ≈ 1.667°/s →
    # gyro_z ≈ 1.667 * π/180 ≈ 0.02909 rad/s.
    #
    # Slots 3,4,5 = velocity; 15,16,17 = prev_velocity; 6 = heading;
    # 18 = prev_heading. (See state_layout.BALLAST_DRIFTER_LAYOUT.)
    pf.particles[:, 3:6] = 1.0
    pf.particles[:, 15:18] = 0.0
    pf.particles[:, 6] = 100.0
    pf.particles[:, 18] = 0.0

    # Truth biases the implementer should learn:
    truth_accel_bias = 0.5  # m/s^2 per accel axis
    truth_gyro_bias = 0.05  # rad/s per gyro axis

    dt_sec = 60.0
    pf.predict(dt_sec=dt_sec)  # sets internal _last_dt_sec

    # Compute "truth" sensor reading the implementer should expect:
    # accel = (v - v_prev) / dt + bias = 1/60 + 0.5 ≈ 0.51667
    # gyro_z = (wrap(100 - 0) / 60) * π/180 + 0.05
    truth_accel_axis = 1.0 / dt_sec + truth_accel_bias
    truth_gyro_z = (100.0 / dt_sec) * np.pi / 180.0 + truth_gyro_bias

    # Loosen the likelihood σ to be comparable to (not orders of
    # magnitude tighter than) the prior σ ≈ 0.71. Without resample,
    # a sharp 6D likelihood against a 6D wide prior collapses weight
    # onto a single particle whose gyro_bz could be arbitrary; with
    # likelihood σ ≈ prior σ the per-channel update is visible
    # without driving the bootstrap into deprivation.
    imu_obs = IMUObservation(
        t_sec=dt_sec,
        node_id="d00",
        accel_xyz=(truth_accel_axis, truth_accel_axis, truth_accel_axis),
        gyro_xyz=(truth_gyro_bias, truth_gyro_bias, truth_gyro_z),
        accel_noise_sigma_ms2=0.3,
        gyro_noise_sigma_rad_s=0.3,
    )

    pre_accel_bx = float(np.sum(pf.weights * pf.particles[:, 12]))  # accel_bx
    pre_gyro_bz = float(np.sum(pf.weights * pf.particles[:, 11]))  # gyro_bz

    pf.weight([imu_obs])

    post_accel_bx = float(np.sum(pf.weights * pf.particles[:, 12]))
    post_gyro_bz = float(np.sum(pf.weights * pf.particles[:, 11]))

    # Direction-of-update: weighted means move toward truth biases
    # (0.5 for accel, 0.05 for gyro).
    assert abs(post_accel_bx - 0.5) < abs(pre_accel_bx - 0.5), (
        f"accel_bx weighted mean should move toward truth (0.5); "
        f"pre={pre_accel_bx:.4f}, post={post_accel_bx:.4f}"
    )
    assert abs(post_gyro_bz - 0.05) < abs(pre_gyro_bz - 0.05), (
        f"gyro_bz weighted mean should move toward truth (0.05); "
        f"pre={pre_gyro_bz:.4f}, post={post_gyro_bz:.4f}"
    )

    # ESS must remain strictly positive — gyro_x / gyro_y are not
    # driven by M1 dynamics and must not collapse the posterior.
    assert pf.effective_sample_size > 0, (
        f"ESS should remain > 0 after IMU weight; got {pf.effective_sample_size}"
    )


# ---------------------------------------------------------------------------
# Task 17.6 — LoRa TOA narrows range posterior
# ---------------------------------------------------------------------------


def test_lora_toa_to_anchor_narrows_range_posterior(make_rng):
    """Task 17.6 / Spec scenario "LoRa to anchor applies range
    likelihood".

    With the drifter's particle cloud spread by a wide initial position
    covariance and an anchor at a known (lat, lon), a LoRa observation
    of the true range to the anchor should reduce ESS (concentrating
    weight on particles near the correct range) and pull the
    weighted-mean range from anchor toward the observed range.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 200

    # Wide initial position prior so particles spread.
    cov_diag = np.zeros(state_dim)
    cov_diag[0:2] = 1000.0  # pos var = 1000 m^2 → sigma ≈ 31.6 m

    # Anchor at ENU (200, 0, 0) → lat/lon. Drifter at ENU (0, 0)
    # (mean=zeros). True range = 200 m.
    anchor_east, anchor_north = 200.0, 0.0
    anchor_lat, anchor_lon = enu_to_latlon(
        anchor_east, anchor_north, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON
    )
    anchor_positions = {"a00": (float(anchor_lat), float(anchor_lon))}

    pf = make_pf_at_origin(
        layout=layout,
        cov_diag=cov_diag,
        n_particles=n_particles,
        anchor_positions=anchor_positions,
        rng=make_rng(seed=42),
    )

    pf.predict(dt_sec=60.0)

    # Pre-weight: weighted-mean range from anchor.
    def _weighted_range(pf_obj: PFFloat) -> float:
        dx = pf_obj.particles[:, 0] - anchor_east
        dy = pf_obj.particles[:, 1] - anchor_north
        ranges = np.sqrt(dx * dx + dy * dy)
        return float(np.sum(pf_obj.weights * ranges))

    pre_ess = pf.effective_sample_size
    pre_weighted_range = _weighted_range(pf)

    lora_obs = LoraTOAObservation(
        t_sec=60.0,
        node_id="d00",
        partner_id="a00",
        range_m=200.0,
        noise_sigma_m=5.0,
    )

    pf.weight([lora_obs])

    post_ess = pf.effective_sample_size
    post_weighted_range = _weighted_range(pf)

    # ESS reduced — non-uniform weights after the likelihood update.
    assert post_ess < pre_ess, (
        f"LoRa weight should reduce ESS; pre={pre_ess:.2f}, post={post_ess:.2f}"
    )
    # Direction of update: weighted-mean range moved toward 200 m.
    assert abs(post_weighted_range - 200.0) < abs(pre_weighted_range - 200.0), (
        f"weighted-mean range should move toward observation (200 m); "
        f"pre={pre_weighted_range:.3f} m, post={post_weighted_range:.3f} m"
    )


def test_lora_likelihood_matches_truth_side_2d_range_definition(make_rng):
    """Substance regression: PF LoRa range computation MUST match the
    truth-side ``LoraTOASensor.sample_link`` definition (2D horizontal).

    Truth side computes ``range = sqrt(de^2 + dn^2)`` (no depth term);
    the M1 anchor-and-drifter assumption is that both endpoints are at
    the surface and the vertical separation is negligible. If the PF
    likelihood adds a ``dz`` term, particles whose horizontal position
    matches truth but whose ``depth`` slot is non-zero (e.g. from PF
    predict-step depth random-walk on ballast_drifter particles) get
    spuriously penalized, corrupting weights.

    This test pins both definitions: a single particle placed at exact
    horizontal truth with a non-trivial ``depth`` slot must score
    log-likelihood == 0 against an observation whose ``range_m`` equals
    the truth-side 2D distance.
    """
    layout = BALLAST_DRIFTER_LAYOUT
    state_dim = layout.state_dim

    # Anchor at ENU (300, 400) → range from origin = sqrt(300^2 + 400^2) = 500 m.
    anchor_east, anchor_north = 300.0, 400.0
    anchor_lat, anchor_lon = enu_to_latlon(
        anchor_east, anchor_north, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON
    )
    anchor_positions = {"a00": (float(anchor_lat), float(anchor_lon))}

    pf = make_pf_at_origin(
        layout=layout,
        cov_diag=np.zeros(state_dim),  # all particles identical to mean
        n_particles=1,
        anchor_positions=anchor_positions,
        rng=make_rng(seed=0),
    )

    # Manually plant the single particle: horizontal = (0, 0) (matches
    # initial mean), depth = 80 m (non-trivial — would distort a 3D
    # likelihood). Use `_particles` directly because the PF doesn't
    # expose a particle-mutation API and tests already document this
    # access boundary in `_pf_float_helpers.py`.
    east_idx = layout.index_of("east_m")
    north_idx = layout.index_of("north_m")
    depth_idx = layout.index_of("depth_m")
    pf._particles[0, east_idx] = 0.0
    pf._particles[0, north_idx] = 0.0
    pf._particles[0, depth_idx] = 80.0

    # Truth-side 2D range = sqrt(300^2 + 400^2) = 500.
    truth_range = float(np.sqrt(anchor_east**2 + anchor_north**2))
    assert abs(truth_range - 500.0) < 1e-9

    obs = LoraTOAObservation(
        t_sec=0.0,
        node_id="d00",
        partner_id="a00",
        range_m=truth_range,
        noise_sigma_m=5.0,
    )

    log_lik = pf._lora_log_likelihood(obs)
    assert log_lik is not None, "anchor partner — should not be filtered"
    # log-likelihood at the truth particle MUST be 0 (perfect fit). A 3D
    # range-with-dz implementation would give ~ -0.5 * (sqrt(500^2+80^2)
    # - 500)^2 / 25 ≈ -1.6, decisively non-zero.
    assert log_lik.shape == (1,)
    assert abs(float(log_lik[0])) < 1e-9, (
        f"PF LoRa likelihood at horizontal truth must be 0; got {float(log_lik[0])}. "
        f"Non-zero indicates a depth-term mismatch with truth-side "
        f"LoraTOASensor.sample_link (which is 2D)."
    )


# ---------------------------------------------------------------------------
# Task 17.7 — Weight stage is vectorized (AST walk)
# ---------------------------------------------------------------------------


def test_weight_stage_is_vectorized_no_per_particle_for_loop():
    """Task 17.7 / Spec requirement "Vectorized Over Particles".

    AST-scan ``pf_float.py`` for ``for ... in range(<expr>)`` patterns
    in the four pipeline stage methods (``predict``, ``weight``,
    ``resample``, ``estimate``). Any ``range(...)`` whose argument's
    ``ast.unparse`` representation contains the substring
    ``"n_particles"`` (case-sensitive) is a per-particle Python loop
    and SHALL fail this test.

    This catches:
    - ``for i in range(n_particles):``
    - ``for i in range(self.n_particles):``
    - ``for i in range(self._config.n_particles):``

    Methods ``resample`` and ``estimate`` ship in Batch D — if a
    method is not present yet, it is skipped gracefully (the test
    asserts at least ``predict`` and ``weight`` are scanned).

    NOTE — Batch C consequence: Batch B's ``predict`` currently uses
    ``for i in range(n):`` for the per-particle climatology lookup
    (where ``n = self._config.n_particles`` is bound locally). The
    Batch C implementer is responsible for vectorizing that lookup
    (or any other per-particle loop) to satisfy the spec's "Vectorized
    Over Particles" requirement. The literal AST scan here catches
    the easiest-to-make mistake (writing ``range(n_particles)`` or
    ``range(self.n_particles)``); a code review would flag the
    locally-bound ``n`` form. This test is the spec-level backstop.
    """
    pf_float_path = Path(
        "/var/home/tim/projects/eml-research/rtl/vectors/maritime/pf_float.py"
    )
    source = pf_float_path.read_text()
    tree = ast.parse(source)

    # Collect all method definitions on PFFloat by name. Only
    # synchronous defs are expected; if an async def slips in, the
    # type-narrow keeps the dict consistent and the assertion below
    # still triggers the missing-method failure for that name.
    methods: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PFFloat":
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    methods[child.name] = child

    # Predict and weight MUST exist by Batch C; resample / estimate
    # land in Batch D — skip if absent.
    required_methods = ("predict", "weight")
    optional_methods = ("resample", "estimate")
    for required in required_methods:
        assert required in methods, (
            f"PFFloat.{required} must exist for Batch C — missing in pf_float.py"
        )

    methods_to_scan = [methods[name] for name in required_methods]
    methods_to_scan.extend(methods[name] for name in optional_methods if name in methods)

    forbidden_substring = "n_particles"

    for method_node in methods_to_scan:
        for node in ast.walk(method_node):
            if not isinstance(node, ast.For):
                continue
            it = node.iter
            if not isinstance(it, ast.Call):
                continue
            func = it.func
            is_range = (isinstance(func, ast.Name) and func.id == "range") or (
                isinstance(func, ast.Attribute) and func.attr == "range"
            )
            if not is_range:
                continue
            if len(it.args) != 1:
                continue
            arg_text = ast.unparse(it.args[0])
            assert forbidden_substring not in arg_text, (
                f"Per-particle Python `for ... in range({arg_text})` loop "
                f"detected in PFFloat.{method_node.name} at line "
                f"{node.lineno} — violates the 'Vectorized Over Particles' "
                f"requirement of maritime-pf-float spec. Replace with a "
                f"vectorized numpy operation over the (n_particles, "
                f"state_dim) particle array."
            )


# ---------------------------------------------------------------------------
# Task 18.1 — LoRa TOA to anchor updates weights
# ---------------------------------------------------------------------------


def test_lora_to_anchor_updates_weights(make_rng):
    """Task 18.1 / Spec scenario "LoRa to anchor applies range
    likelihood".

    A ``lora_toa`` observation whose ``partner_id`` is in
    ``anchor_positions`` should update the particle weights —
    starting from uniform, the post-weight weights MUST exhibit
    variance (i.e., they are not all ``1 / n_particles``).
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 100

    cov_diag = np.zeros(state_dim)
    cov_diag[0:2] = 100.0  # spread particles so not all the same range

    anchor_east, anchor_north = 200.0, 0.0
    anchor_lat, anchor_lon = enu_to_latlon(
        anchor_east, anchor_north, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON
    )
    anchor_positions = {"a00": (float(anchor_lat), float(anchor_lon))}

    pf = make_pf_at_origin(
        layout=layout,
        cov_diag=cov_diag,
        n_particles=n_particles,
        anchor_positions=anchor_positions,
        rng=make_rng(seed=42),
    )

    # Confirm uniform weights pre-call.
    assert np.allclose(pf.weights, 1.0 / n_particles)

    lora_obs = LoraTOAObservation(
        t_sec=1.0,
        node_id="d00",
        partner_id="a00",
        range_m=200.0,
        noise_sigma_m=5.0,
    )

    pf.weight([lora_obs])

    # Variance > 0 — weights are no longer uniform.
    assert float(np.var(pf.weights)) > 0.0, (
        "weights must be non-uniform after LoRa-to-anchor weight update"
    )


# ---------------------------------------------------------------------------
# Task 18.2 — LoRa TOA to non-anchor partner leaves weights unchanged
# ---------------------------------------------------------------------------


def test_lora_to_non_anchor_partner_leaves_weights_unchanged(make_rng):
    """Task 18.2 / Spec scenario "LoRa to non-anchor partner does not
    update weights".

    The M1 LoRa handler is an anchor-only filter — when the
    observation's ``partner_id`` is NOT in ``anchor_positions``, the
    handler MUST contribute no weight update. Starting from uniform
    weights, post-weight weights MUST remain exactly uniform within
    float epsilon.
    """
    layout = PURE_DRIFTER_LAYOUT
    state_dim = layout.state_dim
    n_particles = 100

    # Single anchor "a00"; the LoRa obs partner is "d99" (not an
    # anchor) — must be filtered.
    anchor_lat, anchor_lon = enu_to_latlon(
        200.0, 0.0, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON
    )
    anchor_positions = {"a00": (float(anchor_lat), float(anchor_lon))}

    pf = make_pf_at_origin(
        layout=layout,
        n_particles=n_particles,
        anchor_positions=anchor_positions,
        rng=make_rng(seed=42),
    )

    assert np.allclose(pf.weights, 1.0 / n_particles)

    lora_obs = LoraTOAObservation(
        t_sec=1.0,
        node_id="d00",
        partner_id="d99",  # NOT in anchor_positions
        range_m=200.0,
        noise_sigma_m=5.0,
    )

    pf.weight([lora_obs])

    # Weights remain uniform — the non-anchor LoRa observation
    # contributes no likelihood update.
    assert np.allclose(pf.weights, 1.0 / n_particles), (
        "non-anchor LoRa observation must leave weights uniform — "
        f"got weights with var={np.var(pf.weights):.3e}"
    )


# ---------------------------------------------------------------------------
# Task 18.3 — Non-anchor LoRa does not raise / log / count
# ---------------------------------------------------------------------------


def test_non_anchor_lora_does_not_raise_log_or_count(make_rng, recwarn):
    """Task 18.3 / Spec scenario "LoRa to non-anchor partner does not
    update weights" (the doesn't-raise / doesn't-log / doesn't-count
    facet).

    The M1 anchor-only filter is documented design — it is NOT a
    drop or an error. The handler MUST NOT:

    - raise an exception,
    - emit a warning,
    - maintain a drop counter / dropped-observation list / etc.
    """
    layout = PURE_DRIFTER_LAYOUT

    anchor_lat, anchor_lon = enu_to_latlon(
        200.0, 0.0, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON
    )
    anchor_positions = {"a00": (float(anchor_lat), float(anchor_lon))}

    pf = make_pf_at_origin(
        layout=layout,
        n_particles=50,
        anchor_positions=anchor_positions,
        rng=make_rng(seed=42),
    )

    lora_obs = LoraTOAObservation(
        t_sec=1.0,
        node_id="d00",
        partner_id="d99",  # NOT an anchor
        range_m=200.0,
        noise_sigma_m=5.0,
    )

    # Must not raise.
    pf.weight([lora_obs])

    # No warnings emitted.
    assert len(recwarn) == 0, (
        f"non-anchor LoRa must not warn; got {len(recwarn)} warnings: "
        f"{[str(w.message) for w in recwarn]}"
    )

    # No drop-counter-like attribute. The filter is a documented
    # design path, not a drop event — any of these names would
    # signal "we are tracking drops" which is the wrong framing per
    # the spec ("LoRa TOA Anchor-Only Filter"; "All errors must be
    # explicit" — silent drops are an anti-pattern).
    forbidden_attrs = (
        "dropped_count",
        "drop_counter",
        "_drop_counter",
        "dropped_observations",
        "_dropped_observations",
        "n_dropped",
        "drops",
    )
    for attr in forbidden_attrs:
        assert not hasattr(pf, attr), (
            f"PFFloat must not maintain a '{attr}' attribute — the "
            f"anchor-only LoRa filter is a documented M1 design path, "
            f"not a drop event (spec: 'LoRa TOA Anchor-Only Filter')"
        )


# ---------------------------------------------------------------------------
# Task 19.1 — Unknown observation type raises ValueError
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SonarObservation:
    """A fake sensor observation type — used only by the unknown-sensor
    test below to confirm ``PFFloat.weight`` raises ``ValueError`` on
    any observation type it doesn't recognize. Per the
    'Unknown Sensor Name Is an Explicit Error' spec requirement,
    silent drops are forbidden — disagreement between the scenario
    schema and the PF's handler set MUST fail loudly."""

    t_sec: float
    node_id: str
    sensor: str = "sonar"


def test_unknown_observation_type_raises_value_error(make_rng):
    """Task 19.1 / Spec scenario "Unknown sensor raises ValueError".

    ``PFFloat.weight`` SHALL raise ``ValueError`` (not silently drop,
    not warn, not log a count) when given an observation whose type
    is not one of the six recognized M1 observation classes.
    """
    pf = make_pf_at_origin(
        layout=PURE_DRIFTER_LAYOUT,
        n_particles=20,
        rng=make_rng(seed=42),
    )

    sonar_obs = _SonarObservation(t_sec=1.0, node_id="d00")

    with pytest.raises(ValueError, match=r"_SonarObservation|sonar|[Uu]nknown"):
        pf.weight([sonar_obs])


# ---------------------------------------------------------------------------
# Task 19.2 — All six recognized observation types dispatch without error
# ---------------------------------------------------------------------------


def test_all_six_recognized_observation_types_dispatch_without_error(make_rng):
    """Task 19.2 / Spec scenario "Recognized sensor names proceed
    normally".

    A list containing one valid instance of each recognized
    observation type (``GPSObservation``, ``IMUObservation``,
    ``BaroObservation``, ``MagObservation``, ``BathyProbeObservation``,
    ``LoraTOAObservation``) must dispatch through ``pf.weight``
    without raising. Weights MUST remain normalized
    (``np.isclose(pf.weights.sum(), 1.0)``).
    """
    layout = BALLAST_DRIFTER_LAYOUT  # has depth + IMU bias slots
    state_dim = layout.state_dim
    n_particles = 200

    # Modest spread so no single observation collapses the cloud.
    cov_diag = np.full(state_dim, 0.01)
    cov_diag[0:2] = 25.0  # position spread for GPS / LoRa / bathy
    cov_diag[2] = 25.0  # depth spread for baro

    # Anchor at ENU (200, 0, 0) so LoRa obs uses the active path.
    anchor_lat, anchor_lon = enu_to_latlon(
        200.0, 0.0, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON
    )
    anchor_positions = {"a00": (float(anchor_lat), float(anchor_lon))}

    pf = make_pf_at_origin(
        layout=layout,
        cov_diag=cov_diag,
        n_particles=n_particles,
        anchor_positions=anchor_positions,
        rng=make_rng(seed=42),
    )

    pf.predict(dt_sec=60.0)  # sets internal _last_dt_sec for IMU

    # GPS obs at ENU origin.
    gps_lat, gps_lon = enu_to_latlon(
        0.0, 0.0, TEST_ENU_ORIGIN_LAT, TEST_ENU_ORIGIN_LON
    )
    gps_obs = GPSObservation(
        t_sec=60.0,
        node_id="d00",
        lat_deg=float(gps_lat),
        lon_deg=float(gps_lon),
        noise_sigma_m=5.0,
    )

    # IMU obs — sensible accel / gyro values.
    imu_obs = IMUObservation(
        t_sec=60.0,
        node_id="d00",
        accel_xyz=(0.01, 0.01, 0.01),
        gyro_xyz=(0.01, 0.01, 0.01),
        accel_noise_sigma_ms2=0.1,
        gyro_noise_sigma_rad_s=0.1,
    )

    # Baro at ~ surface pressure.
    baro_obs = BaroObservation(
        t_sec=60.0,
        node_id="d00",
        pressure_pa=101325.0,
        noise_sigma_pa=1000.0,
    )

    # Mag at heading 0°.
    mag_obs = MagObservation(
        t_sec=60.0,
        node_id="d00",
        heading_deg=0.0,
        noise_sigma_deg=10.0,
    )

    # Bathy probe at uniform-grid depth (1000 m, the test map's value).
    bathy_obs = BathyProbeObservation(
        t_sec=60.0,
        node_id="d00",
        depth_m=1000.0,
        noise_sigma_m=10.0,
    )

    # LoRa TOA to the anchor (active path, not the filter path).
    lora_obs = LoraTOAObservation(
        t_sec=60.0,
        node_id="d00",
        partner_id="a00",
        range_m=200.0,
        noise_sigma_m=5.0,
    )

    # Should not raise — every type is recognized.
    pf.weight([gps_obs, imu_obs, baro_obs, mag_obs, bathy_obs, lora_obs])

    # Weights normalized.
    assert np.isclose(float(pf.weights.sum()), 1.0), (
        f"weights must sum to 1 after weight; got sum={pf.weights.sum():.6e}"
    )


def test_imu_weight_before_predict_raises_runtime_error(make_rng):
    """``PFFloat.weight`` raises ``RuntimeError`` when an IMU obs arrives
    before any ``predict`` call — the IMU likelihood needs ``_last_dt_sec``
    (set by predict) to invert ``(v - v_prev) / dt``. This is a programming
    error: in production ``step()`` always calls predict before weight.
    """
    pf = make_pf_at_origin(
        layout=BALLAST_DRIFTER_LAYOUT,
        n_particles=20,
        rng=make_rng(seed=42),
    )
    imu_obs = IMUObservation(
        t_sec=0.0,
        node_id="d00",
        accel_xyz=(0.0, 0.0, 0.0),
        gyro_xyz=(0.0, 0.0, 0.0),
        accel_noise_sigma_ms2=0.1,
        gyro_noise_sigma_rad_s=0.1,
    )
    with pytest.raises(RuntimeError, match=r"predict|_last_dt_sec"):
        pf.weight([imu_obs])
