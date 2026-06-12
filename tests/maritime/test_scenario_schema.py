"""Contract tests for scenario schema module.

Tests for scenario versioning and header structure.
"""

import json
import tempfile
from pathlib import Path

import pytest


class TestSchemaVersionConstants:
    """Tests for schema version module constants."""

    def test_schema_version_is_1_0(self) -> None:
        """SCHEMA_VERSION == '1.0'."""
        from rtl.vectors.maritime.scenario_schema import SCHEMA_VERSION

        assert SCHEMA_VERSION == "1.0"

    def test_supported_schema_versions_contains_1_0(self) -> None:
        """SUPPORTED_SCHEMA_VERSIONS contains '1.0'."""
        from rtl.vectors.maritime.scenario_schema import SUPPORTED_SCHEMA_VERSIONS

        assert "1.0" in SUPPORTED_SCHEMA_VERSIONS


class TestScenarioHeaderConstruction:
    """Tests for ScenarioHeader dataclass construction and validation."""

    def test_valid_header_constructs_successfully(self) -> None:
        """Valid header with all required fields constructs successfully and returns ScenarioHeader."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

        header = ScenarioHeader(
            schema_version="1.0",
            bbox=(36.0, -122.5, 36.5, -122.0),
            fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
            node_ids=("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09"),
            node_classes={
                "n00": "anchor",
                "n01": "anchor",
                "n02": "ballast_drifter",
                "n03": "ballast_drifter",
                "n04": "ballast_drifter",
                "n05": "ballast_drifter",
                "n06": "pure_drifter",
                "n07": "pure_drifter",
                "n08": "pure_drifter",
                "n09": "pure_drifter",
            },
            seed=42,
            duration_sec=3600.0,
            dt_sec=60.0,
            created_at_utc="2026-04-20T00:00:00Z",
            onboard_map_path="onboard_map.json",
            anchor_positions={
                "n00": (36.0, -122.5),
                "n01": (36.5, -122.0),
            },
        )

        assert header.schema_version == "1.0"
        assert header.bbox == (36.0, -122.5, 36.5, -122.0)
        assert header.fleet_composition == {"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4}
        assert len(header.node_ids) == 10
        assert header.seed == 42
        assert header.duration_sec == 3600.0
        assert header.dt_sec == 60.0
        assert header.created_at_utc == "2026-04-20T00:00:00Z"
        assert header.onboard_map_path == "onboard_map.json"
        assert len(header.anchor_positions) == 2

    def test_header_with_bbox_inversion_raises_value_error(self) -> None:
        """Header with bbox inversion (lat_south > lat_north) raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

        with pytest.raises(ValueError) as exc_info:
            ScenarioHeader(
                schema_version="1.0",
                bbox=(49.0, -123.2, 48.4, -123.8),  # lat_south > lat_north
                fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
                node_ids=("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09"),
                node_classes={
                    "n00": "anchor",
                    "n01": "anchor",
                    "n02": "ballast_drifter",
                    "n03": "ballast_drifter",
                    "n04": "ballast_drifter",
                    "n05": "ballast_drifter",
                    "n06": "pure_drifter",
                    "n07": "pure_drifter",
                    "n08": "pure_drifter",
                    "n09": "pure_drifter",
                },
                seed=42,
                duration_sec=3600.0,
                dt_sec=60.0,
                created_at_utc="2026-04-20T00:00:00Z",
                onboard_map_path="onboard_map.json",
                anchor_positions={
                    "n00": (49.0, -123.2),
                    "n01": (48.4, -123.8),
                },
            )

        assert "bbox" in str(exc_info.value).lower()

    def test_header_with_non_positive_duration_raises_value_error(self) -> None:
        """Header with duration_sec <= 0 raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

        with pytest.raises(ValueError) as exc_info:
            ScenarioHeader(
                schema_version="1.0",
                bbox=(36.0, -122.5, 36.5, -122.0),
                fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
                node_ids=("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09"),
                node_classes={
                    "n00": "anchor",
                    "n01": "anchor",
                    "n02": "ballast_drifter",
                    "n03": "ballast_drifter",
                    "n04": "ballast_drifter",
                    "n05": "ballast_drifter",
                    "n06": "pure_drifter",
                    "n07": "pure_drifter",
                    "n08": "pure_drifter",
                    "n09": "pure_drifter",
                },
                seed=42,
                duration_sec=0.0,  # Invalid
                dt_sec=60.0,
                created_at_utc="2026-04-20T00:00:00Z",
                onboard_map_path="onboard_map.json",
                anchor_positions={
                    "n00": (36.0, -122.5),
                    "n01": (36.5, -122.0),
                },
            )

        assert "duration_sec" in str(exc_info.value)

    def test_header_with_unknown_schema_version_raises_value_error(self) -> None:
        """Unknown schema_version raises ValueError naming the version and the supported set."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader, SUPPORTED_SCHEMA_VERSIONS

        with pytest.raises(ValueError) as exc_info:
            ScenarioHeader(
                schema_version="2.0",  # Unknown version
                bbox=(36.0, -122.5, 36.5, -122.0),
                fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
                node_ids=("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09"),
                node_classes={
                    "n00": "anchor",
                    "n01": "anchor",
                    "n02": "ballast_drifter",
                    "n03": "ballast_drifter",
                    "n04": "ballast_drifter",
                    "n05": "ballast_drifter",
                    "n06": "pure_drifter",
                    "n07": "pure_drifter",
                    "n08": "pure_drifter",
                    "n09": "pure_drifter",
                },
                seed=42,
                duration_sec=3600.0,
                dt_sec=60.0,
                created_at_utc="2026-04-20T00:00:00Z",
                onboard_map_path="onboard_map.json",
                anchor_positions={
                    "n00": (36.0, -122.5),
                    "n01": (36.5, -122.0),
                },
            )

        error_msg = str(exc_info.value)
        assert "2.0" in error_msg
        assert "1.0" in error_msg
        assert str(SUPPORTED_SCHEMA_VERSIONS) in error_msg

    def test_header_with_empty_node_ids_raises_value_error(self) -> None:
        """Header with empty node_ids raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

        with pytest.raises(ValueError) as exc_info:
            ScenarioHeader(
                schema_version="1.0",
                bbox=(36.0, -122.5, 36.5, -122.0),
                fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
                node_ids=(),  # Empty
                node_classes={},
                seed=42,
                duration_sec=3600.0,
                dt_sec=60.0,
                created_at_utc="2026-04-20T00:00:00Z",
                onboard_map_path="onboard_map.json",
                anchor_positions={},
            )

        assert "node_ids" in str(exc_info.value)

    def test_header_with_non_positive_dt_sec_raises_value_error(self) -> None:
        """Header with dt_sec <= 0 raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

        with pytest.raises(ValueError) as exc_info:
            ScenarioHeader(
                schema_version="1.0",
                bbox=(36.0, -122.5, 36.5, -122.0),
                fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
                node_ids=("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09"),
                node_classes={
                    "n00": "anchor",
                    "n01": "anchor",
                    "n02": "ballast_drifter",
                    "n03": "ballast_drifter",
                    "n04": "ballast_drifter",
                    "n05": "ballast_drifter",
                    "n06": "pure_drifter",
                    "n07": "pure_drifter",
                    "n08": "pure_drifter",
                    "n09": "pure_drifter",
                },
                seed=42,
                duration_sec=3600.0,
                dt_sec=0.0,  # Invalid
                created_at_utc="2026-04-20T00:00:00Z",
                onboard_map_path="onboard_map.json",
                anchor_positions={
                    "n00": (36.0, -122.5),
                    "n01": (36.5, -122.0),
                },
            )

        assert "dt_sec" in str(exc_info.value)

    def test_header_with_missing_node_class_raises_value_error(self) -> None:
        """Header where node_classes does not cover all node_ids raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

        with pytest.raises(ValueError) as exc_info:
            ScenarioHeader(
                schema_version="1.0",
                bbox=(36.0, -122.5, 36.5, -122.0),
                fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
                node_ids=("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09"),
                node_classes={
                    "n00": "anchor",
                    "n01": "anchor",
                    # Missing n02, n03, n04, n05, n06, n07, n08, n09
                },
                seed=42,
                duration_sec=3600.0,
                dt_sec=60.0,
                created_at_utc="2026-04-20T00:00:00Z",
                onboard_map_path="onboard_map.json",
                anchor_positions={
                    "n00": (36.0, -122.5),
                    "n01": (36.5, -122.0),
                },
            )

        assert "node_classes" in str(exc_info.value)

    def test_header_with_extraneous_node_class_raises_value_error(self) -> None:
        """Header where node_classes contains a key not in node_ids raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

        with pytest.raises(ValueError) as exc_info:
            ScenarioHeader(
                schema_version="1.0",
                bbox=(36.0, -122.5, 36.5, -122.0),
                fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
                node_ids=("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09"),
                node_classes={
                    "n00": "anchor",
                    "n01": "anchor",
                    "n02": "ballast_drifter",
                    "n03": "ballast_drifter",
                    "n04": "ballast_drifter",
                    "n05": "ballast_drifter",
                    "n06": "pure_drifter",
                    "n07": "pure_drifter",
                    "n08": "pure_drifter",
                    "n09": "pure_drifter",
                    "n99": "anchor",  # Extraneous key
                },
                seed=42,
                duration_sec=3600.0,
                dt_sec=60.0,
                created_at_utc="2026-04-20T00:00:00Z",
                onboard_map_path="onboard_map.json",
                anchor_positions={
                    "n00": (36.0, -122.5),
                    "n01": (36.5, -122.0),
                },
            )

        assert "node_classes" in str(exc_info.value)

    def test_header_with_fleet_composition_mismatch_raises_value_error(self) -> None:
        """Header where node_classes counts don't match fleet_composition raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

        with pytest.raises(ValueError) as exc_info:
            ScenarioHeader(
                schema_version="1.0",
                bbox=(36.0, -122.5, 36.5, -122.0),
                fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
                node_ids=("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09"),
                node_classes={
                    "n00": "anchor",
                    "n01": "anchor",
                    "n02": "ballast_drifter",
                    "n03": "ballast_drifter",
                    "n04": "ballast_drifter",
                    "n05": "ballast_drifter",
                    "n06": "ballast_drifter",  # 5 ballast_drifters, but fleet_composition says 4
                    "n07": "pure_drifter",
                    "n08": "pure_drifter",
                    "n09": "pure_drifter",  # 3 pure_drifters, but fleet_composition says 4
                },
                seed=42,
                duration_sec=3600.0,
                dt_sec=60.0,
                created_at_utc="2026-04-20T00:00:00Z",
                onboard_map_path="onboard_map.json",
                anchor_positions={
                    "n00": (36.0, -122.5),
                    "n01": (36.5, -122.0),
                },
            )

        assert "fleet_composition" in str(exc_info.value)

    def test_header_with_missing_anchor_position_raises_value_error(self) -> None:
        """Header where anchor_positions does not cover all anchor node_ids raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

        with pytest.raises(ValueError) as exc_info:
            ScenarioHeader(
                schema_version="1.0",
                bbox=(36.0, -122.5, 36.5, -122.0),
                fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
                node_ids=("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09"),
                node_classes={
                    "n00": "anchor",
                    "n01": "anchor",
                    "n02": "ballast_drifter",
                    "n03": "ballast_drifter",
                    "n04": "ballast_drifter",
                    "n05": "ballast_drifter",
                    "n06": "pure_drifter",
                    "n07": "pure_drifter",
                    "n08": "pure_drifter",
                    "n09": "pure_drifter",
                },
                seed=42,
                duration_sec=3600.0,
                dt_sec=60.0,
                created_at_utc="2026-04-20T00:00:00Z",
                onboard_map_path="onboard_map.json",
                anchor_positions={
                    "n00": (36.0, -122.5),
                    # Missing n01
                },
            )

        assert "anchor_positions" in str(exc_info.value)

    def test_header_with_extraneous_anchor_position_raises_value_error(self) -> None:
        """Header where anchor_positions contains a key that is not an anchor raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

        with pytest.raises(ValueError) as exc_info:
            ScenarioHeader(
                schema_version="1.0",
                bbox=(36.0, -122.5, 36.5, -122.0),
                fleet_composition={"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4},
                node_ids=("n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n09"),
                node_classes={
                    "n00": "anchor",
                    "n01": "anchor",
                    "n02": "ballast_drifter",
                    "n03": "ballast_drifter",
                    "n04": "ballast_drifter",
                    "n05": "ballast_drifter",
                    "n06": "pure_drifter",
                    "n07": "pure_drifter",
                    "n08": "pure_drifter",
                    "n09": "pure_drifter",
                },
                seed=42,
                duration_sec=3600.0,
                dt_sec=60.0,
                created_at_utc="2026-04-20T00:00:00Z",
                onboard_map_path="onboard_map.json",
                anchor_positions={
                    "n00": (36.0, -122.5),
                    "n01": (36.5, -122.0),
                    "n02": (36.2, -122.2),  # n02 is a ballast_drifter, not an anchor
                },
            )

        assert "anchor_positions" in str(exc_info.value)


class TestScenarioReaderMissingHeader:
    """Tests for ScenarioReader handling missing header."""

    def test_file_with_missing_header_raises_value_error(self) -> None:
        """File whose first line is a tick record (not a header) raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ScenarioReader

        tick_record = {
            "record_type": "tick",
            "t": 0,
            "t_sec": 0.0,
            "observations": [],
            "lora_links": [],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            json.dump(tick_record, f)
            f.write("\n")
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError) as exc_info:
                ScenarioReader(temp_path)

            assert "header" in str(exc_info.value).lower()
        finally:
            temp_path.unlink()


class TestObservationTickView:
    """Tests for ObservationTickView dataclass."""

    def test_valid_tick_view_has_expected_fields(self) -> None:
        """Valid tick view decodes with expected t, t_sec, observation count, link count."""
        from rtl.vectors.maritime.scenario_schema import (
            GPSObservation,
            IMUObservation,
            ObservationTickView,
            LoraLinkRecord,
        )

        observations = (
            GPSObservation(
                t_sec=5.0,
                node_id="n00",
                lat_deg=36.75,
                lon_deg=-122.0,
                noise_sigma_m=1.5,
            ),
            IMUObservation(
                t_sec=5.0,
                node_id="n01",
                accel_xyz=(0.1, 0.0, 9.8),
                gyro_xyz=(0.0, 0.0, 0.0),
                accel_noise_sigma_ms2=0.01,
                gyro_noise_sigma_rad_s=0.005,
            ),
        )

        lora_links = (
            LoraLinkRecord(
                t_sec=5.0,
                node_a="n00",
                node_b="n01",
                status="success",
                range_m=3500.0,
            ),
        )

        tick_view = ObservationTickView(
            t=5,
            t_sec=5.0,
            observations=observations,
            lora_links=lora_links,
        )

        assert tick_view.t == 5
        assert tick_view.t_sec == 5.0
        assert len(tick_view.observations) == 2
        assert len(tick_view.lora_links) == 1

    def test_tick_view_missing_t_sec_raises_value_error(self) -> None:
        """Tick view constructed without t_sec raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import ObservationTickView

        with pytest.raises(TypeError):
            ObservationTickView(  # type: ignore[call-arg]
                t=5,
                observations=(),
                lora_links=(),
            )

    def test_tick_view_has_no_truth_fields(self) -> None:
        """ObservationTickView has no truth-related attributes."""
        from rtl.vectors.maritime.scenario_schema import ObservationTickView

        tick_view = ObservationTickView(
            t=5,
            t_sec=5.0,
            observations=(),
            lora_links=(),
        )

        assert not hasattr(tick_view, "node_truth")
        assert not hasattr(tick_view, "truth")
        assert not hasattr(tick_view, "nodes")

        with pytest.raises(AttributeError):
            tick_view.node_truth  # type: ignore[attr-defined]

        with pytest.raises(AttributeError):
            tick_view.truth  # type: ignore[attr-defined]

        with pytest.raises(AttributeError):
            tick_view.nodes  # type: ignore[attr-defined]


class TestTypedObservationRecords:
    """Construction tests for the six typed observation records (Tasks 2.1–2.4)."""

    def test_gps_observation_constructs_with_valid_fields(self) -> None:
        from rtl.vectors.maritime.scenario_schema import GPSObservation

        obs = GPSObservation(
            t_sec=5.01,
            node_id="anchor_01",
            lat_deg=48.6,
            lon_deg=-123.5,
            noise_sigma_m=1.5,
        )
        assert obs.lat_deg == 48.6
        assert obs.lon_deg == -123.5
        assert obs.noise_sigma_m == 1.5

    def test_gps_observation_rejects_lat_out_of_range(self) -> None:
        from rtl.vectors.maritime.scenario_schema import GPSObservation

        with pytest.raises(ValueError) as exc_info:
            GPSObservation(t_sec=0.0, node_id="n00", lat_deg=95.0, lon_deg=0.0, noise_sigma_m=1.0)
        assert "lat_deg" in str(exc_info.value)

    def test_gps_observation_rejects_lon_out_of_range(self) -> None:
        from rtl.vectors.maritime.scenario_schema import GPSObservation

        with pytest.raises(ValueError) as exc_info:
            GPSObservation(t_sec=0.0, node_id="n00", lat_deg=0.0, lon_deg=181.0, noise_sigma_m=1.0)
        assert "lon_deg" in str(exc_info.value)

    def test_gps_observation_rejects_zero_sigma(self) -> None:
        from rtl.vectors.maritime.scenario_schema import GPSObservation

        with pytest.raises(ValueError):
            GPSObservation(t_sec=0.0, node_id="n00", lat_deg=0.0, lon_deg=0.0, noise_sigma_m=0.0)

    def test_imu_observation_carries_separate_accel_and_gyro_sigmas(self) -> None:
        from rtl.vectors.maritime.scenario_schema import IMUObservation

        obs = IMUObservation(
            t_sec=1.0,
            node_id="n00",
            accel_xyz=(0.1, 0.0, 9.8),
            gyro_xyz=(0.0, 0.0, 0.0),
            accel_noise_sigma_ms2=0.05,
            gyro_noise_sigma_rad_s=0.005,
        )
        assert obs.accel_noise_sigma_ms2 == 0.05
        assert obs.gyro_noise_sigma_rad_s == 0.005
        # No joint noise_sigma field exists.
        assert not hasattr(obs, "noise_sigma")

    def test_imu_observation_rejects_negative_accel_sigma(self) -> None:
        from rtl.vectors.maritime.scenario_schema import IMUObservation

        with pytest.raises(ValueError):
            IMUObservation(
                t_sec=0.0,
                node_id="n00",
                accel_xyz=(0.0, 0.0, 0.0),
                gyro_xyz=(0.0, 0.0, 0.0),
                accel_noise_sigma_ms2=-0.01,
                gyro_noise_sigma_rad_s=0.005,
            )

    def test_imu_observation_rejects_zero_gyro_sigma(self) -> None:
        from rtl.vectors.maritime.scenario_schema import IMUObservation

        with pytest.raises(ValueError):
            IMUObservation(
                t_sec=0.0,
                node_id="n00",
                accel_xyz=(0.0, 0.0, 0.0),
                gyro_xyz=(0.0, 0.0, 0.0),
                accel_noise_sigma_ms2=0.05,
                gyro_noise_sigma_rad_s=0.0,
            )

    def test_baro_observation_constructs_with_valid_fields(self) -> None:
        from rtl.vectors.maritime.scenario_schema import BaroObservation

        obs = BaroObservation(t_sec=0.0, node_id="n00", pressure_pa=101325.0, noise_sigma_pa=10.0)
        assert obs.pressure_pa == 101325.0
        assert obs.noise_sigma_pa == 10.0

    def test_baro_observation_rejects_zero_pressure(self) -> None:
        from rtl.vectors.maritime.scenario_schema import BaroObservation

        with pytest.raises(ValueError):
            BaroObservation(t_sec=0.0, node_id="n00", pressure_pa=0.0, noise_sigma_pa=10.0)

    def test_mag_observation_rejects_heading_at_360(self) -> None:
        from rtl.vectors.maritime.scenario_schema import MagObservation

        # 360 is out of [0, 360)
        with pytest.raises(ValueError):
            MagObservation(t_sec=0.0, node_id="n00", heading_deg=360.0, noise_sigma_deg=0.5)

    def test_mag_observation_rejects_negative_heading(self) -> None:
        from rtl.vectors.maritime.scenario_schema import MagObservation

        with pytest.raises(ValueError):
            MagObservation(t_sec=0.0, node_id="n00", heading_deg=-1.0, noise_sigma_deg=0.5)

    def test_bathy_probe_observation_rejects_negative_depth(self) -> None:
        from rtl.vectors.maritime.scenario_schema import BathyProbeObservation

        with pytest.raises(ValueError):
            BathyProbeObservation(t_sec=0.0, node_id="n00", depth_m=-5.0, noise_sigma_m=2.0)

    def test_bathy_probe_observation_accepts_zero_depth(self) -> None:
        from rtl.vectors.maritime.scenario_schema import BathyProbeObservation

        obs = BathyProbeObservation(t_sec=0.0, node_id="n00", depth_m=0.0, noise_sigma_m=2.0)
        assert obs.depth_m == 0.0

    def test_lora_toa_observation_rejects_self_partner(self) -> None:
        from rtl.vectors.maritime.scenario_schema import LoraTOAObservation

        with pytest.raises(ValueError) as exc_info:
            LoraTOAObservation(
                t_sec=0.0,
                node_id="n00",
                partner_id="n00",
                range_m=100.0,
                noise_sigma_m=20.0,
            )
        assert "partner_id" in str(exc_info.value)

    def test_lora_toa_observation_accepts_non_anchor_partner(self) -> None:
        """The schema does not know about anchor identity — any partner_id != node_id is valid here."""
        from rtl.vectors.maritime.scenario_schema import LoraTOAObservation

        obs = LoraTOAObservation(
            t_sec=0.0,
            node_id="drifter_42",
            partner_id="drifter_99",
            range_m=500.0,
            noise_sigma_m=20.0,
        )
        assert obs.partner_id == "drifter_99"

    def test_lora_toa_observation_rejects_negative_range(self) -> None:
        from rtl.vectors.maritime.scenario_schema import LoraTOAObservation

        with pytest.raises(ValueError):
            LoraTOAObservation(
                t_sec=0.0,
                node_id="n00",
                partner_id="n01",
                range_m=-10.0,
                noise_sigma_m=20.0,
            )

    def test_no_typed_record_has_unit_or_noise_unit_field(self) -> None:
        """Units live in field-name suffixes, not in a standalone field (Task 2.4)."""
        from rtl.vectors.maritime.scenario_schema import (
            BaroObservation,
            BathyProbeObservation,
            GPSObservation,
            IMUObservation,
            LoraTOAObservation,
            MagObservation,
        )

        records = [
            GPSObservation(t_sec=0.0, node_id="n", lat_deg=0.0, lon_deg=0.0, noise_sigma_m=1.0),
            IMUObservation(
                t_sec=0.0,
                node_id="n",
                accel_xyz=(0.0, 0.0, 0.0),
                gyro_xyz=(0.0, 0.0, 0.0),
                accel_noise_sigma_ms2=0.01,
                gyro_noise_sigma_rad_s=0.001,
            ),
            BaroObservation(t_sec=0.0, node_id="n", pressure_pa=101325.0, noise_sigma_pa=10.0),
            MagObservation(t_sec=0.0, node_id="n", heading_deg=0.0, noise_sigma_deg=0.5),
            BathyProbeObservation(t_sec=0.0, node_id="n", depth_m=10.0, noise_sigma_m=5.0),
            LoraTOAObservation(
                t_sec=0.0, node_id="n", partner_id="m", range_m=100.0, noise_sigma_m=20.0
            ),
        ]
        for rec in records:
            assert not hasattr(rec, "unit"), f"{type(rec).__name__} unexpectedly has 'unit' field"
            assert not hasattr(rec, "noise_unit"), (
                f"{type(rec).__name__} unexpectedly has 'noise_unit' field"
            )


class TestObservationUnionExhaustiveness:
    """A match statement over Observation handles all six members (Task 2.5)."""

    def test_match_returns_expected_tag_per_member(self) -> None:
        from rtl.vectors.maritime.scenario_schema import (
            BaroObservation,
            BathyProbeObservation,
            GPSObservation,
            IMUObservation,
            LoraTOAObservation,
            MagObservation,
            Observation,
        )

        def tag_for(obs: Observation) -> str:
            match obs:
                case GPSObservation():
                    return "gps"
                case IMUObservation():
                    return "imu"
                case BaroObservation():
                    return "baro"
                case MagObservation():
                    return "mag"
                case BathyProbeObservation():
                    return "bathy_probe"
                case LoraTOAObservation():
                    return "lora_toa"

        cases: list[tuple[Observation, str]] = [
            (GPSObservation(t_sec=0.0, node_id="n", lat_deg=0.0, lon_deg=0.0, noise_sigma_m=1.0), "gps"),
            (
                IMUObservation(
                    t_sec=0.0,
                    node_id="n",
                    accel_xyz=(0.0, 0.0, 0.0),
                    gyro_xyz=(0.0, 0.0, 0.0),
                    accel_noise_sigma_ms2=0.01,
                    gyro_noise_sigma_rad_s=0.001,
                ),
                "imu",
            ),
            (BaroObservation(t_sec=0.0, node_id="n", pressure_pa=101325.0, noise_sigma_pa=10.0), "baro"),
            (MagObservation(t_sec=0.0, node_id="n", heading_deg=10.0, noise_sigma_deg=0.5), "mag"),
            (BathyProbeObservation(t_sec=0.0, node_id="n", depth_m=10.0, noise_sigma_m=5.0), "bathy_probe"),
            (
                LoraTOAObservation(
                    t_sec=0.0, node_id="n", partner_id="m", range_m=100.0, noise_sigma_m=20.0
                ),
                "lora_toa",
            ),
        ]
        for obs, expected in cases:
            assert tag_for(obs) == expected


class TestJSONLDiscriminantDispatch:
    """Reader dispatches on the JSONL "type" key (Tasks 4.1–4.3)."""

    def _wrap_with_header(self, observations: list[dict]) -> tuple[dict, dict]:
        header_record = {
            "record_type": "header",
            "schema_version": "1.0",
            "bbox": [48.0, -124.0, 49.0, -123.0],
            "fleet_composition": {"anchor": 1},
            "node_ids": ["n00"],
            "node_classes": {"n00": "anchor"},
            "seed": 42,
            "duration_sec": 60.0,
            "dt_sec": 1.0,
            "created_at_utc": "2026-01-01T00:00:00Z",
            "onboard_map_path": "onboard_map.pkl",
            "anchor_positions": {"n00": [48.0, -124.0]},
        }
        tick_record = {
            "record_type": "tick",
            "t": 0,
            "t_sec": 0.0,
            "observations": observations,
            "lora_links": [],
        }
        return header_record, tick_record

    def _write_and_read(self, header_record: dict, tick_record: dict):
        from rtl.vectors.maritime.scenario_schema import ScenarioReader

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            json.dump(header_record, f)
            f.write("\n")
            json.dump(tick_record, f)
            f.write("\n")
            path = Path(f.name)
        try:
            reader = ScenarioReader(path)
            ticks = list(reader)
            return ticks
        finally:
            path.unlink()

    def test_each_known_type_parses_into_matching_class(self) -> None:
        from rtl.vectors.maritime.scenario_schema import (
            BaroObservation,
            BathyProbeObservation,
            GPSObservation,
            IMUObservation,
            LoraTOAObservation,
            MagObservation,
        )

        observations = [
            {"type": "gps", "t_sec": 0.0, "node_id": "n00", "lat_deg": 48.0, "lon_deg": -123.5, "noise_sigma_m": 1.5},
            {
                "type": "imu",
                "t_sec": 0.0,
                "node_id": "n00",
                "accel_xyz": [0.0, 0.0, 9.8],
                "gyro_xyz": [0.0, 0.0, 0.0],
                "accel_noise_sigma_ms2": 0.01,
                "gyro_noise_sigma_rad_s": 0.001,
            },
            {"type": "baro", "t_sec": 0.0, "node_id": "n00", "pressure_pa": 101325.0, "noise_sigma_pa": 10.0},
            {"type": "mag", "t_sec": 0.0, "node_id": "n00", "heading_deg": 90.0, "noise_sigma_deg": 0.5},
            {"type": "bathy_probe", "t_sec": 0.0, "node_id": "n00", "depth_m": 200.0, "noise_sigma_m": 5.0},
            {
                "type": "lora_toa",
                "t_sec": 0.0,
                "node_id": "n00",
                "partner_id": "n01",
                "range_m": 500.0,
                "noise_sigma_m": 20.0,
            },
        ]
        header_record, tick_record = self._wrap_with_header(observations)
        ticks = self._write_and_read(header_record, tick_record)
        assert len(ticks) == 1
        obs = ticks[0].observations
        assert isinstance(obs[0], GPSObservation)
        assert isinstance(obs[1], IMUObservation)
        assert isinstance(obs[2], BaroObservation)
        assert isinstance(obs[3], MagObservation)
        assert isinstance(obs[4], BathyProbeObservation)
        assert isinstance(obs[5], LoraTOAObservation)
        assert obs[5].partner_id == "n01"

    def test_unknown_type_discriminant_raises(self) -> None:
        observations = [
            {"type": "sonar", "t_sec": 0.0, "node_id": "n00", "value": [1.0]},
        ]
        header_record, tick_record = self._wrap_with_header(observations)
        with pytest.raises(ValueError) as exc_info:
            self._write_and_read(header_record, tick_record)
        assert "sonar" in str(exc_info.value)

    def test_legacy_sensor_value_record_rejected(self) -> None:
        observations = [
            {
                "t_sec": 0.0,
                "node_id": "n00",
                "sensor": "gps",
                "value": [48.0, -123.5],
                "unit": "deg",
                "noise_sigma": 1.5,
            }
        ]
        header_record, tick_record = self._wrap_with_header(observations)
        with pytest.raises(ValueError) as exc_info:
            self._write_and_read(header_record, tick_record)
        assert "legacy" in str(exc_info.value).lower()


class TestLoraLinkRecord:
    """Tests for LoraLinkRecord dataclass."""

    def test_successful_link_has_range(self) -> None:
        """Successful link has range_m populated."""
        from rtl.vectors.maritime.scenario_schema import LoraLinkRecord

        link = LoraLinkRecord(
            t_sec=5.0,
            node_a="n00",
            node_b="n01",
            status="success",
            range_m=3500.0,
        )

        assert link.status == "success"
        assert link.range_m == 3500.0

    def test_dropped_link_has_none_range(self) -> None:
        """Dropped link has range_m is None."""
        from rtl.vectors.maritime.scenario_schema import LoraLinkRecord

        link = LoraLinkRecord(
            t_sec=5.0,
            node_a="n00",
            node_b="n01",
            status="dropped",
            range_m=None,
        )

        assert link.status == "dropped"
        assert link.range_m is None

    def test_out_of_range_link_has_none_range(self) -> None:
        """Out of range link has range_m is None."""
        from rtl.vectors.maritime.scenario_schema import LoraLinkRecord

        link = LoraLinkRecord(
            t_sec=5.0,
            node_a="n00",
            node_b="n01",
            status="out_of_range",
            range_m=None,
        )

        assert link.status == "out_of_range"
        assert link.range_m is None

    def test_successful_link_without_range_raises_value_error(self) -> None:
        """Successful link without range_m raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import LoraLinkRecord

        with pytest.raises(ValueError) as exc_info:
            LoraLinkRecord(
                t_sec=5.0,
                node_a="n00",
                node_b="n01",
                status="success",
                range_m=None,  # Invalid for success status
            )

        assert "range_m" in str(exc_info.value).lower()
        assert "success" in str(exc_info.value).lower()

    def test_dropped_link_with_range_raises_value_error(self) -> None:
        """Dropped link with range_m raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import LoraLinkRecord

        with pytest.raises(ValueError) as exc_info:
            LoraLinkRecord(
                t_sec=5.0,
                node_a="n00",
                node_b="n01",
                status="dropped",
                range_m=3500.0,  # Invalid for dropped status
            )

        assert "range_m" in str(exc_info.value).lower()
        assert "dropped" in str(exc_info.value).lower()

    def test_out_of_range_link_with_range_raises_value_error(self) -> None:
        """Out of range link with range_m raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import LoraLinkRecord

        with pytest.raises(ValueError) as exc_info:
            LoraLinkRecord(
                t_sec=5.0,
                node_a="n00",
                node_b="n01",
                status="out_of_range",
                range_m=3500.0,  # Invalid for out_of_range status
            )

        assert "range_m" in str(exc_info.value).lower()
        assert "out_of_range" in str(exc_info.value).lower()

    def test_link_with_invalid_status_raises_value_error(self) -> None:
        """Link with invalid status raises ValueError."""
        from rtl.vectors.maritime.scenario_schema import LoraLinkRecord

        with pytest.raises(ValueError) as exc_info:
            LoraLinkRecord(
                t_sec=5.0,
                node_a="n00",
                node_b="n01",
                status="invalid_status",
                range_m=None,
            )

        assert "invalid_status" in str(exc_info.value)


class TestGoldenTraceComparison:
    """Tests for golden trace comparison helper."""

    def test_identical_files_match_returns_none(self) -> None:
        """Identical files match — assert_golden_trace_matches returns None."""
        from rtl.vectors.maritime.scenario_schema import assert_golden_trace_matches

        content = b'{"record_type":"header","schema_version":"1.0"}\n'

        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f1:
            f1.write(content)
            produced_path = Path(f1.name)

        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f2:
            f2.write(content)
            golden_path = Path(f2.name)

        try:
            result = assert_golden_trace_matches(produced_path, golden_path)
            assert result is None
        finally:
            produced_path.unlink()
            golden_path.unlink()

    def test_single_byte_difference_raises_assertion_error_with_unified_diff(self) -> None:
        """Single-byte difference raises AssertionError with a unified diff in the message."""
        from rtl.vectors.maritime.scenario_schema import assert_golden_trace_matches

        golden_content = b'{"record_type":"header","schema_version":"1.0"}\n'
        produced_content = b'{"record_type":"header","schema_version":"1.1"}\n'

        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f1:
            f1.write(produced_content)
            produced_path = Path(f1.name)

        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f2:
            f2.write(golden_content)
            golden_path = Path(f2.name)

        try:
            with pytest.raises(AssertionError) as exc_info:
                assert_golden_trace_matches(produced_path, golden_path)

            error_msg = str(exc_info.value)
            assert "Files differ:" in error_msg
            assert "---" in error_msg
            assert "+++" in error_msg
            assert "@" in error_msg
        finally:
            produced_path.unlink()
            golden_path.unlink()


class TestScenarioReaderIteration:
    """Tests for ScenarioReader iteration behavior."""

    def test_reader_yields_observation_tick_view_not_dict(self) -> None:
        """ScenarioReader yields ObservationTickView objects, not raw dicts."""
        from rtl.vectors.maritime.scenario_schema import ScenarioReader, ObservationTickView

        header_record = {
            "record_type": "header",
            "schema_version": "1.0",
            "bbox": [48.0, -124.0, 49.0, -123.0],
            "fleet_composition": {"anchor": 1},
            "node_ids": ["n00"],
            "node_classes": {"n00": "anchor"},
            "seed": 42,
            "duration_sec": 60.0,
            "dt_sec": 1.0,
            "created_at_utc": "2026-01-01T00:00:00Z",
            "onboard_map_path": "onboard_map.pkl",
            "anchor_positions": {"n00": [48.0, -124.0]},
        }

        tick_record = {
            "record_type": "tick",
            "t": 0,
            "t_sec": 0.0,
            "nodes": {"n00": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
            "observations": [],
            "lora_links": [],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            json.dump(header_record, f)
            f.write("\n")
            json.dump(tick_record, f)
            f.write("\n")
            temp_path = Path(f.name)

        try:
            reader = ScenarioReader(temp_path)
            for tick in reader:
                assert isinstance(tick, ObservationTickView)
                assert not isinstance(tick, dict)
        finally:
            temp_path.unlink()

    def test_reader_strips_nodes_field_from_yielded_views(self) -> None:
        """ScenarioReader strips the nodes field from tick records before yielding."""
        from rtl.vectors.maritime.scenario_schema import ScenarioReader, ObservationTickView

        header_record = {
            "record_type": "header",
            "schema_version": "1.0",
            "bbox": [48.0, -124.0, 49.0, -123.0],
            "fleet_composition": {"anchor": 1},
            "node_ids": ["n00"],
            "node_classes": {"n00": "anchor"},
            "seed": 42,
            "duration_sec": 60.0,
            "dt_sec": 1.0,
            "created_at_utc": "2026-01-01T00:00:00Z",
            "onboard_map_path": "onboard_map.pkl",
            "anchor_positions": {"n00": [48.0, -124.0]},
        }

        tick_record_with_nodes = {
            "record_type": "tick",
            "t": 0,
            "t_sec": 0.0,
            "nodes": {"n00": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
            "observations": [
                {
                    "type": "gps",
                    "t_sec": 0.0,
                    "node_id": "n00",
                    "lat_deg": 48.0,
                    "lon_deg": -124.0,
                    "noise_sigma_m": 1.0,
                }
            ],
            "lora_links": [],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            json.dump(header_record, f)
            f.write("\n")
            json.dump(tick_record_with_nodes, f)
            f.write("\n")
            temp_path = Path(f.name)

        try:
            reader = ScenarioReader(temp_path)
            for tick in reader:
                assert isinstance(tick, ObservationTickView)
                assert not hasattr(tick, "nodes")
                with pytest.raises(AttributeError):
                    tick.nodes  # type: ignore[attr-defined]
        finally:
            temp_path.unlink()

    def test_yielded_view_has_no_truth_attributes(self) -> None:
        """Yielded ObservationTickView has no node_truth, truth, or nodes attributes."""
        from rtl.vectors.maritime.scenario_schema import ScenarioReader, ObservationTickView

        header_record = {
            "record_type": "header",
            "schema_version": "1.0",
            "bbox": [48.0, -124.0, 49.0, -123.0],
            "fleet_composition": {"anchor": 1},
            "node_ids": ["n00"],
            "node_classes": {"n00": "anchor"},
            "seed": 42,
            "duration_sec": 60.0,
            "dt_sec": 1.0,
            "created_at_utc": "2026-01-01T00:00:00Z",
            "onboard_map_path": "onboard_map.pkl",
            "anchor_positions": {"n00": [48.0, -124.0]},
        }

        tick_record = {
            "record_type": "tick",
            "t": 0,
            "t_sec": 0.0,
            "observations": [],
            "lora_links": [],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            json.dump(header_record, f)
            f.write("\n")
            json.dump(tick_record, f)
            f.write("\n")
            temp_path = Path(f.name)

        try:
            reader = ScenarioReader(temp_path)
            for tick in reader:
                assert not hasattr(tick, "node_truth")
                assert not hasattr(tick, "truth")
                assert not hasattr(tick, "nodes")

                with pytest.raises(AttributeError):
                    tick.node_truth  # type: ignore[attr-defined]

                with pytest.raises(AttributeError):
                    tick.truth  # type: ignore[attr-defined]

                with pytest.raises(AttributeError):
                    tick.nodes  # type: ignore[attr-defined]
        finally:
            temp_path.unlink()

    def test_scenario_truth_reader_not_exported_from_module(self) -> None:
        """ScenarioTruthReader is not defined in scenario_schema module."""
        with pytest.raises(ImportError):
            from rtl.vectors.maritime.scenario_schema import ScenarioTruthReader  # noqa: F401

    def test_truth_tick_view_not_defined_in_module(self) -> None:
        """TruthTickView is not defined in scenario_schema module."""
        with pytest.raises(ImportError):
            from rtl.vectors.maritime.scenario_schema import TruthTickView  # noqa: F401

    def test_scenario_reader_parses_observations_and_links(self) -> None:
        """ScenarioReader parses typed Observation records and LoraLinkRecord."""
        from rtl.vectors.maritime.scenario_schema import (
            GPSObservation,
            IMUObservation,
            LoraLinkRecord,
            ScenarioReader,
        )

        header_record = {
            "record_type": "header",
            "schema_version": "1.0",
            "bbox": [48.0, -124.0, 49.0, -123.0],
            "fleet_composition": {"anchor": 2},
            "node_ids": ["n00", "n01"],
            "node_classes": {"n00": "anchor", "n01": "anchor"},
            "seed": 42,
            "duration_sec": 60.0,
            "dt_sec": 1.0,
            "created_at_utc": "2026-01-01T00:00:00Z",
            "onboard_map_path": "onboard_map.pkl",
            "anchor_positions": {"n00": [48.0, -124.0], "n01": [48.5, -123.5]},
        }

        tick_record = {
            "record_type": "tick",
            "t": 5,
            "t_sec": 5.0,
            "observations": [
                {
                    "type": "gps",
                    "t_sec": 5.0,
                    "node_id": "n00",
                    "lat_deg": 48.0,
                    "lon_deg": -124.0,
                    "noise_sigma_m": 1.5,
                },
                {
                    "type": "imu",
                    "t_sec": 5.0,
                    "node_id": "n01",
                    "accel_xyz": [0.1, 0.0, 9.8],
                    "gyro_xyz": [0.0, 0.0, 0.0],
                    "accel_noise_sigma_ms2": 0.01,
                    "gyro_noise_sigma_rad_s": 0.001,
                },
            ],
            "lora_links": [
                {
                    "t_sec": 5.0,
                    "node_a": "n00",
                    "node_b": "n01",
                    "status": "success",
                    "range_m": 3500.0,
                },
                {
                    "t_sec": 5.0,
                    "node_a": "n00",
                    "node_b": "n01",
                    "status": "dropped",
                    "range_m": None,
                },
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            json.dump(header_record, f)
            f.write("\n")
            json.dump(tick_record, f)
            f.write("\n")
            temp_path = Path(f.name)

        try:
            reader = ScenarioReader(temp_path)
            for tick in reader:
                assert tick.t == 5
                assert tick.t_sec == 5.0
                assert len(tick.observations) == 2
                assert len(tick.lora_links) == 2

                assert isinstance(tick.observations[0], GPSObservation)
                assert tick.observations[0].node_id == "n00"
                assert tick.observations[0].lat_deg == 48.0
                assert tick.observations[0].lon_deg == -124.0

                assert isinstance(tick.observations[1], IMUObservation)
                assert tick.observations[1].node_id == "n01"
                assert tick.observations[1].accel_xyz == (0.1, 0.0, 9.8)

                assert isinstance(tick.lora_links[0], LoraLinkRecord)
                assert tick.lora_links[0].node_a == "n00"
                assert tick.lora_links[0].node_b == "n01"
                assert tick.lora_links[0].status == "success"
                assert tick.lora_links[0].range_m == 3500.0

                assert isinstance(tick.lora_links[1], LoraLinkRecord)
                assert tick.lora_links[1].status == "dropped"
                assert tick.lora_links[1].range_m is None
        finally:
            temp_path.unlink()
