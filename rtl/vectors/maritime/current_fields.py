import numpy as np
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from rtl.vectors.maritime.coords import bearing_deg, haversine_m


@runtime_checkable
class CurrentField(Protocol):
    def velocity_at(self, lat_deg: float, lon_deg: float, t_sec: float) -> tuple[float, float]:
        ...


@dataclass
class EddySpec:
    center_lat_deg: float
    center_lon_deg: float
    radius_m: float
    peak_velocity_ms: float
    cyclonic: bool


@dataclass
class FieldConfig:
    mean_vx_ms: float = 0.0
    mean_vy_ms: float = 0.0
    eddies: list[EddySpec] = field(default_factory=list)
    tidal_amplitude_ms: float = 0.0
    tidal_period_sec: float = 44712.0
    tidal_direction_deg: float = 0.0


class SyntheticEddyField:
    def __init__(self, config: FieldConfig) -> None:
        self._config = config

    def velocity_at(self, lat_deg: float, lon_deg: float, t_sec: float) -> tuple[float, float]:
        vx = self._config.mean_vx_ms
        vy = self._config.mean_vy_ms

        for eddy in self._config.eddies:
            r = haversine_m(eddy.center_lat_deg, eddy.center_lon_deg, lat_deg, lon_deg)

            if r > 0:
                bearing = bearing_deg(eddy.center_lat_deg, eddy.center_lon_deg, lat_deg, lon_deg)
                bearing_rad = bearing * np.pi / 180.0

                radial_east = np.sin(bearing_rad)
                radial_north = np.cos(bearing_rad)

                if eddy.cyclonic:
                    tangential_east = -radial_north
                    tangential_north = radial_east
                else:
                    tangential_east = radial_north
                    tangential_north = -radial_east

                v_t = eddy.peak_velocity_ms * np.exp(-r**2 / (2 * eddy.radius_m**2))
                vx += v_t * tangential_east
                vy += v_t * tangential_north

        tidal_v = self._config.tidal_amplitude_ms * np.sin(2 * np.pi * t_sec / self._config.tidal_period_sec)
        tidal_rad = self._config.tidal_direction_deg * np.pi / 180.0
        vx += tidal_v * np.cos(tidal_rad)
        vy += tidal_v * np.sin(tidal_rad)

        return (float(vx), float(vy))
