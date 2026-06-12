"""Shared JSONL header reader + schema-version validator.

Used by every maritime JSONL reader (scenario, scenario truth, PF
estimate stream, particle sidecar) so the "open file → parse first
line → validate record_type → validate schema_version" dance lives
in exactly one place.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path


def read_jsonl_header(
    path: Path,
    *,
    expected_record_type: str,
    supported_versions: Collection[str],
) -> dict:
    """Read the first line of ``path``, validate it, return the parsed dict.

    Raises ``ValueError`` when:
    - the file is empty,
    - the first line is not parseable JSON,
    - the parsed record's ``record_type`` differs from ``expected_record_type``,
    - the parsed record's ``schema_version`` is not in ``supported_versions``.
    """
    with path.open("r") as f:
        first_line = f.readline()
    if not first_line:
        raise ValueError(f"File {path} is empty")
    try:
        record = json.loads(first_line.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse header line as JSON: {exc}") from exc

    actual_type = record.get("record_type")
    if actual_type != expected_record_type:
        raise ValueError(
            f"First line is not a '{expected_record_type}' record "
            f"(record_type={actual_type!r})"
        )

    schema_version = record.get("schema_version")
    if schema_version not in supported_versions:
        raise ValueError(
            f"Unsupported schema_version {schema_version!r}. "
            f"Supported versions: {sorted(supported_versions)}"
        )

    return record
