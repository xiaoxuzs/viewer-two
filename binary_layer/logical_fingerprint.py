from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable, Mapping

from .blocks import SELECTED_PRECURSOR_KIND
from .serialization import canonical_json_bytes


_LOGICAL_JSON_BLOCKS = (
    "global_meta",
    "string_pool",
    "core_runs",
    "core_spectra",
    "core_precursors",
    "core_chromatograms",
    "indexes",
    "extensions",
)


@dataclass(frozen=True, slots=True)
class LogicalArrayFingerprint:
    array_id: str
    array_type: str
    value_count: int
    logical_sha256: str

    def as_json(self) -> dict[str, object]:
        return {
            "array_id": self.array_id,
            "array_type": self.array_type,
            "dtype": "float64",
            "logical_sha256": self.logical_sha256,
            "value_count": self.value_count,
        }


@dataclass(frozen=True, slots=True)
class LogicalFingerprint:
    sha256: str
    array_count: int
    numeric_value_count: int
    arrays: tuple[LogicalArrayFingerprint, ...]


def build_logical_fingerprint(
    blocks: Mapping[str, object],
    arrays: Iterable[LogicalArrayFingerprint],
) -> LogicalFingerprint:
    missing = [name for name in _LOGICAL_JSON_BLOCKS if name not in blocks]
    if missing:
        raise ValueError(f"missing logical blocks: {missing}")
    logical_blocks = {name: deepcopy(blocks[name]) for name in _LOGICAL_JSON_BLOCKS}
    global_meta = logical_blocks["global_meta"]
    if not isinstance(global_meta, dict):
        raise ValueError("global_meta must be an object")
    global_meta.pop("format_version", None)
    precursors = logical_blocks["core_precursors"]
    if not isinstance(precursors, list):
        raise ValueError("core_precursors must be a list")
    for record in precursors:
        if isinstance(record, dict) and record.get("precursor_kind") in {
            None,
            SELECTED_PRECURSOR_KIND,
        }:
            record.pop("precursor_kind", None)
    sorted_arrays = tuple(sorted(arrays, key=lambda item: item.array_id.encode("utf-8")))
    if len({item.array_id for item in sorted_arrays}) != len(sorted_arrays):
        raise ValueError("array_id values must be unique")
    document = {
        "arrays": [item.as_json() for item in sorted_arrays],
        "blocks": logical_blocks,
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    return LogicalFingerprint(
        sha256=digest,
        array_count=len(sorted_arrays),
        numeric_value_count=sum(item.value_count for item in sorted_arrays),
        arrays=sorted_arrays,
    )
