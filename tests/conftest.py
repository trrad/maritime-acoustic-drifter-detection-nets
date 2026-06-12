import numpy as np
import pytest


def pytest_addoption(parser):
    """Register command-line options for pytest."""
    parser.addoption(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible tests (default: 42)",
    )


@pytest.fixture(scope="function")
def rng_seed(pytestconfig):
    """Fixture to get the random seed from command-line options."""
    return pytestconfig.getoption("--seed")


@pytest.fixture(scope="function")
def make_rng(rng_seed):
    """Factory fixture that creates numpy.random.Generator instances.

    When called without arguments, uses the default seed from --seed option.
    When called with seed=<int>, uses that specific seed.

    Returns:
        Callable[[], np.random.Generator] or Callable[[int], np.random.Generator]
    """

    def _make_rng(seed=None):
        """Create a numpy random generator with the given seed.

        Args:
            seed: Optional seed. If None, uses the default seed from --seed.

        Returns:
            np.random.Generator: Seeded random number generator.
        """
        actual_seed = seed if seed is not None else rng_seed
        return np.random.default_rng(actual_seed)

    return _make_rng


@pytest.fixture(scope="function")
def assert_close():
    """Fixture that provides the assert_close helper function.

    This allows tests to use assert_close as a fixture parameter.
    """
    def _assert_close(actual, desired, atol=1e-7, rtol=0, msg=""):
        """Assert that arrays are close within absolute and/or relative tolerance.

        Wraps numpy.testing.assert_allclose with support for custom error messages
        for physical-unit context.

        Args:
            actual: Array-like of actual values.
            desired: Array-like of desired values.
            atol: Absolute tolerance (default: 1e-7).
            rtol: Relative tolerance (default: 0).
            msg: Optional message to include in error for physical-unit context.

        Raises:
            AssertionError: If arrays differ beyond tolerances.
        """
        try:
            np.testing.assert_allclose(actual, desired, atol=atol, rtol=rtol)
        except AssertionError as e:
            if msg:
                raise AssertionError(f"{msg}: {e}") from e
            raise

    return _assert_close
