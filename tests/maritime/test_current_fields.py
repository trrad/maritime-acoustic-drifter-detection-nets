"""Contract tests for synthetic current field implementation.

These tests define the interface and expected behavior for the
rtl.vectors.maritime.current_fields module. The module does not exist yet —
these tests will fail with ImportError until implementation is complete.
"""

import numpy as np
import pytest
from typing import Protocol

# This import will fail until the module is implemented
from rtl.vectors.maritime.coords import haversine_m, latlon_to_enu
from rtl.vectors.maritime.current_fields import (
    CurrentField,
    EddySpec,
    FieldConfig,
    SyntheticEddyField,
)


def test_protocol_satisfaction():
    """Verify structural subtyping via Protocol for CurrentField.

    A test double class implementing velocity_at should satisfy the
    CurrentField protocol without inheritance.
    """
    class TestDouble:
        def velocity_at(self, lat_deg: float, lon_deg: float, t_sec: float) -> tuple[float, float]:
            return (0.0, 0.0)

    double = TestDouble()
    assert isinstance(double, CurrentField)


def test_eddy_spec_construction():
    """Verify EddySpec and FieldConfig dataclass construction.

    Constructs EddySpec and FieldConfig with specified parameters and
    verifies all attributes are stored correctly.
    """
    eddy = EddySpec(
        center_lat_deg=36.75,
        center_lon_deg=-122.0,
        radius_m=10000.0,
        peak_velocity_ms=0.3,
        cyclonic=True
    )
    assert eddy.center_lat_deg == 36.75
    assert eddy.center_lon_deg == -122.0
    assert eddy.radius_m == 10000.0
    assert eddy.peak_velocity_ms == 0.3
    assert eddy.cyclonic is True

    eddies = [
        EddySpec(center_lat_deg=36.5, center_lon_deg=-122.2, radius_m=8000.0,
                 peak_velocity_ms=0.25, cyclonic=False),
        EddySpec(center_lat_deg=37.0, center_lon_deg=-121.8, radius_m=12000.0,
                 peak_velocity_ms=0.35, cyclonic=True),
    ]

    config = FieldConfig(
        mean_vx_ms=0.1,
        mean_vy_ms=-0.05,
        eddies=eddies,
        tidal_amplitude_ms=0.1
    )
    assert config.mean_vx_ms == 0.1
    assert config.mean_vy_ms == -0.05
    assert config.eddies == eddies
    assert config.tidal_amplitude_ms == 0.1


def test_field_config_defaults():
    """Verify FieldConfig default values.

    Constructs FieldConfig with no arguments and verifies all fields
    have their specified defaults.
    """
    config = FieldConfig()
    assert config.mean_vx_ms == 0.0
    assert config.mean_vy_ms == 0.0
    assert config.eddies == []
    assert config.tidal_amplitude_ms == 0.0
    assert config.tidal_period_sec == 44712.0
    assert config.tidal_direction_deg == 0.0


def test_mean_flow_only():
    """Verify mean flow is constant in space and time.

    With no eddies and no tide, velocity should equal the mean flow
    at any position and time.
    """
    config = FieldConfig(mean_vx_ms=0.1, mean_vy_ms=-0.05)
    field = SyntheticEddyField(config)

    vx, vy = field.velocity_at(36.75, -122.0, 0.0)
    assert vx == 0.1
    assert vy == -0.05

    vx2, vy2 = field.velocity_at(37.0, -121.5, 100.0)
    assert vx2 == 0.1
    assert vy2 == -0.05


def test_eddy_tangential_at_sigma():
    """Verify eddy velocity magnitude and direction at r=sigma.

    At one radius from a cyclonic eddy center (r=sigma), the velocity
    magnitude should be peak * exp(-0.5). For a point east of center
    in the Northern Hemisphere, the velocity should be predominantly
    northward (tangential to the eddy center).
    """
    eddy = EddySpec(
        center_lat_deg=36.75,
        center_lon_deg=-122.0,
        radius_m=10000.0,
        peak_velocity_ms=0.3,
        cyclonic=True
    )
    config = FieldConfig(eddies=[eddy], mean_vx_ms=0.0, mean_vy_ms=0.0,
                         tidal_amplitude_ms=0.0)
    field = SyntheticEddyField(config)

    # Point 10km east of center (exactly at r = sigma)
    # At lat ~36.75°, 1 degree lon ≈ 111.32 * cos(36.75°) * 1000 ≈ 89 km
    lon_offset = 10000.0 / 89000.0
    query_lat = 36.75
    query_lon = -122.0 + lon_offset

    vx, vy = field.velocity_at(query_lat, query_lon, 0.0)

    # Velocity magnitude should be peak * exp(-0.5) ≈ 0.182 m/s
    velocity_mag = np.sqrt(vx**2 + vy**2)
    expected_mag = 0.3 * np.exp(-0.5)
    assert abs(velocity_mag - expected_mag) < 0.01

    # Direction should be tangential: northward for point east of cyclonic eddy
    # vx should be small (radial component should be near zero)
    # vy should be positive (northward)
    assert abs(vx) < 0.05
    assert vy > 0.15


def test_velocity_at_eddy_center():
    """Verify eddy contributes zero velocity at its center.

    At the exact eddy center, the tangential velocity is zero, so total
    velocity should equal mean flow plus tide only.
    """
    eddy = EddySpec(
        center_lat_deg=36.75,
        center_lon_deg=-122.0,
        radius_m=10000.0,
        peak_velocity_ms=0.3,
        cyclonic=True
    )
    config = FieldConfig(eddies=[eddy], mean_vx_ms=0.0, mean_vy_ms=0.0,
                         tidal_amplitude_ms=0.0)
    field = SyntheticEddyField(config)

    # Query at exact eddy center
    vx, vy = field.velocity_at(36.75, -122.0, 0.0)
    assert vx == 0.0
    assert vy == 0.0


def test_eddy_velocity_monotonic_decay():
    """Verify eddy velocity decays monotonically with distance.

    Queries velocity at 0.5×, 1.0×, 2.0×, and 3.0× the eddy radius
    and verifies magnitudes are monotonically non-increasing.
    """
    eddy = EddySpec(
        center_lat_deg=36.75,
        center_lon_deg=-122.0,
        radius_m=10000.0,
        peak_velocity_ms=0.3,
        cyclonic=True
    )
    config = FieldConfig(eddies=[eddy], mean_vx_ms=0.0, mean_vy_ms=0.0,
                         tidal_amplitude_ms=0.0)
    field = SyntheticEddyField(config)

    # Query at 0.5x, 1.0x, 2.0x, 3.0x radius along east direction
    radii = [0.5, 1.0, 2.0, 3.0]
    lon_offset_per_km = 1.0 / 89000.0

    magnitudes = []
    for r_factor in radii:
        distance_m = r_factor * 10000.0
        lon_offset = distance_m * lon_offset_per_km
        vx, vy = field.velocity_at(36.75, -122.0 + lon_offset, 0.0)
        mag = np.sqrt(vx**2 + vy**2)
        magnitudes.append(mag)

    # Verify monotonic non-increasing
    for i in range(len(magnitudes) - 1):
        assert magnitudes[i] >= magnitudes[i+1], \
            f"Magnitude at {radii[i]}x radius ({magnitudes[i]:.4f}) < magnitude at {radii[i+1]}x radius ({magnitudes[i+1]:.4f})"


def test_m2_tide_quarter_period():
    """Verify M2 tidal oscillation with correct period.

    Queries tidal velocity at t=0 and t=quarter_period. At quarter period,
    sin(pi/2) = 1, so the tidal contribution magnitude should equal the
    configured amplitude.
    """
    config = FieldConfig(
        mean_vx_ms=0.0,
        mean_vy_ms=0.0,
        eddies=[],
        tidal_amplitude_ms=0.1,
        tidal_period_sec=44712.0
    )
    field = SyntheticEddyField(config)

    # At t=0, sin(0) = 0, so tidal contribution should be ~0
    vx0, vy0 = field.velocity_at(36.75, -122.0, 0.0)
    mag0 = np.sqrt(vx0**2 + vy0**2)
    assert mag0 < 0.01

    # At t=quarter_period, sin(pi/2) = 1, so tidal contribution magnitude ≈ 0.1
    quarter_period = 44712.0 / 4.0
    vx_q, vy_q = field.velocity_at(36.75, -122.0, quarter_period)
    mag_q = np.sqrt(vx_q**2 + vy_q**2)
    assert abs(mag_q - 0.1) < 0.01


def test_m2_tide_northward():
    """Verify tidal oscillation respects configured direction.

    With tidal_direction_deg=90 (northward), the tidal velocity at
    quarter period should be predominantly northward, not eastward.
    """
    config = FieldConfig(
        mean_vx_ms=0.0,
        mean_vy_ms=0.0,
        eddies=[],
        tidal_amplitude_ms=0.1,
        tidal_period_sec=44712.0,
        tidal_direction_deg=90.0
    )
    field = SyntheticEddyField(config)

    quarter_period = 44712.0 / 4.0
    vx, vy = field.velocity_at(36.75, -122.0, quarter_period)

    assert abs(vx) < 0.01
    assert abs(vy - 0.1) < 0.01


def test_velocity_magnitude_bounded(make_rng):
    """Verify velocity magnitude stays below 2.0 m/s with typical parameters.

    Configures field with 3 strong eddies, mean flow, and tide, then queries
    100 random positions and 10 random times. All velocity magnitudes
    should be less than 2.0 m/s.
    """
    rng = make_rng()

    eddies = [
        EddySpec(center_lat_deg=36.5, center_lon_deg=-122.2, radius_m=10000.0,
                 peak_velocity_ms=0.4, cyclonic=True),
        EddySpec(center_lat_deg=37.0, center_lon_deg=-121.8, radius_m=8000.0,
                 peak_velocity_ms=0.4, cyclonic=False),
        EddySpec(center_lat_deg=36.75, center_lon_deg=-122.0, radius_m=12000.0,
                 peak_velocity_ms=0.4, cyclonic=True),
    ]

    config = FieldConfig(
        mean_vx_ms=0.2,
        mean_vy_ms=0.1,
        eddies=eddies,
        tidal_amplitude_ms=0.15,
        tidal_period_sec=44712.0
    )
    field = SyntheticEddyField(config)

    center_lat = 36.75
    center_lon = -122.0

    # Query 100 random positions within ~50km of center
    for _ in range(100):
        lat_offset_deg = rng.uniform(-0.25, 0.25)
        lon_offset_deg = rng.uniform(-0.35, 0.35)
        lat = center_lat + lat_offset_deg
        lon = center_lon + lon_offset_deg

        # Query at 10 random times
        for _ in range(10):
            t = rng.uniform(0.0, 44712.0)
            vx, vy = field.velocity_at(lat, lon, t)
            mag = np.sqrt(vx**2 + vy**2)
            assert mag < 2.0, f"Velocity magnitude {mag:.4f} m/s exceeds 2.0 m/s at ({lat:.4f}, {lon:.4f}, t={t:.1f})"


def test_advection_mean_flow():
    """Verify Euler integration through constant mean flow matches analytical.

    Advects a particle for 60 seconds at 1 Hz through constant mean flow
    (0.1, 0.0) m/s. The final position should be within 5m of (6.0, 0.0)
    meters east of the starting point.
    """
    config = FieldConfig(mean_vx_ms=0.1, mean_vy_ms=0.0, eddies=[],
                         tidal_amplitude_ms=0.0)
    field = SyntheticEddyField(config)

    start_lat = 36.75
    start_lon = -122.0
    dt = 1.0
    num_steps = 60

    lat, lon = start_lat, start_lon
    for _ in range(num_steps):
        vx, vy = field.velocity_at(lat, lon, 0.0)
        dx = vx * dt
        dy = vy * dt

        # Convert ENU displacement to lat/lon offset
        # Approximate conversion for small displacements
        # 1 m north ≈ 1 / 111111 degrees lat
        # 1 m east ≈ 1 / (111111 * cos(lat)) degrees lon
        lat_offset_deg = dy / 111111.0
        lon_offset_deg = dx / (111111.0 * np.cos(lat * np.pi / 180.0))

        lat += lat_offset_deg
        lon += lon_offset_deg

    # Convert final position to ENU relative to start
    east_m, north_m = latlon_to_enu(lat, lon, start_lat, start_lon)

    # Should be approximately (6.0, 0.0) meters
    assert abs(east_m - 6.0) < 5.0, f"East displacement {east_m:.2f}m not within 5m of 6.0m"
    assert abs(north_m) < 5.0, f"North displacement {north_m:.2f}m not within 5m of 0.0m"


def test_advection_bounded_in_eddy(make_rng):
    """Verify particle advection through eddy field remains bounded.

    Starts a particle at the edge of a cyclonic eddy and advects for 300
    seconds. The particle should stay within 2× the eddy radius of the
    starting point (eddy orbits are closed trajectories).
    """
    rng = make_rng()

    eddy = EddySpec(
        center_lat_deg=36.75,
        center_lon_deg=-122.0,
        radius_m=10000.0,
        peak_velocity_ms=0.3,
        cyclonic=True
    )
    config = FieldConfig(eddies=[eddy], mean_vx_ms=0.0, mean_vy_ms=0.0,
                         tidal_amplitude_ms=0.0)
    field = SyntheticEddyField(config)

    # Start at eddy edge (1× radius) in a random direction
    angle = rng.uniform(0.0, 2 * np.pi)
    lon_offset_per_m = 1.0 / (111111.0 * np.cos(36.75 * np.pi / 180.0))
    lat_offset_per_m = 1.0 / 111111.0

    start_east = 10000.0 * np.cos(angle)
    start_north = 10000.0 * np.sin(angle)

    start_lat = 36.75 + start_north * lat_offset_per_m
    start_lon = -122.0 + start_east * lon_offset_per_m

    # Euler integration for 300 seconds at dt=1s
    dt = 1.0
    num_steps = 300

    lat, lon = start_lat, start_lon
    for _ in range(num_steps):
        vx, vy = field.velocity_at(lat, lon, 0.0)
        dx = vx * dt
        dy = vy * dt

        lat_offset_deg = dy / 111111.0
        lon_offset_deg = dx / (111111.0 * np.cos(lat * np.pi / 180.0))

        lat += lat_offset_deg
        lon += lon_offset_deg

    # Check final position is within 2× eddy radius of starting point
    distance = haversine_m(start_lat, start_lon, lat, lon)
    assert distance < 20000.0, f"Particle traveled {distance:.1f}m, exceeds 2× radius (20000m)"
