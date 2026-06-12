"""Truth-access schema types and reader for maritime scenarios.

Defines TruthTickView and ScenarioTruthReader for reading truth state from
maritime scenario JSONL files. This module is physically separate from
scenario_schema to enable import-linter enforcement (PF code cannot import
truth).

The Observation union, LoraLinkRecord, and ScenarioHeader types are imported
from scenario_schema (shared types, not redefined here).
"""

import json
import pickle
from collections.abc import Mapping, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from rtl.vectors.maritime.scenario_schema import (
    SUPPORTED_SCHEMA_VERSIONS,
    LoraLinkRecord,
    Observation,
    ScenarioHeader,
)
from rtl.vectors.maritime._scenario_parse import _parse_observations, _parse_lora_links
from rtl.vectors.maritime._jsonl_header import read_jsonl_header


@dataclass(frozen=True, slots=True)
class TruthTickView:
    """Truth view of a tick — includes node_truth + observations.

    The node_truth mapping holds per-node state ndarrays. The
    Observation union and LoraLinkRecord types are imported from
    scenario_schema (truth views carry the same observation types).
    """

    t: int
    t_sec: float
    node_truth: Mapping[str, np.ndarray]  # node_id -> state vector
    observations: tuple[Observation, ...]
    lora_links: tuple[LoraLinkRecord, ...]


class ScenarioTruthReader:
    """Reader for maritime scenario JSONL files (truth access).

    Parses the same JSONL files that ScenarioReader parses, but yields
    TruthTickView objects populated with per-node truth state. The reader
    exposes a header() method returning the parsed ScenarioHeader (imported
    from scenario_schema).

    The reader raises ValueError on files whose header declares an
    unsupported schema_version (shared version set with scenario_schema).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._header: ScenarioHeader | None = None
        self._header_record: dict | None = read_jsonl_header(
            self._path,
            expected_record_type="header",
            supported_versions=SUPPORTED_SCHEMA_VERSIONS,
        )

    def header(self) -> ScenarioHeader:
        """Return the parsed header as a ScenarioHeader (shared type from scenario_schema)."""
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

    def __iter__(self) -> Iterator[TruthTickView]:
        """Yield TruthTickView objects populated with node_truth + observations."""
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

                t = int(record["t"])
                t_sec = float(record["t_sec"])

                # Parse nodes field into per-node ndarrays
                nodes_dict = record.get("nodes", {})
                node_truth: dict[str, np.ndarray] = {
                    node_id: np.array(state_vec, dtype=float) for node_id, state_vec in nodes_dict.items()
                }

                # Parse observations and links using shared helpers
                observations = _parse_observations(record.get("observations", []))
                lora_links = _parse_lora_links(record.get("lora_links", []))

                yield TruthTickView(
                    t=t,
                    t_sec=t_sec,
                    node_truth=node_truth,
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
