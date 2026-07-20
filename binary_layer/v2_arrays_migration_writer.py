from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import BinaryIO

from .blocks import ArrayBlock
from .v1_arrays_stream_reader import StreamedV1Array
from .v2_arrays_writer import (
    DEFAULT_V2_ARRAY_WRITE_LIMITS,
    V2ArrayDirectoryEntry,
    ZpV2ArrayWriteLimits,
    _ARRAYS_DIRECTORY_OFFSET,
    _ARRAYS_HEADER,
    _ARRAYS_MAGIC,
    _ARRAYS_SCHEMA_VERSION,
    _align8,
    _build_directory_bytes,
    _check_limit,
    _encoded_chunks,
)


class V2ArraysMigrationWriteError(OSError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class SpooledArray:
    array_id: str
    array_type: str
    value_count: int
    spool_offset: int
    byte_length: int
    checksum: str


@dataclass(frozen=True, slots=True)
class MigratedArraysBlock:
    block_length: int
    checksum: str
    array_count: int
    numeric_value_count: int
    payload_spool_bytes: int
    payload_copy_bytes: int
    arrays: tuple[SpooledArray, ...]


def _write_exact(stream: BinaryIO, payload: bytes, digest: object) -> None:
    written = stream.write(payload)
    if written != len(payload):
        raise V2ArraysMigrationWriteError(
            "SHORT_WRITE",
            f"expected {len(payload)} bytes but wrote {written}",
        )
    digest.update(payload)


class V2ArraysMigrationWriter:
    """Spools v1 arrays once, then emits the frozen sorted v2 arrays layout."""

    def __init__(
        self,
        spool: BinaryIO,
        *,
        limits: ZpV2ArrayWriteLimits | None = None,
        copy_chunk_size: int = 256 * 1024,
    ) -> None:
        self.spool = spool
        self.limits = limits or DEFAULT_V2_ARRAY_WRITE_LIMITS
        if isinstance(copy_chunk_size, bool) or not isinstance(copy_chunk_size, int) or copy_chunk_size <= 0:
            raise V2ArraysMigrationWriteError("INVALID_CHUNK_SIZE", "copy_chunk_size must be positive")
        self.copy_chunk_size = copy_chunk_size
        self._arrays: list[SpooledArray] = []
        self._seen: set[str] = set()
        self._spool_bytes = 0
        self._numeric_value_count = 0

    @property
    def arrays(self) -> tuple[SpooledArray, ...]:
        return tuple(self._arrays)

    @property
    def spool_bytes(self) -> int:
        return self._spool_bytes

    @property
    def numeric_value_count(self) -> int:
        return self._numeric_value_count

    def add(self, source: StreamedV1Array) -> SpooledArray:
        if source.array_id in self._seen:
            raise V2ArraysMigrationWriteError(
                "DUPLICATE_ARRAY_ID",
                f"duplicate array_id {source.array_id!r}",
            )
        _check_limit(
            "ARRAY_COUNT_TOO_LARGE",
            len(self._arrays) + 1,
            self.limits.max_entry_count,
            "arrays.entry_count",
        )
        block = ArrayBlock(source.array_id, source.array_type, source.dtype, source.values)
        digest = hashlib.sha256()
        start = self._spool_bytes
        written = 0
        for chunk in _encoded_chunks(block):
            _write_exact(self.spool, chunk, digest)
            written += len(chunk)
        expected = len(source.values) * 8
        if written != expected:
            raise V2ArraysMigrationWriteError(
                "ARRAY_BYTE_LENGTH_MISMATCH",
                f"array {source.array_id!r} wrote {written} bytes instead of {expected}",
            )
        _check_limit(
            "ARRAY_PAYLOAD_TOO_LARGE",
            self._spool_bytes + written,
            self.limits.max_payload_length,
            "arrays.payload_length",
        )
        item = SpooledArray(
            array_id=source.array_id,
            array_type=source.array_type,
            value_count=len(source.values),
            spool_offset=start,
            byte_length=written,
            checksum=digest.hexdigest(),
        )
        self._arrays.append(item)
        self._seen.add(item.array_id)
        self._spool_bytes += written
        self._numeric_value_count += item.value_count
        return item

    def flush_spool(self) -> None:
        try:
            self.spool.flush()
            os.fsync(self.spool.fileno())
        except (AttributeError, OSError, ValueError) as exc:
            raise V2ArraysMigrationWriteError("SPOOL_FSYNC_FAILED", str(exc)) from exc

    def write_block(self, output: BinaryIO) -> MigratedArraysBlock:
        sorted_arrays = tuple(sorted(self._arrays, key=lambda item: item.array_id.encode("utf-8")))
        entries: list[V2ArrayDirectoryEntry] = []
        payload_offset = 0
        for item in sorted_arrays:
            entries.append(
                V2ArrayDirectoryEntry(
                    array_id=item.array_id,
                    array_type=item.array_type,
                    dtype="float64",
                    encoding="raw-le",
                    value_count=item.value_count,
                    data_offset=payload_offset,
                    byte_length=item.byte_length,
                    checksum=item.checksum,
                )
            )
            payload_offset += item.byte_length
        directory_bytes = _build_directory_bytes(entries, self.limits.max_directory_length)
        data_start = _align8(_ARRAYS_DIRECTORY_OFFSET + len(directory_bytes))
        block_length = data_start + payload_offset
        _check_limit(
            "ARRAYS_RESOURCE_LIMIT_EXCEEDED",
            block_length,
            self.limits.max_arrays_block_length,
            "arrays.block_length",
        )
        digest = hashlib.sha256()
        header = _ARRAYS_HEADER.pack(
            _ARRAYS_MAGIC,
            _ARRAYS_SCHEMA_VERSION,
            1,
            0,
            len(entries),
            _ARRAYS_DIRECTORY_OFFSET,
            len(directory_bytes),
            data_start,
            payload_offset,
            b"\0" * 16,
        )
        _write_exact(output, header, digest)
        _write_exact(output, directory_bytes, digest)
        _write_exact(
            output,
            b"\0" * (data_start - _ARRAYS_DIRECTORY_OFFSET - len(directory_bytes)),
            digest,
        )
        copied = 0
        for item in sorted_arrays:
            try:
                self.spool.seek(item.spool_offset)
            except (OSError, ValueError) as exc:
                raise V2ArraysMigrationWriteError("SPOOL_SEEK_FAILED", str(exc)) from exc
            remaining = item.byte_length
            per_array = hashlib.sha256()
            while remaining:
                requested = min(remaining, self.copy_chunk_size)
                chunk = self.spool.read(requested)
                if len(chunk) != requested:
                    raise V2ArraysMigrationWriteError(
                        "SPOOL_READ_FAILED",
                        f"array {item.array_id!r} payload is truncated",
                    )
                _write_exact(output, chunk, digest)
                per_array.update(chunk)
                copied += len(chunk)
                remaining -= len(chunk)
            if per_array.hexdigest() != item.checksum:
                raise V2ArraysMigrationWriteError(
                    "SPOOL_CHECKSUM_MISMATCH",
                    f"array {item.array_id!r} changed in the payload spool",
                )
        if copied != self._spool_bytes:
            raise V2ArraysMigrationWriteError(
                "SPOOL_LENGTH_MISMATCH",
                f"copied {copied} bytes instead of {self._spool_bytes}",
            )
        return MigratedArraysBlock(
            block_length=block_length,
            checksum=digest.hexdigest(),
            array_count=len(sorted_arrays),
            numeric_value_count=self._numeric_value_count,
            payload_spool_bytes=self._spool_bytes,
            payload_copy_bytes=copied,
            arrays=sorted_arrays,
        )

