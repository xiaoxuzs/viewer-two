from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import sys
from array import array
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import BinaryIO, Mapping

from .blocks import ArrayBlock
from .exceptions import ZpV2ArrayReadError


_ARRAYS_HEADER = struct.Struct("<8sHBBIQQQQ16s")
_ARRAYS_MAGIC = b"ZPARRV2\0"
_ARRAYS_SCHEMA_VERSION = 2
_ARRAYS_DIRECTORY_OFFSET = _ARRAYS_HEADER.size
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ENTRY_FIELDS = frozenset(
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


@dataclass(frozen=True, slots=True)
class ZpV2ArrayReadLimits:
    max_arrays_block_length: int = 512 * 1024 * 1024
    max_directory_length: int = 64 * 1024 * 1024
    max_entry_count: int = 100_000
    max_array_value_count: int = 16_000_000
    max_array_id_utf8_length: int = 4096
    max_payload_length: int = 448 * 1024 * 1024
    max_decoded_memory: int = 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                _fail(
                    "INVALID_ARRAY_READ_LIMITS",
                    "read limits must be positive integers",
                    f"v2_limits.{item.name}",
                    actual=value,
                    limit=1,
                )


DEFAULT_V2_ARRAY_READ_LIMITS = ZpV2ArrayReadLimits()


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


@dataclass(frozen=True, slots=True)
class V2ArraysHeader:
    entry_count: int
    directory_offset: int
    directory_length: int
    payload_offset: int
    payload_length: int


@dataclass(frozen=True, slots=True)
class V2ArraysDirectory:
    header: V2ArraysHeader
    entries: tuple[V2ArrayDirectoryEntry, ...]
    entries_by_id: Mapping[str, V2ArrayDirectoryEntry]

    @property
    def directory_length(self) -> int:
        return self.header.directory_length

    @property
    def payload_offset(self) -> int:
        return self.header.payload_offset

    @property
    def payload_length(self) -> int:
        return self.header.payload_length


def _fail(
    code: str,
    message: str,
    location: str,
    *,
    actual: object | None = None,
    limit: int | None = None,
    array_id: str | None = None,
) -> None:
    raise ZpV2ArrayReadError(
        code,
        message,
        location,
        actual=actual,
        limit=limit,
        array_id=array_id,
    )


def _check_limit(code: str, actual: int, limit: int, location: str) -> None:
    if actual > limit:
        _fail(code, "resource limit exceeded", location, actual=actual, limit=limit)


def _read_exact(stream: BinaryIO, length: int, location: str, code: str) -> bytes:
    try:
        payload = stream.read(length)
    except OSError as exc:
        raise ZpV2ArrayReadError(code, "read failed", location, actual=str(exc)) from exc
    if len(payload) != length:
        _fail(code, "data is truncated", location, actual=len(payload), limit=length)
    return payload


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail(
                "INVALID_ARRAY_DIRECTORY_SCHEMA",
                "duplicate JSON object key",
                "arrays.directory",
                actual=key,
            )
        result[key] = value
    return result


def _parse_canonical_directory(payload: bytes, *, require_canonical: bool) -> object:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ZpV2ArrayReadError(
            "INVALID_ARRAY_DIRECTORY_SCHEMA",
            "directory is not valid UTF-8",
            "arrays.directory",
            actual=exc.start,
        ) from exc
    try:
        parsed = (
            json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
            if require_canonical
            else json.loads(text)
        )
    except ZpV2ArrayReadError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise ZpV2ArrayReadError(
            "INVALID_ARRAY_DIRECTORY_SCHEMA",
            "directory is not valid JSON",
            "arrays.directory",
            actual=str(exc),
        ) from exc
    if require_canonical:
        try:
            canonical = _canonical_json_bytes(parsed)
        except (TypeError, UnicodeError, ValueError) as exc:
            raise ZpV2ArrayReadError(
                "INVALID_ARRAY_DIRECTORY_SCHEMA",
                "directory cannot be canonically serialized",
                "arrays.directory",
                actual=str(exc),
            ) from exc
        if canonical != payload:
            _fail(
                "NONCANONICAL_ARRAY_DIRECTORY",
                "directory JSON is not canonical",
                "arrays.directory",
            )
    return parsed


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class ZpV2ArraysReader:
    def __init__(self, limits: ZpV2ArrayReadLimits | None = None) -> None:
        if limits is None:
            limits = DEFAULT_V2_ARRAY_READ_LIMITS
        if not isinstance(limits, ZpV2ArrayReadLimits):
            _fail(
                "INVALID_ARRAY_READ_LIMITS",
                "limits must be a ZpV2ArrayReadLimits instance",
                "v2_limits",
                actual=type(limits).__name__,
            )
        self.limits = limits

    def read_directory(
        self,
        stream: BinaryIO,
        *,
        block_offset: int,
        block_length: int,
        require_canonical: bool = True,
    ) -> V2ArraysDirectory:
        limits = self.limits
        _check_limit(
            "ARRAYS_RESOURCE_LIMIT_EXCEEDED",
            block_length,
            limits.max_arrays_block_length,
            "arrays.block_length",
        )
        if block_length < _ARRAYS_HEADER.size:
            _fail(
                "INVALID_ARRAY_DIRECTORY_LENGTH",
                "arrays block is shorter than its fixed header",
                "arrays.block_length",
                actual=block_length,
                limit=_ARRAYS_HEADER.size,
            )
        try:
            stream.seek(block_offset)
        except (OSError, ValueError) as exc:
            raise ZpV2ArrayReadError(
                "INVALID_ARRAY_DIRECTORY_OFFSET",
                "cannot seek to arrays block",
                "arrays.block_offset",
                actual=block_offset,
            ) from exc
        raw_header = _read_exact(
            stream,
            _ARRAYS_HEADER.size,
            "arrays.header",
            "INVALID_ARRAY_DIRECTORY_LENGTH",
        )
        try:
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
        except struct.error as exc:
            raise ZpV2ArrayReadError(
                "INVALID_ARRAY_DIRECTORY_LENGTH", "invalid arrays header", "arrays.header"
            ) from exc

        if magic != _ARRAYS_MAGIC:
            _fail("INVALID_ARRAYS_MAGIC", "invalid arrays magic", "arrays.header.magic", actual=magic)
        if schema_version != _ARRAYS_SCHEMA_VERSION:
            _fail(
                "UNSUPPORTED_ARRAYS_VERSION",
                "unsupported arrays schema version",
                "arrays.header.schema_version",
                actual=schema_version,
            )
        if endianness != 1:
            _fail(
                "UNSUPPORTED_ARRAYS_ENDIANNESS",
                "unsupported arrays endianness",
                "arrays.header.endianness",
                actual=endianness,
            )
        if flags != 0:
            _fail("UNSUPPORTED_ARRAYS_FLAGS", "arrays flags must be zero", "arrays.header.flags", actual=flags)
        if reserved != b"\0" * 16:
            _fail("NONZERO_ARRAYS_RESERVED", "reserved bytes must be zero", "arrays.header.reserved")
        if directory_offset != _ARRAYS_DIRECTORY_OFFSET:
            _fail(
                "INVALID_ARRAY_DIRECTORY_OFFSET",
                "internal directory must begin at byte 64",
                "arrays.header.directory_offset",
                actual=directory_offset,
            )

        _check_limit("ARRAY_COUNT_TOO_LARGE", entry_count, limits.max_entry_count, "arrays.entry_count")
        _check_limit(
            "ARRAY_DIRECTORY_TOO_LARGE",
            directory_length,
            limits.max_directory_length,
            "arrays.directory_length",
        )
        _check_limit(
            "ARRAY_PAYLOAD_TOO_LARGE",
            payload_length,
            limits.max_payload_length,
            "arrays.payload_length",
        )
        directory_end = directory_offset + directory_length
        expected_payload_offset = (directory_end + 7) & ~7
        if directory_end > block_length:
            _fail(
                "INVALID_ARRAY_DIRECTORY_LENGTH",
                "internal directory is outside the arrays block",
                "arrays.header.directory_length",
                actual=directory_length,
                limit=max(0, block_length - directory_offset),
            )
        if payload_offset % 8:
            _fail(
                "ARRAY_PAYLOAD_MISALIGNED",
                "payload offset must be 8-byte aligned",
                "arrays.header.payload_offset",
                actual=payload_offset,
            )
        if payload_offset != expected_payload_offset:
            _fail(
                "INVALID_ARRAY_PAYLOAD_OFFSET",
                "payload offset does not match aligned directory end",
                "arrays.header.payload_offset",
                actual=payload_offset,
                limit=expected_payload_offset,
            )
        payload_end = payload_offset + payload_length
        if payload_end > block_length:
            _fail(
                "INVALID_ARRAY_PAYLOAD_LENGTH",
                "payload is outside the arrays block",
                "arrays.header.payload_length",
                actual=payload_end,
                limit=block_length,
            )
        if payload_end != block_length:
            _fail(
                "ARRAYS_TRAILING_DATA",
                "arrays block contains trailing data",
                "arrays.block_length",
                actual=block_length,
                limit=payload_end,
            )

        try:
            stream.seek(block_offset + directory_offset)
        except (OSError, ValueError) as exc:
            raise ZpV2ArrayReadError(
                "INVALID_ARRAY_DIRECTORY_OFFSET",
                "cannot seek to internal directory",
                "arrays.header.directory_offset",
                actual=directory_offset,
            ) from exc
        raw_directory = _read_exact(
            stream,
            directory_length,
            "arrays.directory",
            "INVALID_ARRAY_DIRECTORY_LENGTH",
        )
        padding = _read_exact(
            stream,
            payload_offset - directory_end,
            "arrays.padding",
            "INVALID_ARRAY_PAYLOAD_OFFSET",
        )
        if any(padding):
            _fail("NONZERO_ARRAY_PADDING", "array padding must be zero", "arrays.padding")

        parsed = _parse_canonical_directory(
            raw_directory,
            require_canonical=require_canonical,
        )
        if not isinstance(parsed, dict) or set(parsed) != {"entries"} or not isinstance(parsed.get("entries"), list):
            _fail(
                "INVALID_ARRAY_DIRECTORY_SCHEMA",
                "directory must be an object containing only an entries list",
                "arrays.directory",
            )
        raw_entries = parsed["entries"]
        if len(raw_entries) != entry_count:
            _fail(
                "ARRAY_ENTRY_COUNT_MISMATCH",
                "entry_count does not match directory entries",
                "arrays.entry_count",
                actual=len(raw_entries),
                limit=entry_count,
            )

        # Validate identity and sort order before interpreting payload layout so
        # malformed ordering cannot be masked by offsets from the moved entry.
        preliminary_ids: set[str] = set()
        previous_encoded_id: bytes | None = None
        for position, raw_entry in enumerate(raw_entries):
            location = f"arrays.directory.entries[{position}]"
            if not isinstance(raw_entry, dict) or set(raw_entry) != _ENTRY_FIELDS:
                _fail(
                    "INVALID_ARRAY_DIRECTORY_SCHEMA",
                    "array entry has an invalid field set",
                    location,
                )
            array_id = raw_entry["array_id"]
            if not isinstance(array_id, str) or not array_id or "\0" in array_id:
                _fail("INVALID_ARRAY_ID", "array_id must be a nonempty NUL-free string", f"{location}.array_id")
            try:
                encoded_id = array_id.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ZpV2ArrayReadError(
                    "INVALID_ARRAY_ID",
                    "array_id is not valid UTF-8",
                    f"{location}.array_id",
                    array_id=array_id,
                ) from exc
            if array_id in preliminary_ids:
                _fail("DUPLICATE_ARRAY_ID", "array_id values must be unique", f"{location}.array_id", array_id=array_id)
            if previous_encoded_id is not None and encoded_id <= previous_encoded_id:
                _fail("UNSORTED_ARRAY_DIRECTORY", "entries must be sorted by UTF-8 array_id", location, array_id=array_id)
            preliminary_ids.add(array_id)
            previous_encoded_id = encoded_id

        entries: list[V2ArrayDirectoryEntry] = []
        entries_by_id: dict[str, V2ArrayDirectoryEntry] = {}
        previous_id: bytes | None = None
        expected_data_offset = 0
        for position, raw_entry in enumerate(raw_entries):
            location = f"arrays.directory.entries[{position}]"
            if not isinstance(raw_entry, dict) or set(raw_entry) != _ENTRY_FIELDS:
                _fail(
                    "INVALID_ARRAY_DIRECTORY_SCHEMA",
                    "array entry has an invalid field set",
                    location,
                )
            array_id = raw_entry["array_id"]
            if not isinstance(array_id, str) or not array_id or "\0" in array_id:
                _fail("INVALID_ARRAY_ID", "array_id must be a nonempty NUL-free string", f"{location}.array_id")
            try:
                encoded_id = array_id.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ZpV2ArrayReadError(
                    "INVALID_ARRAY_ID", "array_id is not valid UTF-8", f"{location}.array_id", array_id=array_id
                ) from exc
            _check_limit("ARRAY_ID_TOO_LONG", len(encoded_id), limits.max_array_id_utf8_length, f"{location}.array_id")
            if array_id in entries_by_id:
                _fail("DUPLICATE_ARRAY_ID", "array_id values must be unique", f"{location}.array_id", array_id=array_id)
            if previous_id is not None and encoded_id <= previous_id:
                _fail("UNSORTED_ARRAY_DIRECTORY", "entries must be sorted by UTF-8 array_id", location, array_id=array_id)
            previous_id = encoded_id

            array_type = raw_entry["array_type"]
            dtype = raw_entry["dtype"]
            encoding = raw_entry["encoding"]
            checksum = raw_entry["checksum"]
            if array_type not in {"mz", "intensity", "time"}:
                _fail("UNSUPPORTED_ARRAY_TYPE", "unsupported array_type", f"{location}.array_type", actual=array_type)
            if dtype != "float64":
                _fail("UNSUPPORTED_ARRAY_DTYPE", "unsupported dtype", f"{location}.dtype", actual=dtype)
            if encoding != "raw-le":
                _fail("UNSUPPORTED_ARRAY_ENCODING", "unsupported encoding", f"{location}.encoding", actual=encoding)
            if not isinstance(checksum, str) or _SHA256_RE.fullmatch(checksum) is None:
                _fail("INVALID_ARRAY_CHECKSUM_FORMAT", "checksum must be lowercase SHA-256", f"{location}.checksum")

            value_count = raw_entry["value_count"]
            data_offset = raw_entry["data_offset"]
            byte_length = raw_entry["byte_length"]
            for field_name, value in (
                ("value_count", value_count),
                ("data_offset", data_offset),
                ("byte_length", byte_length),
            ):
                if not _is_plain_int(value) or value < 0:
                    _fail(
                        "INVALID_ARRAY_DIRECTORY_SCHEMA",
                        f"{field_name} must be a nonnegative integer",
                        f"{location}.{field_name}",
                        actual=value,
                    )
            _check_limit(
                "ARRAY_VALUE_COUNT_TOO_LARGE",
                value_count,
                limits.max_array_value_count,
                f"{location}.value_count",
            )
            if byte_length != value_count * 8:
                _fail(
                    "ARRAY_BYTE_LENGTH_MISMATCH",
                    "byte_length must equal value_count * 8",
                    f"{location}.byte_length",
                    actual=byte_length,
                    limit=value_count * 8,
                )
            if data_offset > expected_data_offset:
                _fail("ARRAY_PAYLOAD_GAP", "array payloads must be contiguous", f"{location}.data_offset", actual=data_offset, limit=expected_data_offset)
            if data_offset < expected_data_offset:
                _fail("OVERLAPPING_ARRAY_PAYLOAD", "array payloads overlap", f"{location}.data_offset", actual=data_offset, limit=expected_data_offset)
            if data_offset + byte_length > payload_length:
                _fail(
                    "ARRAY_PAYLOAD_OUT_OF_BOUNDS",
                    "array payload is outside payload bounds",
                    location,
                    actual=data_offset + byte_length,
                    limit=payload_length,
                )
            entry = V2ArrayDirectoryEntry(
                array_id=array_id,
                array_type=array_type,
                dtype=dtype,
                encoding=encoding,
                value_count=value_count,
                data_offset=data_offset,
                byte_length=byte_length,
                checksum=checksum,
            )
            entries.append(entry)
            entries_by_id[array_id] = entry
            expected_data_offset = data_offset + byte_length
        if expected_data_offset != payload_length:
            _fail(
                "INVALID_ARRAY_PAYLOAD_LENGTH",
                "directory entries do not cover the complete payload",
                "arrays.payload_length",
                actual=expected_data_offset,
                limit=payload_length,
            )

        header = V2ArraysHeader(
            entry_count=entry_count,
            directory_offset=directory_offset,
            directory_length=directory_length,
            payload_offset=payload_offset,
            payload_length=payload_length,
        )
        return V2ArraysDirectory(
            header=header,
            entries=tuple(entries),
            entries_by_id=MappingProxyType(entries_by_id),
        )

    def read_array(
        self,
        stream: BinaryIO,
        *,
        block_offset: int,
        directory: V2ArraysDirectory,
        array_id: str,
    ) -> ArrayBlock:
        entry = directory.entries_by_id.get(array_id)
        if entry is None:
            _fail(
                "ARRAY_NOT_FOUND",
                "array_id is not present in the arrays directory",
                "arrays.directory",
                array_id=array_id,
            )
        absolute_offset = block_offset + directory.payload_offset + entry.data_offset
        try:
            stream.seek(absolute_offset)
        except (OSError, ValueError) as exc:
            raise ZpV2ArrayReadError(
                "ARRAY_PAYLOAD_OUT_OF_BOUNDS",
                "cannot seek to array payload",
                f"arrays[{array_id!r}].payload",
                actual=absolute_offset,
                array_id=array_id,
            ) from exc
        payload = _read_exact(
            stream,
            entry.byte_length,
            f"arrays[{array_id!r}].payload",
            "ARRAY_PAYLOAD_OUT_OF_BOUNDS",
        )
        actual_checksum = hashlib.sha256(payload).hexdigest()
        if actual_checksum != entry.checksum:
            _fail(
                "ARRAY_CHECKSUM_MISMATCH",
                "array payload checksum does not match its directory entry",
                f"arrays[{array_id!r}].checksum",
                actual=actual_checksum,
                array_id=array_id,
            )
        values = array("d")
        try:
            values.frombytes(payload)
        except (MemoryError, ValueError) as exc:
            raise ZpV2ArrayReadError(
                "ARRAY_BYTE_LENGTH_MISMATCH",
                "array payload cannot be decoded as float64",
                f"arrays[{array_id!r}].payload",
                array_id=array_id,
            ) from exc
        if sys.byteorder != "little":
            values.byteswap()
        decoded = values.tolist()
        for position, value in enumerate(decoded):
            location = f"arrays[{array_id!r}].values[{position}]"
            if not math.isfinite(value):
                _fail("NONFINITE_ARRAY_VALUE", "array values must be finite", location, actual=value, array_id=array_id)
            if entry.array_type == "mz" and value < 0:
                _fail("NEGATIVE_MZ_VALUE", "m/z values must not be negative", location, actual=value, array_id=array_id)
            if entry.array_type == "time" and value < 0:
                _fail("NEGATIVE_TIME_VALUE", "time values must not be negative", location, actual=value, array_id=array_id)
        return ArrayBlock(array_id=entry.array_id, array_type=entry.array_type, dtype=entry.dtype, values=decoded)
