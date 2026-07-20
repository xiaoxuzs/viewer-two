"""Independent standard-library inspector for complete ZP v1/v2 files."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from pathlib import Path
from typing import Any


TOP_HEADER = struct.Struct("<4sHBBQQ")
DIRECTORY_LENGTH = struct.Struct("<Q")
ARRAYS_HEADER = struct.Struct("<8sHBBIQQQQ16s")
BLOCK_NAMES = (
    "global_meta",
    "string_pool",
    "core_runs",
    "core_spectra",
    "core_precursors",
    "core_chromatograms",
    "arrays",
    "indexes",
    "extensions",
)
TOP_ENTRY_FIELDS = {"block_name", "offset", "length", "encoding", "checksum"}
ARRAY_ENTRY_FIELDS = {
    "array_id",
    "array_type",
    "dtype",
    "encoding",
    "value_count",
    "data_offset",
    "byte_length",
    "checksum",
}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class InspectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class _DuplicateKey(ValueError):
    pass


def _fail(code: str, message: str) -> None:
    raise InspectionError(code, message)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _parse_canonical(payload: bytes, location: str) -> object:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateKey, ValueError) as exc:
        _fail("INVALID_CANONICAL_JSON", f"{location}: {exc}")
    try:
        encoded = _canonical(value)
    except (TypeError, ValueError, UnicodeError) as exc:
        _fail("INVALID_CANONICAL_JSON", f"{location}: {exc}")
    if encoded != payload:
        _fail("NONCANONICAL_JSON", location)
    return value


def _logical_hash(values: list[float]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(struct.pack("<d", value))
    return digest.hexdigest()


def _normalize_array(record: dict[str, object]) -> dict[str, object]:
    array_id = record.get("array_id")
    array_type = record.get("array_type")
    dtype = record.get("dtype")
    values = record.get("values")
    if not isinstance(array_id, str) or not array_id:
        _fail("INVALID_ARRAY", "array_id must be a nonempty string")
    if array_type not in {"mz", "intensity", "time"}:
        _fail("INVALID_ARRAY", f"unsupported array_type for {array_id}")
    if dtype != "float64" or not isinstance(values, list):
        _fail("INVALID_ARRAY", f"invalid dtype or values for {array_id}")
    normalized: list[float] = []
    for position, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail("INVALID_ARRAY_VALUE", f"{array_id}[{position}]")
        number = float(value)
        if not math.isfinite(number):
            _fail("INVALID_ARRAY_VALUE", f"{array_id}[{position}] is nonfinite")
        if array_type in {"mz", "time"} and number < 0:
            _fail("INVALID_ARRAY_VALUE", f"{array_id}[{position}] is negative")
        normalized.append(number)
    return {
        "array_id": array_id,
        "array_type": array_type,
        "dtype": "float64",
        "value_count": len(normalized),
        "logical_sha256": _logical_hash(normalized),
        "values": normalized,
    }


def _inspect_v2_arrays(payload: bytes) -> list[dict[str, object]]:
    if len(payload) < ARRAYS_HEADER.size:
        _fail("INVALID_ARRAYS_HEADER", "arrays block is shorter than 64 bytes")
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
    ) = ARRAYS_HEADER.unpack_from(payload)
    if magic != b"ZPARRV2\0" or schema_version != 2 or endianness != 1 or flags != 0:
        _fail("INVALID_ARRAYS_HEADER", "v2 arrays Header identity is invalid")
    if reserved != b"\0" * 16 or directory_offset != ARRAYS_HEADER.size:
        _fail("INVALID_ARRAYS_HEADER", "reserved bytes or directory_offset are invalid")
    directory_end = directory_offset + directory_length
    expected_payload_offset = (directory_end + 7) & ~7
    if directory_end > len(payload) or payload_offset != expected_payload_offset or payload_offset % 8:
        _fail("INVALID_ARRAYS_LAYOUT", "v2 arrays directory/payload offsets are invalid")
    if payload_offset + payload_length != len(payload):
        _fail("INVALID_ARRAYS_LAYOUT", "v2 arrays payload does not end at block boundary")
    if any(payload[directory_end:payload_offset]):
        _fail("NONZERO_ARRAY_PADDING", "v2 arrays padding is not zero")
    directory = _parse_canonical(payload[directory_offset:directory_end], "arrays.directory")
    if not isinstance(directory, dict) or set(directory) != {"entries"}:
        _fail("INVALID_ARRAY_DIRECTORY", "arrays directory must contain only entries")
    entries = directory.get("entries")
    if not isinstance(entries, list) or len(entries) != entry_count:
        _fail("INVALID_ARRAY_DIRECTORY", "entry_count does not match entries")
    identifiers: list[str] = []
    arrays: list[dict[str, object]] = []
    expected_offset = 0
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != ARRAY_ENTRY_FIELDS:
            _fail("INVALID_ARRAY_DIRECTORY", f"entry {position} field set is invalid")
        array_id = entry["array_id"]
        if not isinstance(array_id, str) or not array_id:
            _fail("INVALID_ARRAY_DIRECTORY", f"entry {position} array_id is invalid")
        if entry["array_type"] not in {"mz", "intensity", "time"}:
            _fail("INVALID_ARRAY_DIRECTORY", f"entry {position} array_type is invalid")
        if entry["dtype"] != "float64" or entry["encoding"] != "raw-le":
            _fail("INVALID_ARRAY_DIRECTORY", f"entry {position} dtype/encoding is invalid")
        for field in ("value_count", "data_offset", "byte_length"):
            if isinstance(entry[field], bool) or not isinstance(entry[field], int) or entry[field] < 0:
                _fail("INVALID_ARRAY_DIRECTORY", f"entry {position} {field} is invalid")
        if not isinstance(entry["checksum"], str) or SHA256_RE.fullmatch(entry["checksum"]) is None:
            _fail("INVALID_ARRAY_DIRECTORY", f"entry {position} checksum is invalid")
        if entry["byte_length"] != entry["value_count"] * 8:
            _fail("INVALID_ARRAY_DIRECTORY", f"entry {position} byte length is invalid")
        if entry["data_offset"] != expected_offset:
            _fail("INVALID_ARRAYS_LAYOUT", f"entry {position} is not contiguous")
        start = payload_offset + entry["data_offset"]
        end = start + entry["byte_length"]
        if end > len(payload):
            _fail("INVALID_ARRAYS_LAYOUT", f"entry {position} is out of bounds")
        raw_values = payload[start:end]
        if hashlib.sha256(raw_values).hexdigest() != entry["checksum"]:
            _fail("ARRAY_CHECKSUM_MISMATCH", str(array_id))
        values = [item[0] for item in struct.iter_unpack("<d", raw_values)]
        normalized = _normalize_array(
            {
                "array_id": array_id,
                "array_type": entry["array_type"],
                "dtype": "float64",
                "values": values,
            }
        )
        arrays.append(normalized)
        identifiers.append(array_id)
        expected_offset += entry["byte_length"]
    if expected_offset != payload_length:
        _fail("INVALID_ARRAYS_LAYOUT", "entries do not cover payload_length")
    if identifiers != sorted(identifiers, key=lambda value: value.encode("utf-8")):
        _fail("UNSORTED_ARRAY_DIRECTORY", "array IDs are not in UTF-8 order")
    if len(identifiers) != len(set(identifiers)):
        _fail("DUPLICATE_ARRAY_ID", "array IDs are not unique")
    return arrays


def _validate_business(blocks: dict[str, object], arrays: list[dict[str, object]]) -> None:
    meta = blocks.get("global_meta")
    runs = blocks.get("core_runs")
    spectra = blocks.get("core_spectra")
    precursors = blocks.get("core_precursors")
    chromatograms = blocks.get("core_chromatograms")
    pool = blocks.get("string_pool")
    indexes = blocks.get("indexes")
    extensions = blocks.get("extensions")
    if not isinstance(meta, dict) or not all(
        isinstance(value, list) for value in (runs, spectra, precursors, chromatograms, extensions)
    ):
        _fail("INVALID_LOGICAL_BLOCK", "core block shapes are invalid")
    assert isinstance(runs, list) and isinstance(spectra, list)
    assert isinstance(precursors, list) and isinstance(chromatograms, list)
    assert isinstance(extensions, list)
    counts = {
        "run_count": len(runs),
        "spectrum_count": len(spectra),
        "chromatogram_count": len(chromatograms),
        "array_count": len(arrays),
    }
    if any(meta.get(field) != count for field, count in counts.items()):
        _fail("COUNT_MISMATCH", "GlobalMeta counts are inconsistent")
    run_map = {item.get("run_id"): item for item in runs if isinstance(item, dict)}
    spectrum_map = {item.get("spectrum_id"): item for item in spectra if isinstance(item, dict)}
    precursor_map = {item.get("precursor_id"): item for item in precursors if isinstance(item, dict)}
    chromatogram_map = {
        item.get("chromatogram_id"): item for item in chromatograms if isinstance(item, dict)
    }
    array_map = {item["array_id"]: item for item in arrays}
    for run_id, run in run_map.items():
        if run.get("spectrum_count") != sum(item.get("run_id") == run_id for item in spectra):
            _fail("COUNT_MISMATCH", f"Run {run_id} spectrum_count")
        if run.get("chromatogram_count") != sum(item.get("run_id") == run_id for item in chromatograms):
            _fail("COUNT_MISMATCH", f"Run {run_id} chromatogram_count")
    precursor_use: dict[str, int] = {}
    for spectrum_id, spectrum in spectrum_map.items():
        if spectrum.get("run_id") not in run_map:
            _fail("INVALID_REFERENCE", f"Spectrum {spectrum_id} run")
        mz = array_map.get(spectrum.get("mz_array_id"))
        intensity = array_map.get(spectrum.get("intensity_array_id"))
        if mz is None or mz["array_type"] != "mz" or intensity is None or intensity["array_type"] != "intensity":
            _fail("INVALID_REFERENCE", f"Spectrum {spectrum_id} arrays")
        if mz["value_count"] != intensity["value_count"]:
            _fail("ARRAY_LENGTH_MISMATCH", f"Spectrum {spectrum_id}")
        precursor_id = spectrum.get("precursor_id")
        if spectrum.get("ms_level") == 1 and precursor_id is not None:
            _fail("INVALID_REFERENCE", f"MS1 Spectrum {spectrum_id} precursor")
        if spectrum.get("ms_level") == 2:
            precursor = precursor_map.get(precursor_id)
            if precursor is None or precursor.get("spectrum_id") != spectrum_id:
                _fail("INVALID_REFERENCE", f"MS2 Spectrum {spectrum_id} precursor")
            if isinstance(precursor_id, str):
                precursor_use[precursor_id] = precursor_use.get(precursor_id, 0) + 1
    for precursor_id, precursor in precursor_map.items():
        spectrum = spectrum_map.get(precursor.get("spectrum_id"))
        if spectrum is None or spectrum.get("ms_level") != 2 or spectrum.get("precursor_id") != precursor_id:
            _fail("INVALID_REFERENCE", f"Precursor {precursor_id}")
        if precursor_use.get(precursor_id, 0) != 1:
            _fail("INVALID_REFERENCE", f"Precursor {precursor_id} use")
    for chromatogram_id, chromatogram in chromatogram_map.items():
        if chromatogram.get("run_id") not in run_map:
            _fail("INVALID_REFERENCE", f"Chromatogram {chromatogram_id} run")
        time_array = array_map.get(chromatogram.get("time_array_id"))
        intensity = array_map.get(chromatogram.get("intensity_array_id"))
        if time_array is None or time_array["array_type"] != "time" or intensity is None or intensity["array_type"] != "intensity":
            _fail("INVALID_REFERENCE", f"Chromatogram {chromatogram_id} arrays")
        if time_array["value_count"] != intensity["value_count"]:
            _fail("ARRAY_LENGTH_MISMATCH", f"Chromatogram {chromatogram_id}")
    if not isinstance(pool, dict) or not isinstance(pool.get("strings"), list):
        _fail("INVALID_LOGICAL_BLOCK", "string_pool")
    pooled = set(pool["strings"])
    required = [
        *(value for run in runs for value in (run.get("source_file"), run.get("run_name"))),
        *(item.get("native_id") for item in spectra),
        *(value for item in chromatograms for value in (item.get("chromatogram_type"), item.get("native_id"))),
    ]
    if any(value not in pooled for value in required):
        _fail("INVALID_REFERENCE", "string_pool")
    if not isinstance(indexes, dict):
        _fail("INVALID_LOGICAL_BLOCK", "indexes")
    for name in ("scan_index", "rt_index", "spectrum_id_index"):
        records = indexes.get(name)
        if not isinstance(records, list) or any(record.get("spectrum_id") not in spectrum_map for record in records):
            _fail("INVALID_REFERENCE", f"indexes.{name}")
    for extension in extensions:
        if not isinstance(extension, dict) or extension.get("extension_type") != "mzml_auxiliary_arrays":
            continue
        payload = extension.get("payload")
        if not isinstance(payload, dict) or set(payload) != {"arrays", "schema_version"} or payload.get("schema_version") != 1:
            _fail("INVALID_EXTENSION_SCHEMA", "mzml_auxiliary_arrays")
        records = payload.get("arrays")
        if not isinstance(records, list):
            _fail("INVALID_EXTENSION_SCHEMA", "mzml_auxiliary_arrays.arrays")
        for record in records:
            if not isinstance(record, dict) or record.get("owner_kind") != "chromatogram":
                _fail("INVALID_EXTENSION_SCHEMA", "mzml_auxiliary_arrays.owner_kind")
            if record.get("owner_id") not in chromatogram_map:
                _fail("INVALID_REFERENCE", "mzml_auxiliary_arrays.owner_id")


def inspect_full_zp(path: str | Path, *, validate_business: bool = True) -> dict[str, Any]:
    file_path = Path(path)
    raw = file_path.read_bytes()
    if len(raw) < TOP_HEADER.size:
        _fail("FILE_TOO_SMALL", str(file_path))
    magic, version, endianness, flags, created_at, directory_offset = TOP_HEADER.unpack_from(raw)
    if magic != b"ZPMS" or version not in {1, 2} or endianness != 1 or flags != 0:
        _fail("INVALID_HEADER", "top-level Header identity is invalid")
    if directory_offset < TOP_HEADER.size or directory_offset + DIRECTORY_LENGTH.size > len(raw):
        _fail("INVALID_DIRECTORY", "directory_offset is out of bounds")
    directory_length = DIRECTORY_LENGTH.unpack_from(raw, directory_offset)[0]
    directory_start = directory_offset + DIRECTORY_LENGTH.size
    directory_end = directory_start + directory_length
    if directory_end != len(raw):
        _fail("INVALID_DIRECTORY", "top directory does not end at EOF")
    directory = _parse_canonical(raw[directory_start:directory_end], "directory")
    if not isinstance(directory, list) or len(directory) != len(BLOCK_NAMES):
        _fail("INVALID_DIRECTORY", "top directory must contain nine entries")
    blocks: dict[str, object] = {}
    entries: list[dict[str, object]] = []
    previous_end = TOP_HEADER.size
    arrays: list[dict[str, object]] = []
    for position, expected_name in enumerate(BLOCK_NAMES):
        entry = directory[position]
        if not isinstance(entry, dict) or set(entry) != TOP_ENTRY_FIELDS:
            _fail("INVALID_DIRECTORY", f"entry {position} field set")
        if entry["block_name"] != expected_name:
            _fail("INVALID_DIRECTORY", f"entry {position} block order")
        if isinstance(entry["offset"], bool) or not isinstance(entry["offset"], int):
            _fail("INVALID_DIRECTORY", f"entry {position} offset")
        if isinstance(entry["length"], bool) or not isinstance(entry["length"], int):
            _fail("INVALID_DIRECTORY", f"entry {position} length")
        if entry["offset"] != previous_end or entry["length"] < 0:
            _fail("INVALID_DIRECTORY", f"entry {position} range")
        block_end = entry["offset"] + entry["length"]
        if block_end > directory_offset:
            _fail("INVALID_DIRECTORY", f"entry {position} out of bounds")
        expected_encoding = "json" if version == 1 else (
            "zp-arrays-v2" if expected_name == "arrays" else "utf-8-json"
        )
        if entry["encoding"] != expected_encoding:
            _fail("ENCODING_VERSION_MISMATCH", expected_name)
        if not isinstance(entry["checksum"], str) or SHA256_RE.fullmatch(entry["checksum"]) is None:
            _fail("INVALID_CHECKSUM", expected_name)
        payload = raw[entry["offset"]:block_end]
        if hashlib.sha256(payload).hexdigest() != entry["checksum"]:
            _fail("BLOCK_CHECKSUM_MISMATCH", expected_name)
        if expected_name == "arrays" and version == 2:
            arrays = _inspect_v2_arrays(payload)
            blocks[expected_name] = arrays
        else:
            value = _parse_canonical(payload, expected_name)
            blocks[expected_name] = value
            if expected_name == "arrays":
                if not isinstance(value, list):
                    _fail("INVALID_ARRAY", "v1 arrays must be a list")
                arrays = [_normalize_array(item) for item in value if isinstance(item, dict)]
                if len(arrays) != len(value):
                    _fail("INVALID_ARRAY", "v1 array record shape")
                blocks[expected_name] = arrays
        entries.append(dict(entry))
        previous_end = block_end
    if previous_end != directory_offset:
        _fail("INVALID_DIRECTORY", "last block does not end at directory")
    if validate_business:
        _validate_business(blocks, arrays)
    spectra = blocks["core_spectra"]
    assert isinstance(spectra, list)
    stats = {
        "run_count": len(blocks["core_runs"]),
        "spectrum_count": len(spectra),
        "ms1_count": sum(item.get("ms_level") == 1 for item in spectra),
        "ms2_count": sum(item.get("ms_level") == 2 for item in spectra),
        "precursor_count": len(blocks["core_precursors"]),
        "chromatogram_count": len(blocks["core_chromatograms"]),
        "array_count": len(arrays),
        "numeric_value_count": sum(int(item["value_count"]) for item in arrays),
        "extension_count": len(blocks["extensions"]),
    }
    return {
        "file": file_path.name,
        "file_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "header": {
            "magic": magic.decode("ascii"),
            "version": version,
            "endianness": endianness,
            "flags": flags,
            "created_at": created_at,
            "directory_offset": directory_offset,
        },
        "directory": entries,
        "blocks": blocks,
        "arrays": arrays,
        "statistics": stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Independently inspect one complete ZP v1/v2 file")
    parser.add_argument("path", type=Path)
    parser.add_argument("--no-business-validation", action="store_true")
    args = parser.parse_args()
    try:
        report = inspect_full_zp(args.path, validate_business=not args.no_business_validation)
    except (OSError, InspectionError) as exc:
        error = {"valid": False, "error": str(exc)}
        if isinstance(exc, InspectionError):
            error["error_code"] = exc.code
        print(json.dumps(error, ensure_ascii=False, sort_keys=True, indent=2))
        return 1
    print(json.dumps({"valid": True, **report}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
