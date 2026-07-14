"""Standard-library reference codec for the frozen ZP v2 arrays subformat.

This module is deliberately isolated from ``binary_layer``.  It proves the
P1-B7 byte specification; it is not a production Reader, Writer, or Validator.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence


ARRAYS_HEADER_STRUCT = struct.Struct("<8sHBBIQQQQ16s")
ARRAYS_MAGIC = b"ZPARRV2\0"
ARRAYS_SCHEMA_VERSION = 2
LITTLE_ENDIAN = 1
ARRAYS_DIRECTORY_OFFSET = 64
ARRAY_TYPES = frozenset({"mz", "intensity", "time"})
ENTRY_FIELDS = frozenset(
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
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    max_arrays_block_length: int = 512 * 1024 * 1024
    max_directory_length: int = 64 * 1024 * 1024
    max_entry_count: int = 100_000
    max_array_value_count: int = 16_000_000
    max_array_id_utf8_length: int = 4096
    max_payload_length: int = 448 * 1024 * 1024
    max_decoded_memory: int = 1024 * 1024 * 1024


DEFAULT_LIMITS = ResourceLimits()


@dataclass(frozen=True, slots=True)
class ReferenceArray:
    array_id: str
    array_type: str
    values: tuple[float, ...]

    def __init__(self, array_id: str, array_type: str, values: Iterable[float]) -> None:
        object.__setattr__(self, "array_id", array_id)
        object.__setattr__(self, "array_type", array_type)
        object.__setattr__(self, "values", tuple(values))


@dataclass(frozen=True, slots=True)
class DecodedArraysBlock:
    arrays: tuple[ReferenceArray, ...]
    directory: dict[str, object]
    directory_length: int
    payload_offset: int
    payload_length: int


@dataclass(frozen=True, slots=True)
class ReferenceValidationResult:
    valid: bool
    error_code: str | None
    message: str
    entry_count: int = 0


class ReferenceCodecError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class _ParsedBlock:
    raw: memoryview
    entries: tuple[dict[str, object], ...]
    directory: dict[str, object]
    directory_length: int
    payload_offset: int
    payload_length: int


def _fail(code: str, message: str) -> None:
    raise ReferenceCodecError(code, message)


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("INVALID_ARRAY_DIRECTORY_SCHEMA", str(exc))


def _align8(value: int) -> int:
    return (value + 7) & ~7


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_array_id(array_id: object, limits: ResourceLimits) -> str:
    if not isinstance(array_id, str) or not array_id or "\0" in array_id:
        _fail("INVALID_ARRAY_DIRECTORY_SCHEMA", "array_id must be a nonempty NUL-free string")
    encoded = array_id.encode("utf-8")
    if len(encoded) > limits.max_array_id_utf8_length:
        _fail("ARRAY_ID_TOO_LONG", f"array_id exceeds {limits.max_array_id_utf8_length} UTF-8 bytes")
    return array_id


def _validate_values(array_type: str, values: Sequence[object]) -> tuple[float, ...]:
    normalized: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail("NONFINITE_ARRAY_VALUE", "array values must be finite numbers")
        number = float(value)
        if not math.isfinite(number):
            _fail("NONFINITE_ARRAY_VALUE", "array values must be finite numbers")
        if array_type == "mz" and number < 0:
            _fail("NEGATIVE_MZ_VALUE", "m/z values must not be negative")
        if array_type == "time" and number < 0:
            _fail("NEGATIVE_TIME_VALUE", "time values must not be negative")
        normalized.append(number)
    return tuple(normalized)


def encode_arrays_block(
    arrays: Iterable[ReferenceArray],
    *,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> bytes:
    normalized: list[ReferenceArray] = []
    seen: set[str] = set()
    for item in arrays:
        if not isinstance(item, ReferenceArray):
            _fail("INVALID_ARRAY_DIRECTORY_SCHEMA", "input items must be ReferenceArray instances")
        array_id = _validate_array_id(item.array_id, limits)
        if array_id in seen:
            _fail("DUPLICATE_ARRAY_ID", f"duplicate array_id: {array_id}")
        seen.add(array_id)
        if item.array_type not in ARRAY_TYPES:
            _fail("UNSUPPORTED_ARRAY_TYPE", f"unsupported array_type: {item.array_type}")
        values = _validate_values(item.array_type, item.values)
        if len(values) > limits.max_array_value_count:
            _fail("ARRAY_VALUE_COUNT_TOO_LARGE", f"array {array_id} exceeds the value-count limit")
        normalized.append(ReferenceArray(array_id, item.array_type, values))
    if len(normalized) > limits.max_entry_count:
        _fail("ARRAY_COUNT_TOO_LARGE", "entry_count exceeds the implementation limit")

    normalized.sort(key=lambda item: item.array_id.encode("utf-8"))
    entries: list[dict[str, object]] = []
    payload_parts: list[bytes] = []
    data_offset = 0
    for item in normalized:
        payload = struct.pack(f"<{len(item.values)}d", *item.values)
        entries.append(
            {
                "array_id": item.array_id,
                "array_type": item.array_type,
                "dtype": "float64",
                "encoding": "raw-le",
                "value_count": len(item.values),
                "data_offset": data_offset,
                "byte_length": len(payload),
                "checksum": hashlib.sha256(payload).hexdigest(),
            }
        )
        payload_parts.append(payload)
        data_offset += len(payload)

    directory_bytes = _canonical_json_bytes({"entries": entries})
    if len(directory_bytes) > limits.max_directory_length:
        _fail("ARRAY_DIRECTORY_TOO_LARGE", "array directory exceeds the implementation limit")
    payload = b"".join(payload_parts)
    if len(payload) > limits.max_payload_length:
        _fail("ARRAYS_RESOURCE_LIMIT_EXCEEDED", "array payload exceeds the implementation limit")
    payload_offset = _align8(ARRAYS_DIRECTORY_OFFSET + len(directory_bytes))
    padding = b"\0" * (payload_offset - ARRAYS_DIRECTORY_OFFSET - len(directory_bytes))
    block_length = payload_offset + len(payload)
    if block_length > limits.max_arrays_block_length:
        _fail("ARRAYS_RESOURCE_LIMIT_EXCEEDED", "arrays block exceeds the implementation limit")
    header = ARRAYS_HEADER_STRUCT.pack(
        ARRAYS_MAGIC,
        ARRAYS_SCHEMA_VERSION,
        LITTLE_ENDIAN,
        0,
        len(entries),
        ARRAYS_DIRECTORY_OFFSET,
        len(directory_bytes),
        payload_offset,
        len(payload),
        b"\0" * 16,
    )
    return header + directory_bytes + padding + payload


def _parse_arrays_block(data: bytes | bytearray | memoryview, limits: ResourceLimits) -> _ParsedBlock:
    try:
        raw = memoryview(data).cast("B")
    except TypeError as exc:
        _fail("INVALID_ARRAY_DIRECTORY_SCHEMA", f"arrays block must be bytes-like: {exc}")
    if len(raw) > limits.max_arrays_block_length:
        _fail("ARRAYS_RESOURCE_LIMIT_EXCEEDED", "arrays block exceeds the implementation limit")
    if len(raw) < ARRAYS_HEADER_STRUCT.size:
        _fail("INVALID_ARRAYS_MAGIC", "arrays block is shorter than the 64-byte header")
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
    ) = ARRAYS_HEADER_STRUCT.unpack(raw[:64])
    if magic != ARRAYS_MAGIC:
        _fail("INVALID_ARRAYS_MAGIC", f"unexpected arrays magic: {magic!r}")
    if schema_version != ARRAYS_SCHEMA_VERSION:
        _fail("UNSUPPORTED_ARRAYS_VERSION", f"unsupported arrays schema_version: {schema_version}")
    if endianness != LITTLE_ENDIAN:
        _fail("UNSUPPORTED_ARRAYS_ENDIANNESS", f"unsupported arrays endianness: {endianness}")
    if flags != 0:
        _fail("UNSUPPORTED_ARRAYS_FLAGS", f"unsupported arrays flags: {flags}")
    if reserved != b"\0" * 16:
        _fail("NONZERO_ARRAYS_RESERVED", "reserved header bytes must all be zero")
    if directory_offset != ARRAYS_DIRECTORY_OFFSET:
        _fail("INVALID_ARRAY_DIRECTORY_OFFSET", "directory_offset must equal 64")
    if directory_length > limits.max_directory_length:
        _fail("ARRAY_DIRECTORY_TOO_LARGE", "array directory exceeds the implementation limit")
    directory_end = directory_offset + directory_length
    if directory_end < directory_offset or directory_end > len(raw):
        _fail("INVALID_ARRAY_DIRECTORY_LENGTH", "array directory is outside the arrays block")
    if payload_offset % 8:
        _fail("ARRAY_PAYLOAD_MISALIGNED", "payload_offset must be 8-byte aligned")
    expected_payload_offset = _align8(directory_end)
    if payload_offset != expected_payload_offset:
        _fail("INVALID_ARRAY_PAYLOAD_OFFSET", "payload_offset must be align8(directory end)")
    if payload_length > limits.max_payload_length:
        _fail("ARRAYS_RESOURCE_LIMIT_EXCEEDED", "array payload exceeds the implementation limit")
    payload_end = payload_offset + payload_length
    if payload_end < payload_offset or payload_end > len(raw):
        _fail("INVALID_ARRAY_PAYLOAD_LENGTH", "declared payload exceeds the arrays block")
    if payload_end < len(raw):
        _fail("ARRAYS_TRAILING_DATA", "arrays block contains bytes after the declared payload")
    if any(raw[directory_end:payload_offset]):
        _fail("NONZERO_ARRAY_PADDING", "directory-to-payload padding must be zero")
    directory_bytes = bytes(raw[directory_offset:directory_end])
    try:
        directory = json.loads(directory_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail("INVALID_ARRAY_DIRECTORY_SCHEMA", f"invalid UTF-8 JSON array directory: {exc}")
    if not isinstance(directory, dict) or set(directory) != {"entries"} or not isinstance(directory.get("entries"), list):
        _fail("INVALID_ARRAY_DIRECTORY_SCHEMA", "directory must be an object containing only an entries list")
    if _canonical_json_bytes(directory) != directory_bytes:
        _fail("INVALID_ARRAY_DIRECTORY_SCHEMA", "array directory is not canonical JSON")
    entries = directory["entries"]
    if entry_count > limits.max_entry_count:
        _fail("ARRAY_COUNT_TOO_LARGE", "entry_count exceeds the implementation limit")
    if entry_count != len(entries):
        _fail("ARRAY_ENTRY_COUNT_MISMATCH", "entry_count does not match directory entries")

    typed_entries: list[dict[str, object]] = []
    ids: list[str] = []
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
            _fail("INVALID_ARRAY_DIRECTORY_SCHEMA", f"entry {position} has an invalid field set")
        array_id = _validate_array_id(entry["array_id"], limits)
        array_type = entry["array_type"]
        if array_type not in ARRAY_TYPES:
            _fail("UNSUPPORTED_ARRAY_TYPE", f"unsupported array_type: {array_type}")
        if entry["dtype"] != "float64":
            _fail("UNSUPPORTED_ARRAY_DTYPE", f"unsupported dtype: {entry['dtype']}")
        if entry["encoding"] != "raw-le":
            _fail("UNSUPPORTED_ARRAY_ENCODING", f"unsupported encoding: {entry['encoding']}")
        for field in ("value_count", "data_offset", "byte_length"):
            if not _is_int(entry[field]) or entry[field] < 0:
                _fail("INVALID_ARRAY_DIRECTORY_SCHEMA", f"entry {position} field {field} must be a nonnegative integer")
        if entry["value_count"] > limits.max_array_value_count:
            _fail("ARRAY_VALUE_COUNT_TOO_LARGE", f"array {array_id} exceeds the value-count limit")
        checksum = entry["checksum"]
        if not isinstance(checksum, str) or SHA256_RE.fullmatch(checksum) is None:
            _fail("INVALID_ARRAY_CHECKSUM_FORMAT", f"array {array_id} checksum is not lowercase SHA-256")
        if entry["byte_length"] != entry["value_count"] * 8:
            _fail("ARRAY_BYTE_LENGTH_MISMATCH", f"array {array_id} byte_length must equal value_count * 8")
        ids.append(array_id)
        typed_entries.append(entry)
    if len(ids) != len(set(ids)):
        _fail("DUPLICATE_ARRAY_ID", "array_id values must be unique")
    if ids != sorted(ids, key=lambda item: item.encode("utf-8")):
        _fail("UNSORTED_ARRAY_DIRECTORY", "entries must be sorted by array_id UTF-8 bytes")

    expected_offset = 0
    for entry in typed_entries:
        offset = entry["data_offset"]
        length = entry["byte_length"]
        if offset > expected_offset:
            _fail("ARRAY_PAYLOAD_GAP", f"payload gap before array {entry['array_id']}")
        if offset < expected_offset:
            _fail("OVERLAPPING_ARRAY_PAYLOAD", f"payload overlap at array {entry['array_id']}")
        if offset + length > payload_length:
            _fail("ARRAY_PAYLOAD_OUT_OF_BOUNDS", f"array {entry['array_id']} exceeds the payload")
        expected_offset = offset + length
    if expected_offset != payload_length:
        _fail("INVALID_ARRAY_PAYLOAD_LENGTH", "payload_length must equal the sum of all byte_length values")
    if payload_length > limits.max_decoded_memory:
        _fail("ARRAYS_RESOURCE_LIMIT_EXCEEDED", "declared decoded memory exceeds the implementation limit")
    return _ParsedBlock(raw, tuple(typed_entries), directory, directory_length, payload_offset, payload_length)


def _decode_entry(parsed: _ParsedBlock, entry: dict[str, object], payload: bytes) -> ReferenceArray:
    if hashlib.sha256(payload).hexdigest() != entry["checksum"]:
        _fail("ARRAY_CHECKSUM_MISMATCH", f"checksum mismatch for array {entry['array_id']}")
    value_count = entry["value_count"]
    values = struct.unpack(f"<{value_count}d", payload)
    normalized = _validate_values(entry["array_type"], values)
    return ReferenceArray(entry["array_id"], entry["array_type"], normalized)


def decode_arrays_block(
    data: bytes | bytearray | memoryview,
    *,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> DecodedArraysBlock:
    parsed = _parse_arrays_block(data, limits)
    arrays: list[ReferenceArray] = []
    for entry in parsed.entries:
        start = parsed.payload_offset + entry["data_offset"]
        payload = bytes(parsed.raw[start:start + entry["byte_length"]])
        arrays.append(_decode_entry(parsed, entry, payload))
    return DecodedArraysBlock(
        tuple(arrays),
        parsed.directory,
        parsed.directory_length,
        parsed.payload_offset,
        parsed.payload_length,
    )


PayloadReader = Callable[[int, int], bytes]


def read_array(
    data: bytes | bytearray | memoryview,
    array_id: str,
    *,
    limits: ResourceLimits = DEFAULT_LIMITS,
    payload_reader: PayloadReader | None = None,
) -> tuple[float, ...]:
    parsed = _parse_arrays_block(data, limits)
    target = next((entry for entry in parsed.entries if entry["array_id"] == array_id), None)
    if target is None:
        _fail("UNKNOWN_ARRAY_ID", f"unknown array_id: {array_id}")
    start = parsed.payload_offset + target["data_offset"]
    length = target["byte_length"]
    payload = bytes(parsed.raw[start:start + length]) if payload_reader is None else payload_reader(start, length)
    if not isinstance(payload, bytes) or len(payload) != length:
        _fail("ARRAY_PAYLOAD_OUT_OF_BOUNDS", "payload_reader did not return the exact target bytes")
    return _decode_entry(parsed, target, payload).values


def validate_arrays_block(
    data: bytes | bytearray | memoryview,
    *,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> ReferenceValidationResult:
    try:
        decoded = decode_arrays_block(data, limits=limits)
    except ReferenceCodecError as exc:
        return ReferenceValidationResult(False, exc.code, exc.message)
    return ReferenceValidationResult(True, None, "valid", len(decoded.arrays))

