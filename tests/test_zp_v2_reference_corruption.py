from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Callable

import pytest

from specs.zp_v2.arrays_reference_codec import validate_arrays_block


FIXTURE = Path(__file__).parents[1] / "specs" / "zp_v2" / "fixtures" / "valid_arrays_v2.bin"
HEADER = struct.Struct("<8sHBBIQQQQ16s")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _parts(raw: bytes) -> tuple[list[object], dict[str, object], bytearray]:
    header = list(HEADER.unpack(raw[:64]))
    directory = json.loads(raw[header[5]:header[5] + header[6]].decode("utf-8"))
    payload = bytearray(raw[header[7]:header[7] + header[8]])
    return header, directory, payload


def _rebuild(
    raw: bytes,
    mutate_directory: Callable[[dict[str, object]], None] | None = None,
    mutate_payload: Callable[[dict[str, object], bytearray], None] | None = None,
    *,
    entry_count: int | None = None,
) -> bytes:
    header, directory, payload = _parts(raw)
    if mutate_directory:
        mutate_directory(directory)
    if mutate_payload:
        mutate_payload(directory, payload)
    directory_raw = _canonical(directory)
    payload_offset = (64 + len(directory_raw) + 7) & ~7
    header[4] = len(directory["entries"]) if entry_count is None else entry_count
    header[5] = 64
    header[6] = len(directory_raw)
    header[7] = payload_offset
    header[8] = len(payload)
    return HEADER.pack(*header) + directory_raw + b"\0" * (payload_offset - 64 - len(directory_raw)) + payload


def _header_byte(raw: bytes, offset: int, value: int) -> bytes:
    mutated = bytearray(raw)
    mutated[offset] = value
    return bytes(mutated)


def _header_u16(raw: bytes, offset: int, value: int) -> bytes:
    mutated = bytearray(raw)
    mutated[offset:offset + 2] = value.to_bytes(2, "little")
    return bytes(mutated)


def _header_u64(raw: bytes, offset: int, value: int) -> bytes:
    mutated = bytearray(raw)
    mutated[offset:offset + 8] = value.to_bytes(8, "little")
    return bytes(mutated)


def _entry_mutation(index: int, field: str, value: object) -> Callable[[dict[str, object]], None]:
    def mutate(directory: dict[str, object]) -> None:
        directory["entries"][index][field] = value
    return mutate


def _numeric_mutation(array_type: str, value: float) -> Callable[[dict[str, object], bytearray], None]:
    def mutate(directory: dict[str, object], payload: bytearray) -> None:
        entry = next(item for item in directory["entries"] if item["array_type"] == array_type)
        start = entry["data_offset"]
        payload[start:start + 8] = struct.pack("<d", value)
        chunk = bytes(payload[start:start + entry["byte_length"]])
        entry["checksum"] = hashlib.sha256(chunk).hexdigest()
    return mutate


def _cases(raw: bytes) -> list[tuple[str, bytes, str]]:
    _, directory, _ = _parts(raw)
    original_payload_offset = HEADER.unpack(raw[:64])[7]
    cases: list[tuple[str, bytes, str]] = []
    bad_magic = bytearray(raw); bad_magic[0:8] = b"BADMAGIC"
    cases.append(("arrays magic", bytes(bad_magic), "INVALID_ARRAYS_MAGIC"))
    cases.append(("schema version", _header_u16(raw, 8, 3), "UNSUPPORTED_ARRAYS_VERSION"))
    cases.append(("endianness", _header_byte(raw, 10, 2), "UNSUPPORTED_ARRAYS_ENDIANNESS"))
    cases.append(("flags", _header_byte(raw, 11, 1), "UNSUPPORTED_ARRAYS_FLAGS"))
    cases.append(("reserved", _header_byte(raw, 48, 1), "NONZERO_ARRAYS_RESERVED"))
    cases.append(("directory offset", _header_u64(raw, 16, 65), "INVALID_ARRAY_DIRECTORY_OFFSET"))
    cases.append(("directory length out of bounds", _header_u64(raw, 24, len(raw)), "INVALID_ARRAY_DIRECTORY_LENGTH"))
    cases.append(("payload misalignment", _header_u64(raw, 32, original_payload_offset + 1), "ARRAY_PAYLOAD_MISALIGNED"))
    padded_valid = _rebuild(raw, _entry_mutation(0, "array_id", directory["entries"][0]["array_id"] + "x"))
    padded_header = HEADER.unpack(padded_valid[:64])
    directory_end = 64 + padded_header[6]
    assert directory_end < padded_header[7]
    nonzero_padding = bytearray(padded_valid); nonzero_padding[directory_end] = 1
    cases.append(("nonzero padding", bytes(nonzero_padding), "NONZERO_ARRAY_PADDING"))
    cases.append(("payload length header", _header_u64(raw, 40, HEADER.unpack(raw[:64])[8] + 8), "INVALID_ARRAY_PAYLOAD_LENGTH"))
    cases.append(("trailing byte", raw + b"x", "ARRAYS_TRAILING_DATA"))
    cases.append(("entry count", _rebuild(raw, entry_count=4), "ARRAY_ENTRY_COUNT_MISMATCH"))
    cases.append(("duplicate array id", _rebuild(raw, _entry_mutation(1, "array_id", directory["entries"][0]["array_id"])), "DUPLICATE_ARRAY_ID"))
    cases.append(("unsorted entries", _rebuild(raw, lambda item: item["entries"].reverse()), "UNSORTED_ARRAY_DIRECTORY"))
    cases.append(("unknown array type", _rebuild(raw, _entry_mutation(0, "array_type", "mobility")), "UNSUPPORTED_ARRAY_TYPE"))
    cases.append(("float32 dtype", _rebuild(raw, _entry_mutation(0, "dtype", "float32")), "UNSUPPORTED_ARRAY_DTYPE"))
    cases.append(("zlib encoding", _rebuild(raw, _entry_mutation(0, "encoding", "zlib")), "UNSUPPORTED_ARRAY_ENCODING"))
    cases.append(("checksum format", _rebuild(raw, _entry_mutation(0, "checksum", "A" * 64)), "INVALID_ARRAY_CHECKSUM_FORMAT"))
    checksum_payload = bytearray(raw); checksum_payload[original_payload_offset] ^= 1
    cases.append(("per-array checksum", bytes(checksum_payload), "ARRAY_CHECKSUM_MISMATCH"))
    cases.append(("payload gap", _rebuild(raw, _entry_mutation(1, "data_offset", 32)), "ARRAY_PAYLOAD_GAP"))
    cases.append(("payload overlap", _rebuild(raw, _entry_mutation(1, "data_offset", 16)), "OVERLAPPING_ARRAY_PAYLOAD"))
    def extend_last(item: dict[str, object]) -> None:
        item["entries"][-1]["value_count"] += 1
        item["entries"][-1]["byte_length"] += 8
    cases.append(("entry out of bounds", _rebuild(raw, extend_last), "ARRAY_PAYLOAD_OUT_OF_BOUNDS"))
    cases.append(("byte length mismatch", _rebuild(raw, _entry_mutation(0, "byte_length", 16)), "ARRAY_BYTE_LENGTH_MISMATCH"))
    cases.append(("payload too short", _rebuild(raw, mutate_payload=lambda _d, payload: payload.__delitem__(slice(-8, None))), "ARRAY_PAYLOAD_OUT_OF_BOUNDS"))
    cases.append(("payload too long", _rebuild(raw, mutate_payload=lambda _d, payload: payload.extend(b"\0" * 8)), "INVALID_ARRAY_PAYLOAD_LENGTH"))
    cases.append(("NaN payload", _rebuild(raw, mutate_payload=_numeric_mutation("intensity", float("nan"))), "NONFINITE_ARRAY_VALUE"))
    cases.append(("Infinity payload", _rebuild(raw, mutate_payload=_numeric_mutation("intensity", float("inf"))), "NONFINITE_ARRAY_VALUE"))
    cases.append(("negative mz", _rebuild(raw, mutate_payload=_numeric_mutation("mz", -1.0)), "NEGATIVE_MZ_VALUE"))
    cases.append(("negative time", _rebuild(raw, mutate_payload=_numeric_mutation("time", -1.0)), "NEGATIVE_TIME_VALUE"))
    invalid_utf8 = bytearray(raw); invalid_utf8[64] = 0xFF
    cases.append(("invalid UTF-8 directory", bytes(invalid_utf8), "INVALID_ARRAY_DIRECTORY_SCHEMA"))
    cases.append(("unknown directory field", _rebuild(raw, lambda item: item.__setitem__("unknown", 1)), "INVALID_ARRAY_DIRECTORY_SCHEMA"))
    def remove_field(item: dict[str, object]) -> None:
        del item["entries"][0]["checksum"]
    cases.append(("missing entry field", _rebuild(raw, remove_field), "INVALID_ARRAY_DIRECTORY_SCHEMA"))
    return cases


CASES = _cases(FIXTURE.read_bytes())


def test_corruption_matrix_contains_all_32_required_real_byte_mutations() -> None:
    assert len(CASES) == 32
    assert len({name for name, _, _ in CASES}) == 32
    assert all(mutated != FIXTURE.read_bytes() for _, mutated, _ in CASES)


@pytest.mark.parametrize(("name", "mutated", "expected_code"), CASES, ids=[item[0] for item in CASES])
def test_reference_validator_rejects_corrupted_fixture_with_stable_code(name: str, mutated: bytes, expected_code: str) -> None:
    result = validate_arrays_block(mutated)
    assert result.valid is False, name
    assert result.error_code == expected_code, name
