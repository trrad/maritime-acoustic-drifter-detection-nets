"""Maritime platform profile data structures.

Provides dataclasses for describing sensor capabilities, platform constraints,
and M1 physics component specifications.
"""

from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable

from rtl.vectors.maritime.clock import ClockSpec


@runtime_checkable
class ComponentSpec(Protocol):
    """Protocol for component specifications with kind identifier and power budget.

    Any class with `kind: ClassVar[str]` and `avg_power_mw: float` attributes
    conforms to this protocol. Used for structural typing of physics components.
    """
    kind: ClassVar[str]

    @property
    def avg_power_mw(self) -> float: ...


@dataclass(frozen=True, slots=True)
class MooredPoseSpec:
    """Specification for a moored pose with fixed anchor point.

    The node remains tethered to a fixed geographic anchor at the specified
    depth. Requires no active power for pose maintenance.
    """
    kind: ClassVar[str] = "moored_pose"
    anchor_lat_deg: float
    anchor_lon_deg: float
    anchor_depth_m: float
    avg_power_mw: float = 0.0


@dataclass(frozen=True, slots=True)
class DriftingSurfacePoseSpec:
    """Specification for a drifting surface pose.

    The node floats freely at the surface, subject to currents and waves.
    Requires no active power for pose maintenance.
    """
    kind: ClassVar[str] = "drifting_surface_pose"
    avg_power_mw: float = 0.0


@dataclass(frozen=True, slots=True)
class BallastDriftingPoseSpec:
    """Specification for a drifting pose with ballast control.

    The node can actively control depth via ballast but does not have
    a fixed geographic anchor. Requires no power for pose maintenance
    (power is accounted in BallastSpec).
    """
    kind: ClassVar[str] = "ballast_drifting_pose"
    avg_power_mw: float = 0.0


@dataclass(frozen=True, slots=True)
class BallastSpec:
    """Specification for a ballast pump system.

    Provides depth control capability through active ballast adjustment.
    """
    kind: ClassVar[str] = "ballast_pump"
    capacity_ml: float
    pump_rate_ml_per_s: float
    avg_power_mw: float

    def __post_init__(self) -> None:
        if self.capacity_ml <= 0:
            raise ValueError(f"capacity_ml must be > 0, got {self.capacity_ml}")
        if self.pump_rate_ml_per_s <= 0:
            raise ValueError(f"pump_rate_ml_per_s must be > 0, got {self.pump_rate_ml_per_s}")
        if self.avg_power_mw < 0:
            raise ValueError(f"avg_power_mw must be >= 0, got {self.avg_power_mw}")


@dataclass(frozen=True, slots=True)
class SatelliteUplinkSpec:
    """Specification for a satellite communications uplink.

    Provides long-range data transmission capability beyond LoRa range.
    Duty cycle represents the fraction of time the transmitter is active.
    """
    kind: ClassVar[str] = "satellite_uplink"
    duty_cycle: float
    avg_power_mw: float

    def __post_init__(self) -> None:
        if not (0 <= self.duty_cycle <= 1):
            raise ValueError(f"duty_cycle must be in [0, 1], got {self.duty_cycle}")
        if self.avg_power_mw < 0:
            raise ValueError(f"avg_power_mw must be >= 0, got {self.avg_power_mw}")


@dataclass(frozen=True, slots=True)
class SensorSpec:
    """Specification for a sensor attached to a maritime platform.

    Defines the physical and behavioral characteristics of a sensor, including
    what state dimension it observes, its noise characteristics, and its power/
    timing constraints.

    ``noise_sigma_secondary`` is an optional second noise sigma for sensors
    whose channels have different physical units (currently IMU's accel m/s²
    vs. gyro rad/s). For non-IMU sensors it stays ``None``; for IMU it MUST
    be set to a positive float (the gyro-channel sigma in rad/s, with
    ``noise_sigma`` interpreted as the accel-channel sigma in m/s²).
    """
    name: str
    observed_dim: int
    noise_sigma: float
    noise_unit: str
    max_rate_hz: float
    duty_cycle: float
    avg_power_mw: float
    noise_sigma_secondary: float | None = None

    def __post_init__(self) -> None:
        if self.noise_sigma < 0:
            raise ValueError(f"noise_sigma must be >= 0, got {self.noise_sigma}")
        if self.duty_cycle < 0 or self.duty_cycle > 1:
            raise ValueError(f"duty_cycle must be in [0, 1], got {self.duty_cycle}")
        if self.max_rate_hz <= 0:
            raise ValueError(f"max_rate_hz must be > 0, got {self.max_rate_hz}")
        if self.avg_power_mw < 0:
            raise ValueError(f"avg_power_mw must be >= 0, got {self.avg_power_mw}")
        if self.noise_sigma_secondary is not None and self.noise_sigma_secondary <= 0:
            raise ValueError(
                f"noise_sigma_secondary must be > 0 when set, got {self.noise_sigma_secondary}"
            )
        if self.name == "imu" and self.noise_sigma_secondary is None:
            raise ValueError(
                "noise_sigma_secondary is required for IMU SensorSpec "
                "(gyro-channel sigma in rad/s; accel-channel sigma is noise_sigma)"
            )


@dataclass(frozen=True, slots=True)
class CommsProfile:
    """Specification for TDMA-based LoRa communications on a maritime platform.

    Defines timing constraints, ranging capabilities, packet characteristics,
    and power budget for SX1262-based communications.
    """
    slot_length_sec: float
    tdma_period_sec: float
    max_range_m: float
    ranging_sigma_m: float
    packet_bits: int
    packet_loss_rate: float
    avg_power_mw: float

    def __post_init__(self) -> None:
        if self.slot_length_sec <= 0:
            raise ValueError(f"slot_length_sec must be > 0, got {self.slot_length_sec}")
        if self.slot_length_sec > self.tdma_period_sec:
            raise ValueError(f"slot_length_sec must be <= tdma_period_sec, got {self.slot_length_sec} > {self.tdma_period_sec}")
        if self.max_range_m <= 0:
            raise ValueError(f"max_range_m must be > 0, got {self.max_range_m}")
        if self.ranging_sigma_m < 0:
            raise ValueError(f"ranging_sigma_m must be >= 0, got {self.ranging_sigma_m}")
        if self.packet_bits < 0:
            raise ValueError(f"packet_bits must be >= 0, got {self.packet_bits}")
        if self.packet_loss_rate < 0 or self.packet_loss_rate > 1:
            raise ValueError(f"packet_loss_rate must be in [0, 1], got {self.packet_loss_rate}")
        if self.avg_power_mw < 0:
            raise ValueError(f"avg_power_mw must be >= 0, got {self.avg_power_mw}")


class CapabilityViolation(Exception):
    """Raised when runtime behavior exceeds a NodeProfile's declared capability."""

    def __init__(self, *, node_class: str, sensor_name: str, reason: str) -> None:
        self.node_class = node_class
        self.sensor_name = sensor_name
        self.reason = reason
        super().__init__(f"{sensor_name} on {node_class}: {reason}")


@dataclass(frozen=True, slots=True)
class ComputeBudget:
    """Computational budget constraints for a maritime platform.

    Defines the clock capacity and cycle budget for particle filter operations,
    including predictive updates, weight calculations, resampling, and state
    estimation.
    """
    clock_mhz: float
    cycles_per_step: int
    pf_update_rate_hz: float
    headroom: float = 0.8
    avg_power_mw: float = 0.0

    def __post_init__(self) -> None:
        if self.clock_mhz <= 0:
            raise ValueError(f"clock_mhz must be > 0, got {self.clock_mhz}")
        if self.cycles_per_step <= 0:
            raise ValueError(f"cycles_per_step must be > 0, got {self.cycles_per_step}")
        if self.pf_update_rate_hz <= 0:
            raise ValueError(f"pf_update_rate_hz must be > 0, got {self.pf_update_rate_hz}")
        if not (0 < self.headroom <= 1):
            raise ValueError(f"headroom must be in (0, 1], got {self.headroom}")

        required_cycles_per_sec = self.cycles_per_step * self.pf_update_rate_hz
        available_cycles_per_sec = self.clock_mhz * 1e6 * self.headroom

        if required_cycles_per_sec > available_cycles_per_sec:
            raise ValueError(
                f"Compute budget exceeds capacity: required {required_cycles_per_sec} cycles/sec, "
                f"available {available_cycles_per_sec} cycles/sec"
            )

        if self.avg_power_mw < 0:
            raise ValueError(f"avg_power_mw must be >= 0, got {self.avg_power_mw}")


@dataclass(frozen=True, slots=True)
class NodeProfile:
    """Complete capability profile for a maritime platform node.

    Aggregates sensor specifications, communication profile, compute budget,
    physics components, and platform-level constraints into a single immutable
    description. Used for validating runtime behavior against declared capabilities.
    """
    class_name: str
    state_dim: int
    sensors: tuple["SensorSpec", ...]
    comms: "CommsProfile"
    compute: "ComputeBudget"
    total_power_budget_mw: float
    components: tuple[ComponentSpec, ...] = ()

    def __post_init__(self) -> None:
        if self.state_dim <= 0:
            raise ValueError(f"state_dim must be > 0, got {self.state_dim}")
        if self.total_power_budget_mw <= 0:
            raise ValueError(f"total_power_budget_mw must be > 0, got {self.total_power_budget_mw}")

        sensor_names = [sensor.name for sensor in self.sensors]
        unique_names = set(sensor_names)
        if len(sensor_names) != len(unique_names):
            duplicates = [name for name in unique_names if sensor_names.count(name) > 1]
            raise ValueError(f"Duplicate sensor names: {', '.join(duplicates)}")

        component_kinds = [c.kind for c in self.components]
        unique_kinds = set(component_kinds)
        if len(component_kinds) != len(unique_kinds):
            duplicates = [kind for kind in unique_kinds if component_kinds.count(kind) > 1]
            raise ValueError(f"Duplicate component kinds: {', '.join(duplicates)}")

        required_power = self.total_avg_power_mw
        if required_power > self.total_power_budget_mw:
            raise ValueError(
                f"Total power budget exceeded: required {required_power} mW, "
                f"available {self.total_power_budget_mw} mW"
            )

    @property
    def total_sensor_power_mw(self) -> float:
        """Sum of all sensor avg_power_mw values."""
        return sum(sensor.avg_power_mw for sensor in self.sensors)

    @property
    def total_avg_power_mw(self) -> float:
        """Sum of sensor + comms + compute + component average powers."""
        component_power = sum(c.avg_power_mw for c in self.components)
        return self.total_sensor_power_mw + self.comms.avg_power_mw + self.compute.avg_power_mw + component_power

    def sensor(self, name: str) -> "SensorSpec":
        """Return the sensor with the given name, raise KeyError if absent."""
        for sensor in self.sensors:
            if sensor.name == name:
                return sensor
        raise KeyError(f"Sensor '{name}' not found in profile")

    def component(self, kind: str) -> ComponentSpec:
        """Return the component with the given kind, raise KeyError if absent."""
        for comp in self.components:
            if comp.kind == kind:
                return comp
        raise KeyError(f"Component '{kind}' not found in profile")


_LORA_COMMS = CommsProfile(
    slot_length_sec=0.05,
    tdma_period_sec=3600,
    max_range_m=10000,
    ranging_sigma_m=20.0,
    packet_bits=256,
    packet_loss_rate=0.1,
    avg_power_mw=0.22
)

_GPS_SENSOR = SensorSpec(
    name="gps",
    observed_dim=0,
    noise_sigma=1.5,
    noise_unit="m",
    max_rate_hz=1.0 / 3600,
    duty_cycle=0.000278,
    avg_power_mw=8.0,
)
_IMU_SENSOR = SensorSpec(
    name="imu",
    observed_dim=0,
    noise_sigma=0.01,
    noise_unit="m/s^2;rad/s",
    max_rate_hz=1.0,
    duty_cycle=0.01,
    avg_power_mw=0.5,
    noise_sigma_secondary=0.01,
)
_BARO_SENSOR = SensorSpec(
    name="baro",
    observed_dim=0,
    noise_sigma=10.0,
    noise_unit="Pa",
    max_rate_hz=0.1,
    duty_cycle=0.01,
    avg_power_mw=0.05,
)
_MAG_SENSOR = SensorSpec(
    name="mag",
    observed_dim=0,
    noise_sigma=0.5,
    noise_unit="deg",
    max_rate_hz=0.1,
    duty_cycle=0.01,
    avg_power_mw=0.1,
)
_BATHY_SENSOR = SensorSpec(
    name="bathy_probe",
    observed_dim=0,
    noise_sigma=5.0,
    noise_unit="m",
    max_rate_hz=0.01,
    duty_cycle=0.01,
    avg_power_mw=0.3,
)
_LORA_TOA_SENSOR = SensorSpec(
    name="lora_toa",
    observed_dim=0,
    noise_sigma=20.0,
    noise_unit="m",
    max_rate_hz=1.0 / 3600,
    duty_cycle=0.000014,
    avg_power_mw=0.0,
)


def make_anchor_profile(anchor_lat_deg: float, anchor_lon_deg: float, anchor_depth_m: float = 0.0) -> "NodeProfile":
    """Construct an anchor profile with the given geographic mooring coordinates.

    The module-level ``ANCHOR_PROFILE`` is a capability template; real deployments
    construct per-anchor profiles via this factory so that
    ``MooredPoseSpec.anchor_lat_deg`` / ``anchor_lon_deg`` reflect the actual
    mooring lat/lon of each anchor (consumed by the scenario generator to
    populate ``ScenarioHeader.anchor_positions``, and by sensors via the
    ENU-origin conversion).
    """
    return NodeProfile(
        class_name="anchor",
        state_dim=21,
        sensors=(_GPS_SENSOR, _IMU_SENSOR, _BARO_SENSOR, _MAG_SENSOR, _LORA_TOA_SENSOR),
        comms=_LORA_COMMS,
        compute=ComputeBudget(
            clock_mhz=12.0,
            cycles_per_step=73000,
            pf_update_rate_hz=1.0,
            headroom=0.8,
            avg_power_mw=0.5,
        ),
        total_power_budget_mw=50.0,
        components=(
            MooredPoseSpec(
                anchor_lat_deg=anchor_lat_deg,
                anchor_lon_deg=anchor_lon_deg,
                anchor_depth_m=anchor_depth_m,
            ),
            SatelliteUplinkSpec(duty_cycle=0.01, avg_power_mw=15.0),
            ClockSpec(drift_ppm=0.0, avg_power_mw=0.0),
        ),
    )


ANCHOR_PROFILE = make_anchor_profile(anchor_lat_deg=0.0, anchor_lon_deg=0.0, anchor_depth_m=0.0)

BALLAST_DRIFTER_PROFILE = NodeProfile(
    class_name="ballast_drifter",
    state_dim=21,
    sensors=(_IMU_SENSOR, _BARO_SENSOR, _MAG_SENSOR, _BATHY_SENSOR, _LORA_TOA_SENSOR),
    comms=_LORA_COMMS,
    compute=ComputeBudget(
        clock_mhz=12.0,
        cycles_per_step=50000,
        pf_update_rate_hz=1.0,
        headroom=0.8,
        avg_power_mw=0.15
    ),
    total_power_budget_mw=5.0,
    components=(
        BallastDriftingPoseSpec(),
        BallastSpec(capacity_ml=50.0, pump_rate_ml_per_s=0.5, avg_power_mw=2.0),
        ClockSpec(drift_ppm=0.0, avg_power_mw=0.0)
    )
)

PURE_DRIFTER_PROFILE = NodeProfile(
    class_name="pure_drifter",
    state_dim=19,
    sensors=(_IMU_SENSOR, _BARO_SENSOR, _MAG_SENSOR, _BATHY_SENSOR, _LORA_TOA_SENSOR),
    comms=_LORA_COMMS,
    compute=ComputeBudget(
        clock_mhz=12.0,
        cycles_per_step=33000,
        pf_update_rate_hz=1.0,
        headroom=0.8,
        avg_power_mw=0.09
    ),
    total_power_budget_mw=2.0,
    components=(DriftingSurfacePoseSpec(), ClockSpec(drift_ppm=0.0, avg_power_mw=0.0))
)

ALL_M1_PROFILES: tuple[NodeProfile, ...] = (ANCHOR_PROFILE, BALLAST_DRIFTER_PROFILE, PURE_DRIFTER_PROFILE)
