from __future__ import annotations

import hashlib
import json
import math
import mmap
import os
import re
import struct
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, BinaryIO, Callable

import numpy as np

from .constants import (
    BLOCK_NAMES,
    DIRECTORY_LENGTH_STRUCT,
    HEADER_SIZE,
    ZP_ENDIANNESS_LITTLE,
    ZP_MAGIC,
    ZP_VERSION_V2,
)
from .exceptions import MzmlSchemaError
from .models import BlockDirectoryEntry, ValidationIssue, ValidationResult
from .mzml_schema import (
    MZML_AUXILIARY_ARRAYS_EXTENSION_TYPE,
    MZML_EXTENSION_SCHEMA_VERSION,
    MZML_METADATA_EXTENSION_TYPE,
    MzmlAuxiliaryArraysV1,
    MzmlMetadataV1,
    OwnerKind,
)
from .precursor_contract import PRECURSOR_RECORD_FIELDS, validate_precursor_record
from .serialization import canonical_json_bytes


_ARRAYS_HEADER = struct.Struct("<8sHBBIQQQQ16s")
_ARRAYS_MAGIC = b"ZPARRV2\0"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOP_ENTRY_FIELDS = frozenset({"block_name", "offset", "length", "encoding", "checksum"})
_ARRAY_ENTRY_FIELDS = frozenset(
    {
        "array_id",
        "array_type",
        "dtype",
        "encoding",
        "value_count",
        "data_offset",
        "byte_length",
        "checksum",
    }
)
_JSON_BLOCK_NAMES = tuple(name for name in BLOCK_NAMES if name != "arrays")


@dataclass(frozen=True, slots=True)
class ZpV2ValidationLimits:
    max_arrays_block_length: int = 512 * 1024 * 1024
    max_top_directory_length: int = 64 * 1024 * 1024
    max_array_directory_length: int = 64 * 1024 * 1024
    max_entry_count: int = 100_000
    max_array_value_count: int = 16_000_000
    max_array_id_utf8_length: int = 4096
    max_payload_length: int = 448 * 1024 * 1024
    max_work_memory: int = 64 * 1024 * 1024
    chunk_size: int = 256 * 1024

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{item.name} must be a positive integer")
        if self.chunk_size % 8:
            raise ValueError("chunk_size must be an 8-byte multiple")
        if self.chunk_size > self.max_work_memory:
            raise ValueError("chunk_size must not exceed max_work_memory")


DEFAULT_V2_VALIDATION_LIMITS = ZpV2ValidationLimits()


@dataclass(frozen=True, slots=True)
class _ArrayMeta:
    array_id: str
    array_type: str
    value_count: int
    data_offset: int
    byte_length: int
    checksum: str


class _StopValidation(Exception):
    pass


class _DuplicateJsonKey(ValueError):
    pass


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonnegative_int(value: object) -> bool:
    return _plain_int(value) and value >= 0


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _issue(
    code: str,
    message: str,
    location: str | None = None,
    *,
    actual: object | None = None,
    limit: int | None = None,
) -> ValidationIssue:
    details = message
    if location is not None:
        details += f"; location={location}"
    if actual is not None:
        details += f"; actual={actual!r}"
    if limit is not None:
        details += f"; limit={limit}"
    return ValidationIssue(code, details, "error", location)


def _fingerprint(stat_result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


class _ValidationState:
    def __init__(
        self,
        path: Path,
        stream: BinaryIO,
        version: int,
        issues: list[ValidationIssue],
        limits: ZpV2ValidationLimits,
    ) -> None:
        self.path = path
        self.stream = stream
        self.version = version
        self.issues = issues
        self.limits = limits
        self.checked_blocks = 0
        self.metrics: dict[str, int | bool | str] = {
            "arrays_size": 0,
            "arrays_payload_length": 0,
            "entry_count": 0,
            "numeric_value_count": 0,
            "payload_bytes_read": 0,
            "payload_scan_count": 0,
            "max_single_payload_read": 0,
            "chunk_size": limits.chunk_size,
            "full_payload_materialized": False,
            "array_values_retained": False,
            "numeric_chunk_count": 0,
            "numeric_validation_backend": "numpy-frombuffer",
            "payload_access_backend": "chunked-read",
            "mmap_bytes_visited": 0,
            "bytes_read": 0,
            "read_call_count": 0,
        }
        self._path_start = _fingerprint(path.stat())
        self._handle_start = self._handle_fingerprint()
        if self._handle_start is not None and self._handle_start[:4] != self._path_start[:4]:
            self.stop("FILE_CHANGED_DURING_VALIDATION", "file identity changed while opening", "file")

    def _handle_fingerprint(self) -> tuple[int, int, int, int, int] | None:
        try:
            return _fingerprint(os.fstat(self.stream.fileno()))
        except (AttributeError, OSError, ValueError):
            return None

    def add(
        self,
        code: str,
        message: str,
        location: str | None = None,
        *,
        actual: object | None = None,
        limit: int | None = None,
    ) -> None:
        self.issues.append(_issue(code, message, location, actual=actual, limit=limit))

    def stop(
        self,
        code: str,
        message: str,
        location: str | None = None,
        *,
        actual: object | None = None,
        limit: int | None = None,
    ) -> None:
        self.add(code, message, location, actual=actual, limit=limit)
        raise _StopValidation

    def check_limit(self, code: str, actual: int, limit: int, location: str) -> None:
        if actual > limit:
            self.stop(code, "resource limit exceeded", location, actual=actual, limit=limit)

    def read_exact(self, length: int, code: str, location: str) -> bytes:
        try:
            payload = self.stream.read(length)
        except OSError as exc:
            self.stop(code, "read failed", location, actual=str(exc))
        if len(payload) != length:
            self.stop(code, "data is truncated", location, actual=len(payload), limit=length)
        self.metrics["bytes_read"] = int(self.metrics["bytes_read"]) + len(payload)
        self.metrics["read_call_count"] = int(self.metrics["read_call_count"]) + 1
        return payload

    def finish(self) -> ValidationResult:
        try:
            path_end = _fingerprint(self.path.stat())
            handle_end = self._handle_fingerprint()
            if path_end != self._path_start or (
                self._handle_start is not None and handle_end != self._handle_start
            ):
                if not any(item.code == "FILE_CHANGED_DURING_VALIDATION" for item in self.issues):
                    self.add("FILE_CHANGED_DURING_VALIDATION", "file identity changed during validation", "file")
        except OSError as exc:
            if not any(item.code == "FILE_CHANGED_DURING_VALIDATION" for item in self.issues):
                self.add(
                    "FILE_CHANGED_DURING_VALIDATION",
                    "file identity could not be rechecked",
                    "file",
                    actual=str(exc),
                )
        return ValidationResult(
            not any(item.severity == "error" for item in self.issues),
            self.issues,
            self.checked_blocks,
            self.path,
            self.version,
            metrics=dict(self.metrics),
        )


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant {value}")


def _parse_canonical_json(
    payload: bytes,
    *,
    invalid_code: str,
    noncanonical_code: str,
    location: str,
    add: Callable[..., None],
    timings: dict[str, float] | None = None,
) -> object | None:
    phase_started = time.perf_counter()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        add(invalid_code, "JSON is not valid UTF-8", location, actual=exc.start)
        return None
    if timings is not None:
        timings["decode_seconds"] = time.perf_counter() - phase_started
    phase_started = time.perf_counter()
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateJsonKey as exc:
        add(invalid_code, "duplicate JSON object key", location, actual=str(exc))
        return None
    except (json.JSONDecodeError, ValueError) as exc:
        add(invalid_code, "invalid strict JSON", location, actual=str(exc))
        return None
    if timings is not None:
        timings["parse_seconds"] = time.perf_counter() - phase_started
    phase_started = time.perf_counter()
    try:
        canonical = canonical_json_bytes(value)
    except (TypeError, UnicodeError, ValueError) as exc:
        add(invalid_code, "JSON cannot be canonicalized", location, actual=str(exc))
        return None
    if timings is not None:
        timings["canonicalize_seconds"] = time.perf_counter() - phase_started
    if canonical != payload:
        add(noncanonical_code, "JSON is not canonical", location)
        return None
    return value


def _field_set_is_exact(
    value: object,
    expected: frozenset[str],
    block_name: str,
    state: _ValidationState,
    location: str,
) -> bool:
    if not isinstance(value, dict):
        state.add("INVALID_BLOCK_SCHEMA", "value must be an object", location)
        return False
    actual = frozenset(value)
    if actual != expected:
        state.add(
            "INVALID_BLOCK_SCHEMA",
            "object field set is not exact",
            location,
            actual=sorted(actual),
        )
        return False
    return True


_GLOBAL_FIELDS = frozenset(
    {
        "format_version", "source_type", "source_file_name", "source_file_hash",
        "run_count", "spectrum_count", "chromatogram_count", "array_count",
        "created_at", "generator_name", "generator_version", "notes",
    }
)
_RUN_FIELDS = frozenset(
    {"run_id", "source_file", "run_name", "spectrum_count", "chromatogram_count", "start_rt", "end_rt"}
)
_SPECTRUM_FIELDS = frozenset(
    {
        "spectrum_id", "run_id", "ms_level", "scan_number", "native_id", "rt",
        "precursor_id", "mz_array_id", "intensity_array_id",
    }
)
_CHROMATOGRAM_FIELDS = frozenset(
    {"chromatogram_id", "run_id", "chromatogram_type", "time_array_id", "intensity_array_id", "native_id"}
)
_INDEX_FIELDS = frozenset({"scan_index", "rt_index", "spectrum_id_index"})
_EXTENSION_FIELDS = frozenset({"extension_type", "extension_version", "payload"})


def _validate_json_schemas(blocks: dict[str, object], state: _ValidationState) -> None:
    meta = blocks.get("global_meta")
    if _field_set_is_exact(meta, _GLOBAL_FIELDS, "global_meta", state, "global_meta"):
        assert isinstance(meta, dict)
        for field in ("source_type", "source_file_name", "source_file_hash", "created_at", "generator_name", "generator_version"):
            if not isinstance(meta[field], str):
                state.add("INVALID_FIELD_TYPE", f"{field} must be a string", "global_meta")
        for field in ("run_count", "spectrum_count", "chromatogram_count", "array_count"):
            if not _nonnegative_int(meta[field]):
                state.add("INVALID_FIELD_TYPE", f"{field} must be a nonnegative integer", "global_meta")
        if meta["format_version"] != ZP_VERSION_V2 or not _plain_int(meta["format_version"]):
            state.add(
                "FORMAT_VERSION_MISMATCH",
                "global_meta.format_version must match Header version 2",
                "global_meta.format_version",
                actual=meta["format_version"],
                limit=ZP_VERSION_V2,
            )
        notes = meta["notes"]
        if not isinstance(notes, list) or any(not isinstance(item, str) for item in notes):
            state.add("INVALID_FIELD_TYPE", "notes must be a list of strings", "global_meta")

    pool = blocks.get("string_pool")
    if _field_set_is_exact(pool, frozenset({"strings"}), "string_pool", state, "string_pool"):
        assert isinstance(pool, dict)
        strings = pool["strings"]
        if not isinstance(strings, list) or any(not isinstance(item, str) for item in strings):
            state.add("INVALID_FIELD_TYPE", "strings must be a list of strings", "string_pool")

    _validate_record_block(blocks.get("core_runs"), _RUN_FIELDS, "core_runs", state, _validate_run)
    _validate_record_block(blocks.get("core_spectra"), _SPECTRUM_FIELDS, "core_spectra", state, _validate_spectrum)
    _validate_precursor_block(blocks.get("core_precursors"), state)
    _validate_record_block(
        blocks.get("core_chromatograms"),
        _CHROMATOGRAM_FIELDS,
        "core_chromatograms",
        state,
        _validate_chromatogram,
    )
    _validate_indexes_schema(blocks.get("indexes"), state)
    _validate_record_block(blocks.get("extensions"), _EXTENSION_FIELDS, "extensions", state, _validate_extension_record)


def _validate_record_block(
    value: object,
    expected_fields: frozenset[str],
    block_name: str,
    state: _ValidationState,
    validate_record: Callable[[dict[str, object], _ValidationState, str], None],
) -> None:
    if not isinstance(value, list):
        state.add("INVALID_BLOCK_SCHEMA", "top-level value must be a list", block_name)
        return
    for position, record in enumerate(value):
        location = f"{block_name}[{position}]"
        if not _field_set_is_exact(record, expected_fields, block_name, state, location):
            continue
        assert isinstance(record, dict)
        validate_record(record, state, location)


def _require_nonempty_strings(
    record: dict[str, object], names: tuple[str, ...], state: _ValidationState, location: str
) -> None:
    for name in names:
        if not isinstance(record[name], str) or not record[name]:
            state.add("INVALID_FIELD_TYPE", f"{name} must be a nonempty string", location)


def _validate_run(record: dict[str, object], state: _ValidationState, location: str) -> None:
    _require_nonempty_strings(record, ("run_id", "source_file", "run_name"), state, location)
    for name in ("spectrum_count", "chromatogram_count"):
        if not _nonnegative_int(record[name]):
            state.add("INVALID_FIELD_TYPE", f"{name} must be a nonnegative integer", location)
    for name in ("start_rt", "end_rt"):
        if not _finite_number(record[name]) or record[name] < 0:
            state.add("INVALID_RT", f"{name} must be finite and nonnegative", location)
    if _finite_number(record["start_rt"]) and _finite_number(record["end_rt"]) and record["start_rt"] > record["end_rt"]:
        state.add("INVALID_RT", "start_rt must not exceed end_rt", location)


def _validate_spectrum(record: dict[str, object], state: _ValidationState, location: str) -> None:
    _require_nonempty_strings(
        record,
        ("spectrum_id", "run_id", "native_id", "mz_array_id", "intensity_array_id"),
        state,
        location,
    )
    if record["ms_level"] not in (1, 2) or not _plain_int(record["ms_level"]):
        state.add("INVALID_MS_LEVEL", "ms_level must be 1 or 2", location)
    if not _nonnegative_int(record["scan_number"]):
        state.add("INVALID_SCAN_NUMBER", "scan_number must be nonnegative", location)
    if not _finite_number(record["rt"]) or record["rt"] < 0:
        state.add("INVALID_RT", "rt must be finite and nonnegative", location)
    if record["precursor_id"] is not None and not isinstance(record["precursor_id"], str):
        state.add("INVALID_FIELD_TYPE", "precursor_id must be a string or null", location)


def _validate_precursor_block(value: object, state: _ValidationState) -> None:
    if not isinstance(value, list):
        state.add("INVALID_BLOCK_SCHEMA", "top-level value must be a list", "core_precursors")
        return
    for position, record in enumerate(value):
        location = f"core_precursors[{position}]"
        if not isinstance(record, dict):
            state.add("INVALID_RECORD_SCHEMA", "record must be an object", location)
            continue
        unknown = frozenset(record) - PRECURSOR_RECORD_FIELDS
        if unknown:
            state.add(
                "INVALID_BLOCK_SCHEMA",
                "precursor record contains unknown fields",
                location,
                actual=sorted(unknown),
            )
        _validate_precursor(record, state, location)


def _validate_precursor(record: dict[str, object], state: _ValidationState, location: str) -> None:
    for name in ("precursor_id", "spectrum_id"):
        if not isinstance(record.get(name), str) or not record[name]:
            state.add("INVALID_FIELD_TYPE", f"{name} must be a nonempty string", location)
    for issue in validate_precursor_record(record):
        state.add(issue.code, issue.message, location)


def _validate_chromatogram(record: dict[str, object], state: _ValidationState, location: str) -> None:
    _require_nonempty_strings(
        record,
        ("chromatogram_id", "run_id", "chromatogram_type", "time_array_id", "intensity_array_id", "native_id"),
        state,
        location,
    )


def _validate_indexes_schema(value: object, state: _ValidationState) -> None:
    if not _field_set_is_exact(value, _INDEX_FIELDS, "indexes", state, "indexes"):
        return
    assert isinstance(value, dict)
    specifications = (
        ("scan_index", frozenset({"scan_number", "spectrum_id"}), "scan_number", _nonnegative_int),
        ("rt_index", frozenset({"rt", "spectrum_id"}), "rt", _finite_number),
        ("spectrum_id_index", frozenset({"spectrum_id", "position"}), "position", _nonnegative_int),
    )
    for name, expected, scalar_name, scalar_check in specifications:
        records = value[name]
        if not isinstance(records, list):
            state.add("INVALID_BLOCK_SCHEMA", f"{name} must be a list", "indexes")
            continue
        for position, record in enumerate(records):
            location = f"indexes.{name}[{position}]"
            if not isinstance(record, dict) or frozenset(record) != expected:
                state.add("INVALID_BLOCK_SCHEMA", "index record field set is not exact", location)
                continue
            if not isinstance(record["spectrum_id"], str) or not scalar_check(record[scalar_name]):
                state.add("INVALID_FIELD_TYPE", "index record has invalid field types", location)
            if scalar_name == "rt" and _finite_number(record[scalar_name]) and record[scalar_name] < 0:
                state.add("INVALID_RT", "index rt must be nonnegative", location)


def _validate_extension_record(record: dict[str, object], state: _ValidationState, location: str) -> None:
    if not isinstance(record["extension_type"], str) or not record["extension_type"]:
        state.add("INVALID_FIELD_TYPE", "extension_type must be a nonempty string", location)
    if not isinstance(record["extension_version"], str) or not record["extension_version"]:
        state.add("INVALID_FIELD_TYPE", "extension_version must be a nonempty string", location)
    if not isinstance(record["payload"], dict):
        state.add("INVALID_FIELD_TYPE", "payload must be an object", location)


def _unique_map(
    records: list[dict[str, object]], field: str, block: str, state: _ValidationState
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for record in records:
        value = record.get(field)
        if not isinstance(value, str) or not value:
            continue
        if value in result:
            state.add("DUPLICATE_ID", f"duplicate {field}", block, actual=value)
        else:
            result[value] = record
    return result


def _records(blocks: dict[str, object], name: str) -> list[dict[str, object]]:
    value = blocks.get(name)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _validate_relationships(
    blocks: dict[str, object], arrays: tuple[_ArrayMeta, ...], state: _ValidationState
) -> None:
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    runs = _records(blocks, "core_runs")
    spectra = _records(blocks, "core_spectra")
    precursors = _records(blocks, "core_precursors")
    chromatograms = _records(blocks, "core_chromatograms")
    run_map = _unique_map(runs, "run_id", "core_runs", state)
    spectrum_map = _unique_map(spectra, "spectrum_id", "core_spectra", state)
    precursor_map = _unique_map(precursors, "precursor_id", "core_precursors", state)
    _unique_map(chromatograms, "chromatogram_id", "core_chromatograms", state)
    array_map = {item.array_id: item for item in arrays}

    precursor_use: dict[str, int] = {}
    for spectrum in spectra:
        spectrum_id = spectrum.get("spectrum_id")
        if spectrum.get("run_id") not in run_map:
            state.add("INVALID_REFERENCE", f"Spectrum {spectrum_id} references a missing run", "core_spectra")
        mz = array_map.get(spectrum.get("mz_array_id"))
        intensity = array_map.get(spectrum.get("intensity_array_id"))
        if mz is None:
            state.add("INVALID_REFERENCE", f"Spectrum {spectrum_id} references a missing m/z array", "core_spectra")
        elif mz.array_type != "mz":
            state.add("ARRAY_TYPE_MISMATCH", f"Spectrum {spectrum_id} m/z reference has the wrong type", "core_spectra")
        if intensity is None:
            state.add("INVALID_REFERENCE", f"Spectrum {spectrum_id} references a missing intensity array", "core_spectra")
        elif intensity.array_type != "intensity":
            state.add("ARRAY_TYPE_MISMATCH", f"Spectrum {spectrum_id} intensity reference has the wrong type", "core_spectra")
        if mz is not None and intensity is not None:
            if mz.value_count != intensity.value_count:
                state.add("ARRAY_LENGTH_MISMATCH", f"Spectrum {spectrum_id} arrays have different lengths", "core_spectra")
            if mz.value_count == 0 or intensity.value_count == 0:
                state.add("EMPTY_REFERENCED_ARRAY", f"Spectrum {spectrum_id} arrays must be nonempty", "core_spectra")
        precursor_id = spectrum.get("precursor_id")
        ms_level = spectrum.get("ms_level")
        if ms_level == 1 and precursor_id is not None:
            state.add("INVALID_REFERENCE", f"MS1 Spectrum {spectrum_id} must not reference a precursor", "core_spectra")
        if ms_level == 2:
            if precursor_id not in precursor_map:
                state.add("INVALID_REFERENCE", f"MS2 Spectrum {spectrum_id} references a missing precursor", "core_spectra")
            elif precursor_map[precursor_id].get("spectrum_id") != spectrum_id:
                state.add("INVALID_REFERENCE", f"MS2 Spectrum {spectrum_id} precursor link is not bidirectional", "core_spectra")
            if isinstance(precursor_id, str):
                precursor_use[precursor_id] = precursor_use.get(precursor_id, 0) + 1

    for precursor in precursors:
        precursor_id = precursor.get("precursor_id")
        spectrum = spectrum_map.get(precursor.get("spectrum_id"))
        if spectrum is None:
            state.add("INVALID_REFERENCE", f"Precursor {precursor_id} references a missing Spectrum", "core_precursors")
        elif spectrum.get("ms_level") != 2 or spectrum.get("precursor_id") != precursor_id:
            state.add("INVALID_REFERENCE", f"Precursor {precursor_id} has an invalid reverse Spectrum link", "core_precursors")
        if isinstance(precursor_id, str) and precursor_use.get(precursor_id, 0) != 1:
            state.add("INVALID_REFERENCE", f"Precursor {precursor_id} must be used by exactly one MS2 Spectrum", "core_precursors")

    for chromatogram in chromatograms:
        chromatogram_id = chromatogram.get("chromatogram_id")
        if chromatogram.get("run_id") not in run_map:
            state.add("INVALID_REFERENCE", f"Chromatogram {chromatogram_id} references a missing run", "core_chromatograms")
        time_meta = array_map.get(chromatogram.get("time_array_id"))
        intensity_meta = array_map.get(chromatogram.get("intensity_array_id"))
        if time_meta is None:
            state.add("INVALID_REFERENCE", f"Chromatogram {chromatogram_id} references a missing time array", "core_chromatograms")
        elif time_meta.array_type != "time":
            state.add("ARRAY_TYPE_MISMATCH", f"Chromatogram {chromatogram_id} time array has the wrong type", "core_chromatograms")
        if intensity_meta is None:
            state.add("INVALID_REFERENCE", f"Chromatogram {chromatogram_id} references a missing intensity array", "core_chromatograms")
        elif intensity_meta.array_type != "intensity":
            state.add("ARRAY_TYPE_MISMATCH", f"Chromatogram {chromatogram_id} intensity array has the wrong type", "core_chromatograms")
        if time_meta is not None and intensity_meta is not None:
            if time_meta.value_count != intensity_meta.value_count:
                state.add("ARRAY_LENGTH_MISMATCH", f"Chromatogram {chromatogram_id} arrays have different lengths", "core_chromatograms")
            if time_meta.value_count == 0 or intensity_meta.value_count == 0:
                state.add("EMPTY_REFERENCED_ARRAY", f"Chromatogram {chromatogram_id} arrays must be nonempty", "core_chromatograms")

    for run_id, run in run_map.items():
        spectrum_count = sum(item.get("run_id") == run_id for item in spectra)
        chromatogram_count = sum(item.get("run_id") == run_id for item in chromatograms)
        if run.get("spectrum_count") != spectrum_count or run.get("chromatogram_count") != chromatogram_count:
            state.add("COUNT_MISMATCH", f"Run {run_id} counts do not match owned records", "core_runs")

    meta = blocks.get("global_meta")
    if isinstance(meta, dict):
        expected = {
            "run_count": len(runs),
            "spectrum_count": len(spectra),
            "chromatogram_count": len(chromatograms),
            "array_count": len(arrays),
        }
        for field, count in expected.items():
            if meta.get(field) != count:
                state.add("COUNT_MISMATCH", f"global_meta.{field} does not match logical records", "global_meta", actual=meta.get(field), limit=count)

    _validate_string_pool(blocks, runs, spectra, chromatograms, state)
    _validate_index_references(blocks.get("indexes"), spectra, spectrum_map, state)
    _validate_extensions(blocks.get("extensions"), run_map, spectrum_map, chromatograms, array_map, state)
    state.metrics["relationship_wall_seconds"] = time.perf_counter() - wall_started
    state.metrics["relationship_cpu_seconds"] = time.process_time() - cpu_started
    state.metrics["relationship_spectrum_count"] = len(spectra)
    state.metrics["relationship_array_count"] = len(arrays)


def _validate_string_pool(
    blocks: dict[str, object],
    runs: list[dict[str, object]],
    spectra: list[dict[str, object]],
    chromatograms: list[dict[str, object]],
    state: _ValidationState,
) -> None:
    pool = blocks.get("string_pool")
    if not isinstance(pool, dict) or not isinstance(pool.get("strings"), list):
        return
    strings = pool["strings"]
    if any(not isinstance(item, str) for item in strings):
        return
    if len(strings) != len(set(strings)):
        state.add("DUPLICATE_ID", "string_pool contains duplicate strings", "string_pool")
    string_set = set(strings)
    required: list[object] = []
    for run in runs:
        required.extend((run.get("source_file"), run.get("run_name")))
    required.extend(item.get("native_id") for item in spectra)
    for chromatogram in chromatograms:
        required.extend((chromatogram.get("chromatogram_type"), chromatogram.get("native_id")))
    for value in required:
        if isinstance(value, str) and value not in string_set:
            state.add("INVALID_REFERENCE", "string_pool is missing a referenced string", "string_pool", actual=value)


def _validate_index_references(
    value: object,
    spectra: list[dict[str, object]],
    spectrum_map: dict[str, dict[str, object]],
    state: _ValidationState,
) -> None:
    if not isinstance(value, dict):
        return
    for name in ("scan_index", "rt_index", "spectrum_id_index"):
        records = value.get(name)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict) or record.get("spectrum_id") not in spectrum_map:
                state.add("INVALID_REFERENCE", f"indexes.{name} references a missing Spectrum", "indexes")
    records = value.get("spectrum_id_index")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            position = record.get("position")
            spectrum_id = record.get("spectrum_id")
            if not _nonnegative_int(position) or position >= len(spectra):
                state.add("INVALID_INDEX_POSITION", "spectrum_id_index position is invalid", "indexes")
            elif spectra[position].get("spectrum_id") != spectrum_id:
                state.add("INDEX_POSITION_MISMATCH", "spectrum_id_index position does not match Spectrum order", "indexes")


def _validate_extensions(
    value: object,
    run_map: dict[str, dict[str, object]],
    spectrum_map: dict[str, dict[str, object]],
    chromatograms: list[dict[str, object]],
    array_map: dict[str, _ArrayMeta],
    state: _ValidationState,
) -> None:
    if not isinstance(value, list):
        return
    extension_types: set[str] = set()
    chromatogram_map = {
        item["chromatogram_id"]: item
        for item in chromatograms
        if isinstance(item.get("chromatogram_id"), str)
    }
    for position, record in enumerate(value):
        if not isinstance(record, dict):
            continue
        extension_type = record.get("extension_type")
        extension_version = record.get("extension_version")
        payload = record.get("payload")
        location = f"extensions[{position}]"
        if not isinstance(extension_type, str) or not isinstance(payload, dict):
            continue
        if extension_type in extension_types:
            state.add("DUPLICATE_ID", "duplicate extension_type", "extensions", actual=extension_type)
        extension_types.add(extension_type)
        if extension_type in {MZML_METADATA_EXTENSION_TYPE, MZML_AUXILIARY_ARRAYS_EXTENSION_TYPE} and extension_version != str(MZML_EXTENSION_SCHEMA_VERSION):
            state.add("INVALID_EXTENSION_SCHEMA", "unsupported mzML extension version", location, actual=extension_version)
            continue
        try:
            if extension_type == MZML_METADATA_EXTENSION_TYPE:
                metadata = MzmlMetadataV1.from_payload(payload)
                if metadata.run.run_id not in run_map:
                    state.add("INVALID_REFERENCE", "mzml_metadata references a missing run", "extensions")
                metadata_spectra = {item.spectrum_id: item for item in metadata.spectra}
                metadata_chromatograms = {item.chromatogram_id: item for item in metadata.chromatograms}
                if not set(metadata_spectra).issubset(spectrum_map):
                    state.add("INVALID_REFERENCE", "mzml_metadata references a missing Spectrum owner", "extensions")
                if not set(metadata_chromatograms).issubset(chromatogram_map):
                    state.add("INVALID_REFERENCE", "mzml_metadata references a missing Chromatogram owner", "extensions")
                for spectrum_id, metadata_spectrum in metadata_spectra.items():
                    spectrum = spectrum_map.get(spectrum_id)
                    if spectrum is None:
                        continue
                    array_meta = array_map.get(spectrum.get("mz_array_id"))
                    if array_meta is not None and metadata_spectrum.default_array_length != array_meta.value_count:
                        state.add("COUNT_MISMATCH", "mzml_metadata Spectrum array length is inconsistent", "extensions")
                for chromatogram_id, metadata_chromatogram in metadata_chromatograms.items():
                    chromatogram = chromatogram_map.get(chromatogram_id)
                    if chromatogram is None:
                        continue
                    array_meta = array_map.get(chromatogram.get("time_array_id"))
                    if array_meta is not None and metadata_chromatogram.default_array_length != array_meta.value_count:
                        state.add("COUNT_MISMATCH", "mzml_metadata Chromatogram array length is inconsistent", "extensions")
            elif extension_type == MZML_AUXILIARY_ARRAYS_EXTENSION_TYPE:
                auxiliary = MzmlAuxiliaryArraysV1.from_payload(payload)
                for item in auxiliary.arrays:
                    if item.owner_kind is OwnerKind.SPECTRUM:
                        owner_exists = item.owner_id in spectrum_map
                    else:
                        owner_exists = item.owner_id in chromatogram_map
                    if not owner_exists:
                        state.add("INVALID_REFERENCE", "mzml_auxiliary_arrays references a missing owner", "extensions", actual=item.owner_id)
        except MzmlSchemaError as exc:
            state.add("INVALID_EXTENSION_SCHEMA", str(exc), location)


def _read_top_directory(
    state: _ValidationState,
    *,
    file_size: int,
    directory_offset: int,
) -> tuple[BlockDirectoryEntry, ...]:
    if directory_offset < HEADER_SIZE or directory_offset + DIRECTORY_LENGTH_STRUCT.size > file_size:
        state.stop(
            "INVALID_TOP_DIRECTORY_OFFSET",
            "top directory offset is outside the file",
            "header.directory_offset",
            actual=directory_offset,
            limit=file_size,
        )
    state.stream.seek(directory_offset)
    raw_length = state.read_exact(
        DIRECTORY_LENGTH_STRUCT.size,
        "INVALID_TOP_DIRECTORY_LENGTH",
        "directory.length",
    )
    directory_length = DIRECTORY_LENGTH_STRUCT.unpack(raw_length)[0]
    state.check_limit(
        "TOP_DIRECTORY_TOO_LARGE",
        directory_length,
        state.limits.max_top_directory_length,
        "directory.length",
    )
    state.check_limit(
        "VALIDATION_WORK_MEMORY_EXCEEDED",
        directory_length,
        state.limits.max_work_memory,
        "directory.length",
    )
    expected_eof = directory_offset + DIRECTORY_LENGTH_STRUCT.size + directory_length
    if expected_eof != file_size:
        state.stop(
            "INVALID_TOP_DIRECTORY_LENGTH",
            "top directory must end exactly at EOF",
            "directory.length",
            actual=expected_eof,
            limit=file_size,
        )
    raw_directory = state.read_exact(directory_length, "INVALID_TOP_DIRECTORY_LENGTH", "directory")
    parsed = _parse_canonical_json(
        raw_directory,
        invalid_code="INVALID_TOP_DIRECTORY_SCHEMA",
        noncanonical_code="NONCANONICAL_TOP_DIRECTORY",
        location="directory",
        add=state.add,
    )
    if parsed is None:
        raise _StopValidation
    if not isinstance(parsed, list):
        state.stop("INVALID_TOP_DIRECTORY_SCHEMA", "top directory must be a list", "directory")
    names = [item.get("block_name") for item in parsed if isinstance(item, dict)]
    for name in BLOCK_NAMES:
        if names.count(name) == 0:
            state.stop("MISSING_REQUIRED_BLOCK", "required top-level block is missing", name)
        if names.count(name) > 1:
            state.stop("DUPLICATE_BLOCK_NAME", "top-level block appears more than once", name)
    if len(parsed) != len(BLOCK_NAMES):
        state.stop(
            "INVALID_TOP_DIRECTORY_SCHEMA",
            "top directory must contain exactly nine entries",
            "directory",
            actual=len(parsed),
            limit=len(BLOCK_NAMES),
        )
    entries: list[BlockDirectoryEntry] = []
    previous_end = HEADER_SIZE
    for position, expected_name in enumerate(BLOCK_NAMES):
        raw_entry = parsed[position]
        location = f"directory[{position}]"
        if not isinstance(raw_entry, dict) or frozenset(raw_entry) != _TOP_ENTRY_FIELDS:
            state.stop("INVALID_TOP_DIRECTORY_SCHEMA", "directory entry field set is not exact", location)
        if raw_entry["block_name"] != expected_name:
            state.stop(
                "INVALID_TOP_DIRECTORY_ORDER",
                "top-level block name or order is invalid",
                f"{location}.block_name",
                actual=raw_entry["block_name"],
            )
        offset = raw_entry["offset"]
        length = raw_entry["length"]
        if not _nonnegative_int(offset) or not _nonnegative_int(length):
            state.stop("INVALID_TOP_DIRECTORY_SCHEMA", "offset and length must be nonnegative integers", location)
        if offset < previous_end:
            state.stop("OVERLAPPING_TOP_LEVEL_BLOCKS", "top-level blocks overlap", location, actual=offset, limit=previous_end)
        if offset > previous_end:
            state.stop("TOP_LEVEL_BLOCK_GAP", "top-level blocks must be contiguous", location, actual=offset, limit=previous_end)
        if offset + length > directory_offset:
            state.stop(
                "TOP_LEVEL_BLOCK_OUT_OF_BOUNDS",
                "top-level block extends into the directory",
                location,
                actual=offset + length,
                limit=directory_offset,
            )
        encoding = raw_entry["encoding"]
        expected_encoding = "zp-arrays-v2" if expected_name == "arrays" else "utf-8-json"
        if encoding != expected_encoding:
            state.stop(
                "ARRAYS_ENCODING_VERSION_MISMATCH",
                "block encoding is incompatible with ZP v2",
                f"{location}.encoding",
                actual=encoding,
            )
        checksum = raw_entry["checksum"]
        if not isinstance(checksum, str) or _SHA256_RE.fullmatch(checksum) is None:
            state.stop("INVALID_BLOCK_CHECKSUM_FORMAT", "checksum must be lowercase SHA-256", f"{location}.checksum")
        entries.append(BlockDirectoryEntry(expected_name, offset, length, encoding, checksum))
        previous_end = offset + length
    if previous_end != directory_offset:
        state.stop("TOP_LEVEL_BLOCK_GAP", "last block must end at the top directory", "directory", actual=previous_end, limit=directory_offset)
    return tuple(entries)


def _read_json_blocks(
    state: _ValidationState, entries: tuple[BlockDirectoryEntry, ...]
) -> dict[str, object]:
    parsed_blocks: dict[str, object] = {}
    checksum_issues: list[ValidationIssue] = []
    block_issues: list[ValidationIssue] = []

    def add_block_issue(
        code: str,
        message: str,
        location: str | None = None,
        *,
        actual: object | None = None,
        limit: int | None = None,
    ) -> None:
        block_issues.append(_issue(code, message, location, actual=actual, limit=limit))

    entry_map = {entry.block_name: entry for entry in entries}
    for block_name in _JSON_BLOCK_NAMES:
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        entry = entry_map[block_name]
        state.check_limit(
            "VALIDATION_WORK_MEMORY_EXCEEDED",
            entry.length,
            state.limits.max_work_memory,
            block_name,
        )
        state.stream.seek(entry.offset)
        payload = state.read_exact(entry.length, "TRUNCATED_BLOCK", block_name)
        state.checked_blocks += 1
        actual_checksum = hashlib.sha256(payload).hexdigest()
        if actual_checksum != entry.checksum:
            checksum_issues.append(
                _issue(
                    "BLOCK_CHECKSUM_MISMATCH",
                    "top-level block checksum does not match raw stored bytes",
                    block_name,
                    actual=actual_checksum,
                )
            )
        timings: dict[str, float] = {}
        parsed = _parse_canonical_json(
            payload,
            invalid_code="INVALID_BLOCK_JSON",
            noncanonical_code="NONCANONICAL_BLOCK_JSON",
            location=block_name,
            add=add_block_issue,
            timings=timings,
        )
        if parsed is not None:
            parsed_blocks[block_name] = parsed
        state.metrics[f"{block_name}_wall_seconds"] = (
            time.perf_counter() - wall_started
        )
        state.metrics[f"{block_name}_cpu_seconds"] = (
            time.process_time() - cpu_started
        )
        state.metrics[f"{block_name}_bytes"] = entry.length
        for phase, seconds in timings.items():
            state.metrics[f"{block_name}_{phase}"] = seconds
    state.issues.extend(checksum_issues)
    state.issues.extend(block_issues)
    _validate_json_schemas(parsed_blocks, state)
    return parsed_blocks


def _validate_arrays_region(
    state: _ValidationState, entry: BlockDirectoryEntry
) -> tuple[_ArrayMeta, ...]:
    limits = state.limits
    state.metrics["arrays_size"] = entry.length
    state.check_limit("ARRAYS_RESOURCE_LIMIT_EXCEEDED", entry.length, limits.max_arrays_block_length, "arrays.block_length")
    if entry.length < _ARRAYS_HEADER.size:
        state.stop("INVALID_ARRAY_DIRECTORY_LENGTH", "arrays block is shorter than its fixed Header", "arrays.block_length", actual=entry.length, limit=_ARRAYS_HEADER.size)
    state.stream.seek(entry.offset)
    raw_header = state.read_exact(_ARRAYS_HEADER.size, "INVALID_ARRAY_DIRECTORY_LENGTH", "arrays.header")
    (
        magic,
        schema_version,
        endianness,
        flags,
        entry_count,
        directory_offset,
        directory_length,
        payload_offset,
        payload_length,
        reserved,
    ) = _ARRAYS_HEADER.unpack(raw_header)
    if magic != _ARRAYS_MAGIC:
        state.stop("INVALID_ARRAYS_MAGIC", "invalid arrays magic", "arrays.header.magic", actual=magic)
    if schema_version != ZP_VERSION_V2:
        state.stop("UNSUPPORTED_ARRAYS_VERSION", "unsupported arrays schema version", "arrays.header.schema_version", actual=schema_version)
    if endianness != ZP_ENDIANNESS_LITTLE:
        state.stop("UNSUPPORTED_ARRAYS_ENDIANNESS", "unsupported arrays endianness", "arrays.header.endianness", actual=endianness)
    if flags != 0:
        state.stop("UNSUPPORTED_ARRAYS_FLAGS", "arrays flags must be zero", "arrays.header.flags", actual=flags)
    if reserved != b"\0" * 16:
        state.stop("NONZERO_ARRAYS_RESERVED", "arrays reserved bytes must be zero", "arrays.header.reserved")
    if directory_offset != _ARRAYS_HEADER.size:
        state.stop("INVALID_ARRAY_DIRECTORY_OFFSET", "internal directory must begin at byte 64", "arrays.header.directory_offset", actual=directory_offset, limit=_ARRAYS_HEADER.size)
    state.check_limit("ARRAY_COUNT_TOO_LARGE", entry_count, limits.max_entry_count, "arrays.entry_count")
    state.check_limit("ARRAY_DIRECTORY_TOO_LARGE", directory_length, limits.max_array_directory_length, "arrays.directory_length")
    state.check_limit("VALIDATION_WORK_MEMORY_EXCEEDED", directory_length, limits.max_work_memory, "arrays.directory_length")
    state.check_limit("ARRAY_PAYLOAD_TOO_LARGE", payload_length, limits.max_payload_length, "arrays.payload_length")
    directory_end = directory_offset + directory_length
    expected_payload_offset = (directory_end + 7) & ~7
    if directory_end > entry.length:
        state.stop("INVALID_ARRAY_DIRECTORY_LENGTH", "internal directory is outside the arrays block", "arrays.header.directory_length", actual=directory_length, limit=max(0, entry.length - directory_offset))
    if payload_offset % 8:
        state.stop("ARRAY_PAYLOAD_MISALIGNED", "payload offset must be 8-byte aligned", "arrays.header.payload_offset", actual=payload_offset)
    if payload_offset != expected_payload_offset:
        state.stop("INVALID_ARRAY_PAYLOAD_OFFSET", "payload offset does not match aligned directory end", "arrays.header.payload_offset", actual=payload_offset, limit=expected_payload_offset)
    payload_end = payload_offset + payload_length
    if payload_end > entry.length:
        state.stop("INVALID_ARRAY_PAYLOAD_LENGTH", "payload is outside the arrays block", "arrays.header.payload_length", actual=payload_end, limit=entry.length)
    if payload_end != entry.length:
        state.stop("ARRAYS_TRAILING_DATA", "arrays block contains trailing bytes", "arrays.block_length", actual=entry.length, limit=payload_end)
    state.stream.seek(entry.offset + directory_offset)
    raw_directory = state.read_exact(directory_length, "INVALID_ARRAY_DIRECTORY_LENGTH", "arrays.directory")
    padding = state.read_exact(payload_offset - directory_end, "INVALID_ARRAY_PAYLOAD_OFFSET", "arrays.padding")
    if any(padding):
        state.stop("NONZERO_ARRAY_PADDING", "arrays padding must be zero", "arrays.padding")
    parsed = _parse_canonical_json(
        raw_directory,
        invalid_code="INVALID_ARRAY_DIRECTORY_SCHEMA",
        noncanonical_code="NONCANONICAL_ARRAY_DIRECTORY",
        location="arrays.directory",
        add=state.add,
    )
    if parsed is None:
        raise _StopValidation
    if not isinstance(parsed, dict) or frozenset(parsed) != {"entries"} or not isinstance(parsed.get("entries"), list):
        state.stop("INVALID_ARRAY_DIRECTORY_SCHEMA", "directory must contain only an entries list", "arrays.directory")
    raw_entries = parsed["entries"]
    if len(raw_entries) != entry_count:
        state.stop("ARRAY_ENTRY_COUNT_MISMATCH", "entry_count does not match directory entries", "arrays.entry_count", actual=len(raw_entries), limit=entry_count)
    preliminary_ids: set[str] = set()
    preliminary_previous: bytes | None = None
    for position, raw_entry in enumerate(raw_entries):
        location = f"arrays.directory.entries[{position}]"
        if not isinstance(raw_entry, dict) or frozenset(raw_entry) != _ARRAY_ENTRY_FIELDS:
            state.stop("INVALID_ARRAY_DIRECTORY_SCHEMA", "array entry field set is not exact", location)
        array_id = raw_entry["array_id"]
        if not isinstance(array_id, str) or not array_id or "\0" in array_id:
            state.stop("INVALID_ARRAY_ID", "array_id must be a nonempty NUL-free string", f"{location}.array_id")
        try:
            encoded_id = array_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            state.stop("INVALID_ARRAY_ID", "array_id is not valid UTF-8", f"{location}.array_id", actual=str(exc))
        state.check_limit("ARRAY_ID_TOO_LONG", len(encoded_id), limits.max_array_id_utf8_length, f"{location}.array_id")
        if array_id in preliminary_ids:
            state.stop("DUPLICATE_ARRAY_ID", "array_id values must be unique", f"{location}.array_id", actual=array_id)
        if preliminary_previous is not None and encoded_id <= preliminary_previous:
            state.stop("UNSORTED_ARRAY_DIRECTORY", "entries must be sorted by UTF-8 array_id", location, actual=array_id)
        preliminary_ids.add(array_id)
        preliminary_previous = encoded_id
    metas: list[_ArrayMeta] = []
    seen_ids: set[str] = set()
    previous_id: bytes | None = None
    expected_data_offset = 0
    for position, raw_entry in enumerate(raw_entries):
        location = f"arrays.directory.entries[{position}]"
        if not isinstance(raw_entry, dict) or frozenset(raw_entry) != _ARRAY_ENTRY_FIELDS:
            state.stop("INVALID_ARRAY_DIRECTORY_SCHEMA", "array entry field set is not exact", location)
        array_id = raw_entry["array_id"]
        if not isinstance(array_id, str) or not array_id or "\0" in array_id:
            state.stop("INVALID_ARRAY_ID", "array_id must be a nonempty NUL-free string", f"{location}.array_id")
        try:
            encoded_id = array_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            state.stop("INVALID_ARRAY_ID", "array_id is not valid UTF-8", f"{location}.array_id", actual=str(exc))
        state.check_limit("ARRAY_ID_TOO_LONG", len(encoded_id), limits.max_array_id_utf8_length, f"{location}.array_id")
        if array_id in seen_ids:
            state.stop("DUPLICATE_ARRAY_ID", "array_id values must be unique", f"{location}.array_id", actual=array_id)
        if previous_id is not None and encoded_id <= previous_id:
            state.stop("UNSORTED_ARRAY_DIRECTORY", "entries must be sorted by UTF-8 array_id", location, actual=array_id)
        seen_ids.add(array_id)
        previous_id = encoded_id
        array_type = raw_entry["array_type"]
        if array_type not in {"mz", "intensity", "time"}:
            state.stop("UNSUPPORTED_ARRAY_TYPE", "unsupported array_type", f"{location}.array_type", actual=array_type)
        if raw_entry["dtype"] != "float64":
            state.stop("UNSUPPORTED_ARRAY_DTYPE", "unsupported dtype", f"{location}.dtype", actual=raw_entry["dtype"])
        if raw_entry["encoding"] != "raw-le":
            state.stop("UNSUPPORTED_ARRAY_ENCODING", "unsupported encoding", f"{location}.encoding", actual=raw_entry["encoding"])
        checksum = raw_entry["checksum"]
        if not isinstance(checksum, str) or _SHA256_RE.fullmatch(checksum) is None:
            state.stop("INVALID_ARRAY_CHECKSUM_FORMAT", "checksum must be lowercase SHA-256", f"{location}.checksum")
        value_count = raw_entry["value_count"]
        data_offset = raw_entry["data_offset"]
        byte_length = raw_entry["byte_length"]
        if not _nonnegative_int(value_count) or not _nonnegative_int(data_offset) or not _nonnegative_int(byte_length):
            state.stop("INVALID_ARRAY_DIRECTORY_SCHEMA", "array lengths and offsets must be nonnegative integers", location)
        state.check_limit("ARRAY_VALUE_COUNT_TOO_LARGE", value_count, limits.max_array_value_count, f"{location}.value_count")
        if byte_length != value_count * 8:
            state.stop("ARRAY_BYTE_LENGTH_MISMATCH", "byte_length must equal value_count * 8", f"{location}.byte_length", actual=byte_length, limit=value_count * 8)
        if data_offset > expected_data_offset:
            state.stop("ARRAY_PAYLOAD_GAP", "array payloads must be contiguous", f"{location}.data_offset", actual=data_offset, limit=expected_data_offset)
        if data_offset < expected_data_offset:
            state.stop("OVERLAPPING_ARRAY_PAYLOAD", "array payloads overlap", f"{location}.data_offset", actual=data_offset, limit=expected_data_offset)
        if data_offset + byte_length > payload_length:
            state.stop("ARRAY_PAYLOAD_OUT_OF_BOUNDS", "array payload is outside payload bounds", location, actual=data_offset + byte_length, limit=payload_length)
        metas.append(_ArrayMeta(array_id, array_type, value_count, data_offset, byte_length, checksum))
        expected_data_offset = data_offset + byte_length
    if expected_data_offset != payload_length:
        state.stop("INVALID_ARRAY_PAYLOAD_LENGTH", "directory entries do not cover the complete payload", "arrays.payload_length", actual=expected_data_offset, limit=payload_length)

    state.metrics["arrays_payload_length"] = payload_length
    state.metrics["entry_count"] = entry_count
    state.metrics["numeric_value_count"] = sum(item.value_count for item in metas)
    whole_hash = hashlib.sha256()
    whole_hash.update(raw_header)
    whole_hash.update(raw_directory)
    whole_hash.update(padding)
    per_array_issues: list[ValidationIssue] = []
    numeric_issues: list[ValidationIssue] = []
    mapping: mmap.mmap | None = None
    try:
        mapping = mmap.mmap(state.stream.fileno(), 0, access=mmap.ACCESS_READ)
    except (AttributeError, OSError, ValueError):
        mapping = None
    if mapping is not None:
        state.metrics["payload_access_backend"] = "mmap"
        payload_absolute = entry.offset + payload_offset
        try:
            for meta in metas:
                start = payload_absolute + meta.data_offset
                view = memoryview(mapping)[start : start + meta.byte_length]
                whole_hash.update(view)
                actual_checksum = hashlib.sha256(view).hexdigest()
                _check_numeric_values(
                    meta,
                    view,
                    0,
                    numeric_issues,
                    state,
                )
                state.metrics["payload_bytes_read"] = int(
                    state.metrics["payload_bytes_read"]
                ) + len(view)
                state.metrics["mmap_bytes_visited"] = int(
                    state.metrics["mmap_bytes_visited"]
                ) + len(view)
                view.release()
                if actual_checksum != meta.checksum:
                    per_array_issues.append(_issue("ARRAY_CHECKSUM_MISMATCH", "array payload checksum does not match its entry", f"arrays[{meta.array_id}].checksum", actual=actual_checksum))
        finally:
            mapping.close()
    else:
        for meta in metas:
            per_hash = hashlib.sha256()
            remaining = meta.byte_length
            value_index = 0
            recorded_numeric_issue = False
            while remaining:
                requested = min(remaining, limits.chunk_size)
                chunk = state.read_exact(requested, "ARRAY_PAYLOAD_OUT_OF_BOUNDS", f"arrays[{meta.array_id!r}].payload")
                state.metrics["payload_bytes_read"] = int(state.metrics["payload_bytes_read"]) + len(chunk)
                state.metrics["max_single_payload_read"] = max(int(state.metrics["max_single_payload_read"]), len(chunk))
                whole_hash.update(chunk)
                per_hash.update(chunk)
                if not recorded_numeric_issue:
                    recorded_numeric_issue = _check_numeric_values(
                        meta,
                        chunk,
                        value_index,
                        numeric_issues,
                        state,
                    )
                value_index += len(chunk) // 8
                remaining -= len(chunk)
            actual_checksum = per_hash.hexdigest()
            if actual_checksum != meta.checksum:
                per_array_issues.append(_issue("ARRAY_CHECKSUM_MISMATCH", "array payload checksum does not match its entry", f"arrays[{meta.array_id}].checksum", actual=actual_checksum))
    state.metrics["payload_scan_count"] = 1
    actual_whole_checksum = whole_hash.hexdigest()
    if actual_whole_checksum != entry.checksum:
        state.add("BLOCK_CHECKSUM_MISMATCH", "top-level arrays checksum does not match raw stored bytes", "arrays", actual=actual_whole_checksum)
    state.issues.extend(per_array_issues)
    state.issues.extend(numeric_issues)
    state.checked_blocks += 1
    return tuple(metas)


def _check_numeric_values(
    meta: _ArrayMeta,
    raw: bytes | memoryview,
    value_index: int,
    issues: list[ValidationIssue],
    state: _ValidationState,
) -> bool:
    values = np.frombuffer(raw, dtype="<f8")
    state.metrics["numeric_chunk_count"] = int(
        state.metrics["numeric_chunk_count"]
    ) + 1
    invalid = ~np.isfinite(values)
    if meta.array_type in {"mz", "time"}:
        invalid |= values < 0
    positions = np.flatnonzero(invalid)
    if not positions.size:
        del values
        return False
    position = int(positions[0])
    number = float(values[position])
    location = f"arrays[{meta.array_id}].values[{value_index + position}]"
    if not math.isfinite(number):
        issues.append(_issue("NONFINITE_ARRAY_VALUE", "array value must be finite", location, actual=number))
    elif meta.array_type == "mz":
        issues.append(_issue("NEGATIVE_MZ_VALUE", "m/z array value must not be negative", location, actual=number))
    else:
        issues.append(_issue("NEGATIVE_TIME_VALUE", "time array value must not be negative", location, actual=number))
    del values
    return True


class ZpV2Validator:
    def __init__(self, limits: ZpV2ValidationLimits | None = None) -> None:
        self.limits = limits or DEFAULT_V2_VALIDATION_LIMITS
        if not isinstance(self.limits, ZpV2ValidationLimits):
            raise TypeError("limits must be a ZpV2ValidationLimits instance")
        self.last_metrics: dict[str, int | bool | str] = {}
        self.last_extensions: list[dict[str, object]] | None = None

    def validate_stream(
        self,
        path: Path,
        stream: BinaryIO,
        *,
        file_size: int,
        header: tuple[bytes, int, int, int, int, int],
        initial_issues: list[ValidationIssue],
    ) -> ValidationResult:
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        version = header[1]
        try:
            state = _ValidationState(path, stream, version, initial_issues, self.limits)
        except _StopValidation:
            result = ValidationResult(False, initial_issues, 0, path, version)
            self.last_metrics = {}
            return result
        try:
            magic, version, endianness, flags, _created_at, directory_offset = header
            if magic != ZP_MAGIC:
                state.stop("INVALID_MAGIC", f"Expected {ZP_MAGIC!r}, got {magic!r}", "header.magic")
            if endianness != ZP_ENDIANNESS_LITTLE:
                state.stop("UNSUPPORTED_ENDIANNESS", "unsupported top-level endianness", "header.endianness", actual=endianness)
            if version != ZP_VERSION_V2:
                state.stop("UNSUPPORTED_VERSION", "v2 validator received a different version", "header.version", actual=version)
            if flags != 0:
                state.stop("UNSUPPORTED_TOP_LEVEL_FLAGS", "v2 Header flags must be zero", "header.flags", actual=flags)
            entries = _read_top_directory(state, file_size=file_size, directory_offset=directory_offset)
            blocks = _read_json_blocks(state, entries)
            raw_extensions = blocks.get("extensions")
            self.last_extensions = (
                raw_extensions
                if isinstance(raw_extensions, list)
                and all(isinstance(item, dict) for item in raw_extensions)
                else None
            )
            arrays_entry = next(item for item in entries if item.block_name == "arrays")
            arrays = _validate_arrays_region(state, arrays_entry)
            _validate_relationships(blocks, arrays, state)
        except _StopValidation:
            pass
        except OSError as exc:
            state.add("FILE_READ_ERROR", str(exc), "file")
        except MemoryError:
            state.add("VALIDATION_WORK_MEMORY_EXCEEDED", "validation allocation failed", "validation")
        except (KeyError, TypeError, ValueError, OverflowError, struct.error) as exc:
            state.add("INVALID_V2_STRUCTURE", "malformed v2 structure could not be interpreted", "validation", actual=str(exc))
        result = state.finish()
        state.metrics["wall_seconds"] = time.perf_counter() - wall_started
        state.metrics["cpu_seconds"] = time.process_time() - cpu_started
        self.last_metrics = dict(state.metrics)
        result.metrics.update(self.last_metrics)
        return result
