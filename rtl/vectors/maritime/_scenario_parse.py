"""Internal shared parsing helpers for scenario readers.

This module is an internal implementation detail (underscore prefix) that
provides shared parsing logic for both ScenarioReader and ScenarioTruthReader.
Both readers parse the same file format; they differ in what they yield.

Observation records discriminate on the JSONL ``"type"`` key per the typed-
observation schema (see ``Observation`` in ``scenario_schema``). Records
shaped per the legacy ``"sensor"`` + ``"value"`` schema raise ``ValueError``
— there is no implicit migration.
"""

from rtl.vectors.maritime.scenario_schema import (
    BaroObservation,
    BathyProbeObservation,
    GPSObservation,
    IMUObservation,
    LoraLinkRecord,
    LoraTOAObservation,
    MagObservation,
    Observation,
    VALID_OBSERVATION_TYPES,
)


def _parse_one_observation(obs: dict) -> Observation:
    if "type" not in obs:
        if "sensor" in obs and "value" in obs:
            raise ValueError(
                "Observation record uses legacy schema (keys 'sensor' + "
                "'value'); the typed-observation schema requires a 'type' "
                "discriminant. No silent migration is performed."
            )
        raise ValueError(
            f"Observation record missing required 'type' discriminant: keys={sorted(obs.keys())}"
        )

    type_tag = str(obs["type"])

    if type_tag not in VALID_OBSERVATION_TYPES:
        raise ValueError(
            f"Unknown observation type '{type_tag}'. "
            f"Supported types: {sorted(VALID_OBSERVATION_TYPES)}"
        )

    t_sec = float(obs["t_sec"])
    node_id = str(obs["node_id"])

    if type_tag == "gps":
        return GPSObservation(
            t_sec=t_sec,
            node_id=node_id,
            lat_deg=float(obs["lat_deg"]),
            lon_deg=float(obs["lon_deg"]),
            noise_sigma_m=float(obs["noise_sigma_m"]),
        )
    if type_tag == "imu":
        accel = obs["accel_xyz"]
        gyro = obs["gyro_xyz"]
        return IMUObservation(
            t_sec=t_sec,
            node_id=node_id,
            accel_xyz=(float(accel[0]), float(accel[1]), float(accel[2])),
            gyro_xyz=(float(gyro[0]), float(gyro[1]), float(gyro[2])),
            accel_noise_sigma_ms2=float(obs["accel_noise_sigma_ms2"]),
            gyro_noise_sigma_rad_s=float(obs["gyro_noise_sigma_rad_s"]),
        )
    if type_tag == "baro":
        return BaroObservation(
            t_sec=t_sec,
            node_id=node_id,
            pressure_pa=float(obs["pressure_pa"]),
            noise_sigma_pa=float(obs["noise_sigma_pa"]),
        )
    if type_tag == "mag":
        return MagObservation(
            t_sec=t_sec,
            node_id=node_id,
            heading_deg=float(obs["heading_deg"]),
            noise_sigma_deg=float(obs["noise_sigma_deg"]),
        )
    if type_tag == "bathy_probe":
        return BathyProbeObservation(
            t_sec=t_sec,
            node_id=node_id,
            depth_m=float(obs["depth_m"]),
            noise_sigma_m=float(obs["noise_sigma_m"]),
        )
    if type_tag == "lora_toa":
        return LoraTOAObservation(
            t_sec=t_sec,
            node_id=node_id,
            partner_id=str(obs["partner_id"]),
            range_m=float(obs["range_m"]),
            noise_sigma_m=float(obs["noise_sigma_m"]),
        )

    raise AssertionError(f"unreachable: type_tag '{type_tag}' passed validation but no branch matched")


def _parse_observations(obs_list: list[dict]) -> tuple[Observation, ...]:
    """Parse observations from JSON list into a tuple of typed Observation records.

    Discriminates on the ``"type"`` key. Raises ``ValueError`` on missing or
    unknown discriminant, or on legacy ``"sensor"``/``"value"`` shape.
    """
    return tuple(_parse_one_observation(obs) for obs in obs_list)


def _parse_lora_links(link_list: list[dict]) -> tuple[LoraLinkRecord, ...]:
    """Parse LoRa links from JSON list into tuple of LoraLinkRecord."""
    return tuple(
        LoraLinkRecord(
            t_sec=float(link["t_sec"]),
            node_a=str(link["node_a"]),
            node_b=str(link["node_b"]),
            status=str(link["status"]),
            range_m=float(link["range_m"]) if link.get("range_m") is not None else None,
        )
        for link in link_list
    )
