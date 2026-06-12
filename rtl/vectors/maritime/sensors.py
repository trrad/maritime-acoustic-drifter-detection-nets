from dataclasses import dataclass
from typing import Protocol, runtime_checkable, cast

import numpy as np

from rtl.vectors.maritime.fleet import Node
from rtl.vectors.maritime.map_payload import RegionalMap
from rtl.vectors.maritime.platform_profile import SensorSpec, CapabilityViolation, CommsProfile
from rtl.vectors.maritime.coords import enu_to_latlon
from rtl.vectors.maritime.clock import Clock


VALID_SENSOR_NAMES = frozenset({"gps", "imu", "baro", "mag", "lora_toa", "bathy_probe"})


@dataclass(frozen=True, slots=True)
class Measurement:
    t_sec: float
    node_id: str
    sensor_name: str
    value: tuple[float, ...]
    unit: str
    noise_sigma: float

    def __post_init__(self) -> None:
        if self.sensor_name not in VALID_SENSOR_NAMES:
            raise ValueError(f"Invalid sensor_name: {self.sensor_name}")
        if len(self.value) < 1:
            raise ValueError("value must have at least one element")


@dataclass(frozen=True, slots=True)
class SensorEnv:
    enu_origin_lat_deg: float
    enu_origin_lon_deg: float
    dt_sec: float
    regional_map: RegionalMap | None = None
    fleet: tuple[Node, ...] | None = None

    def __post_init__(self) -> None:
        if self.dt_sec <= 0:
            raise ValueError(f"dt_sec must be > 0, got {self.dt_sec}")


@runtime_checkable
class Sensor(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def spec(self) -> SensorSpec: ...

    def should_sample(self, t_sec: float, last_fire_sec: float) -> bool: ...

    def sample(
        self,
        node: Node,
        env: SensorEnv,
        t_sec: float,
        rng: np.random.Generator,
    ) -> Measurement | None: ...


@dataclass
class GPSSensor:
    spec: SensorSpec

    @property
    def name(self) -> str:
        return "gps"

    def should_sample(self, t_sec: float, last_fire_sec: float) -> bool:
        return t_sec - last_fire_sec >= 1.0 / self.spec.max_rate_hz

    def sample(
        self,
        node: Node,
        env: SensorEnv,
        t_sec: float,
        rng: np.random.Generator,
    ) -> Measurement:
        try:
            node.profile.sensor("gps")
        except KeyError:
            raise CapabilityViolation(node_class=node.profile.class_name, sensor_name="gps", reason="sensor not in profile")

        clock = cast(Clock, node.components["clock"])
        wall_time = clock.wall_time(t_sec)

        east_m, north_m = node.state[0:2]

        lat_array, lon_array = enu_to_latlon(east_m, north_m, env.enu_origin_lat_deg, env.enu_origin_lon_deg)
        lat = float(lat_array)
        lon = float(lon_array)

        sigma_m = self.spec.noise_sigma
        meters_per_degree_lat = 111320.0
        lat_rad = lat * np.pi / 180.0
        meters_per_degree_lon = 111320.0 * np.cos(lat_rad)

        sigma_lat_deg = sigma_m / meters_per_degree_lat
        sigma_lon_deg = sigma_m / meters_per_degree_lon

        lat_noisy = lat + rng.normal(0.0, sigma_lat_deg)
        lon_noisy = lon + rng.normal(0.0, sigma_lon_deg)

        return Measurement(
            t_sec=wall_time,
            node_id=node.node_id,
            sensor_name="gps",
            value=(lat_noisy, lon_noisy),
            unit="deg",
            noise_sigma=self.spec.noise_sigma,
        )


@dataclass
class IMUSensor:
    spec: SensorSpec

    @property
    def name(self) -> str:
        return "imu"

    def should_sample(self, t_sec: float, last_fire_sec: float) -> bool:
        return t_sec - last_fire_sec >= 1.0 / self.spec.max_rate_hz

    def sample(
        self,
        node: Node,
        env: SensorEnv,
        t_sec: float,
        rng: np.random.Generator,
    ) -> Measurement:
        try:
            node.profile.sensor("imu")
        except KeyError:
            raise CapabilityViolation(node_class=node.profile.class_name, sensor_name="imu", reason="sensor not in profile")

        clock = cast(Clock, node.components["clock"])
        wall_time = clock.wall_time(t_sec)

        velocity = node.state[node.layout.slice("velocity")]
        prev_velocity = node.state[node.layout.slice("prev_velocity")]
        heading = float(node.state[node.layout.slice("heading")][0])
        prev_heading = float(node.state[node.layout.slice("prev_heading")][0])
        gyro_bias = node.state[node.layout.slice("imu_bias")][0:3]
        accel_bias = node.state[node.layout.slice("imu_bias")][3:6]

        dt_sec = env.dt_sec
        truth_accel = (velocity - prev_velocity) / dt_sec

        heading_delta_deg = ((heading - prev_heading + 180.0) % 360.0) - 180.0
        truth_heading_rate_deg_s = heading_delta_deg / dt_sec
        truth_gyro_z_rad_s = truth_heading_rate_deg_s * np.pi / 180.0

        accel_sigma = self.spec.noise_sigma
        if self.spec.noise_sigma_secondary is None:
            raise ValueError(
                "IMUSensor requires SensorSpec.noise_sigma_secondary (gyro sigma) to be set"
            )
        gyro_sigma = self.spec.noise_sigma_secondary

        accel_x = truth_accel[0] + accel_bias[0] + rng.normal(0.0, accel_sigma)
        accel_y = truth_accel[1] + accel_bias[1] + rng.normal(0.0, accel_sigma)
        accel_z = truth_accel[2] + accel_bias[2] + rng.normal(0.0, accel_sigma)

        gyro_x = 0.0 + gyro_bias[0] + rng.normal(0.0, gyro_sigma)
        gyro_y = 0.0 + gyro_bias[1] + rng.normal(0.0, gyro_sigma)
        gyro_z = truth_gyro_z_rad_s + gyro_bias[2] + rng.normal(0.0, gyro_sigma)

        return Measurement(
            t_sec=wall_time,
            node_id=node.node_id,
            sensor_name="imu",
            value=(accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z),
            unit="m/s^2;rad/s",
            noise_sigma=self.spec.noise_sigma,
        )


@dataclass
class BaroSensor:
    spec: SensorSpec

    @property
    def name(self) -> str:
        return "baro"

    def should_sample(self, t_sec: float, last_fire_sec: float) -> bool:
        return t_sec - last_fire_sec >= 1.0 / self.spec.max_rate_hz

    def sample(
        self,
        node: Node,
        env: SensorEnv,
        t_sec: float,
        rng: np.random.Generator,
    ) -> Measurement:
        try:
            node.profile.sensor("baro")
        except KeyError:
            raise CapabilityViolation(node_class=node.profile.class_name, sensor_name="baro", reason="sensor not in profile")

        clock = cast(Clock, node.components["clock"])
        wall_time = clock.wall_time(t_sec)

        depth_m = node.state[2]
        pressure = 101325.0 + 10000.0 * depth_m + rng.normal(0.0, self.spec.noise_sigma)

        return Measurement(
            t_sec=wall_time,
            node_id=node.node_id,
            sensor_name="baro",
            value=(pressure,),
            unit="Pa",
            noise_sigma=self.spec.noise_sigma,
        )


@dataclass
class MagSensor:
    spec: SensorSpec

    @property
    def name(self) -> str:
        return "mag"

    def should_sample(self, t_sec: float, last_fire_sec: float) -> bool:
        return t_sec - last_fire_sec >= 1.0 / self.spec.max_rate_hz

    def sample(
        self,
        node: Node,
        env: SensorEnv,
        t_sec: float,
        rng: np.random.Generator,
    ) -> Measurement:
        try:
            node.profile.sensor("mag")
        except KeyError:
            raise CapabilityViolation(node_class=node.profile.class_name, sensor_name="mag", reason="sensor not in profile")

        clock = cast(Clock, node.components["clock"])
        wall_time = clock.wall_time(t_sec)

        heading = node.state[6]
        heading_noisy = (heading + rng.normal(0.0, self.spec.noise_sigma)) % 360.0

        return Measurement(
            t_sec=wall_time,
            node_id=node.node_id,
            sensor_name="mag",
            value=(heading_noisy,),
            unit="deg",
            noise_sigma=self.spec.noise_sigma,
        )


@dataclass
class BathyProbeSensor:
    spec: SensorSpec

    @property
    def name(self) -> str:
        return "bathy_probe"

    def should_sample(self, t_sec: float, last_fire_sec: float) -> bool:
        return t_sec - last_fire_sec >= 1.0 / self.spec.max_rate_hz

    def sample(
        self,
        node: Node,
        env: SensorEnv,
        t_sec: float,
        rng: np.random.Generator,
    ) -> Measurement | None:
        try:
            node.profile.sensor("bathy_probe")
        except KeyError:
            raise CapabilityViolation(node_class=node.profile.class_name, sensor_name="bathy_probe", reason="sensor not in profile")

        if env.regional_map is None:
            raise ValueError("regional_map required for bathy_probe sensor")

        clock = cast(Clock, node.components["clock"])
        wall_time = clock.wall_time(t_sec)

        east_m = node.state[0]
        north_m = node.state[1]

        lat_array, lon_array = enu_to_latlon(east_m, north_m, env.enu_origin_lat_deg, env.enu_origin_lon_deg)
        lat = float(lat_array)
        lon = float(lon_array)

        depth = env.regional_map.depth_at(lat, lon)
        if np.isnan(depth):
            return None

        noisy_depth = depth + rng.normal(0.0, self.spec.noise_sigma)

        return Measurement(
            t_sec=wall_time,
            node_id=node.node_id,
            sensor_name="bathy_probe",
            value=(noisy_depth,),
            unit="m",
            noise_sigma=self.spec.noise_sigma,
        )


LORA_LINK_STATUSES: frozenset[str] = frozenset({"success", "dropped", "out_of_range"})


@dataclass(frozen=True, slots=True)
class LoraLinkOutcome:
    """Result of one bidirectional ranging round between two nodes.

    A successful round yields two Measurements — one attributed to each node end —
    sharing the same noisy_range (both ends derive range from the same RTT). A
    dropped or out-of-range attempt yields no measurements but is still recorded
    with status and range_m=None.
    """

    status: str
    range_m: float | None
    measurements: tuple[Measurement, ...]

    def __post_init__(self) -> None:
        if self.status not in LORA_LINK_STATUSES:
            raise ValueError(f"Invalid status: {self.status}. Must be in {LORA_LINK_STATUSES}")
        if self.status == "success":
            if self.range_m is None:
                raise ValueError("LoraLinkOutcome with status='success' must have range_m populated")
            if len(self.measurements) != 2:
                raise ValueError(
                    f"LoraLinkOutcome with status='success' must have 2 measurements, "
                    f"got {len(self.measurements)}"
                )
        else:
            if self.range_m is not None:
                raise ValueError(
                    f"LoraLinkOutcome with status='{self.status}' must have range_m=None"
                )
            if self.measurements:
                raise ValueError(
                    f"LoraLinkOutcome with status='{self.status}' must have no measurements"
                )


@dataclass
class LoraTOASensor:
    spec: SensorSpec
    comms: CommsProfile

    @property
    def name(self) -> str:
        return "lora_toa"

    def should_sample(self, t_sec: float, last_fire_sec: float) -> bool:
        return t_sec - last_fire_sec >= self.comms.tdma_period_sec

    def sample_pair(
        self,
        self_node: Node,
        neighbor_node: Node,
        env: SensorEnv,
        t_sec: float,
        rng: np.random.Generator,
    ) -> Measurement | None:
        east1 = self_node.state[0]
        north1 = self_node.state[1]
        east2 = neighbor_node.state[0]
        north2 = neighbor_node.state[1]

        distance = np.sqrt((east1 - east2) ** 2 + (north1 - north2) ** 2)

        if distance > self.comms.max_range_m:
            return None

        if rng.uniform(0.0, 1.0) < self.comms.packet_loss_rate:
            return None

        clock = cast(Clock, self_node.components["clock"])
        wall_time = clock.wall_time(t_sec)

        noisy_range = distance + rng.normal(0.0, self.comms.ranging_sigma_m)

        return Measurement(
            t_sec=wall_time,
            node_id=self_node.node_id,
            sensor_name="lora_toa",
            value=(noisy_range,),
            unit="m",
            noise_sigma=self.comms.ranging_sigma_m,
        )

    def sample_link(
        self,
        node_a: Node,
        node_b: Node,
        env: SensorEnv,
        t_sec: float,
        rng: np.random.Generator,
    ) -> LoraLinkOutcome:
        east_a = node_a.state[0]
        north_a = node_a.state[1]
        east_b = node_b.state[0]
        north_b = node_b.state[1]

        distance = float(np.sqrt((east_a - east_b) ** 2 + (north_a - north_b) ** 2))

        if distance > self.comms.max_range_m:
            return LoraLinkOutcome(status="out_of_range", range_m=None, measurements=())

        if rng.uniform(0.0, 1.0) < self.comms.packet_loss_rate:
            return LoraLinkOutcome(status="dropped", range_m=None, measurements=())

        noisy_range = distance + float(rng.normal(0.0, self.comms.ranging_sigma_m))

        clock_a = cast(Clock, node_a.components["clock"])
        clock_b = cast(Clock, node_b.components["clock"])
        wall_time_a = clock_a.wall_time(t_sec)
        wall_time_b = clock_b.wall_time(t_sec)

        measurements = (
            Measurement(
                t_sec=wall_time_a,
                node_id=node_a.node_id,
                sensor_name="lora_toa",
                value=(noisy_range,),
                unit="m",
                noise_sigma=self.comms.ranging_sigma_m,
            ),
            Measurement(
                t_sec=wall_time_b,
                node_id=node_b.node_id,
                sensor_name="lora_toa",
                value=(noisy_range,),
                unit="m",
                noise_sigma=self.comms.ranging_sigma_m,
            ),
        )

        return LoraLinkOutcome(
            status="success",
            range_m=noisy_range,
            measurements=measurements,
        )

    def sample_all_pairs(
        self,
        self_node: Node,
        env: SensorEnv,
        t_sec: float,
        rng: np.random.Generator,
    ) -> tuple[Measurement, ...]:
        if env.fleet is None:
            raise ValueError("fleet required for sample_all_pairs")

        measurements = []
        for node in env.fleet:
            if node.node_id == self_node.node_id:
                continue
            measurement = self.sample_pair(self_node, node, env, t_sec, rng)
            if measurement is not None:
                measurements.append(measurement)

        return tuple(measurements)
