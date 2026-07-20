from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass, fields
from typing import BinaryIO, Iterable, Iterator

import numpy as np

from .blocks import ArrayBlock, NormalizedFloat64List
from .exceptions import ZpV2ArrayWriteError, ZpV2ResourceLimitError


_ARRAYS_HEADER = struct.Struct("<8sHBBIQQQQ16s")
_FLOAT64 = struct.Struct("<d")
_ARRAYS_MAGIC = b"ZPARRV2\0"
_ARRAYS_SCHEMA_VERSION = 2
_ARRAYS_DIRECTORY_OFFSET = 64
_SUPPORTED_ARRAY_TYPES = frozenset({"mz", "intensity", "time"})
_CHUNK_VALUE_COUNT = 8192


@dataclass(frozen=True, slots=True)
class ZpV2ArrayWriteLimits:
    max_arrays_block_length: int = 512 * 1024 * 1024
    max_directory_length: int = 64 * 1024 * 1024
    max_entry_count: int = 100_000
    max_array_value_count: int = 16_000_000
    max_array_id_utf8_length: int = 4096
    max_payload_length: int = 448 * 1024 * 1024

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ZpV2ArrayWriteError(
                    "INVALID_ARRAY_WRITE_LIMITS",
                    "write limits must be positive integers",
                    f"v2_limits.{item.name}",
                    actual=value,
                    limit=1,
                )


DEFAULT_V2_ARRAY_WRITE_LIMITS = ZpV2ArrayWriteLimits()


@dataclass(frozen=True, slots=True)
class V2ArrayDirectoryEntry:
    array_id: str
    array_type: str
    dtype: str
    encoding: str
    value_count: int
    data_offset: int
    byte_length: int
    checksum: str

    def as_json(self) -> dict[str, object]:
        return {
            "array_id": self.array_id,
            "array_type": self.array_type,
            "dtype": self.dtype,
            "encoding": self.encoding,
            "value_count": self.value_count,
            "data_offset": self.data_offset,
            "byte_length": self.byte_length,
            "checksum": self.checksum,
        }


@dataclass(frozen=True, slots=True)
class V2ArraysLayout:
    arrays: tuple[ArrayBlock, ...]
    entries: tuple[V2ArrayDirectoryEntry, ...]
    directory_bytes: bytes
    payload_offset: int
    payload_length: int
    block_length: int


def _fail(
    code: str,
    message: str,
    location: str,
    *,
    actual: object | None = None,
    limit: int | None = None,
) -> None:
    error_type = ZpV2ResourceLimitError if limit is not None else ZpV2ArrayWriteError
    raise error_type(code, message, location, actual=actual, limit=limit)


def _check_limit(code: str, actual: int, limit: int, location: str) -> None:
    if actual > limit:
        _fail(code, "resource limit exceeded", location, actual=actual, limit=limit)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _align8(value: int) -> int:
    return (value + 7) & ~7


def _build_directory_bytes(entries: list[V2ArrayDirectoryEntry], limit: int) -> bytes:
    directory = bytearray(b'{"entries":[')
    for position, entry in enumerate(entries):
        encoded = _canonical_json_bytes(entry.as_json())
        projected = len(directory) + (1 if position else 0) + len(encoded) + 2
        _check_limit("ARRAY_DIRECTORY_TOO_LARGE", projected, limit, "arrays.directory_length")
        if position:
            directory.extend(b",")
        directory.extend(encoded)
    directory.extend(b"]}")
    _check_limit("ARRAY_DIRECTORY_TOO_LARGE", len(directory), limit, "arrays.directory_length")
    return bytes(directory)


def _encode_value(array: ArrayBlock, value: object, position: int) -> bytes:
    location = f"arrays[{array.array_id!r}].values[{position}]"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("INVALID_ARRAY_VALUE", "array values must be int or float, excluding bool", location, actual=type(value).__name__)
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        _fail("INVALID_ARRAY_VALUE", "array value cannot be represented as float64", location, actual=value)
    if not math.isfinite(number):
        _fail("NONFINITE_ARRAY_VALUE", "array values must be finite", location, actual=number)
    if array.array_type == "mz" and number < 0:
        _fail("NEGATIVE_MZ_VALUE", "m/z values must not be negative", location, actual=number)
    if array.array_type == "time" and number < 0:
        _fail("NEGATIVE_TIME_VALUE", "time values must not be negative", location, actual=number)
    return _FLOAT64.pack(number)


def _numeric_buffer(array: ArrayBlock, *, validate_types: bool) -> np.ndarray:
    if validate_types and not isinstance(array.values, NormalizedFloat64List):
        for position, value in enumerate(array.values):
            _encode_value(array, value, position)
    try:
        values = np.asarray(array.values, dtype="<f8")
    except (OverflowError, TypeError, ValueError) as exc:
        _fail(
            "INVALID_ARRAY_VALUE",
            "array values cannot be represented as float64",
            f"arrays[{array.array_id!r}].values",
            actual=str(exc),
        )
    if values.ndim != 1:
        _fail(
            "INVALID_ARRAY_VALUE",
            "array values must be one-dimensional",
            f"arrays[{array.array_id!r}].values",
            actual=values.ndim,
        )
    invalid = ~np.isfinite(values)
    if array.array_type in {"mz", "time"}:
        invalid |= values < 0
    positions = np.flatnonzero(invalid)
    if positions.size:
        position = int(positions[0])
        number = float(values[position])
        if not math.isfinite(number):
            code = "NONFINITE_ARRAY_VALUE"
            message = "array values must be finite"
        elif array.array_type == "mz":
            code = "NEGATIVE_MZ_VALUE"
            message = "m/z values must not be negative"
        else:
            code = "NEGATIVE_TIME_VALUE"
            message = "time values must not be negative"
        _fail(
            code,
            message,
            f"arrays[{array.array_id!r}].values[{position}]",
            actual=number,
        )
    return np.ascontiguousarray(values)


def _encoded_chunks(
    array: ArrayBlock,
    *,
    validate_types: bool = True,
) -> Iterator[memoryview]:
    values = _numeric_buffer(array, validate_types=validate_types)
    raw = memoryview(values).cast("B")
    chunk_size = _CHUNK_VALUE_COUNT * _FLOAT64.size
    for offset in range(0, len(raw), chunk_size):
        yield raw[offset : offset + chunk_size]


def prepare_v2_arrays_layout(
    arrays: Iterable[ArrayBlock],
    *,
    limits: ZpV2ArrayWriteLimits | None = None,
) -> V2ArraysLayout:
    active_limits = DEFAULT_V2_ARRAY_WRITE_LIMITS if limits is None else limits
    if not isinstance(active_limits, ZpV2ArrayWriteLimits):
        _fail(
            "INVALID_ARRAY_WRITE_LIMITS",
            "limits must be a ZpV2ArrayWriteLimits instance",
            "v2_limits",
            actual=type(active_limits).__name__,
        )
    try:
        array_view = tuple(arrays)
    except TypeError:
        _fail("INVALID_ARRAY_BLOCK", "arrays must be iterable", "arrays", actual=type(arrays).__name__)
    _check_limit("ARRAY_COUNT_TOO_LARGE", len(array_view), active_limits.max_entry_count, "arrays.entry_count")

    seen: set[str] = set()
    keyed: list[tuple[bytes, ArrayBlock]] = []
    for position, array in enumerate(array_view):
        location = f"arrays[{position}]"
        if not isinstance(array, ArrayBlock):
            _fail("INVALID_ARRAY_BLOCK", "every item must be an ArrayBlock", location, actual=type(array).__name__)
        if not isinstance(array.array_id, str) or not array.array_id or "\0" in array.array_id:
            _fail("INVALID_ARRAY_ID", "array_id must be a nonempty NUL-free string", f"{location}.array_id", actual=array.array_id)
        try:
            encoded_id = array.array_id.encode("utf-8")
        except UnicodeEncodeError:
            _fail("INVALID_ARRAY_ID", "array_id must be valid UTF-8", f"{location}.array_id", actual=array.array_id)
        _check_limit(
            "ARRAY_ID_TOO_LONG",
            len(encoded_id),
            active_limits.max_array_id_utf8_length,
            f"{location}.array_id",
        )
        if array.array_id in seen:
            _fail("DUPLICATE_ARRAY_ID", "array_id values must be unique", f"{location}.array_id", actual=array.array_id)
        seen.add(array.array_id)
        if not isinstance(array.array_type, str) or array.array_type not in _SUPPORTED_ARRAY_TYPES:
            _fail(
                "UNSUPPORTED_ARRAY_TYPE",
                "array_type must be mz, intensity, or time",
                f"{location}.array_type",
                actual=array.array_type,
            )
        if array.dtype != "float64":
            _fail(
                "UNSUPPORTED_ARRAY_DTYPE",
                "dtype must be float64",
                f"{location}.dtype",
                actual=array.dtype,
            )
        if not isinstance(array.values, list):
            _fail("INVALID_ARRAY_VALUE", "values must be a list", f"{location}.values", actual=type(array.values).__name__)
        _check_limit(
            "ARRAY_VALUE_COUNT_TOO_LARGE",
            len(array.values),
            active_limits.max_array_value_count,
            f"{location}.values",
        )
        keyed.append((encoded_id, array))

    keyed.sort(key=lambda item: item[0])
    sorted_arrays = tuple(item[1] for item in keyed)
    entries: list[V2ArrayDirectoryEntry] = []
    data_offset = 0
    for array in sorted_arrays:
        checksum = hashlib.sha256()
        for chunk in _encoded_chunks(array, validate_types=True):
            checksum.update(chunk)
        byte_length = len(array.values) * _FLOAT64.size
        entries.append(
            V2ArrayDirectoryEntry(
                array_id=array.array_id,
                array_type=array.array_type,
                dtype="float64",
                encoding="raw-le",
                value_count=len(array.values),
                data_offset=data_offset,
                byte_length=byte_length,
                checksum=checksum.hexdigest(),
            )
        )
        data_offset += byte_length
        _check_limit(
            "ARRAY_PAYLOAD_TOO_LARGE",
            data_offset,
            active_limits.max_payload_length,
            "arrays.payload_length",
        )

    directory_bytes = _build_directory_bytes(entries, active_limits.max_directory_length)
    payload_offset = _align8(_ARRAYS_DIRECTORY_OFFSET + len(directory_bytes))
    block_length = payload_offset + data_offset
    _check_limit(
        "ARRAYS_RESOURCE_LIMIT_EXCEEDED",
        block_length,
        active_limits.max_arrays_block_length,
        "arrays.block_length",
    )
    return V2ArraysLayout(
        arrays=sorted_arrays,
        entries=tuple(entries),
        directory_bytes=directory_bytes,
        payload_offset=payload_offset,
        payload_length=data_offset,
        block_length=block_length,
    )


def _write_exact(stream: BinaryIO, value: bytes | memoryview, digest: object) -> None:
    written = stream.write(value)
    if written != len(value):
        raise OSError(f"short write: expected {len(value)} bytes, wrote {written}")
    digest.update(value)


def write_v2_arrays_block(stream: BinaryIO, layout: V2ArraysLayout) -> tuple[int, str]:
    digest = hashlib.sha256()
    header = _ARRAYS_HEADER.pack(
        _ARRAYS_MAGIC,
        _ARRAYS_SCHEMA_VERSION,
        1,
        0,
        len(layout.entries),
        _ARRAYS_DIRECTORY_OFFSET,
        len(layout.directory_bytes),
        layout.payload_offset,
        layout.payload_length,
        b"\0" * 16,
    )
    _write_exact(stream, header, digest)
    _write_exact(stream, layout.directory_bytes, digest)
    padding = b"\0" * (layout.payload_offset - _ARRAYS_DIRECTORY_OFFSET - len(layout.directory_bytes))
    _write_exact(stream, padding, digest)
    payload_written = 0
    for array, entry in zip(layout.arrays, layout.entries):
        array_digest = hashlib.sha256()
        array_written = 0
        for chunk in _encoded_chunks(array, validate_types=False):
            _write_exact(stream, chunk, digest)
            array_digest.update(chunk)
            array_written += len(chunk)
        if array_written != entry.byte_length or array_digest.hexdigest() != entry.checksum:
            _fail(
                "ARRAY_VALUES_CHANGED_DURING_WRITE",
                "array values changed after layout preparation",
                f"arrays[{array.array_id!r}].values",
                actual=array_written,
            )
        payload_written += array_written
    if payload_written != layout.payload_length:
        _fail(
            "ARRAY_VALUES_CHANGED_DURING_WRITE",
            "payload length changed after layout preparation",
            "arrays.payload_length",
            actual=payload_written,
        )
    return layout.block_length, digest.hexdigest()
