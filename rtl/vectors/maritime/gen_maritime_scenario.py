#!/usr/bin/env python
"""Maritime scenario generator CLI.

Generates deterministic JSONL scenario files for maritime platform simulations.
Composes fleet, truth/onboard maps, current fields, and sensor observations.
"""

import argparse
import datetime
import json
import math
import pickle
import sys
from pathlib import Path
from typing import cast

import numpy as np

from rtl.vectors.maritime.fleet import Node, make_m1_fleet, is_moored, KIND_MOORED_POSE
from rtl.vectors.maritime.dynamics import propagate_truth, PhysicsEnv
from rtl.vectors.maritime.sensors import (
    GPSSensor,
    IMUSensor,
    BaroSensor,
    MagSensor,
    BathyProbeSensor,
    LoraTOASensor,
    Measurement,
    SensorEnv,
)
from rtl.vectors.maritime.clock import Clock
from rtl.vectors.maritime.platform_profile import MooredPoseSpec
from rtl.vectors.maritime.current_fields import EddySpec, FieldConfig, SyntheticEddyField
from rtl.vectors.maritime.map_payload import (
    RegionalMap,
    generate_synthetic_bathymetry,
    make_onboard_map,
    climatology_from_field,
)
from rtl.vectors.maritime.scenario_schema import SCHEMA_VERSION


_ALL_SENSOR_NAMES: frozenset[str] = frozenset(
    {"gps", "imu", "baro", "mag", "bathy_probe", "lora_toa"}
)


def parse_bbox(bbox_str: str) -> tuple[float, float, float, float]:
    parts = bbox_str.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"Invalid bbox format: '{bbox_str}'. Expected four comma-separated floats: south,west,north,east"
        )
    try:
        floats = tuple(float(p.strip()) for p in parts)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid bbox format: '{bbox_str}'. Expected four comma-separated floats: south,west,north,east"
        )
    return cast(tuple[float, float, float, float], floats)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate maritime scenario JSONL files"
    )
    parser.add_argument(
        "--seed",
        type=int,
        required=True,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--bbox",
        type=parse_bbox,
        required=True,
        help="Bounding box as four comma-separated floats: south,west,north,east",
    )
    parser.add_argument(
        "--duration-hours",
        type=float,
        default=24.0,
        help="Scenario duration in hours (default: 24.0)",
    )
    parser.add_argument(
        "--dt-sec",
        type=float,
        default=60.0,
        help="Time step in seconds (default: 60.0)",
    )
    parser.add_argument(
        "--nodes",
        type=int,
        required=True,
        help="Number of nodes (M1 requires exactly 10)",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Output JSONL file path",
    )
    parser.add_argument(
        "--created-at",
        type=str,
        default=None,
        help=(
            "ISO 8601 UTC timestamp for the header (default: current wall-clock time). "
            "Pin this when byte-identical reproducibility is required (e.g., golden-trace tests)."
        ),
    )
    parser.add_argument(
        "--lora-period-sec",
        type=float,
        default=None,
        help=(
            "Override LoRa TDMA cycle period (seconds). Default: bundled M1 "
            "profile value (3600s — power-budget regime). Set ~60 for "
            "obs-rich accuracy testing."
        ),
    )
    parser.add_argument(
        "--gps-period-sec",
        type=float,
        default=None,
        help=(
            "Override GPS sampling period for anchors (seconds). Default: "
            "bundled M1 profile value (3600s)."
        ),
    )
    parser.add_argument(
        "--enable-sensors",
        type=str,
        default=None,
        help=(
            "Comma-separated subset of sensor names to emit observations from "
            "(e.g. 'lora_toa,imu'). When set, all OTHER sensors are silently "
            "skipped at gen time. LoRa cycles only fire if 'lora_toa' is in "
            "the set. Default: emit all sensors. Used for isolation experiments "
            "in the run pipeline."
        ),
    )
    parser.add_argument(
        "--mean-flow-east-ms", type=float, default=0.0,
        help="Mean eastward flow (m/s). Default 0 (no bulk flow).",
    )
    parser.add_argument(
        "--mean-flow-north-ms", type=float, default=0.0,
        help="Mean northward flow (m/s). Default 0.",
    )
    parser.add_argument(
        "--tidal-amplitude-ms", type=float, default=0.0,
        help="Peak tidal current amplitude (m/s). Default 0 (no tide).",
    )
    parser.add_argument(
        "--tidal-period-sec", type=float, default=44712.0,
        help="Tidal period in seconds. Default 44712 (M2 semidiurnal ~12.42h).",
    )
    parser.add_argument(
        "--tidal-direction-deg", type=float, default=0.0,
        help=(
            "Tidal flow direction in degrees (0 = east, 90 = north). "
            "Default 0 (eastward tide)."
        ),
    )
    parser.add_argument(
        "--eddy", action="append", default=[],
        help=(
            "Add an eddy to the field. Format: "
            "'center_lat,center_lon,radius_m,peak_ms,cyclonic' where cyclonic "
            "is 0 or 1. Repeatable — pass --eddy multiple times for multiple "
            "eddies. Default: no eddies."
        ),
    )
    return parser.parse_args()


def _parse_eddy_spec(raw: str) -> EddySpec:
    """Parse 'lat,lon,radius_m,peak_ms,cyclonic' into an EddySpec."""
    parts = raw.split(",")
    if len(parts) != 5:
        raise ValueError(
            f"--eddy expects 'lat,lon,radius_m,peak_ms,cyclonic' (5 fields); got: {raw!r}"
        )
    lat, lon, radius, peak, cyc = parts
    cyc_int = int(cyc)
    if cyc_int not in (0, 1):
        raise ValueError(
            f"--eddy cyclonic flag must be 0 or 1; got {cyc!r} in: {raw!r}"
        )
    return EddySpec(
        center_lat_deg=float(lat),
        center_lon_deg=float(lon),
        radius_m=float(radius),
        peak_velocity_ms=float(peak),
        cyclonic=bool(cyc_int),
    )


def _build_field_config(args: argparse.Namespace) -> FieldConfig:
    """Compose a FieldConfig from the gen CLI's field-related args."""
    eddies = [_parse_eddy_spec(s) for s in args.eddy]
    return FieldConfig(
        mean_vx_ms=args.mean_flow_east_ms,
        mean_vy_ms=args.mean_flow_north_ms,
        eddies=eddies,
        tidal_amplitude_ms=args.tidal_amplitude_ms,
        tidal_period_sec=args.tidal_period_sec,
        tidal_direction_deg=args.tidal_direction_deg,
    )


def validate_args(args: argparse.Namespace) -> None:
    if args.nodes != 10:
        print(f"Error: M1 requires exactly 10 nodes, got {args.nodes}", file=sys.stderr)
        sys.exit(1)

    if args.duration_hours <= 0:
        print(
            f"Error: --duration-hours must be > 0, got {args.duration_hours}",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.dt_sec <= 0:
        print(
            f"Error: --dt-sec must be > 0, got {args.dt_sec}",
            file=sys.stderr,
        )
        sys.exit(1)


def _measurement_to_obs_dict(
    measurement: Measurement,
    sensor: GPSSensor | IMUSensor | BaroSensor | MagSensor | BathyProbeSensor,
) -> dict:
    """Convert a non-LoRa Measurement to its typed-observation JSONL dict.

    Dispatches on the sensor instance class. The output dict matches the
    typed Observation record's field names with a "type" discriminant.
    """
    if isinstance(sensor, GPSSensor):
        lat, lon = measurement.value
        return {
            "type": "gps",
            "t_sec": measurement.t_sec,
            "node_id": measurement.node_id,
            "lat_deg": float(lat),
            "lon_deg": float(lon),
            "noise_sigma_m": float(sensor.spec.noise_sigma),
        }
    if isinstance(sensor, IMUSensor):
        ax, ay, az, gx, gy, gz = measurement.value
        gyro_sigma = sensor.spec.noise_sigma_secondary
        if gyro_sigma is None:
            raise ValueError(
                f"IMU sensor on node {measurement.node_id} has no "
                f"noise_sigma_secondary; cannot emit IMUObservation"
            )
        return {
            "type": "imu",
            "t_sec": measurement.t_sec,
            "node_id": measurement.node_id,
            "accel_xyz": [float(ax), float(ay), float(az)],
            "gyro_xyz": [float(gx), float(gy), float(gz)],
            "accel_noise_sigma_ms2": float(sensor.spec.noise_sigma),
            "gyro_noise_sigma_rad_s": float(gyro_sigma),
        }
    if isinstance(sensor, BaroSensor):
        (pressure,) = measurement.value
        return {
            "type": "baro",
            "t_sec": measurement.t_sec,
            "node_id": measurement.node_id,
            "pressure_pa": float(pressure),
            "noise_sigma_pa": float(sensor.spec.noise_sigma),
        }
    if isinstance(sensor, MagSensor):
        (heading,) = measurement.value
        return {
            "type": "mag",
            "t_sec": measurement.t_sec,
            "node_id": measurement.node_id,
            "heading_deg": float(heading),
            "noise_sigma_deg": float(sensor.spec.noise_sigma),
        }
    if isinstance(sensor, BathyProbeSensor):
        (depth,) = measurement.value
        return {
            "type": "bathy_probe",
            "t_sec": measurement.t_sec,
            "node_id": measurement.node_id,
            "depth_m": float(depth),
            "noise_sigma_m": float(sensor.spec.noise_sigma),
        }
    raise TypeError(f"Unsupported sensor type for typed-observation emit: {type(sensor).__name__}")


def _lora_measurement_to_obs_dict(measurement: Measurement, partner_id: str) -> dict:
    """Convert one end of a LoRa pair measurement to its typed-observation dict.

    The Measurement carries the bidirectional noisy range; partner_id is
    determined by the caller from the pair loop (the other end of the link).
    """
    (range_m,) = measurement.value
    return {
        "type": "lora_toa",
        "t_sec": measurement.t_sec,
        "node_id": measurement.node_id,
        "partner_id": partner_id,
        "range_m": float(range_m),
        "noise_sigma_m": float(measurement.noise_sigma),
    }


def build_sensors_for_node(node: Node) -> tuple[list, LoraTOASensor]:
    """Instantiate every sensor declared in node.profile.sensors.

    Returns (non_lora_sensors, lora_sensor). Raises if the profile declares an
    unknown sensor name or omits lora_toa (every M1 node must participate in
    LoRa ranging).
    """
    non_lora: list = []
    lora_sensor: LoraTOASensor | None = None
    for sensor_spec in node.profile.sensors:
        if sensor_spec.name == "gps":
            non_lora.append(GPSSensor(spec=sensor_spec))
        elif sensor_spec.name == "imu":
            non_lora.append(IMUSensor(spec=sensor_spec))
        elif sensor_spec.name == "baro":
            non_lora.append(BaroSensor(spec=sensor_spec))
        elif sensor_spec.name == "mag":
            non_lora.append(MagSensor(spec=sensor_spec))
        elif sensor_spec.name == "bathy_probe":
            non_lora.append(BathyProbeSensor(spec=sensor_spec))
        elif sensor_spec.name == "lora_toa":
            lora_sensor = LoraTOASensor(spec=sensor_spec, comms=node.profile.comms)
        else:
            raise ValueError(
                f"Unknown sensor '{sensor_spec.name}' on node {node.node_id} "
                f"(profile {node.profile.class_name}); generator cannot dispatch it."
            )
    if lora_sensor is None:
        raise ValueError(
            f"Node {node.node_id} (profile {node.profile.class_name}) has no lora_toa "
            f"sensor; every M1 fleet member must participate in LoRa ranging."
        )
    return non_lora, lora_sensor


def main() -> None:
    args = parse_args()
    validate_args(args)

    bbox: tuple[float, float, float, float] = args.bbox

    parent_rng = np.random.default_rng(args.seed)

    fleet_seed = int(parent_rng.integers(0, 2**32))
    fleet = list(
        make_m1_fleet(
            fleet_seed,
            bbox,
            lora_period_sec=args.lora_period_sec,
            gps_period_sec=args.gps_period_sec,
        )
    )

    bathy = generate_synthetic_bathymetry(bbox)
    field = SyntheticEddyField(_build_field_config(args))

    climatology_seed = int(parent_rng.integers(0, 2**32))
    climatology = climatology_from_field(field, bbox, seed=climatology_seed)

    truth_map = RegionalMap(
        bathymetry=bathy,
        land_polygons=[],
        shipping_lanes=[],
        climatology=climatology,
    )

    onboard_map_seed = int(parent_rng.integers(0, 2**32))
    onboard_map = make_onboard_map(truth_map, fidelity=0.5, seed=onboard_map_seed)

    out_path = Path(args.out)
    onboard_map_filename = "onboard_map.pkl"
    onboard_map_full_path = out_path.parent / onboard_map_filename

    with open(onboard_map_full_path, "wb") as f:
        pickle.dump(onboard_map, f)

    dynamics_rng = np.random.default_rng(int(parent_rng.integers(0, 2**32)))
    sensor_rng = np.random.default_rng(int(parent_rng.integers(0, 2**32)))
    lora_rng = np.random.default_rng(int(parent_rng.integers(0, 2**32)))

    sensors_by_node: dict[str, list] = {}
    lora_sensors_by_node: dict[str, LoraTOASensor] = {}
    for node in fleet:
        non_lora, lora_sensor = build_sensors_for_node(node)
        sensors_by_node[node.node_id] = non_lora
        lora_sensors_by_node[node.node_id] = lora_sensor

    # Sensor allow-list: defaults to all valid names. A `--enable-sensors`
    # CLI subset narrows it. Both branches produce a frozenset, so all call
    # sites use plain `name in enabled_sensors` membership checks.
    if args.enable_sensors is None:
        enabled_sensors: frozenset[str] = _ALL_SENSOR_NAMES
    else:
        names = tuple(s.strip() for s in args.enable_sensors.split(",") if s.strip())
        unknown = set(names) - _ALL_SENSOR_NAMES
        if unknown:
            print(
                f"Error: --enable-sensors contains unknown names: {sorted(unknown)}; "
                f"valid: {sorted(_ALL_SENSOR_NAMES)}",
                file=sys.stderr,
            )
            sys.exit(1)
        enabled_sensors = frozenset(names)

    duration_sec = args.duration_hours * 3600.0
    num_ticks = math.ceil(duration_sec / args.dt_sec)

    fleet_composition = {"anchor": 0, "ballast_drifter": 0, "pure_drifter": 0}
    node_ids: list[str] = []
    node_classes: dict[str, str] = {}
    anchor_positions: dict[str, tuple[float, float]] = {}

    for node in fleet:
        node_ids.append(node.node_id)
        node_classes[node.node_id] = node.profile.class_name
        fleet_composition[node.profile.class_name] += 1

        if is_moored(node):
            moored_pose = cast(MooredPoseSpec, node.profile.component(KIND_MOORED_POSE))
            anchor_positions[node.node_id] = (
                moored_pose.anchor_lat_deg,
                moored_pose.anchor_lon_deg,
            )

    if args.created_at is not None:
        created_at_utc = args.created_at
    else:
        created_at_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

    header = {
        "record_type": "header",
        "schema_version": SCHEMA_VERSION,
        "bbox": list(bbox),
        "fleet_composition": fleet_composition,
        "node_ids": node_ids,
        "node_classes": node_classes,
        "seed": args.seed,
        "duration_sec": duration_sec,
        "dt_sec": args.dt_sec,
        "created_at_utc": created_at_utc,
        "onboard_map_path": onboard_map_filename,
        "anchor_positions": {
            k: [v[0], v[1]] for k, v in anchor_positions.items()
        },
    }

    # All M1 nodes share the same comms profile, so any node's lora sensor
    # determines the global TDMA cadence.
    cycle_pacemaker = next(iter(lora_sensors_by_node.values()))
    last_lora_cycle_t = float("-inf")

    with open(out_path, "w") as f:
        f.write(json.dumps(header) + "\n")

        last_fire: dict[tuple[str, str], float] = {}

        for t in range(num_ticks):
            t_sec = t * args.dt_sec

            for i, node in enumerate(fleet):
                env = PhysicsEnv(
                    current_field=field,
                    t_sec=t_sec,
                    enu_origin_lat_deg=bbox[0],
                    enu_origin_lon_deg=bbox[1],
                )
                new_state = propagate_truth(node, args.dt_sec, env, dynamics_rng)
                fleet[i] = Node(
                    node_id=node.node_id,
                    profile=node.profile,
                    layout=node.layout,
                    state=new_state,
                    components=node.components,
                )

            sensor_env = SensorEnv(
                enu_origin_lat_deg=bbox[0],
                enu_origin_lon_deg=bbox[1],
                dt_sec=args.dt_sec,
                regional_map=truth_map,
                fleet=tuple(fleet),
            )

            observations: list[dict] = []
            for node in fleet:
                for sensor in sensors_by_node[node.node_id]:
                    if sensor.name not in enabled_sensors:
                        continue
                    last_t = last_fire.get((node.node_id, sensor.name), float("-inf"))
                    if not sensor.should_sample(t_sec, last_t):
                        continue
                    measurement = sensor.sample(node, sensor_env, t_sec, sensor_rng)
                    if measurement is None:
                        continue
                    observations.append(_measurement_to_obs_dict(measurement, sensor))
                    last_fire[(node.node_id, sensor.name)] = t_sec

            lora_links: list[dict] = []
            if "lora_toa" in enabled_sensors and cycle_pacemaker.should_sample(t_sec, last_lora_cycle_t):
                last_lora_cycle_t = t_sec
                for i, node_a in enumerate(fleet):
                    for j in range(i + 1, len(fleet)):
                        node_b = fleet[j]
                        outcome = lora_sensors_by_node[node_a.node_id].sample_link(
                            node_a, node_b, sensor_env, t_sec, lora_rng
                        )
                        lora_links.append(
                            {
                                "t_sec": t_sec,
                                "node_a": node_a.node_id,
                                "node_b": node_b.node_id,
                                "status": outcome.status,
                                "range_m": outcome.range_m,
                            }
                        )
                        if outcome.status == "success":
                            m_a, m_b = outcome.measurements
                            observations.append(
                                _lora_measurement_to_obs_dict(m_a, partner_id=node_b.node_id)
                            )
                            observations.append(
                                _lora_measurement_to_obs_dict(m_b, partner_id=node_a.node_id)
                            )

            for node in fleet:
                clock = cast(Clock, node.components["clock"])
                clock.advance(args.dt_sec)

            tick_record = {
                "record_type": "tick",
                "t": t,
                "t_sec": t_sec,
                "nodes": {node.node_id: node.state.tolist() for node in fleet},
                "observations": observations,
                "lora_links": lora_links,
            }

            f.write(json.dumps(tick_record) + "\n")


if __name__ == "__main__":
    main()
