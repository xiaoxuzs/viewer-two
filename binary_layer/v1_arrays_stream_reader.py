from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import BinaryIO, Iterator

from .serialization import canonical_json_bytes
from .v2_arrays_writer import DEFAULT_V2_ARRAY_WRITE_LIMITS, ZpV2ArrayWriteLimits


_ARRAY_FIELDS = frozenset({"array_id", "array_type", "dtype", "values"})
_SUPPORTED_ARRAY_TYPES = frozenset({"mz", "intensity", "time"})


class V1ArraysStreamError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class _DuplicateJsonKey(ValueError):
    pass


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant {value}")


@dataclass(frozen=True, slots=True)
class StreamedV1Array:
    array_id: str
    array_type: str
    dtype: str
    values: list[int | float]


@dataclass(slots=True)
class V1ArraysStreamMetrics:
    scan_count: int = 0
    array_count: int = 0
    numeric_value_count: int = 0
    max_live_array_count: int = 0
    max_single_array_value_count: int = 0
    max_single_array_json_bytes: int = 0
    block_bytes_read: int = 0


class V1ArraysStreamReader:
    """Bounded-memory parser for one canonical v1 JSON arrays block."""

    def __init__(
        self,
        stream: BinaryIO,
        *,
        block_offset: int,
        block_length: int,
        expected_checksum: str,
        chunk_size: int = 256 * 1024,
        limits: ZpV2ArrayWriteLimits | None = None,
    ) -> None:
        if isinstance(block_offset, bool) or not isinstance(block_offset, int) or block_offset < 0:
            raise V1ArraysStreamError("INVALID_ARRAYS_RANGE", "block_offset must be nonnegative")
        if isinstance(block_length, bool) or not isinstance(block_length, int) or block_length < 0:
            raise V1ArraysStreamError("INVALID_ARRAYS_RANGE", "block_length must be nonnegative")
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
            raise V1ArraysStreamError("INVALID_CHUNK_SIZE", "chunk_size must be positive")
        self.stream = stream
        self.block_offset = block_offset
        self.block_length = block_length
        self.expected_checksum = expected_checksum
        self.chunk_size = chunk_size
        self.limits = limits or DEFAULT_V2_ARRAY_WRITE_LIMITS
        self.metrics = V1ArraysStreamMetrics()
        self._remaining = block_length
        self._buffer = b""
        self._position = 0
        self._digest = hashlib.sha256()
        self._decoder = json.JSONDecoder(
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )

    def iter_arrays(self) -> Iterator[StreamedV1Array]:
        if self.metrics.scan_count:
            raise V1ArraysStreamError("ARRAYS_RESCANNED", "one reader instance may scan only once")
        self.metrics.scan_count = 1
        try:
            self.stream.seek(self.block_offset)
        except (OSError, ValueError) as exc:
            raise V1ArraysStreamError("ARRAYS_SEEK_FAILED", str(exc)) from exc
        seen: set[str] = set()
        if self._read_byte() != ord("["):
            raise V1ArraysStreamError("INVALID_ARRAYS_JSON", "arrays block must begin with '['")
        next_byte = self._read_byte()
        if next_byte == ord("]"):
            self._finish()
            return
        while True:
            if next_byte != ord("{"):
                raise V1ArraysStreamError(
                    "INVALID_ARRAYS_JSON",
                    "every arrays list element must be a JSON object",
                )
            raw_record = self._read_complete_object(next_byte)
            record = self._decode_record(raw_record, self.metrics.array_count)
            if record.array_id in seen:
                raise V1ArraysStreamError(
                    "DUPLICATE_ARRAY_ID",
                    f"duplicate array_id {record.array_id!r}",
                )
            seen.add(record.array_id)
            self.metrics.array_count += 1
            self.metrics.numeric_value_count += len(record.values)
            self.metrics.max_live_array_count = 1
            self.metrics.max_single_array_value_count = max(
                self.metrics.max_single_array_value_count,
                len(record.values),
            )
            self.metrics.max_single_array_json_bytes = max(
                self.metrics.max_single_array_json_bytes,
                len(raw_record),
            )
            yield record
            delimiter = self._read_byte()
            if delimiter is None:
                raise V1ArraysStreamError(
                    "TRUNCATED_ARRAYS_JSON",
                    "arrays list is missing its closing bracket",
                )
            if delimiter == ord("]"):
                self._finish()
                return
            if delimiter != ord(","):
                raise V1ArraysStreamError(
                    "INVALID_ARRAYS_JSON",
                    "array records must be separated by a comma",
                )
            next_byte = self._read_byte()

    def _read_complete_object(self, first_byte: int) -> bytes:
        raw = bytearray((first_byte,))
        nesting = 1
        in_string = False
        escaped = False
        while nesting:
            value = self._read_byte()
            if value is None:
                raise V1ArraysStreamError("TRUNCATED_ARRAYS_JSON", "array record is truncated")
            raw.append(value)
            if in_string:
                if escaped:
                    escaped = False
                elif value == ord("\\"):
                    escaped = True
                elif value == ord('"'):
                    in_string = False
            elif value == ord('"'):
                in_string = True
            elif value in (ord("{"), ord("[")):
                nesting += 1
            elif value in (ord("}"), ord("]")):
                nesting -= 1
                if nesting < 0:
                    raise V1ArraysStreamError("INVALID_ARRAYS_JSON", "unbalanced JSON delimiters")
            if len(raw) > self.limits.max_arrays_block_length:
                raise V1ArraysStreamError(
                    "ARRAY_RECORD_TOO_LARGE",
                    "single array JSON record exceeds the arrays block resource limit",
                )
        return bytes(raw)

    def _decode_record(self, payload: bytes, position: int) -> StreamedV1Array:
        try:
            text = payload.decode("utf-8")
            value, end = self._decoder.raw_decode(text)
        except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
            raise V1ArraysStreamError(
                "INVALID_ARRAYS_JSON",
                f"arrays[{position}] is not strict JSON: {exc}",
            ) from exc
        if end != len(text) or not isinstance(value, dict):
            raise V1ArraysStreamError(
                "INVALID_ARRAY_RECORD",
                f"arrays[{position}] must contain exactly one object",
            )
        try:
            if canonical_json_bytes(value) != payload:
                raise V1ArraysStreamError(
                    "NONCANONICAL_ARRAYS_JSON",
                    f"arrays[{position}] is not canonical JSON",
                )
        except (TypeError, UnicodeError, ValueError) as exc:
            if isinstance(exc, V1ArraysStreamError):
                raise
            raise V1ArraysStreamError("INVALID_ARRAY_RECORD", str(exc)) from exc
        if frozenset(value) != _ARRAY_FIELDS:
            raise V1ArraysStreamError(
                "INVALID_ARRAY_RECORD",
                f"arrays[{position}] has an invalid field set",
            )
        array_id = value["array_id"]
        array_type = value["array_type"]
        dtype = value["dtype"]
        values = value["values"]
        if not isinstance(array_id, str) or not array_id or "\0" in array_id:
            raise V1ArraysStreamError("INVALID_ARRAY_ID", f"arrays[{position}].array_id is invalid")
        try:
            encoded_id = array_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise V1ArraysStreamError("INVALID_ARRAY_ID", str(exc)) from exc
        if len(encoded_id) > self.limits.max_array_id_utf8_length:
            raise V1ArraysStreamError("ARRAY_ID_TOO_LONG", f"array_id {array_id!r} is too long")
        if array_type not in _SUPPORTED_ARRAY_TYPES:
            raise V1ArraysStreamError("UNSUPPORTED_ARRAY_TYPE", f"unsupported array_type {array_type!r}")
        if dtype != "float64":
            raise V1ArraysStreamError("UNSUPPORTED_ARRAY_DTYPE", f"unsupported dtype {dtype!r}")
        if not isinstance(values, list):
            raise V1ArraysStreamError("INVALID_ARRAY_VALUES", "values must be a list")
        if len(values) > self.limits.max_array_value_count:
            raise V1ArraysStreamError("ARRAY_VALUE_COUNT_TOO_LARGE", f"array {array_id!r} is too large")
        for value_position, item in enumerate(values):
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise V1ArraysStreamError(
                    "INVALID_ARRAY_VALUE",
                    f"array {array_id!r} value {value_position} is not numeric",
                )
            try:
                number = float(item)
            except (OverflowError, TypeError, ValueError) as exc:
                raise V1ArraysStreamError("INVALID_ARRAY_VALUE", str(exc)) from exc
            if not math.isfinite(number):
                raise V1ArraysStreamError(
                    "NONFINITE_ARRAY_VALUE",
                    f"array {array_id!r} value {value_position} is non-finite",
                )
            if array_type in {"mz", "time"} and number < 0:
                raise V1ArraysStreamError(
                    "NEGATIVE_ARRAY_VALUE",
                    f"array {array_id!r} value {value_position} is negative",
                )
        return StreamedV1Array(array_id, array_type, dtype, values)

    def _read_byte(self) -> int | None:
        if self._position >= len(self._buffer):
            if self._remaining == 0:
                return None
            requested = min(self.chunk_size, self._remaining)
            try:
                chunk = self.stream.read(requested)
            except OSError as exc:
                raise V1ArraysStreamError("ARRAYS_READ_FAILED", str(exc)) from exc
            if len(chunk) != requested:
                raise V1ArraysStreamError(
                    "TRUNCATED_ARRAYS_BLOCK",
                    f"expected {requested} bytes, read {len(chunk)}",
                )
            self._buffer = chunk
            self._position = 0
            self._remaining -= len(chunk)
            self.metrics.block_bytes_read += len(chunk)
            self._digest.update(chunk)
        value = self._buffer[self._position]
        self._position += 1
        return value

    def _finish(self) -> None:
        if self._read_byte() is not None:
            raise V1ArraysStreamError("ARRAYS_TRAILING_DATA", "arrays JSON has trailing bytes")
        if self._digest.hexdigest() != self.expected_checksum:
            raise V1ArraysStreamError(
                "ARRAYS_CHECKSUM_MISMATCH",
                "arrays block checksum changed during streaming read",
            )
