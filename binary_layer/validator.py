from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .constants import (
    DIRECTORY_LENGTH_STRUCT,
    HEADER_SIZE,
    HEADER_STRUCT,
    KNOWN_ZP_VERSIONS,
    REQUIRED_BLOCK_NAMES,
    SUPPORTED_ZP_VALIDATE_VERSIONS,
    SUPPORTED_ARRAY_TYPES,
    SUPPORTED_DTYPES,
    SUPPORTED_ENCODINGS,
    ZP_ENDIANNESS_LITTLE,
    ZP_EXTENSION,
    ZP_MAGIC,
    ZP_VERSION,
)
from .exceptions import MzmlSchemaError
from .models import BlockDirectoryEntry, ValidationIssue, ValidationResult
from .mzml_schema import (
    MZML_AUXILIARY_ARRAYS_EXTENSION_TYPE,
    MZML_EXTENSION_SCHEMA_VERSION,
    MzmlAuxiliaryArraysV1,
    OwnerKind,
)
from .serialization import parse_json_bytes
from .v2_validator import DEFAULT_V2_VALIDATION_LIMITS, ZpV2ValidationLimits, ZpV2Validator

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ZpValidator:
    v2_limits: ZpV2ValidationLimits = DEFAULT_V2_VALIDATION_LIMITS

    def validate(self, file_path: str | Path) -> ValidationResult:
        path = Path(file_path)
        issues: list[ValidationIssue] = []
        checked_blocks = 0
        version: int | None = None

        def add(code: str, message: str, block_name: str | None = None) -> None:
            issues.append(ValidationIssue(code, message, "error", block_name))

        if path.suffix != ZP_EXTENSION:
            add("INVALID_EXTENSION", f"File extension must be exactly {ZP_EXTENSION}")
        if not path.exists():
            add("FILE_NOT_FOUND", f"File does not exist: {path}")
            return self._result(path, version, issues, checked_blocks)
        if not path.is_file():
            add("NOT_REGULAR_FILE", f"Path is not a regular file: {path}")
            return self._result(path, version, issues, checked_blocks)

        try:
            file_size = path.stat().st_size
            if file_size < HEADER_SIZE:
                add("FILE_TOO_SMALL", f"File is smaller than {HEADER_SIZE}-byte header")
                return self._result(path, version, issues, checked_blocks)
            with path.open("rb") as stream:
                header_raw = stream.read(HEADER_SIZE)
                magic, version, endianness, _flags, _created_at, directory_offset = HEADER_STRUCT.unpack(header_raw)
                if magic != ZP_MAGIC:
                    add("INVALID_MAGIC", f"Expected {ZP_MAGIC!r}, got {magic!r}")
                if endianness != ZP_ENDIANNESS_LITTLE:
                    add("UNSUPPORTED_ENDIANNESS", f"Unsupported endianness: {endianness}")
                if version == 2:
                    if magic != ZP_MAGIC or endianness != ZP_ENDIANNESS_LITTLE:
                        return self._result(path, version, issues, checked_blocks)
                    validator = ZpV2Validator(self.v2_limits)
                    result = validator.validate_stream(
                        path,
                        stream,
                        file_size=file_size,
                        header=(magic, version, endianness, _flags, _created_at, directory_offset),
                        initial_issues=issues,
                    )
                    self._last_v2_metrics = validator.last_metrics
                    return result
                if version not in SUPPORTED_ZP_VALIDATE_VERSIONS:
                    if magic != ZP_MAGIC or endianness != ZP_ENDIANNESS_LITTLE:
                        return self._result(path, version, issues, checked_blocks)
                    if version in KNOWN_ZP_VERSIONS:
                        add(
                            "ZP_V2_VALIDATION_NOT_IMPLEMENTED",
                            f"ZP version {version} validation is not implemented",
                            "header.version",
                        )
                    else:
                        add("UNSUPPORTED_VERSION", f"Unsupported version: {version}")
                    return self._result(path, version, issues, checked_blocks)
                if directory_offset < HEADER_SIZE or directory_offset + DIRECTORY_LENGTH_STRUCT.size > file_size:
                    add("INVALID_DIRECTORY_OFFSET", f"Directory offset is out of bounds: {directory_offset}")
                    return self._result(path, version, issues, checked_blocks)

                stream.seek(directory_offset)
                length_raw = stream.read(DIRECTORY_LENGTH_STRUCT.size)
                if len(length_raw) != DIRECTORY_LENGTH_STRUCT.size:
                    add("TRUNCATED_DIRECTORY_LENGTH", "Directory length is not readable")
                    return self._result(path, version, issues, checked_blocks)
                directory_length = DIRECTORY_LENGTH_STRUCT.unpack(length_raw)[0]
                directory_end = directory_offset + DIRECTORY_LENGTH_STRUCT.size + directory_length
                if directory_end > file_size:
                    add("INVALID_DIRECTORY_LENGTH", "Declared directory length exceeds the remaining file bytes")
                    return self._result(path, version, issues, checked_blocks)
                if directory_end < file_size:
                    add("TRAILING_DATA", "Directory does not end at EOF")
                directory_raw = stream.read(directory_length)
                if len(directory_raw) != directory_length:
                    add("TRUNCATED_DIRECTORY", "Directory JSON is truncated")
                    return self._result(path, version, issues, checked_blocks)
        except OSError as exc:
            add("FILE_READ_ERROR", str(exc))
            return self._result(path, version, issues, checked_blocks)

        try:
            directory_data = parse_json_bytes(directory_raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            add("INVALID_DIRECTORY_JSON", str(exc))
            return self._result(path, version, issues, checked_blocks)
        if not isinstance(directory_data, list):
            add("INVALID_DIRECTORY_SCHEMA", "Directory JSON must be a list")
            return self._result(path, version, issues, checked_blocks)

        entries: list[BlockDirectoryEntry] = []
        for position, raw_entry in enumerate(directory_data):
            if not isinstance(raw_entry, dict):
                add("INVALID_DIRECTORY_ENTRY", f"Entry {position} must be an object")
                continue
            required = ("block_name", "offset", "length", "encoding", "checksum")
            if any(key not in raw_entry for key in required):
                add("INVALID_DIRECTORY_ENTRY", f"Entry {position} is missing required fields")
                continue
            if (
                not isinstance(raw_entry["block_name"], str)
                or not self._is_int(raw_entry["offset"])
                or not self._is_int(raw_entry["length"])
                or not isinstance(raw_entry["encoding"], str)
                or not isinstance(raw_entry["checksum"], str)
            ):
                add("INVALID_DIRECTORY_ENTRY", f"Entry {position} has invalid field types")
                continue
            entries.append(BlockDirectoryEntry(**{key: raw_entry[key] for key in required}))

        names = [entry.block_name for entry in entries]
        for name in sorted(set(names)):
            if names.count(name) > 1:
                add("DUPLICATE_BLOCK_NAME", f"Block appears more than once: {name}", name)
        for name in sorted(REQUIRED_BLOCK_NAMES - set(names)):
            add("MISSING_REQUIRED_BLOCK", f"Required block is missing: {name}", name)

        safe_entries: list[BlockDirectoryEntry] = []
        for entry in entries:
            if entry.offset < HEADER_SIZE:
                add("BLOCK_OVERLAPS_HEADER", "Block starts inside the header", entry.block_name)
            if entry.length < 0 or entry.offset < 0:
                add("NEGATIVE_BLOCK_RANGE", "Block offset and length must be non-negative", entry.block_name)
            elif entry.offset + entry.length > directory_offset:
                add("BLOCK_OUT_OF_BOUNDS", "Block extends into or beyond the directory", entry.block_name)
            else:
                safe_entries.append(entry)
            if entry.encoding not in SUPPORTED_ENCODINGS:
                add("UNSUPPORTED_ENCODING", f"Unsupported encoding: {entry.encoding}", entry.block_name)
            if not _SHA256_RE.fullmatch(entry.checksum):
                add("INVALID_CHECKSUM_FORMAT", "Checksum must be lowercase SHA-256 hex", entry.block_name)

        ranges = sorted((entry.offset, entry.offset + entry.length, entry.block_name) for entry in safe_entries)
        for previous, current in zip(ranges, ranges[1:]):
            if current[0] < previous[1]:
                add("OVERLAPPING_BLOCKS", f"Blocks {previous[2]} and {current[2]} overlap", current[2])

        parsed: dict[str, Any] = {}
        try:
            with path.open("rb") as stream:
                for entry in safe_entries:
                    stream.seek(entry.offset)
                    payload = stream.read(entry.length)
                    if len(payload) != entry.length:
                        add("TRUNCATED_BLOCK", "Block bytes are truncated", entry.block_name)
                        continue
                    checked_blocks += 1
                    if hashlib.sha256(payload).hexdigest() != entry.checksum:
                        add("CHECKSUM_MISMATCH", "Block checksum does not match raw stored bytes", entry.block_name)
                    if entry.encoding == "json" and names.count(entry.block_name) == 1:
                        try:
                            parsed[entry.block_name] = parse_json_bytes(payload)
                        except (UnicodeError, json.JSONDecodeError) as exc:
                            add("INVALID_BLOCK_JSON", str(exc), entry.block_name)
        except OSError as exc:
            add("FILE_READ_ERROR", str(exc))
            return self._result(path, version, issues, checked_blocks)

        self._validate_schema(parsed, add)
        self._validate_references(parsed, add)
        return self._result(path, version, issues, checked_blocks)

    @staticmethod
    def _result(path: Path, version: int | None, issues: list[ValidationIssue], checked: int) -> ValidationResult:
        return ValidationResult(not any(item.severity == "error" for item in issues), issues, checked, path, version)

    @staticmethod
    def _is_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    @classmethod
    def _validate_schema(cls, blocks: dict[str, Any], add: Any) -> None:
        singleton_fields = {
            "global_meta": {
                "format_version": int, "source_type": str, "source_file_name": str,
                "source_file_hash": str, "run_count": int, "spectrum_count": int,
                "chromatogram_count": int, "array_count": int, "created_at": str,
                "generator_name": str, "generator_version": str, "notes": list,
            },
            "string_pool": {"strings": list},
            "indexes": {"scan_index": list, "rt_index": list, "spectrum_id_index": list},
        }
        list_fields = {
            "core_runs": {
                "run_id": str, "source_file": str, "run_name": str, "spectrum_count": int,
                "chromatogram_count": int, "start_rt": (int, float), "end_rt": (int, float),
            },
            "core_spectra": {
                "spectrum_id": str, "run_id": str, "ms_level": int, "scan_number": int,
                "native_id": str, "rt": (int, float), "precursor_id": (str, type(None)),
                "mz_array_id": str, "intensity_array_id": str,
            },
            "core_precursors": {
                "precursor_id": str, "spectrum_id": str, "precursor_mz": (int, float),
                "charge": int, "intensity": (int, float),
            },
            "core_chromatograms": {
                "chromatogram_id": str, "run_id": str, "chromatogram_type": str,
                "time_array_id": str, "intensity_array_id": str, "native_id": str,
            },
            "arrays": {"array_id": str, "array_type": str, "dtype": str, "values": list},
            "extensions": {"extension_type": str, "extension_version": str, "payload": dict},
        }
        for block_name, fields in singleton_fields.items():
            value = blocks.get(block_name)
            if value is None:
                continue
            if not isinstance(value, dict):
                add("INVALID_BLOCK_SCHEMA", "Top-level value must be an object", block_name)
                continue
            cls._check_fields(value, fields, block_name, add)
        for block_name, fields in list_fields.items():
            value = blocks.get(block_name)
            if value is None:
                continue
            if not isinstance(value, list):
                add("INVALID_BLOCK_SCHEMA", "Top-level value must be a list", block_name)
                continue
            for position, item in enumerate(value):
                if not isinstance(item, dict):
                    add("INVALID_RECORD_SCHEMA", f"Record {position} must be an object", block_name)
                    continue
                cls._check_fields(item, fields, block_name, add, position)

        spectra = blocks.get("core_spectra")
        if isinstance(spectra, list):
            for item in spectra:
                if not isinstance(item, dict):
                    continue
                forbidden = {"mz_values", "intensity_values", "mz_array", "intensity_array"} & set(item)
                if forbidden:
                    add("EMBEDDED_ARRAY_VALUES", f"Spectrum embeds prohibited array fields: {sorted(forbidden)}", "core_spectra")
                ms_level, scan, rt = item.get("ms_level"), item.get("scan_number"), item.get("rt")
                if not cls._is_int(ms_level) or ms_level <= 0:
                    add("INVALID_MS_LEVEL", "ms_level must be a positive integer", "core_spectra")
                if not cls._is_int(scan) or scan < 0:
                    add("INVALID_SCAN_NUMBER", "scan_number must be a non-negative integer", "core_spectra")
                if isinstance(rt, bool) or not isinstance(rt, (int, float)) or not math.isfinite(rt) or rt < 0:
                    add("INVALID_RT", "rt must be a finite non-negative number", "core_spectra")

        arrays = blocks.get("arrays")
        if isinstance(arrays, list):
            for item in arrays:
                if not isinstance(item, dict):
                    continue
                array_type, dtype, values = item.get("array_type"), item.get("dtype"), item.get("values")
                if array_type not in SUPPORTED_ARRAY_TYPES:
                    add("UNSUPPORTED_ARRAY_TYPE", f"Unsupported array_type: {array_type}", "arrays")
                if dtype not in SUPPORTED_DTYPES:
                    add("UNSUPPORTED_DTYPE", f"Unsupported dtype: {dtype}", "arrays")
                if isinstance(values, list):
                    for value in values:
                        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                            add("INVALID_ARRAY_VALUE", "Array values must be finite numbers", "arrays")
                            break
                    if array_type == "mz" and any(isinstance(value, (int, float)) and value < 0 for value in values):
                        add("NEGATIVE_MZ", "m/z values must not be negative", "arrays")

    @classmethod
    def _check_fields(cls, item: dict[str, Any], fields: dict[str, Any], block: str, add: Any, position: int | None = None) -> None:
        label = "record" if position is None else f"record {position}"
        for name, expected in fields.items():
            if name not in item:
                add("MISSING_FIELD", f"{label} is missing field {name}", block)
                continue
            value = item[name]
            if expected is int:
                valid = cls._is_int(value)
            elif expected == (int, float):
                valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            else:
                valid = isinstance(value, expected)
            if not valid:
                add("INVALID_FIELD_TYPE", f"Field {name} has an invalid type", block)

    @classmethod
    def _validate_references(cls, blocks: dict[str, Any], add: Any) -> None:
        runs = cls._records(blocks, "core_runs")
        spectra = cls._records(blocks, "core_spectra")
        precursors = cls._records(blocks, "core_precursors")
        arrays = cls._records(blocks, "arrays")
        chromatograms = cls._records(blocks, "core_chromatograms")

        run_ids = cls._unique_ids(runs, "run_id", "core_runs", add)
        spectrum_ids = cls._unique_ids(spectra, "spectrum_id", "core_spectra", add)
        precursor_ids = cls._unique_ids(precursors, "precursor_id", "core_precursors", add)
        array_ids = cls._unique_ids(arrays, "array_id", "arrays", add)
        chromatogram_ids = {
            item["chromatogram_id"]
            for item in chromatograms
            if isinstance(item.get("chromatogram_id"), str)
        }
        array_map = {item.get("array_id"): item for item in arrays if isinstance(item.get("array_id"), str)}

        for spectrum in spectra:
            spectrum_id = spectrum.get("spectrum_id")
            if spectrum.get("run_id") not in run_ids:
                add("INVALID_REFERENCE", f"Spectrum {spectrum_id} references a missing run", "core_spectra")
            mz_id, intensity_id = spectrum.get("mz_array_id"), spectrum.get("intensity_array_id")
            if mz_id not in array_ids:
                add("INVALID_REFERENCE", f"Spectrum {spectrum_id} references missing m/z array {mz_id}", "core_spectra")
            elif array_map[mz_id].get("array_type") != "mz":
                add("ARRAY_TYPE_MISMATCH", f"Spectrum {spectrum_id} m/z reference is not an m/z array", "core_spectra")
            if intensity_id not in array_ids:
                add("INVALID_REFERENCE", f"Spectrum {spectrum_id} references missing intensity array {intensity_id}", "core_spectra")
            elif array_map[intensity_id].get("array_type") != "intensity":
                add("ARRAY_TYPE_MISMATCH", f"Spectrum {spectrum_id} intensity reference is not an intensity array", "core_spectra")
            if mz_id in array_map and intensity_id in array_map:
                mz_values, intensity_values = array_map[mz_id].get("values"), array_map[intensity_id].get("values")
                if isinstance(mz_values, list) and isinstance(intensity_values, list) and len(mz_values) != len(intensity_values):
                    add("ARRAY_LENGTH_MISMATCH", f"Spectrum {spectrum_id} arrays have different lengths", "core_spectra")
            precursor_id = spectrum.get("precursor_id")
            if precursor_id is not None and precursor_id not in precursor_ids:
                add("INVALID_REFERENCE", f"Spectrum {spectrum_id} references missing precursor {precursor_id}", "core_spectra")

        for precursor in precursors:
            if precursor.get("spectrum_id") not in spectrum_ids:
                add("INVALID_REFERENCE", "Precursor references a missing spectrum", "core_precursors")
        for chromatogram in chromatograms:
            if chromatogram.get("run_id") not in run_ids:
                add("INVALID_REFERENCE", "Chromatogram references a missing run", "core_chromatograms")
            for field, expected in (("time_array_id", "time"), ("intensity_array_id", "intensity")):
                array_id = chromatogram.get(field)
                if array_id not in array_ids:
                    add("INVALID_REFERENCE", f"Chromatogram references missing array {array_id}", "core_chromatograms")
                elif array_map[array_id].get("array_type") != expected:
                    add("ARRAY_TYPE_MISMATCH", f"Chromatogram {field} has the wrong array type", "core_chromatograms")
            time_array = array_map.get(chromatogram.get("time_array_id"))
            intensity_array = array_map.get(chromatogram.get("intensity_array_id"))
            if time_array is not None and intensity_array is not None:
                time_values = time_array.get("values")
                intensity_values = intensity_array.get("values")
                if isinstance(time_values, list) and isinstance(intensity_values, list):
                    if len(time_values) != len(intensity_values):
                        add("ARRAY_LENGTH_MISMATCH", "Chromatogram arrays have different lengths", "core_chromatograms")
                    if any(isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0 for value in time_values):
                        add("INVALID_TIME_ARRAY_VALUE", "Chromatogram time values must not be negative", "core_chromatograms")

        indexes = blocks.get("indexes")
        if isinstance(indexes, dict):
            for key in ("scan_index", "rt_index", "spectrum_id_index"):
                records = indexes.get(key)
                if not isinstance(records, list):
                    continue
                for record in records:
                    if not isinstance(record, dict) or record.get("spectrum_id") not in spectrum_ids:
                        add("INVALID_REFERENCE", f"{key} references a missing spectrum", "indexes")
            position_records = indexes.get("spectrum_id_index")
            if isinstance(position_records, list):
                for record in position_records:
                    if not isinstance(record, dict):
                        continue
                    position, spectrum_id = record.get("position"), record.get("spectrum_id")
                    if not cls._is_int(position) or position < 0 or position >= len(spectra):
                        add("INVALID_INDEX_POSITION", f"Invalid spectrum position: {position}", "indexes")
                    elif spectra[position].get("spectrum_id") != spectrum_id:
                        add("INDEX_POSITION_MISMATCH", f"Position {position} does not match {spectrum_id}", "indexes")

        cls._validate_extension_references(
            blocks.get("extensions"),
            spectrum_ids=spectrum_ids,
            chromatogram_ids=chromatogram_ids,
            add=add,
        )

    @staticmethod
    def _validate_extension_references(
        value: object,
        *,
        spectrum_ids: set[str],
        chromatogram_ids: set[str],
        add: Any,
    ) -> None:
        if not isinstance(value, list):
            return
        ids_by_owner_kind = {
            OwnerKind.SPECTRUM: spectrum_ids,
            OwnerKind.CHROMATOGRAM: chromatogram_ids,
        }
        for extension_position, record in enumerate(value):
            if not isinstance(record, dict) or record.get("extension_type") != MZML_AUXILIARY_ARRAYS_EXTENSION_TYPE:
                continue
            extension_location = f"extensions[{extension_position}]"
            if record.get("extension_version") != str(MZML_EXTENSION_SCHEMA_VERSION):
                add(
                    "INVALID_EXTENSION_SCHEMA",
                    f"Unsupported mzML extension version: {record.get('extension_version')!r}",
                    extension_location,
                )
                continue
            try:
                auxiliary = MzmlAuxiliaryArraysV1.from_payload(record.get("payload"))
            except MzmlSchemaError as exc:
                add("INVALID_EXTENSION_SCHEMA", str(exc), extension_location)
                continue
            for array_position, item in enumerate(auxiliary.arrays):
                if item.owner_id in ids_by_owner_kind[item.owner_kind]:
                    continue
                owner_location = f"{extension_location}.payload.arrays[{array_position}].owner_id"
                add(
                    "INVALID_REFERENCE",
                    (
                        "mzml_auxiliary_arrays "
                        f"owner_kind={item.owner_kind.value} owner_id={item.owner_id!r} "
                        "references a missing owner"
                    ),
                    owner_location,
                )

    @staticmethod
    def _records(blocks: dict[str, Any], name: str) -> list[dict[str, Any]]:
        value = blocks.get(name)
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _unique_ids(records: list[dict[str, Any]], field: str, block: str, add: Any) -> set[str]:
        values = [item.get(field) for item in records if isinstance(item.get(field), str)]
        for value in set(values):
            if values.count(value) > 1:
                add("DUPLICATE_ID", f"Duplicate {field}: {value}", block)
        return set(values)
