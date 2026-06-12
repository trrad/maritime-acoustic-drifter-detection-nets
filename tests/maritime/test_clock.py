"""Contract tests for clock module.

Tests for ClockSpec and Clock dataclasses.
"""

import dataclasses

import pytest


class TestClockSpec:
    """Tests for ClockSpec dataclass."""

    def test_default_construction(self) -> None:
        """ClockSpec(0.0, 0.0) constructs; kind=='clock'; isinstance(spec, ComponentSpec); spec is immutable (FrozenInstanceError on mutation)."""
        from rtl.vectors.maritime.clock import ClockSpec
        from rtl.vectors.maritime.platform_profile import ComponentSpec

        spec = ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)

        assert spec.kind == "clock"
        assert isinstance(spec, ComponentSpec)

        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.drift_ppm = 10.0  # type: ignore[misc]

    def test_nonzero_drift_construction(self) -> None:
        """ClockSpec(20.0, 0.5) constructs and exposes provided values."""
        from rtl.vectors.maritime.clock import ClockSpec

        spec = ClockSpec(drift_ppm=20.0, avg_power_mw=0.5)

        assert spec.drift_ppm == 20.0
        assert spec.avg_power_mw == 0.5

    def test_negative_drift_ppm_rejected(self) -> None:
        """ClockSpec(-0.1, 0.0) raises ValueError."""
        from rtl.vectors.maritime.clock import ClockSpec

        with pytest.raises(ValueError):
            ClockSpec(drift_ppm=-0.1, avg_power_mw=0.0)

    def test_negative_avg_power_rejected(self) -> None:
        """ClockSpec(0.0, -1.0) raises ValueError."""
        from rtl.vectors.maritime.clock import ClockSpec

        with pytest.raises(ValueError):
            ClockSpec(drift_ppm=0.0, avg_power_mw=-1.0)


class TestClockAdvance:
    """Tests for Clock.advance behavior."""

    def test_zero_drift_advance_is_noop(self) -> None:
        """Zero-drift Clock.advance(60.0) leaves _accumulated_offset_sec == 0.0."""
        from rtl.vectors.maritime.clock import Clock, ClockSpec

        spec = ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)
        clock = Clock(spec=spec)

        clock.advance(60.0)

        assert clock._accumulated_offset_sec == 0.0

    def test_nonzero_drift_single_advance(self) -> None:
        """Clock with drift_ppm=10.0: advance(100.0) yields _accumulated_offset_sec == 0.001."""
        from rtl.vectors.maritime.clock import Clock, ClockSpec

        spec = ClockSpec(drift_ppm=10.0, avg_power_mw=0.5)
        clock = Clock(spec=spec)

        clock.advance(100.0)

        assert clock._accumulated_offset_sec == 0.001

    def test_nonzero_drift_repeated_advance(self) -> None:
        """Clock with drift_ppm=10.0: three advance(30.0) calls yield _accumulated_offset_sec == 0.0009."""
        from rtl.vectors.maritime.clock import Clock, ClockSpec

        spec = ClockSpec(drift_ppm=10.0, avg_power_mw=0.5)
        clock = Clock(spec=spec)

        clock.advance(30.0)
        clock.advance(30.0)
        clock.advance(30.0)

        assert clock._accumulated_offset_sec == 0.0009

    def test_negative_dt_rejected(self) -> None:
        """Clock.advance(-1.0) raises ValueError."""
        from rtl.vectors.maritime.clock import Clock, ClockSpec

        spec = ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)
        clock = Clock(spec=spec)

        with pytest.raises(ValueError):
            clock.advance(-1.0)


class TestClockWallTime:
    """Tests for Clock.wall_time behavior."""

    def test_zero_drift_no_advance_identity(self) -> None:
        """Zero-drift clock, no advances: wall_time(100.0) == 100.0 exactly (bitwise)."""
        from rtl.vectors.maritime.clock import Clock, ClockSpec

        spec = ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)
        clock = Clock(spec=spec)

        result = clock.wall_time(100.0)

        assert result == 100.0

    def test_zero_drift_many_advances_identity(self) -> None:
        """Zero-drift clock advanced 100 times by dt=60.0: wall_time(7200.0) == 7200.0 exactly."""
        from rtl.vectors.maritime.clock import Clock, ClockSpec

        spec = ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)
        clock = Clock(spec=spec)

        for _ in range(100):
            clock.advance(60.0)

        result = clock.wall_time(7200.0)

        assert result == 7200.0

    def test_nonzero_drift_wall_time(self) -> None:
        """Clock(drift_ppm=10.0) advanced once by dt=1000.0: wall_time(1000.0) == 1000.01."""
        from rtl.vectors.maritime.clock import Clock, ClockSpec

        spec = ClockSpec(drift_ppm=10.0, avg_power_mw=0.5)
        clock = Clock(spec=spec)

        clock.advance(1000.0)

        result = clock.wall_time(1000.0)

        assert result == 1000.01

    def test_wall_time_purity(self) -> None:
        """wall_time does not mutate _accumulated_offset_sec; repeated calls between advances return identical values."""
        from rtl.vectors.maritime.clock import Clock, ClockSpec

        spec = ClockSpec(drift_ppm=10.0, avg_power_mw=0.5)
        clock = Clock(spec=spec)

        clock.advance(100.0)

        offset_before = clock._accumulated_offset_sec
        result1 = clock.wall_time(100.0)
        offset_after = clock._accumulated_offset_sec
        result2 = clock.wall_time(100.0)

        assert result1 == result2
        assert offset_before == offset_after


class TestModuleInterface:
    """Tests for module-level interface requirements."""

    def test_no_make_clock_function(self) -> None:
        """No module-level make_clock function in clock.py."""
        import rtl.vectors.maritime.clock as clock_module

        assert hasattr(clock_module, "make_clock") is False
