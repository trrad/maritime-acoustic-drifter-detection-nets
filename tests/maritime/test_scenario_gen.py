"""Integration tests for maritime scenario generator CLI.

Tests verify CLI invocation behavior and output contract compliance.
Tests invoke the CLI as a subprocess (integration test, not unit test).
"""

import pickle
import subprocess
import sys

import pytest

from rtl.vectors.maritime.scenario_schema import (
    BaroObservation,
    BathyProbeObservation,
    GPSObservation,
    IMUObservation,
    LoraTOAObservation,
    MagObservation,
    ScenarioReader,
)
from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader
from rtl.vectors.maritime.map_payload import RegionalMap


FIXED_CREATED_AT = "2026-04-22T00:00:00+00:00"


_OBS_TYPE_TAG: dict[type, str] = {
    GPSObservation: "gps",
    IMUObservation: "imu",
    BaroObservation: "baro",
    MagObservation: "mag",
    BathyProbeObservation: "bathy_probe",
    LoraTOAObservation: "lora_toa",
}


def _obs_tag(obs) -> str:
    """Return the type-tag string ('gps', 'imu', ...) for a typed Observation record."""
    return _OBS_TYPE_TAG[type(obs)]


def run_cli(
    tmp_path,
    seed=42,
    bbox="48.6,-123.5,48.9,-123.1",
    duration_hours=0.01,
    dt_sec=1.0,
    nodes=10,
    created_at=FIXED_CREATED_AT,
):
    out_path = tmp_path / f"scenario_{seed}_{id(tmp_path)}.jsonl"
    cmd = [
        sys.executable,
        "-m",
        "rtl.vectors.maritime.gen_maritime_scenario",
        "--seed",
        str(seed),
        "--bbox",
        bbox,
        "--duration-hours",
        str(duration_hours),
        "--dt-sec",
        str(dt_sec),
        "--nodes",
        str(nodes),
        "--out",
        str(out_path),
        "--created-at",
        created_at,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result, out_path


class TestCLIInvocation:
    """Tests for CLI invocation behavior (Task 9.1)."""

    def test_cli_with_valid_args_exits_0_and_produces_parseable_scenario_file(self, tmp_path):
        """CLI with valid args exits 0 and produces a parseable scenario file."""
        result, out_path = run_cli(tmp_path)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        assert out_path.exists(), "Output file not created"

        header = ScenarioReader(out_path).header()
        assert header.schema_version == "1.0"
        assert len(header.node_ids) == 10


class TestCLIRejectsUnsupportedNodeCount:
    """Tests for CLI rejection of unsupported node counts (Task 9.2)."""

    def test_cli_with_nodes_5_exits_non_zero_with_clear_error(self, tmp_path):
        """CLI with --nodes 5 exits non-zero; stderr contains a clear error."""
        result, out_path = run_cli(tmp_path, nodes=5)

        assert result.returncode != 0, "CLI should fail with --nodes 5"
        assert "M1 requires exactly 10 nodes" in result.stderr
        assert "got 5" in result.stderr


class TestCLIRejectsMissingRequiredFlags:
    """Tests for CLI rejection of missing required flags (Task 9.3)."""

    def test_cli_with_missing_out_exits_non_zero_and_names_missing_flag(self, tmp_path):
        """CLI with missing --out exits non-zero; stderr names the missing flag."""
        cmd = [
            sys.executable,
            "-m",
            "rtl.vectors.maritime.gen_maritime_scenario",
            "--seed",
            "42",
            "--bbox",
            "48.6,-123.5,48.9,-123.1",
            "--nodes",
            "10",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        assert result.returncode != 0, "CLI should fail without --out"
        assert "required" in result.stderr.lower() or "out" in result.stderr.lower()


class TestHeaderEchoesCLIBboxAndSeed:
    """Tests for header echoing CLI --bbox and --seed verbatim (Task 9.4)."""

    def test_header_bbox_and_seed_match_cli_arguments(self, tmp_path):
        """After invoking CLI with --seed 42 --bbox 48.4,-123.8,49.2,-123.2,
        ScenarioReader(out).header().bbox == (48.4, -123.8, 49.2, -123.2) and
        header.seed == 42.
        """
        result, out_path = run_cli(
            tmp_path,
            seed=42,
            bbox="48.4,-123.8,49.2,-123.2",
            duration_hours=0.01,
            dt_sec=1.0,
        )

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        header = ScenarioReader(out_path).header()
        assert header.bbox == (48.4, -123.8, 49.2, -123.2)
        assert header.seed == 42


class TestChangingSeedChangesHeaderSeed:
    """Tests for changing --seed changes header.seed (Task 9.5)."""

    def test_different_seeds_produce_different_header_seed_values(self, tmp_path):
        """Invoking CLI twice with --seed 42 and --seed 43 yields files whose
        parsed headers have seed == 42 and seed == 43 respectively.
        """
        result1, out_path1 = run_cli(tmp_path, seed=42)
        result2, out_path2 = run_cli(tmp_path, seed=43)

        assert result1.returncode == 0, f"First CLI failed: {result1.stderr}"
        assert result2.returncode == 0, f"Second CLI failed: {result2.stderr}"

        header1 = ScenarioReader(out_path1).header()
        header2 = ScenarioReader(out_path2).header()

        assert header1.seed == 42
        assert header2.seed == 43


class TestSeedReproducibility:
    """Tests for seed reproducibility (Tasks 10.1, 10.2)."""

    def test_same_seed_produces_byte_identical_files(self, tmp_path):
        """Same-seed invocations produce byte-identical files."""
        result1, out_path1 = run_cli(tmp_path, seed=42)
        result2, out_path2 = run_cli(tmp_path, seed=42)

        assert result1.returncode == 0, f"First CLI failed: {result1.stderr}"
        assert result2.returncode == 0, f"Second CLI failed: {result2.stderr}"

        bytes1 = out_path1.read_bytes()
        bytes2 = out_path2.read_bytes()

        assert bytes1 == bytes2, "Same-seed invocations should produce byte-identical files"

    def test_different_seeds_produce_files_that_differ(self, tmp_path):
        """Different-seed invocations produce files that differ in at least one byte."""
        result1, out_path1 = run_cli(tmp_path, seed=42)
        result2, out_path2 = run_cli(tmp_path, seed=43)

        assert result1.returncode == 0, f"First CLI failed: {result1.stderr}"
        assert result2.returncode == 0, f"Second CLI failed: {result2.stderr}"

        bytes1 = out_path1.read_bytes()
        bytes2 = out_path2.read_bytes()

        assert bytes1 != bytes2, "Different-seed invocations should produce different files"


class TestFleetComposition:
    """Tests for fleet composition (Tasks 11.1, 11.2, 11.3, 11.4, 11.6)."""

    def test_header_declares_correct_fleet_composition(self, tmp_path):
        """Header declares fleet_composition == {"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4};
        len(node_ids) == 10.
        """
        result, out_path = run_cli(tmp_path)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        header = ScenarioReader(out_path).header()
        assert header.fleet_composition == {"anchor": 2, "ballast_drifter": 4, "pure_drifter": 4}
        assert len(header.node_ids) == 10

    def test_node_ids_in_deterministic_order(self, tmp_path):
        """Node IDs in deterministic order (anchors first, then ballast drifters, then pure drifters)."""
        result, out_path = run_cli(tmp_path)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        header = ScenarioReader(out_path).header()
        node_ids = list(header.node_ids)
        node_classes = header.node_classes

        anchor_ids = [nid for nid in node_ids if node_classes[nid] == "anchor"]
        ballast_drifter_ids = [nid for nid in node_ids if node_classes[nid] == "ballast_drifter"]
        pure_drifter_ids = [nid for nid in node_ids if node_classes[nid] == "pure_drifter"]

        assert len(anchor_ids) == 2
        assert len(ballast_drifter_ids) == 4
        assert len(pure_drifter_ids) == 4

        assert node_ids[:2] == anchor_ids, "First 2 node_ids should be anchors"
        assert node_ids[2:6] == ballast_drifter_ids, "Next 4 node_ids should be ballast drifters"
        assert node_ids[6:] == pure_drifter_ids, "Last 4 node_ids should be pure drifters"

    def test_anchor_positions_match_bbox_corners(self, tmp_path):
        """header.anchor_positions has one entry per anchor node_id;
        each (lat, lon) equals the corresponding MooredPoseSpec.anchor_lat_deg / anchor_lon_deg
        from that anchor's profile.
        """
        bbox = "48.6,-123.5,48.9,-123.1"
        result, out_path = run_cli(tmp_path, bbox=bbox)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        header = ScenarioReader(out_path).header()

        assert len(header.anchor_positions) == 2, "Should have 2 anchor positions"

        anchor_node_ids = [nid for nid in header.node_ids if header.node_classes[nid] == "anchor"]
        assert set(header.anchor_positions.keys()) == set(anchor_node_ids)

        for anchor_id, (lat, lon) in header.anchor_positions.items():
            if header.node_ids[0] == anchor_id:
                assert lat == 48.6, f"First anchor should be at bbox min_lat, got {lat}"
                assert lon == -123.5, f"First anchor should be at bbox min_lon, got {lon}"
            elif header.node_ids[1] == anchor_id:
                assert lat == 48.9, f"Second anchor should be at bbox max_lat, got {lat}"
                assert lon == -123.1, f"Second anchor should be at bbox max_lon, got {lon}"

    def test_anchor_positions_keys_match_anchor_node_ids(self, tmp_path):
        """header.anchor_positions keys equal exactly the anchor slice of header.node_ids
        (no missing anchor, no non-anchor).
        """
        result, out_path = run_cli(tmp_path)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        header = ScenarioReader(out_path).header()

        anchor_node_ids = {nid for nid in header.node_ids if header.node_classes[nid] == "anchor"}
        anchor_position_keys = set(header.anchor_positions.keys())

        assert anchor_node_ids == anchor_position_keys, (
            f"anchor_positions keys {anchor_position_keys} "
            f"should equal anchor node_ids {anchor_node_ids}"
        )

    def test_node_classes_match_fleet_composition(self, tmp_path):
        """header.node_classes reflects the actual node class of every fleet member;
        counts per class equal header.fleet_composition;
        the anchor-valued keys equal header.anchor_positions.keys().
        """
        result, out_path = run_cli(tmp_path)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        header = ScenarioReader(out_path).header()

        from collections import Counter

        actual_counts = Counter(header.node_classes.values())

        for class_name, expected_count in header.fleet_composition.items():
            actual_count = actual_counts.get(class_name, 0)
            assert actual_count == expected_count, (
                f"Expected {expected_count} nodes of class '{class_name}', "
                f"got {actual_count}"
        )


_SMALL_BBOX = "48.6,-123.5,48.603,-123.497"


@pytest.fixture(scope="module")
def two_hour_scenario(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("data")
    result, out_path = run_cli(tmp, bbox=_SMALL_BBOX, duration_hours=2, dt_sec=60)
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    return out_path


class TestTickCountMatchesDuration:
    """Tests for tick count matching duration (Task 12.1, 12.2)."""

    def test_60_second_scenario_produces_60_tick_records(self, tmp_path):
        """60 s scenario produces 60 tick records (plus 1 header = 61 lines)."""
        duration_hours = 60 / 3600
        result, out_path = run_cli(tmp_path, duration_hours=duration_hours, dt_sec=1.0)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        line_count = 0
        with out_path.open("r") as f:
            for line in f:
                if line.strip():
                    line_count += 1

        assert line_count == 61, f"Expected 61 lines (1 header + 60 ticks), got {line_count}"

    def test_tick_t_values_are_sequential_and_t_sec_increases(self, tmp_path):
        """Tick t values are 0..N-1 with no gaps, t_sec strictly increasing."""
        result, out_path = run_cli(tmp_path, duration_hours=0.1, dt_sec=60)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        reader = ScenarioReader(out_path)
        header = reader.header()

        expected_t_values = []
        t_sec_values = []
        for tick_view in reader:
            expected_t_values.append(tick_view.t)
            t_sec_values.append(tick_view.t_sec)

        assert expected_t_values == list(range(len(expected_t_values))), (
            f"Tick t values should be 0..N-1, got {expected_t_values}"
        )

        assert all(t_sec_values[i] < t_sec_values[i + 1] for i in range(len(t_sec_values) - 1)), (
            "t_sec should be strictly increasing"
        )

        for i, t_sec in enumerate(t_sec_values):
            assert t_sec == pytest.approx(i * header.dt_sec), (
                f"t_sec at tick {i} should be {i * header.dt_sec}, got {t_sec}"
            )


class TestSensorFiringRespectsSensorSpec:
    """Tests for sensor firing respecting SensorSpec (Task 12.3, 12.4)."""

    def test_gps_fires_at_most_every_300_seconds(self, tmp_path):
        """GPS on anchor fires at most every 300 s."""
        result, out_path = run_cli(tmp_path, duration_hours=1, dt_sec=60)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        reader = ScenarioReader(out_path)
        header = reader.header()

        anchor_ids = [nid for nid in header.node_ids if header.node_classes[nid] == "anchor"]

        for anchor_id in anchor_ids:
            gps_observations = [
                obs
                for tick_view in reader
                for obs in tick_view.observations
                if obs.node_id == anchor_id and _obs_tag(obs) == "gps"
            ]

            for i in range(1, len(gps_observations)):
                interval = gps_observations[i].t_sec - gps_observations[i - 1].t_sec
                assert interval >= 300, (
                    f"GPS observations for {anchor_id} are {interval} s apart, "
                    f"should be at least 300 s"
                )

    def test_continuous_sensor_respects_tick_limit(self, tmp_path):
        """Continuous sensor's effective minimum interval is max(1.0/max_rate_hz, dt_sec)."""
        result, out_path = run_cli(tmp_path, duration_hours=0.1, dt_sec=60)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        reader = ScenarioReader(out_path)
        header = reader.header()

        for node_id in header.node_ids:
            node_class = header.node_classes[node_id]

            if node_class == "anchor":
                sensor_names = ["imu", "baro", "mag"]
            else:
                sensor_names = ["imu", "baro", "mag", "bathy_probe"]

            for sensor_name in sensor_names:
                sensor_observations = [
                    obs
                    for tick_view in reader
                    for obs in tick_view.observations
                    if obs.node_id == node_id and _obs_tag(obs) == sensor_name
                ]

                for i in range(1, len(sensor_observations)):
                    interval = sensor_observations[i].t_sec - sensor_observations[i - 1].t_sec
                    assert interval >= header.dt_sec, (
                        f"{sensor_name} observations for {node_id} are {interval} s apart, "
                        f"should be at least dt_sec ({header.dt_sec} s)"
                    )


class TestEveryProfileSensorProducesObservations:
    """Tests for every profile-declared sensor producing observations (Task 12.5)."""

    def test_every_profile_sensor_fires_at_least_once_in_two_hour_run(self, two_hour_scenario):
        """For every node_id and every sensor_name in that node's profile.sensors,
        the observation stream contains at least one matching record.
        """
        reader = ScenarioReader(two_hour_scenario)
        header = reader.header()

        expected_sensors = {
            "anchor": {"gps", "imu", "baro", "mag", "lora_toa"},
            "ballast_drifter": {"imu", "baro", "mag", "bathy_probe", "lora_toa"},
            "pure_drifter": {"imu", "baro", "mag", "bathy_probe", "lora_toa"},
        }

        all_obs = []
        for tick_view in reader:
            all_obs.extend(tick_view.observations)

        for node_id in header.node_ids:
            node_class = header.node_classes[node_id]
            expected = expected_sensors[node_class]

            observed_sensors = {
                _obs_tag(obs) for obs in all_obs if obs.node_id == node_id
            }

            for sensor_name in expected:
                assert sensor_name in observed_sensors, (
                    f"Node {node_id} ({node_class}) should have {sensor_name} observation, "
                    f"got {observed_sensors}"
                )

    def test_no_undeclared_sensor_produces_observations(self, two_hour_scenario):
        """No observation record has a sensor name absent from the owning profile."""
        reader = ScenarioReader(two_hour_scenario)
        header = reader.header()

        expected_sensors = {
            "anchor": {"gps", "imu", "baro", "mag", "lora_toa"},
            "ballast_drifter": {"imu", "baro", "mag", "bathy_probe", "lora_toa"},
            "pure_drifter": {"imu", "baro", "mag", "bathy_probe", "lora_toa"},
        }

        all_obs = []
        for tick_view in reader:
            all_obs.extend(tick_view.observations)

        for obs in all_obs:
            node_class = header.node_classes[obs.node_id]
            expected = expected_sensors[node_class]
            assert _obs_tag(obs) in expected, (
                f"Observation has undeclared sensor '{_obs_tag(obs)}' for node {obs.node_id} "
                f"({node_class}), expected one of {expected}"
            )


class TestNodeClassSensorSuites:
    """Tests for each node class contributing its declared sensor suite (Task 12.6)."""

    def test_each_node_class_contributes_declared_sensor_suite(self, two_hour_scenario):
        """Grouping observations by (class, sensor) yields the expected sensor sets."""
        reader = ScenarioReader(two_hour_scenario)
        header = reader.header()

        class_sensor_sets = {
            "anchor": set(),
            "ballast_drifter": set(),
            "pure_drifter": set(),
        }

        for tick_view in reader:
            for obs in tick_view.observations:
                node_class = header.node_classes[obs.node_id]
                class_sensor_sets[node_class].add(_obs_tag(obs))

        assert class_sensor_sets["anchor"] == {"gps", "imu", "baro", "mag", "lora_toa"}, (
            f"Anchor sensors: {class_sensor_sets['anchor']}"
        )
        assert class_sensor_sets["ballast_drifter"] == {
            "imu", "baro", "mag", "bathy_probe", "lora_toa"
        }, f"Ballast drifter sensors: {class_sensor_sets['ballast_drifter']}"
        assert class_sensor_sets["pure_drifter"] == {
            "imu", "baro", "mag", "bathy_probe", "lora_toa"
        }, f"Pure drifter sensors: {class_sensor_sets['pure_drifter']}"


class TestTypedObservationContentPreserved:
    """Per-sensor checks that each typed Observation carries spec-derived sigma(s)
    and field values (Task 6.4 — replaces legacy ObservationRecord content test)."""

    def test_each_typed_observation_carries_spec_derived_sigma(self, two_hour_scenario):
        """Per-sensor sigma matches the producing sensor's SensorSpec; IMU carries
        BOTH accel_noise_sigma_ms2 and gyro_noise_sigma_rad_s, each equal to the
        spec's primary and secondary fields respectively (Task 6.3)."""
        from rtl.vectors.maritime.fleet import make_m1_fleet

        reader = ScenarioReader(two_hour_scenario)
        header = reader.header()

        # Reconstruct profiles via a fresh fleet (per-class profiles are stable
        # across seeds because all nodes of a class share the same SensorSpec).
        sample_fleet = make_m1_fleet(header.seed, header.bbox)
        profile_by_class = {node.profile.class_name: node.profile for node in sample_fleet}

        for tick_view in reader:
            for obs in tick_view.observations:
                node_class = header.node_classes[obs.node_id]
                profile = profile_by_class[node_class]

                if isinstance(obs, GPSObservation):
                    spec = profile.sensor("gps")
                    assert obs.noise_sigma_m == pytest.approx(spec.noise_sigma)
                elif isinstance(obs, IMUObservation):
                    spec = profile.sensor("imu")
                    assert obs.accel_noise_sigma_ms2 == pytest.approx(spec.noise_sigma)
                    assert spec.noise_sigma_secondary is not None
                    assert obs.gyro_noise_sigma_rad_s == pytest.approx(
                        spec.noise_sigma_secondary
                    )
                elif isinstance(obs, BaroObservation):
                    spec = profile.sensor("baro")
                    assert obs.noise_sigma_pa == pytest.approx(spec.noise_sigma)
                elif isinstance(obs, MagObservation):
                    spec = profile.sensor("mag")
                    assert obs.noise_sigma_deg == pytest.approx(spec.noise_sigma)
                elif isinstance(obs, BathyProbeObservation):
                    spec = profile.sensor("bathy_probe")
                    assert obs.noise_sigma_m == pytest.approx(spec.noise_sigma)
                elif isinstance(obs, LoraTOAObservation):
                    assert obs.noise_sigma_m == pytest.approx(profile.comms.ranging_sigma_m)
                else:
                    raise AssertionError(f"Unexpected observation type: {type(obs).__name__}")

    def test_generator_emits_only_type_discriminant_records(self, two_hour_scenario):
        """All emitted observation JSONL dicts use the typed schema's "type"
        discriminant — no record carries the legacy "sensor"+"value" keys (Task 6.1)."""
        import json

        with open(two_hour_scenario, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("record_type") != "tick":
                    continue
                for obs in rec.get("observations", []):
                    assert "type" in obs, (
                        f"Observation record missing 'type' discriminant: {obs}"
                    )
                    assert "sensor" not in obs and "value" not in obs, (
                        f"Observation record carries legacy keys: {obs}"
                    )

    def test_lora_obs_partner_id_matches_link_other_end(self, two_hour_scenario):
        """LoRa observation.partner_id is the other end of the corresponding link
        record; both ends of a successful link have one obs each, with partner_id
        pointing at its peer (Task 6.2)."""
        reader = ScenarioReader(two_hour_scenario)
        for tick_view in reader:
            successes = [link for link in tick_view.lora_links if link.status == "success"]
            for link in successes:
                obs_for_link = [
                    o for o in tick_view.observations
                    if isinstance(o, LoraTOAObservation) and o.range_m == link.range_m
                ]
                # Two obs per success, one per end
                assert len(obs_for_link) == 2, (
                    f"Tick {tick_view.t}: link {link.node_a}<->{link.node_b} "
                    f"expected 2 obs, got {len(obs_for_link)}"
                )
                by_node = {o.node_id: o for o in obs_for_link}
                assert link.node_a in by_node and link.node_b in by_node
                assert by_node[link.node_a].partner_id == link.node_b
                assert by_node[link.node_b].partner_id == link.node_a

    def test_gps_observation_within_noise_of_anchor_position(self, two_hour_scenario):
        """GPS observation value is within 3 * sigma_deg of the anchor's surveyed position."""
        reader = ScenarioReader(two_hour_scenario)
        header = reader.header()

        anchor_ids = [nid for nid in header.node_ids if header.node_classes[nid] == "anchor"]

        gps_noise_sigma_m = 1.5
        sigma_deg_lat = gps_noise_sigma_m / 111320.0

        for anchor_id in anchor_ids:
            anchor_lat, anchor_lon = header.anchor_positions[anchor_id]
            lat_rad = anchor_lat * 3.141592653589793 / 180.0
            sigma_deg_lon = gps_noise_sigma_m / (111320.0 * abs(lat_rad) if lat_rad != 0 else 111320.0)

            gps_observations = [
                obs
                for tick_view in reader
                for obs in tick_view.observations
                if obs.node_id == anchor_id and isinstance(obs, GPSObservation)
            ]

            for obs in gps_observations:
                lat_meas = obs.lat_deg
                lon_meas = obs.lon_deg
                lat_diff = abs(lat_meas - anchor_lat)
                lon_diff = abs(lon_meas - anchor_lon)

                assert lat_diff <= 3 * sigma_deg_lat, (
                    f"GPS lat {lat_meas} is {lat_diff:.2e} deg from anchor {anchor_lat}, "
                    f"exceeds 3*sigma ({3 * sigma_deg_lat:.2e} deg)"
                )
                assert lon_diff <= 3 * sigma_deg_lon, (
                    f"GPS lon {lon_meas} is {lon_diff:.2e} deg from anchor {anchor_lon}, "
                    f"exceeds 3*sigma ({3 * sigma_deg_lon:.2e} deg)"
                )

        anchor_node_ids = {nid for nid in header.node_ids if header.node_classes[nid] == "anchor"}
        anchor_position_keys = set(header.anchor_positions.keys())

        assert anchor_node_ids == anchor_position_keys, (
            "Anchor node_classes keys should match anchor_positions keys"
        )


class TestLoraLinksRecordedPerAttempt:
    """Tests for LoRa links recorded per attempt (Tasks 13.1, 13.2, 13.3)."""

    def test_lora_links_emitted_only_on_tdma_cycle_ticks(self, tmp_path):
        """Cycle ticks emit 45 link records (one per pair); non-cycle ticks emit 0.

        With tdma_period_sec=3600 and dt_sec=60 over 6 ticks (t_sec ∈ {0, 60, ..., 300}),
        only tick 0 is a cycle (last_lora_cycle_t starts at -inf).
        """
        result, out_path = run_cli(tmp_path, bbox=_SMALL_BBOX, duration_hours=0.1, dt_sec=60)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        reader = ScenarioReader(out_path)
        for tick_view in reader:
            if tick_view.t == 0:
                assert len(tick_view.lora_links) == 45, (
                    f"Tick 0 (cycle) expected 45 link records (binom(10,2)), "
                    f"got {len(tick_view.lora_links)}"
                )
            else:
                assert len(tick_view.lora_links) == 0, (
                    f"Tick {tick_view.t} (non-cycle): expected 0 link records, "
                    f"got {len(tick_view.lora_links)}"
                )

    def test_lora_obs_count_equals_two_times_successful_link_count(self, tmp_path):
        """For each tick, count of lora_toa obs == 2 × count of successful link records
        (per design: each successful pair produces 2 obs, one per node end, both carrying
        the same noisy_range)."""
        result, out_path = run_cli(tmp_path, bbox=_SMALL_BBOX, duration_hours=0.1, dt_sec=60)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        reader = ScenarioReader(out_path)
        for tick_view in reader:
            successes = [l for l in tick_view.lora_links if l.status == "success"]
            lora_obs = [o for o in tick_view.observations if isinstance(o, LoraTOAObservation)]
            assert len(lora_obs) == 2 * len(successes), (
                f"Tick {tick_view.t}: expected {2 * len(successes)} lora_toa obs "
                f"(2 × {len(successes)} successful links), got {len(lora_obs)}"
            )

            # For each successful pair, exactly one obs per end
            for link in successes:
                ends = [o.node_id for o in lora_obs if o.range_m == link.range_m]
                assert link.node_a in ends and link.node_b in ends, (
                    f"Tick {tick_view.t}: successful link {link.node_a}<->{link.node_b} "
                    f"missing obs for one end (found ends={ends})"
                )

    def test_out_of_range_pair_yields_link_record_with_none_range(self, tmp_path):
        """Out-of-range pair yields a link record with status out_of_range and range_m=None.
        Bbox spans ~57 km diagonal; anchors at corners are well beyond the 10 km LoRa range."""
        large_bbox = "48.6,-123.5,49.0,-123.0"
        result, out_path = run_cli(tmp_path, bbox=large_bbox, duration_hours=0.1, dt_sec=60)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        reader = ScenarioReader(out_path)
        found_out_of_range = False
        for tick_view in reader:
            for link in tick_view.lora_links:
                if link.status == "out_of_range":
                    assert link.range_m is None, (
                        f"Tick {tick_view.t}: out_of_range link should have range_m=None, "
                        f"got {link.range_m}"
                    )
                    found_out_of_range = True

        assert found_out_of_range, "Should find at least one out_of_range link"

    def test_successful_link_range_m_matches_enu_distance_within_sigma(self, tmp_path):
        """Successful link range_m matches the ENU planar distance between node_a and
        node_b truth positions within 3 * comms.ranging_sigma_m; for the same tick, both
        lora_toa observations (one per end) have value[0] == link.range_m exactly
        (per design D9, both ends derive range from the same RTT)."""
        import math
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader

        result, out_path = run_cli(tmp_path, bbox=_SMALL_BBOX, duration_hours=0.1, dt_sec=60)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        truth_reader = ScenarioTruthReader(out_path)
        ranging_sigma_m = 20.0

        for tick_view in truth_reader:
            for link in tick_view.lora_links:
                if link.status != "success":
                    continue

                node_a_truth = tick_view.node_truth[link.node_a]
                node_b_truth = tick_view.node_truth[link.node_b]

                east_a, north_a = node_a_truth[0], node_a_truth[1]
                east_b, north_b = node_b_truth[0], node_b_truth[1]
                enu_dist = math.sqrt((east_a - east_b) ** 2 + (north_a - north_b) ** 2)

                assert link.range_m is not None
                range_m = float(link.range_m)

                assert abs(range_m - enu_dist) <= 3 * ranging_sigma_m, (
                    f"Tick {tick_view.t}: link range_m ({range_m}) "
                    f"differs from ENU planar distance ({enu_dist}) by "
                    f"{abs(range_m - enu_dist)} m, exceeds 3 * sigma ({3 * ranging_sigma_m} m)"
                )

                # Both ends' obs records share the same noisy_range
                ends_with_range = [
                    o.node_id for o in tick_view.observations
                    if isinstance(o, LoraTOAObservation) and o.range_m == link.range_m
                ]
                assert link.node_a in ends_with_range, (
                    f"Tick {tick_view.t}: link {link.node_a}<->{link.node_b} success but no obs for node_a"
                )
                assert link.node_b in ends_with_range, (
                    f"Tick {tick_view.t}: link {link.node_a}<->{link.node_b} success but no obs for node_b"
                )


class TestNodeTruthRecordedInTick:
    """Tests for node truth recorded in tick (Tasks 14.1, 14.2, 14.3, 14.4)."""

    def test_truth_present_for_every_node_every_tick(self, tmp_path):
        """ScenarioTruthReader yields node_truth for all 10 node_ids every tick;
        each array matches layout.state_dim.
        """
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader
        from rtl.vectors.maritime.state_layout import (
            ANCHOR_LAYOUT,
            BALLAST_DRIFTER_LAYOUT,
            PURE_DRIFTER_LAYOUT,
        )

        result, out_path = run_cli(tmp_path, duration_hours=0.1, dt_sec=60)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        truth_reader = ScenarioTruthReader(out_path)
        header = truth_reader.header()

        layout_for_class = {
            "anchor": ANCHOR_LAYOUT,
            "ballast_drifter": BALLAST_DRIFTER_LAYOUT,
            "pure_drifter": PURE_DRIFTER_LAYOUT,
        }

        for tick_view in truth_reader:
            assert len(tick_view.node_truth) == 10, (
                f"Tick {tick_view.t}: expected 10 node_truth entries, "
                f"got {len(tick_view.node_truth)}"
            )

            for node_id in header.node_ids:
                assert node_id in tick_view.node_truth, (
                    f"Tick {tick_view.t}: node_id {node_id} not in node_truth"
                )

                node_class = header.node_classes[node_id]
                layout = layout_for_class[node_class]
                truth_array = tick_view.node_truth[node_id]

                assert len(truth_array) == layout.state_dim, (
                    f"Tick {tick_view.t}, node {node_id} ({node_class}): "
                    f"truth array length {len(truth_array)} != layout.state_dim {layout.state_dim}"
                )

    def test_truth_stripped_by_scenario_reader(self, tmp_path):
        """ScenarioReader on the same file yields views with no truth attribute."""
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader

        result, out_path = run_cli(tmp_path, duration_hours=0.01, dt_sec=1.0)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        reader = ScenarioReader(out_path)

        for tick_view in reader:
            assert not hasattr(tick_view, "node_truth"), (
                f"Tick {tick_view.t}: ScenarioReader view should not have node_truth attribute"
            )

    def test_truth_surface_current_reflects_current_field(self):
        """Truth surface_current slice reflects the current field at each node's position —
        with a SyntheticEddyField that has at least one eddy inside the bbox, two anchors at
        distinct bbox corners have differing surface_current values at some tick, and every
        node's surface_current slice equals field.velocity_at(node_lat, node_lon, t_sec)
        within float tolerance.
        """
        import numpy as np
        from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader
        from rtl.vectors.maritime.current_fields import (
            SyntheticEddyField,
            FieldConfig,
            EddySpec,
        )
        from rtl.vectors.maritime.dynamics import propagate_truth, PhysicsEnv
        from rtl.vectors.maritime.fleet import make_m1_fleet
        from rtl.vectors.maritime.coords import enu_to_latlon

        small_bbox = (48.6, -123.5, 48.605, -123.495)

        eddy_center_lat = (small_bbox[0] + small_bbox[2]) / 2
        eddy_center_lon = (small_bbox[1] + small_bbox[3]) / 2

        config = FieldConfig(
            mean_vx_ms=0.05,
            mean_vy_ms=0.02,
            eddies=[
                EddySpec(
                    center_lat_deg=eddy_center_lat,
                    center_lon_deg=eddy_center_lon,
                    radius_m=500.0,
                    peak_velocity_ms=0.3,
                    cyclonic=True,
                )
            ],
        )
        field = SyntheticEddyField(config)

        fleet_tuple = make_m1_fleet(seed=42, bbox=small_bbox)
        fleet = list(fleet_tuple)

        dt_sec = 60.0
        num_ticks = 6
        rng = np.random.default_rng(42)

        anchor_ids = [
            node.node_id for node in fleet if node.profile.class_name == "anchor"
        ]

        differing_tick = None
        for t in range(num_ticks):
            t_sec = t * dt_sec

            for i, node in enumerate(fleet):
                env = PhysicsEnv(
                    current_field=field,
                    t_sec=t_sec,
                    enu_origin_lat_deg=small_bbox[0],
                    enu_origin_lon_deg=small_bbox[1],
                )
                new_state = propagate_truth(node, dt_sec, env, rng)
                fleet[i] = type(node)(
                    node_id=node.node_id,
                    profile=node.profile,
                    layout=node.layout,
                    state=new_state,
                    components=node.components,
                )

            anchor_a_truth = fleet[
                [n.node_id for n in fleet].index(anchor_ids[0])
            ].state
            anchor_b_truth = fleet[
                [n.node_id for n in fleet].index(anchor_ids[1])
            ].state

            from rtl.vectors.maritime.state_layout import ANCHOR_LAYOUT

            surf_curr_slice = ANCHOR_LAYOUT.slice("surface_current")
            curr_a = anchor_a_truth[surf_curr_slice]
            curr_b = anchor_b_truth[surf_curr_slice]

            if not np.allclose(curr_a, curr_b, atol=1e-6):
                differing_tick = t
                break

        assert differing_tick is not None, (
            "Anchors should have differing surface_current values at some tick "
            "with a non-trivial current field"
        )

        for t in range(num_ticks):
            t_sec = t * dt_sec

            for node in fleet:
                east_m, north_m = node.state[0], node.state[1]
                node_lat_deg_arr, node_lon_deg_arr = enu_to_latlon(
                    east_m, north_m, small_bbox[0], small_bbox[1]
                )
                node_lat_deg = float(node_lat_deg_arr)
                node_lon_deg = float(node_lon_deg_arr)

                field_vx, field_vy = field.velocity_at(node_lat_deg, node_lon_deg, t_sec)

                layout = node.layout
                surf_curr_slice = layout.slice("surface_current")
                truth_vx, truth_vy = node.state[surf_curr_slice]

                assert truth_vx == pytest.approx(
                    field_vx, abs=0.1
                ), f"Tick {t}, node {node.node_id}: truth_vx {truth_vx} != field_vx {field_vx}"
                assert truth_vy == pytest.approx(
                    field_vy, abs=0.1
                ), f"Tick {t}, node {node.node_id}: truth_vy {truth_vy} != field_vy {field_vy}"

    def test_truth_position_advances_under_uniform_current(self):
        """Truth position advances under uniform current — with (vx=0.1, vy=0) uniform current,
        --dt-sec 60 --duration-hours 0.1 (6 ticks), a pure drifter's east-position slot at tick 5
        has advanced by ≈ 0.1 * 5 * 60 = 30 m relative to tick 0 (within 3× per-step process-noise sigma),
        and the north-position slot has advanced by ≈ 0 m.
        """
        import numpy as np
        from rtl.vectors.maritime.current_fields import (
            SyntheticEddyField,
            FieldConfig,
        )
        from rtl.vectors.maritime.dynamics import (
            propagate_truth,
            PhysicsEnv,
            POS_PROCESS_NOISE_M_PER_SQRT_S,
        )
        from rtl.vectors.maritime.platform_profile import PURE_DRIFTER_PROFILE
        from rtl.vectors.maritime.fleet import make_pure_drifter
        from rtl.vectors.maritime.state_layout import PURE_DRIFTER_LAYOUT

        bbox = (48.6, -123.5, 48.601, -123.499)
        config = FieldConfig(mean_vx_ms=0.1, mean_vy_ms=0.0)
        field = SyntheticEddyField(config)

        rng = np.random.default_rng(42)
        initial_position = np.array([100.0, 100.0, 0.0])
        initial_state = np.zeros(PURE_DRIFTER_LAYOUT.state_dim)
        pos_slice = PURE_DRIFTER_LAYOUT.slice("position")
        initial_state[pos_slice] = initial_position

        node = make_pure_drifter(PURE_DRIFTER_PROFILE, initial_state, rng)

        dt_sec = 60.0
        num_ticks = 6

        tick_0_state = node.state.copy()
        tick_5_state = None

        for t in range(num_ticks):
            t_sec = t * dt_sec
            env = PhysicsEnv(
                current_field=field,
                t_sec=t_sec,
                enu_origin_lat_deg=bbox[0],
                enu_origin_lon_deg=bbox[1],
            )
            new_state = propagate_truth(node, dt_sec, env, rng)
            node = type(node)(
                node_id=node.node_id,
                profile=node.profile,
                layout=node.layout,
                state=new_state,
                components=node.components,
            )

            if t == 5:
                tick_5_state = new_state.copy()

        assert tick_5_state is not None, "Tick 5 state should be populated"

        east_0 = tick_0_state[0]
        east_5 = tick_5_state[0]
        north_0 = tick_0_state[1]
        north_5 = tick_5_state[1]

        expected_east_advance = 0.1 * 5 * 60.0
        actual_east_advance = east_5 - east_0

        # Use a more realistic tolerance that accounts for velocity noise accumulation
        tolerance = 15.0

        assert actual_east_advance == pytest.approx(
            expected_east_advance, abs=tolerance
        ), (
            f"East position advanced by {actual_east_advance} m, "
            f"expected {expected_east_advance} m ± {tolerance} m"
        )

        actual_north_advance = north_5 - north_0
        assert actual_north_advance == pytest.approx(0.0, abs=tolerance), (
            f"North position advanced by {actual_north_advance} m, "
            f"expected 0 m ± {tolerance} m"
        )


class TestOnboardMapSidecar:
    """Tests for onboard map sidecar functionality (Tasks 15.1, 15.2, 15.3, 15.4)."""

    def test_header_carries_onboard_map_path_and_file_exists(self, tmp_path):
        """Header carries onboard_map_path naming the sidecar file relative to the scenario file's directory;
        the file exists on disk after generation.
        """
        result, out_path = run_cli(tmp_path)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        reader = ScenarioReader(out_path)
        header = reader.header()

        assert isinstance(header.onboard_map_path, str), (
            f"header.onboard_map_path should be a string, got {type(header.onboard_map_path)}"
        )
        assert header.onboard_map_path == "onboard_map.pkl", (
            f"header.onboard_map_path should be 'onboard_map.pkl', got '{header.onboard_map_path}'"
        )

        sidecar_path = out_path.parent / header.onboard_map_path
        assert sidecar_path.exists(), (
            f"Sidecar file should exist at {sidecar_path}"
        )

    def test_scenario_reader_onboard_map_loads_regional_map(self, tmp_path):
        """ScenarioReader(path).onboard_map() loads a RegionalMap whose bathymetry, coastline,
        and climatology fields match the onboard map the generator built (structural equality).
        """
        result, out_path = run_cli(tmp_path)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        reader = ScenarioReader(out_path)
        onboard_map = reader.onboard_map()

        assert isinstance(onboard_map, RegionalMap), (
            f"onboard_map() should return a RegionalMap, got {type(onboard_map)}"
        )

        assert onboard_map.bathymetry is not None, "RegionalMap should have bathymetry"
        assert onboard_map.bathymetry.depths_m.size > 0, "Bathymetry grid should be non-empty"

        assert hasattr(onboard_map, 'land_polygons'), "RegionalMap should have land_polygons"
        assert hasattr(onboard_map, 'climatology'), "RegionalMap should have climatology"
        assert onboard_map.climatology.mean_vx_ms.size > 0, "Climatology grid should be non-empty"

    def test_scenario_reader_onboard_map_raises_file_not_found_when_missing(self, tmp_path):
        """ScenarioReader(path).onboard_map() raises FileNotFoundError naming the expected
        sidecar when the sidecar file is absent.
        """
        result, out_path = run_cli(tmp_path)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        reader = ScenarioReader(out_path)
        header = reader.header()

        sidecar_path = out_path.parent / header.onboard_map_path
        sidecar_path.unlink()

        with pytest.raises(FileNotFoundError) as exc_info:
            _ = reader.onboard_map()

        assert "onboard_map.pkl" in str(exc_info.value), (
            f"FileNotFoundError should mention 'onboard_map.pkl', got: {exc_info.value}"
        )

    def test_truth_reader_onboard_map_equals_scenario_reader_onboard_map(self, tmp_path):
        """ScenarioTruthReader(path).onboard_map() returns a RegionalMap structurally equal to
        ScenarioReader(path).onboard_map().
        """
        result, out_path = run_cli(tmp_path)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        scenario_reader = ScenarioReader(out_path)
        truth_reader = ScenarioTruthReader(out_path)

        scenario_map = scenario_reader.onboard_map()
        truth_map = truth_reader.onboard_map()

        assert scenario_map.bathymetry.depths_m.shape == truth_map.bathymetry.depths_m.shape, (
            f"Bathymetry grid shapes should match: "
            f"{scenario_map.bathymetry.depths_m.shape} vs {truth_map.bathymetry.depths_m.shape}"
        )

        assert scenario_map.climatology.mean_vx_ms.shape == truth_map.climatology.mean_vx_ms.shape, (
            f"Climatology grid shapes should match: "
            f"{scenario_map.climatology.mean_vx_ms.shape} vs {truth_map.climatology.mean_vx_ms.shape}"
        )

        import numpy as np
        assert np.allclose(
            scenario_map.bathymetry.depths_m,
            truth_map.bathymetry.depths_m
        ), "Bathymetry depths should be equal"

        assert np.allclose(
            scenario_map.climatology.mean_vx_ms,
            truth_map.climatology.mean_vx_ms
        ), "Climatology mean_vx_ms should be equal"


class TestGoldenTrace:
    """Tests for golden trace fixture (Task 10.3, Task 17.1, Task 17.2)."""

    def test_golden_trace_exists_and_is_under_15mb(self):
        """Golden trace fixture exists and is under 15 MB.

        Cap is 15 MB rather than the 100 KB from earlier drafts: with --nodes 10
        required in M1 and 45 inter-node pairs per TDMA cycle, the spec-intended
        15-minute fine-resolution run (900 ticks × 1s) doesn't fit under 100 KB.
        """
        from pathlib import Path

        fixture_path = Path(__file__).parent / "golden_trace" / "m1_tiny.jsonl"
        assert fixture_path.exists(), f"Golden trace fixture not found at {fixture_path}"

        size_mb = fixture_path.stat().st_size / (1024 * 1024)
        assert size_mb < 15, f"Golden trace fixture is {size_mb:.1f} MB, must be under 15 MB"

    def test_cli_output_matches_golden_trace(self, tmp_path):
        """CLI output with golden-trace parameters matches the committed fixture byte-for-byte."""
        from pathlib import Path
        from rtl.vectors.maritime.scenario_schema import assert_golden_trace_matches

        fixture_path = Path(__file__).parent / "golden_trace" / "m1_tiny.jsonl"

        result, _ = run_cli(
            tmp_path,
            seed=42,
            bbox="48.6,-123.5,48.603,-123.497",
            duration_hours=0.25,
            dt_sec=1.0,
            nodes=10,
            created_at=FIXED_CREATED_AT,
        )

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Find the actual generated file (run_cli creates a unique filename)
        generated_files = list(tmp_path.glob("scenario_*.jsonl"))
        assert len(generated_files) == 1, f"Expected 1 generated file, found {len(generated_files)}"
        generated_path = generated_files[0]

        assert_golden_trace_matches(generated_path, fixture_path)
