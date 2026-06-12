"""Clock module for maritime simulation.

Provides ClockSpec for describing clock characteristics and Clock runtime
component for tracking accumulated time offset due to drift.
"""

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class ClockSpec:
    kind: ClassVar[str] = "clock"
    drift_ppm: float
    avg_power_mw: float

    def __post_init__(self) -> None:
        if self.drift_ppm < 0:
            raise ValueError(f"drift_ppm must be >= 0, got {self.drift_ppm}")
        if self.avg_power_mw < 0:
            raise ValueError(f"avg_power_mw must be >= 0, got {self.avg_power_mw}")


@dataclass
class Clock:
    spec: ClockSpec
    _accumulated_offset_sec: float = 0.0

    def advance(self, dt_sec: float) -> None:
        if dt_sec < 0:
            raise ValueError(f"dt_sec must be >= 0, got {dt_sec}")
        self._accumulated_offset_sec += dt_sec * self.spec.drift_ppm * 1e-6

    def wall_time(self, true_sec: float) -> float:
        return true_sec + self._accumulated_offset_sec
