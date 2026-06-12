"""PF estimate schema types and JSONL readers / writer.

Defines the versioned newline-delimited JSON contract for the M1 particle
filter's outputs:

- Main estimate stream (``pf_estimates.jsonl``): per ``(node_id, tick)``
  records carrying ``mean`` + ``cov_diag`` + ``n_effective``. No
  particle-level data — that lives in the sidecar.
- Particle sidecar (``pf_particles.jsonl``): per ``(node_id, tick)``
  records carrying the (subsampled) particles and weights, subject to
  tick / particle / node thinning declared in the sidecar header.

The two streams have separate header records and separate readers so
consumers can open one without paying for the other. A ``Protocol``-typed
``ParticleStreamWriter`` keeps the producer (`pf_float`) and consumer
(dashboard) decoupled from the JSONL backing — when the sidecar grows
beyond JSONL's sweet spot (see design D11), only the impl class swaps.

See ``openspec/changes/maritime-pf-float/design.md`` decisions D9, D10,
D11 for the rationale behind the dual-stream + Protocol design.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from rtl.vectors.maritime._jsonl_header import read_jsonl_header


PF_ESTIMATE_SCHEMA_VERSION: str = "1.0"
SUPPORTED_PF_ESTIMATE_VERSIONS: frozenset[str] = frozenset({"1.0"})

# Tolerance for the "weights sum to one" invariant on particle records.
# 1e-6 matches the spec band declared in the maritime-pf-estimate-schema
# delta ("Particle record weights sum to one").
_WEIGHT_SUM_TOL: float = 1e-6


# ---------------------------------------------------------------------------
# Main estimate stream — header + record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PFEstimateHeader:
    """Header record for the PF main estimate stream.

    Echoes the CLI inputs and PF configuration (`scenario_path`,
    `scenario_seed`, `pf_impl`, `n_particles`) so consumers can cross-
    check against the source scenario without reopening it. ``node_ids``
    enumerates every node the PF instance ran for — the privileged-
    subset ``focus_node_ids`` design from earlier drafts is gone (see
    design.md D9 / D10).
    """

    schema_version: str
    scenario_path: str
    scenario_seed: int
    pf_impl: str
    n_particles: int
    node_ids: tuple[str, ...]
    created_at_utc: str

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_PF_ESTIMATE_VERSIONS:
            raise ValueError(
                f"Unknown schema_version '{self.schema_version}'. "
                f"Supported versions: {SUPPORTED_PF_ESTIMATE_VERSIONS}"
            )
        if self.n_particles <= 0:
            raise ValueError(
                f"n_particles must be > 0, got {self.n_particles}"
            )
        if len(self.node_ids) == 0:
            raise ValueError("node_ids must be non-empty")


@dataclass(frozen=True, slots=True)
class PFEstimateRecord:
    """One PF estimate for a single ``(node_id, tick)``.

    The record carries summary statistics only — ``mean`` + ``cov_diag``
    + ``n_effective``. Particle-level data lives in the sidecar
    (``ParticleRecord``); a downstream consumer that only needs the
    posterior summary never has to open the sidecar.

    Construction-time invariants:
    - ``len(mean) == len(cov_diag)`` (both match the node's layout
      ``state_dim``; the dataclass cannot see the layout, so this just
      asserts equal length).
    - All ``cov_diag`` entries are non-negative.
    - ``n_effective`` is finite and strictly positive. The upper bound
      ``n_effective <= n_particles`` is enforced by ``PFEstimateReader``
      because ``n_particles`` lives in the header, not the record.
    - ``mean`` entries are finite (NaN / inf would silently propagate
      into downstream estimators).
    """

    t: int
    t_sec: float
    node_id: str
    mean: tuple[float, ...]
    cov_diag: tuple[float, ...]
    n_effective: float

    def __post_init__(self) -> None:
        if len(self.mean) != len(self.cov_diag):
            raise ValueError(
                f"mean and cov_diag length mismatch: "
                f"len(mean)={len(self.mean)}, len(cov_diag)={len(self.cov_diag)}"
            )
        for i, value in enumerate(self.mean):
            if not math.isfinite(value):
                raise ValueError(
                    f"mean[{i}] must be finite, got {value}"
                )
        for i, value in enumerate(self.cov_diag):
            if value < 0:
                raise ValueError(
                    f"cov_diag[{i}] must be >= 0, got {value}"
                )
        if not math.isfinite(self.n_effective) or self.n_effective <= 0:
            raise ValueError(
                f"n_effective must be finite and > 0, got {self.n_effective}"
            )


# ---------------------------------------------------------------------------
# Particle sidecar — header + record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PFEstimateHeader_Particles:
    """Header record for the particle sidecar stream.

    The sidecar is configured at PF run time via ``--thin-ticks``,
    ``--thin-particles``, and ``--thin-nodes``. The header records the
    chosen configuration so the consumer knows what shape to expect and
    can validate per-record ``len(particles) == thin_particles`` (the
    consumer-side check the dataclass cannot do alone).

    ``thin_nodes is None`` encodes "no subset restriction" — every node
    in the PF's fleet was eligible to emit records. A non-None tuple
    restricts the sidecar to the listed subset.
    """

    schema_version: str
    parent_estimate_path: str
    scenario_seed: int
    n_particles_full: int
    thin_ticks: int
    thin_particles: int
    thin_nodes: tuple[str, ...] | None
    created_at_utc: str

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_PF_ESTIMATE_VERSIONS:
            raise ValueError(
                f"Unknown schema_version '{self.schema_version}'. "
                f"Supported versions: {SUPPORTED_PF_ESTIMATE_VERSIONS}"
            )
        if self.thin_ticks < 1:
            raise ValueError(
                f"thin_ticks must be >= 1, got {self.thin_ticks}"
            )
        if self.thin_particles < 1:
            raise ValueError(
                f"thin_particles must be >= 1, got {self.thin_particles}"
            )
        if self.thin_particles > self.n_particles_full:
            raise ValueError(
                f"thin_particles ({self.thin_particles}) must not exceed "
                f"n_particles_full ({self.n_particles_full})"
            )


@dataclass(frozen=True, slots=True)
class ParticleRecord:
    """One particle cloud for a single ``(node_id, tick)``.

    Carries ``thin_particles``-many state vectors and matching weights.
    The ``len(particles) == header.thin_particles`` cross-check is the
    reader's job (the dataclass cannot see the header). The dataclass
    enforces:

    - ``len(particles) == len(weights)``
    - ``abs(sum(weights) - 1.0) <= 1e-6`` — weights are a categorical
      distribution; a sum that drifts off one is a producer bug worth
      surfacing immediately.
    """

    t: int
    t_sec: float
    node_id: str
    particles: tuple[tuple[float, ...], ...]
    weights: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.particles) != len(self.weights):
            raise ValueError(
                f"particles and weights length mismatch: "
                f"len(particles)={len(self.particles)}, "
                f"len(weights)={len(self.weights)}"
            )
        weight_sum = sum(self.weights)
        if abs(weight_sum - 1.0) > _WEIGHT_SUM_TOL:
            raise ValueError(
                f"weights must sum to 1.0 within {_WEIGHT_SUM_TOL}, "
                f"got sum={weight_sum}"
            )


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


class PFEstimateReader:
    """Reader for the PF main estimate stream (``pf_estimates.jsonl``).

    Parses the header at construction time so unknown schema versions
    surface immediately (the consumer never gets a chance to iterate a
    file it doesn't understand). Iteration re-opens the file and yields
    typed ``PFEstimateRecord`` instances; the per-record ``n_effective
    <= header.n_particles`` cross-check happens here because the bound
    requires both halves of the contract.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._header_dict = read_jsonl_header(
            self._path,
            expected_record_type="header",
            supported_versions=SUPPORTED_PF_ESTIMATE_VERSIONS,
        )
        self._header = PFEstimateHeader(
            schema_version=str(self._header_dict["schema_version"]),
            scenario_path=str(self._header_dict["scenario_path"]),
            scenario_seed=int(self._header_dict["scenario_seed"]),
            pf_impl=str(self._header_dict["pf_impl"]),
            n_particles=int(self._header_dict["n_particles"]),
            node_ids=tuple(self._header_dict["node_ids"]),
            created_at_utc=str(self._header_dict["created_at_utc"]),
        )

    def header(self) -> PFEstimateHeader:
        return self._header

    def __iter__(self) -> Iterator[PFEstimateRecord]:
        n_particles = self._header.n_particles
        with self._path.open("r") as f:
            # Skip the header line.
            f.readline()
            for line in f:
                if line.strip() == "":
                    continue
                try:
                    record = json.loads(line.strip())
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Failed to parse estimate line as JSON: {exc}"
                    ) from exc

                if record.get("record_type") != "estimate":
                    raise ValueError(
                        f"Expected record_type='estimate', got "
                        f"{record.get('record_type')!r}"
                    )

                n_effective = float(record["n_effective"])
                if n_effective > n_particles:
                    raise ValueError(
                        f"n_effective ({n_effective}) exceeds "
                        f"n_particles ({n_particles}) declared in header"
                    )

                yield PFEstimateRecord(
                    t=int(record["t"]),
                    t_sec=float(record["t_sec"]),
                    node_id=str(record["node_id"]),
                    mean=tuple(float(v) for v in record["mean"]),
                    cov_diag=tuple(float(v) for v in record["cov_diag"]),
                    n_effective=n_effective,
                )


class ParticleStreamReader:
    """Reader for the particle sidecar stream.

    Same construction-time schema-version check as ``PFEstimateReader``.
    Iteration enforces the per-record ``len(particles) == thin_particles``
    contract that the ``ParticleRecord`` dataclass cannot enforce alone.
    A separate ``node_ids_present()`` method walks the file once and
    returns the distinct ``node_id`` set — a dashboard-discovery
    convenience that doesn't iterate the full record stream.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._header_dict = read_jsonl_header(
            self._path,
            expected_record_type="particle_header",
            supported_versions=SUPPORTED_PF_ESTIMATE_VERSIONS,
        )
        thin_nodes_raw = self._header_dict.get("thin_nodes")
        thin_nodes: tuple[str, ...] | None = (
            None if thin_nodes_raw is None else tuple(str(n) for n in thin_nodes_raw)
        )

        self._header = PFEstimateHeader_Particles(
            schema_version=str(self._header_dict["schema_version"]),
            parent_estimate_path=str(self._header_dict["parent_estimate_path"]),
            scenario_seed=int(self._header_dict["scenario_seed"]),
            n_particles_full=int(self._header_dict["n_particles_full"]),
            thin_ticks=int(self._header_dict["thin_ticks"]),
            thin_particles=int(self._header_dict["thin_particles"]),
            thin_nodes=thin_nodes,
            created_at_utc=str(self._header_dict["created_at_utc"]),
        )

    def header(self) -> PFEstimateHeader_Particles:
        return self._header

    def __iter__(self) -> Iterator[ParticleRecord]:
        thin_particles = self._header.thin_particles
        with self._path.open("r") as f:
            # Skip the header line.
            f.readline()
            for line in f:
                if line.strip() == "":
                    continue
                try:
                    record = json.loads(line.strip())
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Failed to parse particle line as JSON: {exc}"
                    ) from exc

                if record.get("record_type") != "particle":
                    raise ValueError(
                        f"Expected record_type='particle', got "
                        f"{record.get('record_type')!r}"
                    )

                particles_raw = record["particles"]
                if len(particles_raw) != thin_particles:
                    raise ValueError(
                        f"particle record shape mismatch: header declares "
                        f"thin_particles={thin_particles}, record carries "
                        f"{len(particles_raw)} particles"
                    )

                particles = tuple(
                    tuple(float(v) for v in p) for p in particles_raw
                )
                weights = tuple(float(w) for w in record["weights"])

                yield ParticleRecord(
                    t=int(record["t"]),
                    t_sec=float(record["t_sec"]),
                    node_id=str(record["node_id"]),
                    particles=particles,
                    weights=weights,
                )

    def node_ids_present(self) -> frozenset[str]:
        """Return the set of distinct ``node_id`` values appearing in the file.

        Walks the file independently of ``__iter__`` so callers can ask
        "which nodes show up?" without paying to materialize every record.
        """
        seen: set[str] = set()
        with self._path.open("r") as f:
            # Skip the header line.
            f.readline()
            for line in f:
                if line.strip() == "":
                    continue
                try:
                    record = json.loads(line.strip())
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Failed to parse particle line as JSON: {exc}"
                    ) from exc
                actual_type = record.get("record_type")
                if actual_type != "particle":
                    raise ValueError(
                        f"Expected record_type='particle', got {actual_type!r}"
                    )
                seen.add(str(record["node_id"]))
        return frozenset(seen)


# ---------------------------------------------------------------------------
# Writer protocol + JSONL implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class ParticleStreamWriter(Protocol):
    """Protocol for writing the particle sidecar stream.

    The producer (``pf_float`` / ``run_pf_float``) depends on this
    Protocol, not on the JSONL impl class — when the sidecar grows
    beyond JSONL's sweet spot (design D11), only the impl swaps.
    """

    def write_header(self, header: PFEstimateHeader_Particles) -> None: ...
    def write_record(self, record: ParticleRecord) -> None: ...
    def close(self) -> None: ...


class _JSONLParticleStreamWriter:
    """JSONL-backed implementation of ``ParticleStreamWriter`` for M1.

    Lifecycle:
    - File is opened on the first ``write_header`` call so a writer
      that's only ``close()``d (e.g., ``--no-particles``) doesn't
      create an empty file.
    - ``write_record`` before ``write_header`` raises ``RuntimeError`` —
      the spec requires header-then-records ordering and an out-of-
      order call is a programming error worth surfacing loudly.
    - ``write_record`` after ``close`` raises ``RuntimeError`` for the
      same reason.
    - ``close`` is idempotent — defensive against caller cleanup paths
      that may close in a ``finally`` block after a successful close.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._file = None  # type: ignore[var-annotated]
        self._header_written = False
        self._closed = False

    def write_header(self, header: PFEstimateHeader_Particles) -> None:
        if self._closed:
            raise RuntimeError(
                f"Cannot write_header on a closed writer ({self._path})"
            )
        if self._header_written:
            raise RuntimeError(
                f"write_header called twice on the same writer ({self._path})"
            )
        self._file = self._path.open("w")
        record = {
            "record_type": "particle_header",
            "schema_version": header.schema_version,
            "parent_estimate_path": header.parent_estimate_path,
            "scenario_seed": header.scenario_seed,
            "n_particles_full": header.n_particles_full,
            "thin_ticks": header.thin_ticks,
            "thin_particles": header.thin_particles,
            "thin_nodes": (
                None if header.thin_nodes is None else list(header.thin_nodes)
            ),
            "created_at_utc": header.created_at_utc,
        }
        json.dump(record, self._file)
        self._file.write("\n")
        self._header_written = True

    def write_record(self, record: ParticleRecord) -> None:
        if self._closed:
            raise RuntimeError(
                f"Cannot write_record on a closed writer ({self._path})"
            )
        if not self._header_written:
            raise RuntimeError(
                "write_record called before write_header — header must be "
                "written first per the ParticleStreamWriter contract"
            )
        assert self._file is not None  # invariant: header_written implies file open
        line = {
            "record_type": "particle",
            "t": record.t,
            "t_sec": record.t_sec,
            "node_id": record.node_id,
            "particles": [list(p) for p in record.particles],
            "weights": list(record.weights),
        }
        json.dump(line, self._file)
        self._file.write("\n")

    def close(self) -> None:
        if self._closed:
            return
        if self._file is not None:
            self._file.close()
            self._file = None
        self._closed = True


def make_jsonl_particle_writer(path: str | Path) -> ParticleStreamWriter:
    """Construct a JSONL-backed ``ParticleStreamWriter``.

    The factory return type is the Protocol, not the concrete class —
    callers depend on the interface so the JSONL → binary swap (design
    D11) doesn't ripple into the producer.
    """
    return _JSONLParticleStreamWriter(path)
