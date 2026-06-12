import numpy as np
import pytest


def test_smoke():
    """Smoke test to verify test infrastructure is working."""
    assert True


# Tests for make_rng fixture
def test_make_rng_returns_generator(make_rng):
    """Verify make_rng returns a numpy.random.Generator."""
    rng = make_rng()
    assert isinstance(rng, np.random.Generator)


def test_make_rng_default_seed_determinism(make_rng):
    """Two calls to make_rng() with no args produce identical random values."""
    rng1 = make_rng()
    rng2 = make_rng()
    assert rng1.random() == rng2.random()


def test_make_rng_custom_seed_differs_from_default(make_rng):
    """make_rng(seed=123) produces different values than the default seed."""
    rng_default = make_rng()
    rng_custom = make_rng(seed=123)
    assert rng_default.random() != rng_custom.random()


def test_make_rng_custom_seed_determinism(make_rng):
    """make_rng(seed=123) called twice produces identical values."""
    rng1 = make_rng(seed=123)
    rng2 = make_rng(seed=123)
    assert rng1.random() == rng2.random()


# Tests for assert_close helper
def test_assert_close_passes_within_tolerance(assert_close):
    """assert_close passes when arrays are within tolerance."""
    assert_close([1.0, 2.0], [1.0, 2.0], atol=0.1)
    assert_close([1.0, 2.0], [1.05, 1.95], atol=0.1)


def test_assert_close_raises_exceeds_tolerance(assert_close):
    """assert_close raises AssertionError when arrays exceed tolerance with msg."""
    with pytest.raises(AssertionError) as exc_info:
        assert_close([1.0, 2.0], [1.2, 2.2], atol=0.1, msg="test context")
    assert "test context" in str(exc_info.value)


def test_assert_close_relative_tolerance(assert_close):
    """assert_close works with relative tolerance."""
    assert_close([1.0, 100.0], [1.01, 101.0], rtol=0.01)
