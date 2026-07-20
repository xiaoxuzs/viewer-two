from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, BinaryIO, Callable, TypeVar

from .blocks import (
    ArrayBlock,
    ChromatogramBlock,
    ExtensionBlock,
    GlobalMetaBlock,
    IndexBlock,
    PrecursorBlock,
    RunBlock,
    SpectrumBlock,
)
from .constants import (
    BLOCK_NAMES,
    DIRECTORY_LENGTH_STRUCT,
    HEADER_SIZE,
    HEADER_STRUCT,
    KNOWN_ZP_VERSIONS,
    SUPPORTED_ENCODINGS,
    SUPPORTED_ZP_READ_VERSIONS,
    ZP_ENDIANNESS_LITTLE,
    ZP_MAGIC,
    ZP_VERSION_V1,
    ZP_VERSION_V2,
)
from .exceptions import UnsupportedVersionError, ZpReadError, ZpV2ArrayReadError, ZpVersionNotImplementedError
from .models import BlockDirectoryEntry, ZpHeader
from .serialization import canonical_json_bytes, parse_json_bytes, parse_utc_datetime
from .v2_arrays_reader import V2ArraysDirectory, ZpV2ArrayReadLimits, ZpV2ArraysReader


_T = TypeVar("_T")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOP_ENTRY_FIELDS = frozenset({"block_name", "offset", "length", "encoding", "checksum"})
_MAX_TOP_DIRECTORY_LENGTH = 64 * 1024 * 1024
_FileFingerprint = tuple[str, int, int, int, int, int]


def _v2_fail(
    code: str,
    message: str,
    location: str,
    *,
    actual: object | None = None,
    limit: int | None = None,
    array_id: str | None = None,
) -> None:
    raise ZpV2ArrayReadError(code, message, location, actual=actual, limit=limit, array_id=array_id)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _v2_fail("INVALID_TOP_DIRECTORY_SCHEMA", "duplicate JSON object key", "directory", actual=key)
        result[key] = value
    return result


def _parse_canonical_top_directory(payload: bytes) -> object:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ZpV2ArrayReadError(
            "INVALID_TOP_DIRECTORY_SCHEMA", "top directory is not valid UTF-8", "directory", actual=exc.start
        ) from exc
    try:
        parsed = json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
    except ZpV2ArrayReadError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise ZpV2ArrayReadError(
            "INVALID_TOP_DIRECTORY_SCHEMA", "top directory is not valid JSON", "directory", actual=str(exc)
        ) from exc
    try:
        canonical = canonical_json_bytes(parsed)
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ZpV2ArrayReadError(
            "INVALID_TOP_DIRECTORY_SCHEMA", "top directory cannot be canonicalized", "directory", actual=str(exc)
        ) from exc
    if canonical != payload:
        _v2_fail("NONCANONICAL_TOP_DIRECTORY", "top directory JSON is not canonical", "directory")
    return parsed


def _plain_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class ZpReader:
    def __init__(self, file_path: str | Path, *, v2_limits: ZpV2ArrayReadLimits | None = None) -> None:
        self.file_path = Path(file_path)
        self._v2_arrays_reader = ZpV2ArraysReader(v2_limits)
        self._v2_top_directory_cache: tuple[_FileFingerprint, tuple[BlockDirectoryEntry, ...]] | None = None
        self._v2_arrays_directory_cache: tuple[
            _FileFingerprint,
            V2ArraysDirectory,
            bool,
        ] | None = None

    def _fingerprint(self, stream: BinaryIO) -> _FileFingerprint:
        handle_stat = os.fstat(stream.fileno())
        fingerprint = self._path_fingerprint()
        if (
            handle_stat.st_dev,
            handle_stat.st_ino,
            handle_stat.st_size,
            handle_stat.st_mtime_ns,
        ) != fingerprint[1:5]:
            _v2_fail("FILE_CHANGED_DURING_READ", "file identity changed while opening", "file")
        return fingerprint

    def _path_fingerprint(self) -> _FileFingerprint:
        stat = self.file_path.stat()
        return (
            str(self.file_path.resolve(strict=False)),
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )

    def _synchronize_caches(self, fingerprint: _FileFingerprint) -> None:
        cached_fingerprint = None
        if self._v2_top_directory_cache is not None:
            cached_fingerprint = self._v2_top_directory_cache[0]
        elif self._v2_arrays_directory_cache is not None:
            cached_fingerprint = self._v2_arrays_directory_cache[0]
        if cached_fingerprint is not None and cached_fingerprint != fingerprint:
            self._v2_top_directory_cache = None
            self._v2_arrays_directory_cache = None

    def _with_file(self, operation: Callable[[BinaryIO, ZpHeader, _FileFingerprint], _T]) -> _T:
        try:
            with self.file_path.open("rb") as stream:
                fingerprint = self._fingerprint(stream)
                self._synchronize_caches(fingerprint)
                header = self._read_header_from_stream(stream, fingerprint[3])
                try:
                    result = operation(stream, header, fingerprint)
                except Exception as operation_error:
                    try:
                        changed = self._path_fingerprint() != fingerprint
                    except OSError:
                        changed = True
                    if changed:
                        self._v2_top_directory_cache = None
                        self._v2_arrays_directory_cache = None
                        raise ZpV2ArrayReadError(
                            "FILE_CHANGED_DURING_READ", "file identity changed during read", "file"
                        ) from operation_error
                    raise
                try:
                    current = self._path_fingerprint()
                except OSError as exc:
                    raise ZpV2ArrayReadError(
                        "FILE_CHANGED_DURING_READ",
                        "file path became unavailable during read",
                        "file",
                        actual=str(exc),
                    ) from exc
                if current != fingerprint:
                    self._v2_top_directory_cache = None
                    self._v2_arrays_directory_cache = None
                    _v2_fail("FILE_CHANGED_DURING_READ", "file identity changed during read", "file")
                return result
        except (ZpReadError, UnsupportedVersionError, ZpVersionNotImplementedError):
            raise
        except OSError as exc:
            raise ZpReadError(f"Cannot read {self.file_path}: {exc}") from exc

    @staticmethod
    def _read_header_from_stream(stream: BinaryIO, file_size: int) -> ZpHeader:
        stream.seek(0)
        raw = stream.read(HEADER_SIZE)
        if len(raw) != HEADER_SIZE:
            raise ZpReadError("File is shorter than the fixed 24-byte header")
        header = ZpHeader(*HEADER_STRUCT.unpack(raw))
        if header.magic != ZP_MAGIC:
            raise ZpReadError(f"Invalid magic: {header.magic!r}")
        if header.endianness != ZP_ENDIANNESS_LITTLE:
            raise ZpReadError(f"Unsupported endianness: {header.endianness}")
        if header.version not in SUPPORTED_ZP_READ_VERSIONS:
            if header.version in KNOWN_ZP_VERSIONS:
                raise ZpVersionNotImplementedError(header.version, "read")
            raise UnsupportedVersionError(header.version, "read")
        if header.version == ZP_VERSION_V2:
            if header.flags != 0:
                _v2_fail("UNSUPPORTED_TOP_LEVEL_FLAGS", "v2 header flags must be zero", "header.flags", actual=header.flags)
            if header.directory_offset < HEADER_SIZE or header.directory_offset + DIRECTORY_LENGTH_STRUCT.size > file_size:
                _v2_fail(
                    "INVALID_TOP_DIRECTORY_OFFSET",
                    "top directory offset is outside the file",
                    "header.directory_offset",
                    actual=header.directory_offset,
                    limit=file_size,
                )
        return header

    def read_header(self) -> ZpHeader:
        return self._with_file(lambda _stream, header, _fingerprint: header)

    def _read_v1_directory(self, stream: BinaryIO, header: ZpHeader) -> list[BlockDirectoryEntry]:
        try:
            stream.seek(header.directory_offset)
            length_raw = stream.read(DIRECTORY_LENGTH_STRUCT.size)
            if len(length_raw) != DIRECTORY_LENGTH_STRUCT.size:
                raise ZpReadError("Directory length is truncated")
            length = DIRECTORY_LENGTH_STRUCT.unpack(length_raw)[0]
            payload = stream.read(length)
            if len(payload) != length:
                raise ZpReadError("Directory JSON is truncated")
            parsed = parse_json_bytes(payload)
            if not isinstance(parsed, list):
                raise ZpReadError("Directory JSON must be a list")
            return [BlockDirectoryEntry(**entry) for entry in parsed]
        except ZpReadError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ZpReadError(f"Invalid directory: {exc}") from exc

    def _read_v2_directory(
        self, stream: BinaryIO, header: ZpHeader, fingerprint: _FileFingerprint
    ) -> list[BlockDirectoryEntry]:
        if self._v2_top_directory_cache is not None and self._v2_top_directory_cache[0] == fingerprint:
            return list(self._v2_top_directory_cache[1])
        stream.seek(header.directory_offset)
        length_raw = stream.read(DIRECTORY_LENGTH_STRUCT.size)
        if len(length_raw) != DIRECTORY_LENGTH_STRUCT.size:
            _v2_fail("INVALID_TOP_DIRECTORY_LENGTH", "top directory length is truncated", "directory.length")
        directory_length = DIRECTORY_LENGTH_STRUCT.unpack(length_raw)[0]
        max_directory_length = _MAX_TOP_DIRECTORY_LENGTH
        if directory_length > max_directory_length:
            _v2_fail(
                "TOP_DIRECTORY_TOO_LARGE",
                "top directory resource limit exceeded",
                "directory.length",
                actual=directory_length,
                limit=max_directory_length,
            )
        expected_eof = header.directory_offset + DIRECTORY_LENGTH_STRUCT.size + directory_length
        if expected_eof != fingerprint[3]:
            _v2_fail(
                "INVALID_TOP_DIRECTORY_LENGTH",
                "top directory must end exactly at EOF",
                "directory.length",
                actual=expected_eof,
                limit=fingerprint[3],
            )
        payload = stream.read(directory_length)
        if len(payload) != directory_length:
            _v2_fail(
                "INVALID_TOP_DIRECTORY_LENGTH",
                "top directory JSON is truncated",
                "directory",
                actual=len(payload),
                limit=directory_length,
            )
        parsed = _parse_canonical_top_directory(payload)
        if not isinstance(parsed, list) or len(parsed) != len(BLOCK_NAMES):
            _v2_fail(
                "INVALID_TOP_DIRECTORY_SCHEMA",
                "top directory must contain exactly nine entries",
                "directory",
                actual=len(parsed) if isinstance(parsed, list) else type(parsed).__name__,
                limit=len(BLOCK_NAMES),
            )
        entries: list[BlockDirectoryEntry] = []
        previous_end = HEADER_SIZE
        for position, (raw_entry, expected_name) in enumerate(zip(parsed, BLOCK_NAMES)):
            location = f"directory[{position}]"
            if not isinstance(raw_entry, dict) or set(raw_entry) != _TOP_ENTRY_FIELDS:
                _v2_fail("INVALID_TOP_DIRECTORY_SCHEMA", "directory entry has an invalid field set", location)
            if raw_entry["block_name"] != expected_name:
                _v2_fail(
                    "INVALID_TOP_DIRECTORY_ORDER",
                    "block name or order is invalid",
                    f"{location}.block_name",
                    actual=raw_entry["block_name"],
                )
            offset = raw_entry["offset"]
            length = raw_entry["length"]
            if not _plain_nonnegative_int(offset) or not _plain_nonnegative_int(length):
                _v2_fail("INVALID_TOP_DIRECTORY_SCHEMA", "offset and length must be nonnegative integers", location)
            if offset < previous_end:
                _v2_fail("OVERLAPPING_TOP_LEVEL_BLOCKS", "top-level blocks overlap", location, actual=offset, limit=previous_end)
            if offset != previous_end:
                _v2_fail("TOP_LEVEL_BLOCK_GAP", "top-level blocks must be contiguous", location, actual=offset, limit=previous_end)
            if offset + length > header.directory_offset:
                _v2_fail(
                    "TOP_LEVEL_BLOCK_OUT_OF_BOUNDS",
                    "top-level block extends into the directory",
                    location,
                    actual=offset + length,
                    limit=header.directory_offset,
                )
            encoding = raw_entry["encoding"]
            expected_encoding = "zp-arrays-v2" if expected_name == "arrays" else "utf-8-json"
            if encoding != expected_encoding:
                _v2_fail(
                    "ARRAYS_ENCODING_VERSION_MISMATCH",
                    "block encoding is incompatible with ZP v2",
                    f"{location}.encoding",
                    actual=encoding,
                )
            checksum = raw_entry["checksum"]
            if not isinstance(checksum, str) or _SHA256_RE.fullmatch(checksum) is None:
                _v2_fail("INVALID_BLOCK_CHECKSUM_FORMAT", "checksum must be lowercase SHA-256", f"{location}.checksum")
            entries.append(
                BlockDirectoryEntry(
                    block_name=expected_name,
                    offset=offset,
                    length=length,
                    encoding=encoding,
                    checksum=checksum,
                )
            )
            previous_end = offset + length
        if previous_end != header.directory_offset:
            _v2_fail(
                "TOP_LEVEL_BLOCK_GAP",
                "last block must end at the top directory",
                "directory",
                actual=previous_end,
                limit=header.directory_offset,
            )
        frozen = tuple(entries)
        self._v2_top_directory_cache = (fingerprint, frozen)
        return list(frozen)

    def _directory_for(
        self, stream: BinaryIO, header: ZpHeader, fingerprint: _FileFingerprint
    ) -> list[BlockDirectoryEntry]:
        if header.version == ZP_VERSION_V1:
            return self._read_v1_directory(stream, header)
        return self._read_v2_directory(stream, header, fingerprint)

    def read_directory(self) -> list[BlockDirectoryEntry]:
        return self._with_file(lambda stream, header, fingerprint: self._directory_for(stream, header, fingerprint))

    @staticmethod
    def _entry(entries: list[BlockDirectoryEntry], block_name: str) -> BlockDirectoryEntry:
        matches = [entry for entry in entries if entry.block_name == block_name]
        if len(matches) != 1:
            raise ZpReadError(f"Expected exactly one directory entry for block {block_name!r}")
        return matches[0]

    @staticmethod
    def _read_exact_block(stream: BinaryIO, entry: BlockDirectoryEntry) -> bytes:
        stream.seek(entry.offset)
        payload = stream.read(entry.length)
        if len(payload) != entry.length:
            raise ZpReadError(f"Block {entry.block_name} is truncated")
        return payload

    def _read_json_block(
        self,
        stream: BinaryIO,
        header: ZpHeader,
        fingerprint: _FileFingerprint,
        block_name: str,
    ) -> Any:
        entries = self._directory_for(stream, header, fingerprint)
        entry = self._entry(entries, block_name)
        if header.version == ZP_VERSION_V1:
            if entry.encoding not in SUPPORTED_ENCODINGS:
                raise ZpReadError(f"Unsupported encoding {entry.encoding!r} for {block_name}")
        elif block_name == "arrays":
            return [
                {
                    "array_id": item.array_id,
                    "array_type": item.array_type,
                    "dtype": item.dtype,
                    "values": item.values,
                }
                for item in self._read_all_v2_arrays(stream, header, fingerprint)
            ]
        payload = self._read_exact_block(stream, entry)
        if header.version == ZP_VERSION_V2:
            actual = hashlib.sha256(payload).hexdigest()
            if actual != entry.checksum:
                _v2_fail(
                    "BLOCK_CHECKSUM_MISMATCH",
                    "block checksum does not match top directory",
                    f"blocks.{block_name}.checksum",
                    actual=actual,
                )
        try:
            return parse_json_bytes(payload)
        except (UnicodeError, ValueError) as exc:
            if header.version == ZP_VERSION_V2:
                raise ZpV2ArrayReadError(
                    "INVALID_JSON_BLOCK", "block is not valid UTF-8 JSON", f"blocks.{block_name}", actual=str(exc)
                ) from exc
            raise ZpReadError(f"Cannot read block {block_name}: {exc}") from exc

    def read_block(self, block_name: str) -> Any:
        return self._with_file(
            lambda stream, header, fingerprint: self._read_json_block(stream, header, fingerprint, block_name)
        )

    def read_global_meta(self) -> GlobalMetaBlock:
        payload = self.read_block("global_meta")
        payload["created_at"] = parse_utc_datetime(payload["created_at"])
        return GlobalMetaBlock(**payload)

    def read_runs(self) -> list[RunBlock]:
        return [RunBlock(**item) for item in self.read_block("core_runs")]

    def read_spectra(self) -> list[SpectrumBlock]:
        return [SpectrumBlock(**item) for item in self.read_block("core_spectra")]

    def read_precursors(self) -> list[PrecursorBlock]:
        return [PrecursorBlock(**item) for item in self.read_block("core_precursors")]

    def read_chromatograms(self) -> list[ChromatogramBlock]:
        return [ChromatogramBlock(**item) for item in self.read_block("core_chromatograms")]

    def _v2_arrays_directory(
        self,
        stream: BinaryIO,
        header: ZpHeader,
        fingerprint: _FileFingerprint,
        *,
        require_canonical: bool,
    ) -> tuple[BlockDirectoryEntry, V2ArraysDirectory]:
        entries = self._read_v2_directory(stream, header, fingerprint)
        arrays_entry = self._entry(entries, "arrays")
        if (
            self._v2_arrays_directory_cache is not None
            and self._v2_arrays_directory_cache[0] == fingerprint
            and (not require_canonical or self._v2_arrays_directory_cache[2])
        ):
            return arrays_entry, self._v2_arrays_directory_cache[1]
        directory = self._v2_arrays_reader.read_directory(
            stream,
            block_offset=arrays_entry.offset,
            block_length=arrays_entry.length,
            require_canonical=require_canonical,
        )
        self._v2_arrays_directory_cache = (
            fingerprint,
            directory,
            require_canonical,
        )
        return arrays_entry, directory

    def _read_v2_arrays_by_ids(
        self,
        stream: BinaryIO,
        header: ZpHeader,
        fingerprint: _FileFingerprint,
        array_ids: list[str],
    ) -> list[ArrayBlock]:
        arrays_entry, directory = self._v2_arrays_directory(
            stream,
            header,
            fingerprint,
            require_canonical=False,
        )
        unique_ids = list(dict.fromkeys(array_ids))
        for array_id in unique_ids:
            if array_id not in directory.entries_by_id:
                _v2_fail(
                    "ARRAY_NOT_FOUND",
                    "array_id is not present in the arrays directory",
                    "arrays.directory",
                    array_id=array_id,
                )
        unique_ids.sort(key=lambda item: directory.entries_by_id[item].data_offset)
        decoded = {
            array_id: self._v2_arrays_reader.read_array(
                stream,
                block_offset=arrays_entry.offset,
                directory=directory,
                array_id=array_id,
            )
            for array_id in unique_ids
        }
        return [decoded[array_id] for array_id in array_ids]

    def _verify_v2_arrays_block_checksum(self, stream: BinaryIO, entry: BlockDirectoryEntry) -> None:
        stream.seek(entry.offset)
        remaining = entry.length
        digest = hashlib.sha256()
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                _v2_fail(
                    "ARRAY_PAYLOAD_OUT_OF_BOUNDS",
                    "arrays block is truncated",
                    "blocks.arrays",
                    actual=entry.length - remaining,
                    limit=entry.length,
                )
            digest.update(chunk)
            remaining -= len(chunk)
        actual = digest.hexdigest()
        if actual != entry.checksum:
            _v2_fail(
                "BLOCK_CHECKSUM_MISMATCH",
                "arrays block checksum does not match top directory",
                "blocks.arrays.checksum",
                actual=actual,
            )

    def _read_all_v2_arrays(
        self, stream: BinaryIO, header: ZpHeader, fingerprint: _FileFingerprint
    ) -> list[ArrayBlock]:
        arrays_entry, directory = self._v2_arrays_directory(
            stream,
            header,
            fingerprint,
            require_canonical=True,
        )
        estimated_decoded_memory = (
            directory.payload_length
            + sum(entry.value_count for entry in directory.entries) * 40
            + len(directory.entries) * 256
        )
        budget = self._v2_arrays_reader.limits.max_decoded_memory
        if estimated_decoded_memory > budget:
            _v2_fail(
                "ARRAY_DECODE_BUDGET_EXCEEDED",
                "full arrays decode exceeds the configured memory budget",
                "arrays.estimated_decoded_memory",
                actual=estimated_decoded_memory,
                limit=budget,
            )
        self._verify_v2_arrays_block_checksum(stream, arrays_entry)
        return self._read_v2_arrays_by_ids(
            stream,
            header,
            fingerprint,
            [entry.array_id for entry in directory.entries],
        )

    def read_arrays(self) -> list[ArrayBlock]:
        def operation(stream: BinaryIO, header: ZpHeader, fingerprint: _FileFingerprint) -> list[ArrayBlock]:
            if header.version == ZP_VERSION_V1:
                return [ArrayBlock(**item) for item in self._read_json_block(stream, header, fingerprint, "arrays")]
            return self._read_all_v2_arrays(stream, header, fingerprint)

        return self._with_file(operation)

    def read_array(self, array_id: str) -> ArrayBlock:
        def operation(stream: BinaryIO, header: ZpHeader, fingerprint: _FileFingerprint) -> ArrayBlock:
            if header.version == ZP_VERSION_V1:
                arrays = [ArrayBlock(**item) for item in self._read_json_block(stream, header, fingerprint, "arrays")]
                match = next((item for item in arrays if item.array_id == array_id), None)
                if match is None:
                    raise ZpReadError(f"Unknown array_id: {array_id}")
                return match
            return self._read_v2_arrays_by_ids(stream, header, fingerprint, [array_id])[0]

        return self._with_file(operation)

    def read_indexes(self) -> IndexBlock:
        return IndexBlock(**self.read_block("indexes"))

    def read_extensions(self) -> list[ExtensionBlock]:
        return [ExtensionBlock(**item) for item in self.read_block("extensions")]

    def read_spectrum(self, spectrum_id: str) -> SpectrumBlock:
        spectrum = next((item for item in self.read_spectra() if item.spectrum_id == spectrum_id), None)
        if spectrum is None:
            raise ZpReadError(f"Unknown spectrum_id: {spectrum_id}")
        return spectrum

    def read_spectrum_arrays(self, spectrum_id: str) -> tuple[SpectrumBlock, ArrayBlock, ArrayBlock]:
        def operation(
            stream: BinaryIO, header: ZpHeader, fingerprint: _FileFingerprint
        ) -> tuple[SpectrumBlock, ArrayBlock, ArrayBlock]:
            spectra = [
                SpectrumBlock(**item)
                for item in self._read_json_block(stream, header, fingerprint, "core_spectra")
            ]
            spectrum = next((item for item in spectra if item.spectrum_id == spectrum_id), None)
            if spectrum is None:
                raise ZpReadError(f"Unknown spectrum_id: {spectrum_id}")
            if header.version == ZP_VERSION_V2:
                mz_array, intensity_array = self._read_v2_arrays_by_ids(
                    stream,
                    header,
                    fingerprint,
                    [spectrum.mz_array_id, spectrum.intensity_array_id],
                )
            else:
                arrays = {
                    item.array_id: item
                    for item in (
                        ArrayBlock(**raw)
                        for raw in self._read_json_block(stream, header, fingerprint, "arrays")
                    )
                }
                try:
                    mz_array, intensity_array = arrays[spectrum.mz_array_id], arrays[spectrum.intensity_array_id]
                except KeyError as exc:
                    raise ZpReadError(
                        f"Spectrum {spectrum_id} references a missing array: {exc.args[0]}"
                    ) from exc
            return spectrum, mz_array, intensity_array

        return self._with_file(operation)

    def read_chromatogram_arrays(
        self, chromatogram_id: str
    ) -> tuple[ChromatogramBlock, ArrayBlock, ArrayBlock]:
        def operation(
            stream: BinaryIO, header: ZpHeader, fingerprint: _FileFingerprint
        ) -> tuple[ChromatogramBlock, ArrayBlock, ArrayBlock]:
            chromatograms = [
                ChromatogramBlock(**item)
                for item in self._read_json_block(stream, header, fingerprint, "core_chromatograms")
            ]
            chromatogram = next(
                (item for item in chromatograms if item.chromatogram_id == chromatogram_id), None
            )
            if chromatogram is None:
                raise ZpReadError(f"Unknown chromatogram_id: {chromatogram_id}")
            if header.version == ZP_VERSION_V2:
                time_array, intensity_array = self._read_v2_arrays_by_ids(
                    stream,
                    header,
                    fingerprint,
                    [chromatogram.time_array_id, chromatogram.intensity_array_id],
                )
            else:
                arrays = {
                    item.array_id: item
                    for item in (
                        ArrayBlock(**raw)
                        for raw in self._read_json_block(stream, header, fingerprint, "arrays")
                    )
                }
                try:
                    time_array = arrays[chromatogram.time_array_id]
                    intensity_array = arrays[chromatogram.intensity_array_id]
                except KeyError as exc:
                    raise ZpReadError(
                        f"Chromatogram {chromatogram_id} references a missing array: {exc.args[0]}"
                    ) from exc
            return chromatogram, time_array, intensity_array

        return self._with_file(operation)
