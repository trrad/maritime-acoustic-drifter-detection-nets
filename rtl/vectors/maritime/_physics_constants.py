"""Shared physics constants and small helpers used by both the
truth-side sensor models (`sensors.py`) and the PF observation
likelihoods (`pf_float.py`).

Keeping these in one place ensures the forward model and its
inversion share the same values; a future audit can grep one file
instead of several.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike


SEA_LEVEL_PRESSURE_PA: float = 101_325.0
"""Standard atmosphere at depth 0 m, used by the hydrostatic baro model."""

PRESSURE_PER_METER_PA: float = 10_000.0
"""Linear hydrostatic gradient: pressure_pa(depth_m) =
SEA_LEVEL_PRESSURE_PA + PRESSURE_PER_METER_PA * depth_m."""


def wrap_signed_deg(delta_deg: ArrayLike) -> np.ndarray | float:
    """Wrap a heading delta to the canonical signed range ``[-180, 180]``.

    Vectorized: accepts scalars or numpy arrays; returns the same
    shape. Used by the IMU truth model (heading rate → gyro_z) and by
    the PF mag/IMU likelihoods (residual against a wrap-aware
    predicted reading).
    """
    return ((np.asarray(delta_deg) + 180.0) % 360.0) - 180.0
