"""Scenario schema types and reader.

Defines the versioned JSONL schema for maritime scenarios, including
ScenarioHeader dataclass and ScenarioReader for observation-only access.

Observations are a sealed union of typed records — one frozen dataclass
per sensor type, each carrying exactly the fields its downstream
likelihood model needs. JSONL records discriminate on a "type" key.
See `Observation` (TypeAlias) and the per-sensor classes.
"""

import difflib
import json
import pickle
from collections.abc import Mapping, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from rtl.vectors.maritime._jsonl_header import read_jsonl_header


SCHEMA_VERSION: str = "1.0"
SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1.0"})
VALID_SENSOR_NAMES: frozenset[str] = frozenset(
    {"gps", "imu", "baro", "mag", "lora_toa", "bathy_probe"}
)
VALID_OBSERVATION_TYPES: frozenset[str] = frozenset(
    {"gps", "imu", "baro", "mag", "bathy_probe", "lora_toa"}
)
VALID_LINK_STATUSES: frozenset[str] = frozenset({"success", "dropped", "out_of_range"})


@dataclass(frozen=True, slots=True)
class ScenarioHeader:
    """Header record for a maritime scenario JSONL file.

    All fields are validated at construction time.
    """

    schema_version: str
    bbox: tuple[float, float, float, float]
    fleet_composition: Mapping[str, int]
    node_ids: tuple[str, ...]
    node_classes: Mapping[str, str]
    seed: int
    duration_sec: float
    dt_sec: float
    created_at_utc: str
    onboard_map_path: str
    anchor_positions: Mapping[str, tuple[float, float]]

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unknown schema_version '{self.schema_version}'. "
                f"Supported versions: {SUPPORTED_SCHEMA_VERSIONS}"
            )

        lat_south, lon_west, lat_north, lon_east = self.bbox
        if lat_south >= lat_north:
            raise ValueError(
                f"Invalid bbox: lat_south ({lat_south}) must be less than lat_north ({lat_north})"
            )

        if len(self.node_ids) == 0:
            raise ValueError("node_ids must be non-empty")

        if self.duration_sec <= 0:
            raise ValueError(f"duration_sec must be positive, got {self.duration_sec}")

        if self.dt_sec <= 0:
            raise ValueError(f"dt_sec must be positive, got {self.dt_sec}")

        node_ids_set = set(self.node_ids)
        node_classes_keys = set(self.node_classes.keys())

        if node_classes_keys != node_ids_set:
            missing = node_ids_set - node_classes_keys
            extraneous = node_classes_keys - node_ids_set
            error_parts = []
            if missing:
                error_parts.append(f"missing node_ids: {sorted(missing)}")
            if extraneous:
                error_parts.append(f"extraneous node_ids: {sorted(extraneous)}")
            raise ValueError(f"node_classes does not match node_ids: {'; '.join(error_parts)}")

        for node_id, class_name in self.node_classes.items():
            if class_name not in self.fleet_composition:
                raise ValueError(
                    f"node_classes[{node_id}] = '{class_name}' is not in fleet_composition keys"
                )

        from collections import Counter

        actual_counts = Counter(self.node_classes.values())
        for class_name, expected_count in self.fleet_composition.items():
            actual_count = actual_counts.get(class_name, 0)
            if actual_count != expected_count:
                raise ValueError(
                    f"fleet_composition mismatch for '{class_name}': "
                    f"expected {expected_count}, got {actual_count}"
                )

        anchor_node_ids = {
            node_id for node_id, class_name in self.node_classes.items() if class_name == "anchor"
        }
        anchor_positions_keys = set(self.anchor_positions.keys())

        if anchor_positions_keys != anchor_node_ids:
            missing = anchor_node_ids - anchor_positions_keys
            extraneous = anchor_positions_keys - anchor_node_ids
            error_parts = []
            if missing:
                error_parts.append(f"missing anchors: {sorted(missing)}")
            if extraneous:
                error_parts.append(f"non-anchors in positions: {sorted(extraneous)}")
            raise ValueError(f"anchor_positions does not match anchor node_ids: {'; '.join(error_parts)}")


@dataclass(frozen=True, slots=True)
class GPSObservation:
    """GPS fix: noisy (lat, lon) reading with horizontal sigma in meters."""

    t_sec: float
    node_id: str
    lat_deg: float
    lon_deg: float
    noise_sigma_m: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.lat_deg <= 90.0):
            raise ValueError(
                f"GPSObservation.lat_deg out of range [-90, 90]: {self.lat_deg}"
            )
        if not (-180.0 <= self.lon_deg <= 180.0):
            raise ValueError(
                f"GPSObservation.lon_deg out of range [-180, 180]: {self.lon_deg}"
            )
        if self.noise_sigma_m <= 0:
            raise ValueError(
                f"GPSObservation.noise_sigma_m must be > 0, got {self.noise_sigma_m}"
            )


@dataclass(frozen=True, slots=True)
class IMUObservation:
    """IMU sample: 3-axis accelerometer + 3-axis gyro with separate sigmas."""

    t_sec: float
    node_id: str
    accel_xyz: tuple[float, float, float]
    gyro_xyz: tuple[float, float, float]
    accel_noise_sigma_ms2: float
    gyro_noise_sigma_rad_s: float

    def __post_init__(self) -> None:
        if self.accel_noise_sigma_ms2 <= 0:
            raise ValueError(
                f"IMUObservation.accel_noise_sigma_ms2 must be > 0, "
                f"got {self.accel_noise_sigma_ms2}"
            )
        if self.gyro_noise_sigma_rad_s <= 0:
            raise ValueError(
                f"IMUObservation.gyro_noise_sigma_rad_s must be > 0, "
                f"got {self.gyro_noise_sigma_rad_s}"
            )


@dataclass(frozen=True, slots=True)
class BaroObservation:
    """Barometer: absolute pressure in Pascals."""

    t_sec: float
    node_id: str
    pressure_pa: float
    noise_sigma_pa: float

    def __post_init__(self) -> None:
        if self.pressure_pa <= 0:
            raise ValueError(
                f"BaroObservation.pressure_pa must be > 0, got {self.pressure_pa}"
            )
        if self.noise_sigma_pa <= 0:
            raise ValueError(
                f"BaroObservation.noise_sigma_pa must be > 0, got {self.noise_sigma_pa}"
            )


@dataclass(frozen=True, slots=True)
class MagObservation:
    """Magnetometer-derived heading in degrees [0, 360)."""

    t_sec: float
    node_id: str
    heading_deg: float
    noise_sigma_deg: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.heading_deg < 360.0):
            raise ValueError(
                f"MagObservation.heading_deg out of range [0, 360): {self.heading_deg}"
            )
        if self.noise_sigma_deg <= 0:
            raise ValueError(
                f"MagObservation.noise_sigma_deg must be > 0, got {self.noise_sigma_deg}"
            )


@dataclass(frozen=True, slots=True)
class BathyProbeObservation:
    """Bathymetry probe reading: sea-floor depth at the node's position, meters."""

    t_sec: float
    node_id: str
    depth_m: float
    noise_sigma_m: float

    def __post_init__(self) -> None:
        if self.depth_m < 0:
            raise ValueError(
                f"BathyProbeObservation.depth_m must be >= 0, got {self.depth_m}"
            )
        if self.noise_sigma_m <= 0:
            raise ValueError(
                f"BathyProbeObservation.noise_sigma_m must be > 0, got {self.noise_sigma_m}"
            )


@dataclass(frozen=True, slots=True)
class LoraTOAObservation:
    """LoRa time-of-arrival range to a partner node."""

    t_sec: float
    node_id: str
    partner_id: str
    range_m: float
    noise_sigma_m: float

    def __post_init__(self) -> None:
        if self.range_m < 0:
            raise ValueError(
                f"LoraTOAObservation.range_m must be >= 0, got {self.range_m}"
            )
        if self.partner_id == self.node_id:
            raise ValueError(
                f"LoraTOAObservation.partner_id must differ from node_id "
                f"(both '{self.node_id}'); a node cannot range against itself"
            )
        if self.noise_sigma_m <= 0:
            raise ValueError(
                f"LoraTOAObservation.noise_sigma_m must be > 0, got {self.noise_sigma_m}"
            )


Observation: TypeAlias = (
    GPSObservation
    | IMUObservation
    | BaroObservation
    | MagObservation
    | BathyProbeObservation
    | LoraTOAObservation
)


@dataclass(frozen=True, slots=True)
class LoraLinkRecord:
    """A LoRa link attempt between two nodes."""

    t_sec: float
    node_a: str
    node_b: str
    status: str
    range_m: float | None

    def __post_init__(self) -> None:
        if self.status not in VALID_LINK_STATUSES:
            raise ValueError(
                f"Unknown status '{self.status}'. Valid statuses: {VALID_LINK_STATUSES}"
            )

        if self.status == "success":
            if self.range_m is None:
                raise ValueError(
                    f"LoraLinkRecord with status='success' must have range_m populated"
                )
        else:
            if self.range_m is not None:
                raise ValueError(
                    f"LoraLinkRecord with status='{self.status}' must have range_m=None"
                )


@dataclass(frozen=True, slots=True)
class ObservationTickView:
    """Observation-only view of a tick — no truth fields."""

    t: int
    t_sec: float
    observations: tuple[Observation, ...]
    lora_links: tuple[LoraLinkRecord, ...]


class ScenarioReader:
    """Reader for maritime scenario JSONL files (observation-only access)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._header: ScenarioHeader | None = None
        self._header_record: dict | None = read_jsonl_header(
            self._path,
            expected_record_type="header",
            supported_versions=SUPPORTED_SCHEMA_VERSIONS,
        )

    def header(self) -> ScenarioHeader:
        if self._header is None:
            if self._header_record is None:
                raise RuntimeError("Header record not loaded")

            bbox_tuple = tuple(self._header_record["bbox"])
            if len(bbox_tuple) != 4:
                raise ValueError(f"bbox must be a 4-tuple, got {len(bbox_tuple)} elements")

            self._header = ScenarioHeader(
                schema_version=self._header_record["schema_version"],
                bbox=(float(bbox_tuple[0]), float(bbox_tuple[1]), float(bbox_tuple[2]), float(bbox_tuple[3])),
                fleet_composition=dict(self._header_record["fleet_composition"]),
                node_ids=tuple(self._header_record["node_ids"]),
                node_classes=dict(self._header_record["node_classes"]),
                seed=int(self._header_record["seed"]),
                duration_sec=float(self._header_record["duration_sec"]),
                dt_sec=float(self._header_record["dt_sec"]),
                created_at_utc=str(self._header_record["created_at_utc"]),
                onboard_map_path=str(self._header_record["onboard_map_path"]),
                anchor_positions={
                    k: (float(v[0]), float(v[1]))
                    for k, v in self._header_record["anchor_positions"].items()
                },
            )
        return self._header

    def __iter__(self) -> Iterator[ObservationTickView]:
        from rtl.vectors.maritime._scenario_parse import _parse_observations, _parse_lora_links

        with self._path.open("r") as f:
            for line in f:
                if line.strip() == "":
                    continue

                try:
                    record = json.loads(line.strip())
                except json.JSONDecodeError as e:
                    raise ValueError(f"Failed to parse tick line as JSON: {e}") from e

                if record.get("record_type") != "tick":
                    continue

                # Strip truth state (nodes field) - observation-only access
                if "nodes" in record:
                    del record["nodes"]

                t = int(record["t"])
                t_sec = float(record["t_sec"])

                # Parse observations and links using shared helpers
                observations = _parse_observations(record.get("observations", []))
                lora_links = _parse_lora_links(record.get("lora_links", []))

                yield ObservationTickView(
                    t=t,
                    t_sec=t_sec,
                    observations=observations,
                    lora_links=lora_links,
                )

    def onboard_map(self):
        """Load and return the onboard map sidecar as a RegionalMap.

        Returns:
            RegionalMap: The onboard map loaded from the sidecar pickle file.

        Raises:
            FileNotFoundError: If the sidecar file does not exist.
        """
        from rtl.vectors.maritime.map_payload import RegionalMap

        header = self.header()
        sidecar_path = self._path.parent / header.onboard_map_path

        if not sidecar_path.exists():
            raise FileNotFoundError(
                f"Onboard map sidecar file not found: {sidecar_path}"
            )

        with sidecar_path.open("rb") as f:
            onboard_map = pickle.load(f)

        if not isinstance(onboard_map, RegionalMap):
            raise TypeError(
                f"Expected RegionalMap from sidecar file, got {type(onboard_map)}"
            )

        return onboard_map


def assert_golden_trace_matches(produced_path: Path, golden_path: Path) -> None:
    """Assert that two files match byte-for-byte.

    If the files differ, raises AssertionError with a unified diff.
    """
    produced_bytes = produced_path.read_bytes()
    golden_bytes = golden_path.read_bytes()

    if produced_bytes == golden_bytes:
        return None

    produced_lines = produced_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
    golden_lines = golden_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)

    diff = difflib.unified_diff(
        golden_lines,
        produced_lines,
        fromfile=str(golden_path),
        tofile=str(produced_path),
        lineterm="",
    )

    diff_text = "".join(diff)
    raise AssertionError(f"Files differ:\n{diff_text}")
