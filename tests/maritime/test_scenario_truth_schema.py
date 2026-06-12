"""Contract tests for scenario truth schema module.

Tests for truth-access types and ScenarioTruthReader.
"""

import json
import tempfile
from pathlib import Path

import pytest


class TestTruthModuleLocation:
    """Tests for truth module location and structure (Task 6A.1)."""

    def test_truth_module_exists_at_specified_path(self) -> None:
        """rtl/vectors/maritime/scenario_truth_schema.py exists and is a module."""
        import rtl.vectors.maritime.scenario_truth_schema as truth_module

        assert truth_module is not None

    def test_truth_module_defines_truth_tick_view(self) -> None:
        """Truth module defines TruthTickView."""
        from rtl.vectors.maritime.scenario_truth_schema import TruthTickView

        assert TruthTickView is not None

    def test_truth_module_defines_scenario_truth_reader(self) -> None:
        """Truth module defines ScenarioTruthReader."""
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader

        assert ScenarioTruthReader is not None


class TestTruthTickViewStructure:
    """Tests for TruthTickView structure and imports (Task 6A.2)."""

    def test_truth_tick_view_has_expected_fields(self) -> None:
        """TruthTickView has t, t_sec, node_truth, observations, lora_links fields."""
        from rtl.vectors.maritime.scenario_truth_schema import TruthTickView
        from rtl.vectors.maritime.scenario_schema import GPSObservation, LoraLinkRecord

        import numpy as np

        node_truth = {"n00": np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])}
        observations = (
            GPSObservation(
                t_sec=5.0,
                node_id="n00",
                lat_deg=36.75,
                lon_deg=-122.0,
                noise_sigma_m=1.5,
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

        view = TruthTickView(
            t=5,
            t_sec=5.0,
            node_truth=node_truth,
            observations=observations,
            lora_links=lora_links,
        )

        assert view.t == 5
        assert view.t_sec == 5.0
        assert "n00" in view.node_truth
        assert len(view.observations) == 1
        assert len(view.lora_links) == 1

    def test_truth_tick_view_is_immutable(self) -> None:
        """TruthTickView is frozen (immutable)."""
        from rtl.vectors.maritime.scenario_truth_schema import TruthTickView

        import numpy as np

        node_truth = {"n00": np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])}
        observations: tuple = ()
        lora_links: tuple = ()

        view = TruthTickView(
            t=5,
            t_sec=5.0,
            node_truth=node_truth,
            observations=observations,
            lora_links=lora_links,
        )

        # Attempting to modify should raise an error
        with pytest.raises((AttributeError, TypeError)):
            view.t = 10  # type: ignore[misc]

    def test_truth_tick_view_imports_obs_types_from_scenario_schema(self) -> None:
        """TruthTickView imports the Observation union and LoraLinkRecord from scenario_schema."""
        import rtl.vectors.maritime.scenario_truth_schema as truth_module
        import inspect

        # Get the source code
        source = inspect.getsource(truth_module)

        # Check that it imports from scenario_schema
        assert "from rtl.vectors.maritime.scenario_schema import" in source
        assert "Observation" in source
        assert "LoraLinkRecord" in source

        # Check that the imported types are the same objects re-exported through the truth module
        from rtl.vectors.maritime.scenario_schema import Observation as ScenarioObservation
        from rtl.vectors.maritime.scenario_schema import LoraLinkRecord as ScenarioLinkRecord

        assert truth_module.Observation is ScenarioObservation
        assert truth_module.LoraLinkRecord is ScenarioLinkRecord


class TestScenarioTruthReaderYieldsPopulatedNodeTruth:
    """Tests for ScenarioTruthReader yielding TruthTickView with node_truth (Task 6A.3)."""

    def test_reader_yields_truth_tick_view(self) -> None:
        """ScenarioTruthReader yields TruthTickView objects."""
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader, TruthTickView

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
            "nodes": {"n00": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]},
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
            reader = ScenarioTruthReader(temp_path)
            for tick in reader:
                assert isinstance(tick, TruthTickView)
        finally:
            temp_path.unlink()

    def test_reader_yields_truth_tick_view_with_populated_node_truth(self) -> None:
        """ScenarioTruthReader yields TruthTickView with populated node_truth ndarrays."""
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader

        import numpy as np

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
            "t": 0,
            "t_sec": 0.0,
            "nodes": {
                "n00": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                "n01": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
            },
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
            reader = ScenarioTruthReader(temp_path)
            for tick in reader:
                assert "n00" in tick.node_truth
                assert "n01" in tick.node_truth

                # Check that node_truth values are numpy arrays
                assert isinstance(tick.node_truth["n00"], np.ndarray)
                assert isinstance(tick.node_truth["n01"], np.ndarray)

                # Check the values
                np.testing.assert_array_equal(tick.node_truth["n00"], np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))
                np.testing.assert_array_equal(tick.node_truth["n01"], np.array([7.0, 8.0, 9.0, 10.0, 11.0, 12.0]))
        finally:
            temp_path.unlink()

    def test_reader_node_truth_has_correct_shape(self) -> None:
        """Reader decodes nodes into node_truth ndarrays with correct shape."""
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader

        import numpy as np

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

        # 6-element state vector
        tick_record = {
            "record_type": "tick",
            "t": 0,
            "t_sec": 0.0,
            "nodes": {"n00": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]},
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
            reader = ScenarioTruthReader(temp_path)
            for tick in reader:
                assert tick.node_truth["n00"].shape == (6,)
        finally:
            temp_path.unlink()


class TestScenarioTruthReaderHeader:
    """Tests for ScenarioTruthReader.header() returning ScenarioHeader (Task 6A.4)."""

    def test_reader_header_returns_scenario_header(self) -> None:
        """ScenarioTruthReader.header() returns scenario_schema.ScenarioHeader."""
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

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
            "nodes": {"n00": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]},
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
            reader = ScenarioTruthReader(temp_path)
            header = reader.header()
            assert isinstance(header, ScenarioHeader)
        finally:
            temp_path.unlink()

    def test_header_is_shared_type_not_duplicate(self) -> None:
        """ScenarioTruthReader uses the same ScenarioHeader type as scenario_schema."""
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader
        from rtl.vectors.maritime.scenario_schema import ScenarioHeader

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
            "nodes": {"n00": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]},
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
            reader = ScenarioTruthReader(temp_path)
            header = reader.header()

            # It's the exact same type
            assert type(header) is ScenarioHeader
        finally:
            temp_path.unlink()


class TestScenarioTruthReaderRejectsUnknownSchemaVersion:
    """Tests for ScenarioTruthReader rejecting unknown schema version (Task 6A.5)."""

    def test_reader_raises_value_error_for_unknown_schema_version(self) -> None:
        """Reader rejects unknown schema version with ValueError."""
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader

        header_record = {
            "record_type": "header",
            "schema_version": "2.0",  # Unknown version
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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            json.dump(header_record, f)
            f.write("\n")
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError) as exc_info:
                ScenarioTruthReader(temp_path)

            assert "2.0" in str(exc_info.value)
            assert "schema_version" in str(exc_info.value).lower()
        finally:
            temp_path.unlink()


class TestScenarioTruthReaderNotReexported:
    """Tests for ScenarioTruthReader not being re-exported (Task 6A.6)."""

    def test_import_from_package_raises_import_error(self) -> None:
        """from rtl.vectors.maritime import ScenarioTruthReader raises ImportError."""
        with pytest.raises(ImportError):
            from rtl.vectors.maritime import ScenarioTruthReader  # noqa: F401

    def test_import_from_full_path_succeeds(self) -> None:
        """from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader succeeds."""
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader

        assert ScenarioTruthReader is not None


class TestScenarioTruthReaderParsesObservationsAndLinks:
    """Tests for ScenarioTruthReader parsing observations and lora_links."""

    def test_reader_parses_observations_and_links(self) -> None:
        """ScenarioTruthReader parses typed Observation records and LoraLinkRecord."""
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader
        from rtl.vectors.maritime.scenario_schema import (
            GPSObservation,
            IMUObservation,
            LoraLinkRecord,
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
            "nodes": {
                "n00": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                "n01": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
            },
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
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            json.dump(header_record, f)
            f.write("\n")
            json.dump(tick_record, f)
            f.write("\n")
            temp_path = Path(f.name)

        try:
            reader = ScenarioTruthReader(temp_path)
            for tick in reader:
                assert tick.t == 5
                assert tick.t_sec == 5.0
                assert len(tick.observations) == 2
                assert len(tick.lora_links) == 1
                assert len(tick.node_truth) == 2

                assert isinstance(tick.observations[0], GPSObservation)
                assert tick.observations[0].node_id == "n00"
                assert tick.observations[0].lat_deg == 48.0

                assert isinstance(tick.observations[1], IMUObservation)
                assert tick.observations[1].accel_xyz == (0.1, 0.0, 9.8)

                assert isinstance(tick.lora_links[0], LoraLinkRecord)
                assert tick.lora_links[0].node_a == "n00"
                assert tick.lora_links[0].status == "success"
        finally:
            temp_path.unlink()
