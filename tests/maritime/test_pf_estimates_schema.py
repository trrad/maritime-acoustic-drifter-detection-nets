"""Contract tests for the PF estimate JSONL schema module.

Defines the observable behavior of:
- ``PFEstimateHeader`` / ``PFEstimateRecord`` (main estimate stream types)
- ``PFEstimateHeader_Particles`` / ``ParticleRecord`` (sidecar types)
- ``PFEstimateReader`` / ``ParticleStreamReader`` (file readers)
- ``ParticleStreamWriter`` Protocol + ``make_jsonl_particle_writer`` factory

Tests intentionally fail with ``ImportError`` until
``rtl/vectors/maritime/pf_estimates_schema.py`` is implemented; once the
module exists, every behavior asserted here is what ``done`` means for
Batch A of the maritime-pf-float change. The implementer makes these tests
pass without modifying them.

Tests construct JSONL fixtures on disk via the ``tmp_path`` pytest fixture
following the pattern in ``test_scenario_schema.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture helpers — JSONL record builders
# ---------------------------------------------------------------------------
#
# Kept as module-level functions (not pytest fixtures) so each test can
# customize the records it writes. The structure mirrors the on-disk
# format documented in the spec / task brief.


def _valid_main_header_record(
    *,
    schema_version: str = "1.0",
    scenario_path: str = "/tmp/scenario.jsonl",
    scenario_seed: int = 42,
    pf_impl: str = "float64_bootstrap",
    n_particles: int = 500,
    node_ids: tuple[str, ...] = ("n00", "n01", "n02"),
    created_at_utc: str = "2026-04-22T00:00:00Z",
) -> dict:
    return {
        "record_type": "header",
        "schema_version": schema_version,
        "scenario_path": scenario_path,
        "scenario_seed": scenario_seed,
        "pf_impl": pf_impl,
        "n_particles": n_particles,
        "node_ids": list(node_ids),
        "created_at_utc": created_at_utc,
    }


def _valid_estimate_record(
    *,
    t: int = 0,
    t_sec: float = 0.0,
    node_id: str = "n00",
    mean: tuple[float, ...] = (1.0, 2.0, 3.0),
    cov_diag: tuple[float, ...] = (0.1, 0.2, 0.3),
    n_effective: float = 350.0,
) -> dict:
    return {
        "record_type": "estimate",
        "t": t,
        "t_sec": t_sec,
        "node_id": node_id,
        "mean": list(mean),
        "cov_diag": list(cov_diag),
        "n_effective": n_effective,
    }


def _valid_sidecar_header_record(
    *,
    schema_version: str = "1.0",
    parent_estimate_path: str = "/tmp/pf_estimates.jsonl",
    scenario_seed: int = 42,
    n_particles_full: int = 500,
    thin_ticks: int = 1,
    thin_particles: int = 50,
    thin_nodes: tuple[str, ...] | None = None,
    created_at_utc: str = "2026-04-22T00:00:00Z",
) -> dict:
    return {
        "record_type": "particle_header",
        "schema_version": schema_version,
        "parent_estimate_path": parent_estimate_path,
        "scenario_seed": scenario_seed,
        "n_particles_full": n_particles_full,
        "thin_ticks": thin_ticks,
        "thin_particles": thin_particles,
        "thin_nodes": None if thin_nodes is None else list(thin_nodes),
        "created_at_utc": created_at_utc,
    }


def _valid_particle_record(
    *,
    t: int = 0,
    t_sec: float = 0.0,
    node_id: str = "n00",
    particles: tuple[tuple[float, ...], ...] | None = None,
    weights: tuple[float, ...] | None = None,
) -> dict:
    if particles is None:
        # Default: 3 particles, state_dim = 4
        particles = ((1.0, 2.0, 3.0, 4.0),) * 3
    if weights is None:
        # Uniform weights over however many particles the caller passed.
        n = len(particles)
        weights = tuple(1.0 / n for _ in range(n))
    return {
        "record_type": "particle",
        "t": t,
        "t_sec": t_sec,
        "node_id": node_id,
        "particles": [list(p) for p in particles],
        "weights": list(weights),
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w") as f:
        for rec in records:
            json.dump(rec, f)
            f.write("\n")


# ---------------------------------------------------------------------------
# Section 1: Schema version constants and PFEstimateHeader (tasks 1.1–1.5)
# ---------------------------------------------------------------------------


class TestSchemaVersionConstants:
    """Module-level version constants (task 1.1)."""

    def test_schema_version_constant_is_1_0(self) -> None:
        from rtl.vectors.maritime.pf_estimates_schema import (
            PF_ESTIMATE_SCHEMA_VERSION,
            SUPPORTED_PF_ESTIMATE_VERSIONS,
        )

        assert PF_ESTIMATE_SCHEMA_VERSION == "1.0"
        assert "1.0" in SUPPORTED_PF_ESTIMATE_VERSIONS


class TestPFEstimateHeaderConstruction:
    """PFEstimateHeader dataclass invariants (tasks 1.2–1.5)."""

    def test_header_constructs_and_round_trips(self) -> None:
        """Task 1.2: every field is settable and readable."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateHeader

        header = PFEstimateHeader(
            schema_version="1.0",
            scenario_path="/tmp/scenario.jsonl",
            scenario_seed=42,
            pf_impl="float64_bootstrap",
            n_particles=500,
            node_ids=("n00", "n01", "n02"),
            created_at_utc="2026-04-22T00:00:00Z",
        )

        assert header.schema_version == "1.0"
        assert header.scenario_path == "/tmp/scenario.jsonl"
        assert header.scenario_seed == 42
        assert header.pf_impl == "float64_bootstrap"
        assert header.n_particles == 500
        assert header.node_ids == ("n00", "n01", "n02")
        assert header.created_at_utc == "2026-04-22T00:00:00Z"

    def test_header_unknown_version_raises(self) -> None:
        """Task 1.3: schema_version not in SUPPORTED_PF_ESTIMATE_VERSIONS rejected."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateHeader

        with pytest.raises(ValueError, match=r"schema_version|2\.0"):
            PFEstimateHeader(
                schema_version="2.0",
                scenario_path="/tmp/scenario.jsonl",
                scenario_seed=42,
                pf_impl="float64_bootstrap",
                n_particles=500,
                node_ids=("n00", "n01", "n02"),
                created_at_utc="2026-04-22T00:00:00Z",
            )

    def test_header_non_positive_n_particles_raises(self) -> None:
        """Task 1.4: n_particles=0 rejected with ValueError."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateHeader

        with pytest.raises(ValueError, match=r"n_particles"):
            PFEstimateHeader(
                schema_version="1.0",
                scenario_path="/tmp/scenario.jsonl",
                scenario_seed=42,
                pf_impl="float64_bootstrap",
                n_particles=0,
                node_ids=("n00", "n01", "n02"),
                created_at_utc="2026-04-22T00:00:00Z",
            )

    def test_header_no_focus_node_ids_attribute(self) -> None:
        """Task 1.5: header has no focus_node_ids; node_ids covers the fleet."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateHeader

        header = PFEstimateHeader(
            schema_version="1.0",
            scenario_path="/tmp/scenario.jsonl",
            scenario_seed=42,
            pf_impl="float64_bootstrap",
            n_particles=500,
            node_ids=("n00", "n01", "n02", "n03", "n04"),
            created_at_utc="2026-04-22T00:00:00Z",
        )

        assert not hasattr(header, "focus_node_ids")
        # The fleet membership lives in node_ids — the privileged-subset
        # design from the earlier draft is gone (see design.md D9, D10).
        assert header.node_ids == ("n00", "n01", "n02", "n03", "n04")


# ---------------------------------------------------------------------------
# Section 2: PFEstimateRecord invariants (tasks 3.1–3.4)
# ---------------------------------------------------------------------------


class TestPFEstimateRecord:
    """PFEstimateRecord construction-time validation (tasks 3.1–3.4)."""

    def test_record_has_no_particles_or_weights_attribute(self) -> None:
        """Task 3.1: particle-level data lives in the sidecar, not the main stream."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateRecord

        record = PFEstimateRecord(
            t=0,
            t_sec=0.0,
            node_id="n00",
            mean=(1.0, 2.0, 3.0),
            cov_diag=(0.1, 0.2, 0.3),
            n_effective=350.0,
        )

        assert not hasattr(record, "particles")
        assert not hasattr(record, "weights")

    def test_record_negative_cov_diag_raises(self) -> None:
        """Task 3.2: any negative cov_diag entry rejected."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateRecord

        with pytest.raises(ValueError, match=r"cov_diag"):
            PFEstimateRecord(
                t=0,
                t_sec=0.0,
                node_id="n00",
                mean=(1.0, 2.0, 3.0),
                cov_diag=(0.1, -0.5, 0.2),
                n_effective=10.0,
            )

    def test_record_n_effective_zero_raises(self) -> None:
        """Task 3.3 (record-level, lower bound): n_effective=0 rejected."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateRecord

        with pytest.raises(ValueError, match=r"n_effective"):
            PFEstimateRecord(
                t=0,
                t_sec=0.0,
                node_id="n00",
                mean=(1.0, 2.0, 3.0),
                cov_diag=(0.1, 0.2, 0.3),
                n_effective=0.0,
            )

    def test_record_n_effective_negative_raises(self) -> None:
        """Task 3.3 (record-level, lower bound): n_effective<0 rejected."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateRecord

        with pytest.raises(ValueError, match=r"n_effective"):
            PFEstimateRecord(
                t=0,
                t_sec=0.0,
                node_id="n00",
                mean=(1.0, 2.0, 3.0),
                cov_diag=(0.1, 0.2, 0.3),
                n_effective=-1.0,
            )

    def test_record_mean_cov_diag_length_mismatch_raises(self) -> None:
        """Task 3.4: mean and cov_diag must be the same length."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateRecord

        with pytest.raises(ValueError, match=r"mean|cov_diag|length"):
            PFEstimateRecord(
                t=0,
                t_sec=0.0,
                node_id="n00",
                mean=(1.0, 2.0),
                cov_diag=(0.1,),
                n_effective=10.0,
            )


# ---------------------------------------------------------------------------
# Section 3: PFEstimateReader (tasks 5.1–5.3 + reader-side n_effective bound)
# ---------------------------------------------------------------------------


class TestPFEstimateReader:
    """PFEstimateReader file-level behavior (tasks 5.1–5.3, plus 3.3 reader bound)."""

    def test_reader_yields_typed_records(self, tmp_path: Path) -> None:
        """Task 5.1: iteration yields PFEstimateRecord instances, not raw dicts."""
        from rtl.vectors.maritime.pf_estimates_schema import (
            PFEstimateReader,
            PFEstimateRecord,
        )

        path = tmp_path / "pf_estimates.jsonl"
        records = [
            _valid_main_header_record(n_particles=500),
            _valid_estimate_record(t=0, t_sec=0.0, node_id="n00"),
            _valid_estimate_record(t=1, t_sec=60.0, node_id="n00"),
            _valid_estimate_record(t=2, t_sec=120.0, node_id="n00"),
        ]
        _write_jsonl(path, records)

        reader = PFEstimateReader(path)
        yielded = list(reader)

        assert len(yielded) == 3
        for rec in yielded:
            assert isinstance(rec, PFEstimateRecord)
            assert not isinstance(rec, dict)

    def test_reader_header_scenario_seed_propagates(self, tmp_path: Path) -> None:
        """Task 5.2: header().scenario_seed matches the value written to disk."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateReader

        path = tmp_path / "pf_estimates.jsonl"
        _write_jsonl(path, [_valid_main_header_record(scenario_seed=42)])

        reader = PFEstimateReader(path)
        assert reader.header().scenario_seed == 42

    def test_reader_unknown_version_on_open_raises(self, tmp_path: Path) -> None:
        """Task 5.3: schema_version validation happens at construction time.

        The reader's __init__ reads the header and raises ValueError on an
        unknown version; the consumer never gets a chance to iterate.
        """
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateReader

        path = tmp_path / "pf_estimates_bad_version.jsonl"
        _write_jsonl(path, [_valid_main_header_record(schema_version="2.0")])

        with pytest.raises(ValueError, match=r"schema_version|2\.0"):
            PFEstimateReader(path)

    def test_reader_rejects_n_effective_above_n_particles(
        self, tmp_path: Path
    ) -> None:
        """Task 3.3 (reader-level upper bound): n_effective > n_particles rejected.

        The dataclass alone cannot enforce this — n_particles lives in the
        header and isn't visible to a single ``PFEstimateRecord``. The
        reader has both sides of the contract and is the right place to
        enforce the upper bound.
        """
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateReader

        path = tmp_path / "pf_estimates_bad_ess.jsonl"
        records = [
            _valid_main_header_record(n_particles=500),
            _valid_estimate_record(n_effective=501.0),
        ]
        _write_jsonl(path, records)

        # The exact failure point (open vs iterate) depends on whether the
        # reader pre-loads records or streams them lazily. Either is fine;
        # the contract is "the consumer cannot get a record back that
        # violates the bound."
        with pytest.raises(ValueError, match=r"n_effective|n_particles"):
            reader = PFEstimateReader(path)
            list(reader)


# ---------------------------------------------------------------------------
# Section 4: PFEstimateHeader_Particles (sidecar header, tasks 7.1–7.2)
# ---------------------------------------------------------------------------


class TestPFEstimateHeaderParticles:
    """Sidecar header invariants (tasks 7.1, 7.2)."""

    def test_sidecar_header_thin_ticks_at_least_one(self) -> None:
        """Task 7.1 part 1: thin_ticks=0 rejected (must be >= 1)."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateHeader_Particles

        with pytest.raises(ValueError, match=r"thin_ticks"):
            PFEstimateHeader_Particles(
                schema_version="1.0",
                parent_estimate_path="/tmp/pf_estimates.jsonl",
                scenario_seed=42,
                n_particles_full=500,
                thin_ticks=0,
                thin_particles=50,
                thin_nodes=None,
                created_at_utc="2026-04-22T00:00:00Z",
            )

    def test_sidecar_header_thin_particles_at_least_one(self) -> None:
        """Task 7.1 part 2: thin_particles=0 rejected (must be >= 1)."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateHeader_Particles

        with pytest.raises(ValueError, match=r"thin_particles"):
            PFEstimateHeader_Particles(
                schema_version="1.0",
                parent_estimate_path="/tmp/pf_estimates.jsonl",
                scenario_seed=42,
                n_particles_full=500,
                thin_ticks=1,
                thin_particles=0,
                thin_nodes=None,
                created_at_utc="2026-04-22T00:00:00Z",
            )

    def test_sidecar_header_thin_particles_le_n_particles_full(self) -> None:
        """Task 7.1 part 3: thin_particles cannot exceed n_particles_full."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateHeader_Particles

        with pytest.raises(ValueError, match=r"thin_particles|n_particles_full"):
            PFEstimateHeader_Particles(
                schema_version="1.0",
                parent_estimate_path="/tmp/pf_estimates.jsonl",
                scenario_seed=42,
                n_particles_full=500,
                thin_ticks=1,
                thin_particles=600,
                thin_nodes=None,
                created_at_utc="2026-04-22T00:00:00Z",
            )

    def test_sidecar_header_thin_nodes_none_allowed(self) -> None:
        """Task 7.2: thin_nodes=None encodes ``no subset restriction``."""
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateHeader_Particles

        header = PFEstimateHeader_Particles(
            schema_version="1.0",
            parent_estimate_path="/tmp/pf_estimates.jsonl",
            scenario_seed=42,
            n_particles_full=500,
            thin_ticks=1,
            thin_particles=50,
            thin_nodes=None,
            created_at_utc="2026-04-22T00:00:00Z",
        )
        assert header.thin_nodes is None


# ---------------------------------------------------------------------------
# Section 5: ParticleRecord invariants (task 7.4) + sidecar shape mismatch (7.3)
# ---------------------------------------------------------------------------


class TestParticleRecordWeights:
    """Per-record weight-sum validation (task 7.4)."""

    def test_particle_record_weights_summing_to_off_value_raises(self) -> None:
        """Weights summing to 0.9 (well outside 1e-6 tolerance) rejected."""
        from rtl.vectors.maritime.pf_estimates_schema import ParticleRecord

        with pytest.raises(ValueError, match=r"weights|sum"):
            ParticleRecord(
                t=0,
                t_sec=0.0,
                node_id="n00",
                particles=((1.0, 2.0), (3.0, 4.0), (5.0, 6.0)),
                weights=(0.4, 0.3, 0.2),
            )

    def test_particle_record_weights_summing_to_one_within_tolerance_accepted(
        self,
    ) -> None:
        """Weights summing to 1.0 ± 1e-7 accepted (well within the 1e-6 spec band)."""
        from rtl.vectors.maritime.pf_estimates_schema import ParticleRecord

        # Construct weights that sum to exactly 1 + 1e-7 — inside the
        # 1e-6 spec tolerance band.
        weights = (0.4 + 1e-7, 0.3, 0.3)
        # Sanity check on the fixture itself.
        assert abs(sum(weights) - 1.0) < 1e-6

        record = ParticleRecord(
            t=0,
            t_sec=0.0,
            node_id="n00",
            particles=((1.0, 2.0), (3.0, 4.0), (5.0, 6.0)),
            weights=weights,
        )
        assert record.weights == weights


# ---------------------------------------------------------------------------
# Section 6: ParticleStreamReader (tasks 7.3, 7.5, 9.1–9.3)
# ---------------------------------------------------------------------------


class TestParticleStreamReader:
    """ParticleStreamReader behavior (tasks 7.3, 7.5, 9.1, 9.2, 9.3)."""

    def test_sidecar_reader_rejects_particle_shape_mismatch(
        self, tmp_path: Path
    ) -> None:
        """Task 7.3: header thin_particles=50 + record with 40 rows is a contract violation."""
        from rtl.vectors.maritime.pf_estimates_schema import ParticleStreamReader

        path = tmp_path / "particles_bad_shape.jsonl"
        # Header declares 50 particles per record; record carries only 40.
        bad_record = _valid_particle_record(
            particles=tuple((float(i),) * 25 for i in range(40)),
            weights=tuple(1.0 / 40 for _ in range(40)),
        )
        _write_jsonl(
            path,
            [
                _valid_sidecar_header_record(thin_particles=50, n_particles_full=500),
                bad_record,
            ],
        )

        with pytest.raises(ValueError, match=r"thin_particles|particles|shape|50|40"):
            reader = ParticleStreamReader(path)
            list(reader)

    def test_sidecar_reader_unknown_version_on_open_raises(
        self, tmp_path: Path
    ) -> None:
        """Task 7.5: sidecar header with unknown schema_version rejected at __init__."""
        from rtl.vectors.maritime.pf_estimates_schema import ParticleStreamReader

        path = tmp_path / "particles_bad_version.jsonl"
        _write_jsonl(path, [_valid_sidecar_header_record(schema_version="2.0")])

        with pytest.raises(ValueError, match=r"schema_version|2\.0"):
            ParticleStreamReader(path)

    def test_sidecar_reader_yields_typed_records(self, tmp_path: Path) -> None:
        """Task 9.1: iteration yields ParticleRecord instances."""
        from rtl.vectors.maritime.pf_estimates_schema import (
            ParticleRecord,
            ParticleStreamReader,
        )

        path = tmp_path / "particles.jsonl"
        # Use 3 particles per record, state_dim 4, matching the fixture default.
        records = [
            _valid_sidecar_header_record(
                thin_particles=3, n_particles_full=10
            ),
            _valid_particle_record(t=0, t_sec=0.0, node_id="n00"),
            _valid_particle_record(t=1, t_sec=60.0, node_id="n00"),
            _valid_particle_record(t=2, t_sec=120.0, node_id="n00"),
        ]
        _write_jsonl(path, records)

        reader = ParticleStreamReader(path)
        yielded = list(reader)
        assert len(yielded) == 3
        for rec in yielded:
            assert isinstance(rec, ParticleRecord)
            assert not isinstance(rec, dict)

    def test_node_ids_present_returns_appearing_node_ids(
        self, tmp_path: Path
    ) -> None:
        """Task 9.2: node_ids_present() returns the frozenset of seen node_ids."""
        from rtl.vectors.maritime.pf_estimates_schema import ParticleStreamReader

        path = tmp_path / "particles_subset.jsonl"
        records = [
            _valid_sidecar_header_record(
                thin_particles=3,
                n_particles_full=10,
                thin_nodes=("n01", "n05"),
            ),
            _valid_particle_record(t=0, t_sec=0.0, node_id="n01"),
            _valid_particle_record(t=0, t_sec=0.0, node_id="n05"),
            _valid_particle_record(t=1, t_sec=60.0, node_id="n01"),
            _valid_particle_record(t=1, t_sec=60.0, node_id="n05"),
        ]
        _write_jsonl(path, records)

        reader = ParticleStreamReader(path)
        assert reader.node_ids_present() == frozenset({"n01", "n05"})

    def test_empty_sidecar_reader_works(self, tmp_path: Path) -> None:
        """Task 9.3: header-only sidecar yields zero records and empty node set."""
        from rtl.vectors.maritime.pf_estimates_schema import ParticleStreamReader

        path = tmp_path / "particles_empty.jsonl"
        _write_jsonl(path, [_valid_sidecar_header_record()])

        reader = ParticleStreamReader(path)
        assert list(reader) == []
        assert reader.node_ids_present() == frozenset()


# ---------------------------------------------------------------------------
# Section 7: ParticleStreamWriter (tasks 11.1–11.3)
# ---------------------------------------------------------------------------


class TestParticleStreamWriter:
    """JSONL writer behavior (tasks 11.1, 11.2, 11.3)."""

    def test_jsonl_writer_round_trips_header_and_records(
        self, tmp_path: Path
    ) -> None:
        """Task 11.1: header + 3 records round-trip with identical field values."""
        from rtl.vectors.maritime.pf_estimates_schema import (
            ParticleRecord,
            ParticleStreamReader,
            PFEstimateHeader_Particles,
            make_jsonl_particle_writer,
        )

        path = tmp_path / "particles_roundtrip.jsonl"

        header = PFEstimateHeader_Particles(
            schema_version="1.0",
            parent_estimate_path="/tmp/pf_estimates.jsonl",
            scenario_seed=42,
            n_particles_full=500,
            thin_ticks=1,
            thin_particles=3,
            thin_nodes=None,
            created_at_utc="2026-04-22T00:00:00Z",
        )

        records = [
            ParticleRecord(
                t=0,
                t_sec=0.0,
                node_id="n00",
                particles=((1.0, 2.0), (3.0, 4.0), (5.0, 6.0)),
                weights=(1.0 / 3, 1.0 / 3, 1.0 / 3),
            ),
            ParticleRecord(
                t=1,
                t_sec=60.0,
                node_id="n00",
                particles=((1.1, 2.1), (3.1, 4.1), (5.1, 6.1)),
                weights=(0.5, 0.25, 0.25),
            ),
            ParticleRecord(
                t=2,
                t_sec=120.0,
                node_id="n01",
                particles=((10.0, 20.0), (30.0, 40.0), (50.0, 60.0)),
                weights=(0.1, 0.7, 0.2),
            ),
        ]

        writer = make_jsonl_particle_writer(path)
        writer.write_header(header)
        for r in records:
            writer.write_record(r)
        writer.close()

        reader = ParticleStreamReader(path)

        # Header round-trip — every field equal.
        read_header = reader.header()
        assert read_header.schema_version == header.schema_version
        assert read_header.parent_estimate_path == header.parent_estimate_path
        assert read_header.scenario_seed == header.scenario_seed
        assert read_header.n_particles_full == header.n_particles_full
        assert read_header.thin_ticks == header.thin_ticks
        assert read_header.thin_particles == header.thin_particles
        assert read_header.thin_nodes == header.thin_nodes
        assert read_header.created_at_utc == header.created_at_utc

        # Records round-trip — every field equal.
        read_records = list(reader)
        assert len(read_records) == 3
        for orig, got in zip(records, read_records, strict=True):
            assert got.t == orig.t
            assert got.t_sec == orig.t_sec
            assert got.node_id == orig.node_id
            assert got.particles == orig.particles
            assert got.weights == orig.weights

    def test_writer_write_record_before_header_raises_runtime_error(
        self, tmp_path: Path
    ) -> None:
        """Task 11.2: write_record before write_header is a programming error."""
        from rtl.vectors.maritime.pf_estimates_schema import (
            ParticleRecord,
            make_jsonl_particle_writer,
        )

        path = tmp_path / "particles_out_of_order.jsonl"
        writer = make_jsonl_particle_writer(path)

        record = ParticleRecord(
            t=0,
            t_sec=0.0,
            node_id="n00",
            particles=((1.0, 2.0), (3.0, 4.0), (5.0, 6.0)),
            weights=(1.0 / 3, 1.0 / 3, 1.0 / 3),
        )

        with pytest.raises(RuntimeError):
            writer.write_record(record)

    def test_writer_close_is_idempotent(self, tmp_path: Path) -> None:
        """Task 11.3: close() called twice does not raise."""
        from rtl.vectors.maritime.pf_estimates_schema import (
            make_jsonl_particle_writer,
        )

        path = tmp_path / "particles_close_twice.jsonl"
        writer = make_jsonl_particle_writer(path)
        writer.close()
        # Second close must not raise — defensive against caller cleanup
        # paths that may close in a finally block after a successful close.
        writer.close()


# ---------------------------------------------------------------------------
# Defensive-guard rejection branches — added during pre-archive cleanup
# from the dual-model verification (claude/glm5.1).
# ---------------------------------------------------------------------------


class TestPFEstimateHeaderEmptyNodeIds:
    """``PFEstimateHeader.__post_init__`` rejects an empty ``node_ids``
    tuple — the main estimate stream is meaningless if no nodes are
    declared. Defensive guard the implementer added; this test pins it
    so future refactors cannot silently relax it."""

    def test_header_empty_node_ids_raises(self) -> None:
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateHeader

        with pytest.raises(ValueError, match=r"node_ids"):
            PFEstimateHeader(
                schema_version="1.0",
                scenario_path="/tmp/scenario.jsonl",
                scenario_seed=42,
                pf_impl="float64_bootstrap",
                n_particles=500,
                node_ids=(),  # empty
                created_at_utc="2026-04-22T00:00:00Z",
            )


class TestPFEstimateRecordNonFiniteMean:
    """``PFEstimateRecord.__post_init__`` rejects non-finite (NaN/inf)
    entries in ``mean`` — a NaN in the posterior would propagate
    silently downstream, breaking the spec's "mean entries are finite"
    sanity invariant (task 31.1) at write time rather than at audit
    time."""

    def test_record_nan_mean_entry_raises(self) -> None:
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateRecord

        with pytest.raises(ValueError, match=r"mean|finite"):
            PFEstimateRecord(
                t=0,
                t_sec=0.0,
                node_id="n00",
                mean=(1.0, float("nan"), 3.0),
                cov_diag=(0.1, 0.2, 0.3),
                n_effective=10.0,
            )

    def test_record_inf_mean_entry_raises(self) -> None:
        from rtl.vectors.maritime.pf_estimates_schema import PFEstimateRecord

        with pytest.raises(ValueError, match=r"mean|finite"):
            PFEstimateRecord(
                t=0,
                t_sec=0.0,
                node_id="n00",
                mean=(1.0, float("inf"), 3.0),
                cov_diag=(0.1, 0.2, 0.3),
                n_effective=10.0,
            )


class TestParticleStreamReaderUnknownRecordType:
    """``ParticleStreamReader.node_ids_present`` raises ``ValueError``
    on an unknown ``record_type`` in the body — symmetric with
    ``__iter__``'s behavior. Originally ``node_ids_present`` silently
    skipped unknown types; per AGENTS.md "all errors must be explicit"
    the asymmetry is now closed."""

    def test_node_ids_present_raises_on_unknown_record_type(
        self, tmp_path: Path
    ) -> None:
        from rtl.vectors.maritime.pf_estimates_schema import (
            ParticleStreamReader,
        )

        path = tmp_path / "particles_corrupt.jsonl"
        # Header is valid; body has an unrecognized record_type.
        records = [
            _valid_sidecar_header_record(),
            {"record_type": "garbage", "t": 0, "t_sec": 0.0, "node_id": "n00"},
        ]
        _write_jsonl(path, records)
        reader = ParticleStreamReader(path)
        with pytest.raises(ValueError, match=r"record_type|particle"):
            reader.node_ids_present()
