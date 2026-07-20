from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from .constants import (
    BLOCK_NAMES,
    DIRECTORY_LENGTH_STRUCT,
    HEADER_SIZE,
    HEADER_STRUCT,
    ZP_ENDIANNESS_LITTLE,
    ZP_EXTENSION,
    ZP_MAGIC,
    ZP_VERSION_V1,
    ZP_VERSION_V2,
)
from .logical_fingerprint import (
    LogicalArrayFingerprint,
    LogicalFingerprint,
    build_logical_fingerprint,
)
from .models import BlockDirectoryEntry
from .reader import ZpReader
from .serialization import canonical_json_bytes
from .v1_arrays_stream_reader import V1ArraysStreamError, V1ArraysStreamReader
from .v2_arrays_migration_writer import (
    MigratedArraysBlock,
    V2ArraysMigrationWriteError,
    V2ArraysMigrationWriter,
)
from .v2_arrays_reader import ZpV2ArraysReader
from .v2_arrays_writer import DEFAULT_V2_ARRAY_WRITE_LIMITS, ZpV2ArrayWriteLimits
from .validator import ZpValidator


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOP_ENTRY_FIELDS = frozenset({"block_name", "offset", "length", "encoding", "checksum"})
_NON_ARRAY_BLOCKS = tuple(name for name in BLOCK_NAMES if name != "arrays")


class MigrationError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str,
        exit_code: int,
    ) -> None:
        self.code = code
        self.message = message
        self.stage = stage
        self.exit_code = exit_code
        super().__init__(f"{code} at {stage}: {message}")


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "SourceIdentity":
        return cls(
            device=int(value.st_dev),
            inode=int(value.st_ino),
            size=int(value.st_size),
            mtime_ns=int(value.st_mtime_ns),
            ctime_ns=int(value.st_ctime_ns),
        )


@dataclass(frozen=True, slots=True)
class MigrationResult:
    source_path: Path
    target_path: Path
    source_version: int
    target_version: int
    source_size: int
    target_size: int
    source_sha256: str
    target_sha256: str
    source_logical_fingerprint: str
    target_logical_fingerprint: str
    source_checked_blocks: int
    target_checked_blocks: int
    array_count: int
    numeric_value_count: int
    arrays_scan_count: int
    max_live_array_count: int
    max_single_array_value_count: int
    payload_spool_bytes: int
    payload_copy_bytes: int
    source_validation_seconds: float
    conversion_seconds: float
    target_validation_seconds: float
    fingerprint_seconds: float
    total_seconds: float
    source_validator_peak_rss: int
    conversion_peak_rss: int
    target_validator_peak_rss: int
    peak_rss: int
    temporary_disk_peak: int
    disk_free_bytes: int
    disk_required_bytes: int
    created_at: int
    source_identity_before: SourceIdentity
    source_identity_after: SourceIdentity
    validators_serial: bool = True
    simultaneous_full_arrays: bool = False
    tracemalloc_enabled: bool = False

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["source_path"] = str(self.source_path)
        result["target_path"] = str(self.target_path)
        return result


@dataclass(frozen=True, slots=True)
class _SourceLayout:
    created_at: int
    entries: tuple[BlockDirectoryEntry, ...]
    raw_blocks: dict[str, bytes]
    parsed_blocks: dict[str, object]

    @property
    def arrays_entry(self) -> BlockDirectoryEntry:
        return next(item for item in self.entries if item.block_name == "arrays")


class _DuplicateJsonKey(ValueError):
    pass


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _parse_canonical_json(payload: bytes, location: str) -> object:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise MigrationError(
            "INVALID_CANONICAL_JSON",
            f"{location}: {exc}",
            stage="source_layout",
            exit_code=3,
        ) from exc
    try:
        canonical = canonical_json_bytes(value)
    except (TypeError, UnicodeError, ValueError) as exc:
        raise MigrationError(
            "INVALID_CANONICAL_JSON",
            f"{location}: {exc}",
            stage="source_layout",
            exit_code=3,
        ) from exc
    if canonical != payload:
        raise MigrationError(
            "NONCANONICAL_JSON",
            location,
            stage="source_layout",
            exit_code=3,
        )
    return value


def _read_exact(stream: BinaryIO, length: int, *, code: str, stage: str) -> bytes:
    try:
        payload = stream.read(length)
    except OSError as exc:
        exit_code = 3 if stage == "source_layout" else 4
        raise MigrationError(code, str(exc), stage=stage, exit_code=exit_code) from exc
    if len(payload) != length:
        raise MigrationError(
            code,
            f"expected {length} bytes, read {len(payload)}",
            stage=stage,
            exit_code=3,
        )
    return payload


def _read_source_layout(stream: BinaryIO, *, file_size: int) -> _SourceLayout:
    stream.seek(0)
    raw_header = _read_exact(stream, HEADER_SIZE, code="TRUNCATED_HEADER", stage="source_layout")
    try:
        magic, version, endianness, flags, created_at, directory_offset = HEADER_STRUCT.unpack(raw_header)
    except Exception as exc:
        raise MigrationError("INVALID_HEADER", str(exc), stage="source_layout", exit_code=3) from exc
    if magic != ZP_MAGIC or version != ZP_VERSION_V1 or endianness != ZP_ENDIANNESS_LITTLE or flags != 0:
        raise MigrationError(
            "INVALID_V1_HEADER",
            "source Header is not the frozen v1 identity",
            stage="source_layout",
            exit_code=3,
        )
    if directory_offset < HEADER_SIZE or directory_offset + DIRECTORY_LENGTH_STRUCT.size > file_size:
        raise MigrationError(
            "INVALID_DIRECTORY_OFFSET",
            "source directory_offset is outside the file",
            stage="source_layout",
            exit_code=3,
        )
    stream.seek(directory_offset)
    directory_length = DIRECTORY_LENGTH_STRUCT.unpack(
        _read_exact(
            stream,
            DIRECTORY_LENGTH_STRUCT.size,
            code="TRUNCATED_DIRECTORY_LENGTH",
            stage="source_layout",
        )
    )[0]
    if directory_offset + DIRECTORY_LENGTH_STRUCT.size + directory_length != file_size:
        raise MigrationError(
            "INVALID_DIRECTORY_LENGTH",
            "source directory must end exactly at EOF",
            stage="source_layout",
            exit_code=3,
        )
    raw_directory = _read_exact(
        stream,
        directory_length,
        code="TRUNCATED_DIRECTORY",
        stage="source_layout",
    )
    parsed_directory = _parse_canonical_json(raw_directory, "directory")
    if not isinstance(parsed_directory, list) or len(parsed_directory) != len(BLOCK_NAMES):
        raise MigrationError(
            "INVALID_DIRECTORY_SCHEMA",
            "source directory must contain exactly nine entries",
            stage="source_layout",
            exit_code=3,
        )
    entries: list[BlockDirectoryEntry] = []
    previous_end = HEADER_SIZE
    for position, expected_name in enumerate(BLOCK_NAMES):
        raw_entry = parsed_directory[position]
        if not isinstance(raw_entry, dict) or frozenset(raw_entry) != _TOP_ENTRY_FIELDS:
            raise MigrationError(
                "INVALID_DIRECTORY_SCHEMA",
                f"directory[{position}] has an invalid field set",
                stage="source_layout",
                exit_code=3,
            )
        if raw_entry["block_name"] != expected_name:
            raise MigrationError(
                "INVALID_DIRECTORY_ORDER",
                f"directory[{position}] is not {expected_name}",
                stage="source_layout",
                exit_code=3,
            )
        offset = raw_entry["offset"]
        length = raw_entry["length"]
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or isinstance(length, bool)
            or not isinstance(length, int)
            or length < 0
            or offset != previous_end
            or offset + length > directory_offset
        ):
            raise MigrationError(
                "INVALID_BLOCK_RANGE",
                f"directory[{position}] is not contiguous or in bounds",
                stage="source_layout",
                exit_code=3,
            )
        if raw_entry["encoding"] != "json":
            raise MigrationError(
                "SOURCE_ENCODING_NOT_V1",
                f"{expected_name} encoding must be json",
                stage="source_layout",
                exit_code=3,
            )
        checksum = raw_entry["checksum"]
        if not isinstance(checksum, str) or _SHA256_RE.fullmatch(checksum) is None:
            raise MigrationError(
                "INVALID_BLOCK_CHECKSUM",
                f"{expected_name} checksum is malformed",
                stage="source_layout",
                exit_code=3,
            )
        entries.append(BlockDirectoryEntry(expected_name, offset, length, "json", checksum))
        previous_end = offset + length
    if previous_end != directory_offset:
        raise MigrationError(
            "INVALID_BLOCK_RANGE",
            "last source block must end at the directory",
            stage="source_layout",
            exit_code=3,
        )
    raw_blocks: dict[str, bytes] = {}
    parsed_blocks: dict[str, object] = {}
    for entry in entries:
        if entry.block_name == "arrays":
            continue
        stream.seek(entry.offset)
        payload = _read_exact(
            stream,
            entry.length,
            code="TRUNCATED_BLOCK",
            stage="source_layout",
        )
        if hashlib.sha256(payload).hexdigest() != entry.checksum:
            raise MigrationError(
                "BLOCK_CHECKSUM_MISMATCH",
                entry.block_name,
                stage="source_layout",
                exit_code=3,
            )
        raw_blocks[entry.block_name] = payload
        parsed_blocks[entry.block_name] = _parse_canonical_json(payload, entry.block_name)
    global_meta = parsed_blocks.get("global_meta")
    if not isinstance(global_meta, dict) or global_meta.get("format_version") != ZP_VERSION_V1:
        raise MigrationError(
            "GLOBAL_META_VERSION_MISMATCH",
            "global_meta.format_version must be 1",
            stage="source_layout",
            exit_code=3,
        )
    return _SourceLayout(created_at, tuple(entries), raw_blocks, parsed_blocks)


def _write_exact_output(stream: BinaryIO, payload: bytes) -> None:
    written = stream.write(payload)
    if written != len(payload):
        raise V2ArraysMigrationWriteError(
            "SHORT_WRITE",
            f"expected {len(payload)} bytes but wrote {written}",
        )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> SourceIdentity:
    return SourceIdentity.from_stat(path.stat())


def _same_open_file_identity(left: SourceIdentity, right: SourceIdentity) -> bool:
    return (
        left.device,
        left.inode,
        left.size,
        left.mtime_ns,
    ) == (
        right.device,
        right.inode,
        right.size,
        right.mtime_ns,
    )


def _normalized_path(path: Path, *, strict: bool) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path.resolve(strict=strict))))


def _preflight_paths(source: Path, target: Path) -> tuple[Path, Path]:
    if source.suffix != ZP_EXTENSION or target.suffix != ZP_EXTENSION:
        raise MigrationError(
            "INVALID_EXTENSION",
            "input and output extensions must both be exactly .zp",
            stage="path_preflight",
            exit_code=2,
        )
    if not source.exists():
        raise MigrationError("SOURCE_NOT_FOUND", str(source), stage="path_preflight", exit_code=2)
    if source.is_symlink():
        raise MigrationError(
            "SOURCE_SYMLINK_NOT_ALLOWED",
            str(source),
            stage="path_preflight",
            exit_code=2,
        )
    if not source.is_file():
        raise MigrationError(
            "SOURCE_NOT_REGULAR_FILE",
            str(source),
            stage="path_preflight",
            exit_code=2,
        )
    source_resolved = source.resolve(strict=True)
    target_absolute = Path(os.path.abspath(os.fspath(target)))
    if _normalized_path(source_resolved, strict=True) == _normalized_path(target_absolute, strict=False):
        raise MigrationError(
            "SOURCE_DESTINATION_ALIAS",
            "source and destination resolve to the same path",
            stage="path_preflight",
            exit_code=2,
        )
    if target_absolute.exists():
        try:
            if os.path.samefile(source_resolved, target_absolute):
                raise MigrationError(
                    "SOURCE_DESTINATION_ALIAS",
                    "source and destination identify the same file",
                    stage="path_preflight",
                    exit_code=2,
                )
        except OSError:
            pass
        raise MigrationError(
            "DESTINATION_EXISTS",
            str(target_absolute),
            stage="path_preflight",
            exit_code=2,
        )
    parent = target_absolute.parent
    if not parent.exists() or not parent.is_dir():
        raise MigrationError(
            "DESTINATION_PARENT_INVALID",
            str(parent),
            stage="path_preflight",
            exit_code=2,
        )
    return source_resolved, target_absolute


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _working_set() -> int:
    if os.name == "nt":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_ProcessMemoryCounters),
                ctypes.c_ulong,
            ]
            psapi.GetProcessMemoryInfo.restype = ctypes.c_int
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(),
                ctypes.byref(counters),
                counters.cb,
            ):
                return int(counters.WorkingSetSize)
        except (AttributeError, ctypes.ArgumentError, OSError, ValueError):
            return 0
    if sys.platform.startswith("linux"):
        try:
            pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
            return pages * int(os.sysconf("SC_PAGE_SIZE"))
        except (IndexError, OSError, ValueError):
            return 0
    try:
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return peak if sys.platform == "darwin" else peak * 1024
    except (ImportError, OSError, ValueError):
        return 0


class _RssSampler:
    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.02):
            self.peak = max(self.peak, _working_set())

    def __enter__(self) -> "_RssSampler":
        self.peak = _working_set()
        self._thread.start()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, _working_set())


def _fault_point(_stage: str) -> None:
    """Test seam for deterministic failure injection; production is a no-op."""


def _new_sibling_temp(target: Path, label: str, suffix: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.{label}-",
        suffix=suffix,
        dir=target.parent,
    )
    os.close(descriptor)
    return Path(name)


def _cleanup(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _target_fingerprint(path: Path) -> LogicalFingerprint:
    reader = ZpReader(path)
    directory = reader.read_directory()
    entries = {item.block_name: item for item in directory}
    blocks = {name: reader.read_block(name) for name in _NON_ARRAY_BLOCKS}
    arrays_entry = entries["arrays"]
    with path.open("rb") as stream:
        arrays_directory = ZpV2ArraysReader().read_directory(
            stream,
            block_offset=arrays_entry.offset,
            block_length=arrays_entry.length,
        )
    arrays = (
        LogicalArrayFingerprint(
            array_id=item.array_id,
            array_type=item.array_type,
            value_count=item.value_count,
            logical_sha256=item.checksum,
        )
        for item in arrays_directory.entries
    )
    return build_logical_fingerprint(blocks, arrays)


def _disk_budget(source_size: int, arrays_json_size: int, parent: Path) -> tuple[int, int]:
    free = int(shutil.disk_usage(parent).free)
    margin = max(256 * 1024 * 1024, source_size // 10)
    required = arrays_json_size + source_size * 2 + margin
    if free < required:
        raise MigrationError(
            "INSUFFICIENT_DISK_SPACE",
            f"free={free}, required={required}",
            stage="disk_preflight",
            exit_code=5,
        )
    return free, required


def migrate_v1_to_v2(
    source: str | Path,
    target: str | Path,
    *,
    v2_limits: ZpV2ArrayWriteLimits | None = None,
    arrays_chunk_size: int = 256 * 1024,
) -> MigrationResult:
    started = time.perf_counter()
    source_path, target_path = _preflight_paths(Path(source), Path(target))
    limits = v2_limits or DEFAULT_V2_ARRAY_WRITE_LIMITS
    temporary_paths: list[Path] = []
    committed = False
    stage = "source_identity"
    try:
        with _RssSampler() as overall_rss:
            identity_before = _identity(source_path)
            source_sha256 = _hash_file(source_path)

            stage = "source_validation"
            before = time.perf_counter()
            with _RssSampler() as source_rss:
                source_validation = ZpValidator().validate(source_path)
            source_validation_seconds = time.perf_counter() - before
            if not source_validation.valid:
                codes = [item.code for item in source_validation.issues]
                raise MigrationError(
                    "SOURCE_VALIDATION_FAILED",
                    f"source validator rejected the file: {codes}",
                    stage=stage,
                    exit_code=3,
                )
            if source_validation.version != ZP_VERSION_V1:
                raise MigrationError(
                    "SOURCE_VERSION_NOT_V1",
                    f"source version is {source_validation.version}",
                    stage=stage,
                    exit_code=3,
                )
            if source_validation.checked_blocks != len(BLOCK_NAMES):
                raise MigrationError(
                    "SOURCE_VALIDATION_INCOMPLETE",
                    f"checked_blocks={source_validation.checked_blocks}",
                    stage=stage,
                    exit_code=3,
                )
            gc.collect()
            if _identity(source_path) != identity_before:
                raise MigrationError(
                    "SOURCE_CHANGED",
                    "source identity changed after validation",
                    stage=stage,
                    exit_code=8,
                )
            _fault_point("after_source_validation")

            stage = "conversion"
            overall_rss.peak = _working_set()
            conversion_started = time.perf_counter()
            with source_path.open("rb") as source_stream:
                handle_identity_before = SourceIdentity.from_stat(os.fstat(source_stream.fileno()))
                if not _same_open_file_identity(handle_identity_before, identity_before):
                    raise MigrationError(
                        "SOURCE_CHANGED",
                        "opened source handle does not match the preflight identity",
                        stage=stage,
                        exit_code=8,
                    )
                layout = _read_source_layout(source_stream, file_size=identity_before.size)
                disk_free_bytes, disk_required_bytes = _disk_budget(
                    identity_before.size,
                    layout.arrays_entry.length,
                    target_path.parent,
                )
                temp_path = _new_sibling_temp(target_path, "migrating", ".tmp")
                spool_path = _new_sibling_temp(target_path, "payload", ".tmp")
                temporary_paths.extend((temp_path, spool_path))
                _fault_point("after_temp_create")
                with spool_path.open("w+b") as spool, temp_path.open("w+b") as output:
                    _write_exact_output(output, b"\0" * HEADER_SIZE)
                    entries: list[BlockDirectoryEntry] = []
                    arrays_result: MigratedArraysBlock | None = None
                    spool_writer = V2ArraysMigrationWriter(spool, limits=limits)
                    source_arrays: list[LogicalArrayFingerprint] = []
                    for block_name in BLOCK_NAMES:
                        offset = output.tell()
                        if block_name == "arrays":
                            _fault_point("before_arrays_scan")
                            arrays_reader = V1ArraysStreamReader(
                                source_stream,
                                block_offset=layout.arrays_entry.offset,
                                block_length=layout.arrays_entry.length,
                                expected_checksum=layout.arrays_entry.checksum,
                                chunk_size=arrays_chunk_size,
                                limits=limits,
                            )
                            for item in arrays_reader.iter_arrays():
                                spooled = spool_writer.add(item)
                                source_arrays.append(
                                    LogicalArrayFingerprint(
                                        array_id=spooled.array_id,
                                        array_type=spooled.array_type,
                                        value_count=spooled.value_count,
                                        logical_sha256=spooled.checksum,
                                    )
                                )
                            spool_writer.flush_spool()
                            _fault_point("after_arrays_scan")
                            arrays_result = spool_writer.write_block(output)
                            _fault_point("after_arrays_write")
                            length = arrays_result.block_length
                            checksum = arrays_result.checksum
                            encoding = "zp-arrays-v2"
                        else:
                            if block_name == "global_meta":
                                meta = dict(layout.parsed_blocks[block_name])
                                meta["format_version"] = ZP_VERSION_V2
                                payload = canonical_json_bytes(meta)
                                _fault_point("after_global_meta")
                            else:
                                payload = layout.raw_blocks[block_name]
                            _write_exact_output(output, payload)
                            length = len(payload)
                            checksum = hashlib.sha256(payload).hexdigest()
                            encoding = "utf-8-json"
                        entries.append(
                            BlockDirectoryEntry(
                                block_name=block_name,
                                offset=offset,
                                length=length,
                                encoding=encoding,
                                checksum=checksum,
                            )
                        )
                    if arrays_result is None:
                        raise MigrationError(
                            "MISSING_ARRAYS_RESULT",
                            "arrays block was not written",
                            stage=stage,
                            exit_code=4,
                        )
                    directory_offset = output.tell()
                    directory_payload = canonical_json_bytes(entries)
                    _write_exact_output(output, DIRECTORY_LENGTH_STRUCT.pack(len(directory_payload)))
                    _write_exact_output(output, directory_payload)
                    _fault_point("after_top_directory")
                    output.seek(0)
                    _write_exact_output(
                        output,
                        HEADER_STRUCT.pack(
                            ZP_MAGIC,
                            ZP_VERSION_V2,
                            ZP_ENDIANNESS_LITTLE,
                            0,
                            layout.created_at,
                            directory_offset,
                        ),
                    )
                    output.flush()
                    os.fsync(output.fileno())
                    _fault_point("after_temp_fsync")
                handle_identity_after = SourceIdentity.from_stat(os.fstat(source_stream.fileno()))
                if not _same_open_file_identity(handle_identity_after, handle_identity_before):
                    raise MigrationError(
                        "SOURCE_CHANGED",
                        "source handle identity changed during conversion",
                        stage=stage,
                        exit_code=8,
                    )
            conversion_seconds = time.perf_counter() - conversion_started
            conversion_peak_rss = max(overall_rss.peak, _working_set())
            temporary_disk_peak = spool_path.stat().st_size + temp_path.stat().st_size

            stage = "target_validation"
            validation_alias = target_path.with_name(
                f".{target_path.name}.validating-{uuid.uuid4().hex}.zp"
            )
            temporary_paths.append(validation_alias)
            os.replace(temp_path, validation_alias)
            before = time.perf_counter()
            try:
                with _RssSampler() as target_rss:
                    target_validation = ZpValidator().validate(validation_alias)
            finally:
                if validation_alias.exists():
                    os.replace(validation_alias, temp_path)
            target_validation_seconds = time.perf_counter() - before
            if not target_validation.valid or target_validation.version != ZP_VERSION_V2:
                codes = [item.code for item in target_validation.issues]
                raise MigrationError(
                    "TARGET_VALIDATION_FAILED",
                    f"target validator rejected the file: {codes}",
                    stage=stage,
                    exit_code=6,
                )
            if target_validation.checked_blocks != len(BLOCK_NAMES):
                raise MigrationError(
                    "TARGET_VALIDATION_INCOMPLETE",
                    f"checked_blocks={target_validation.checked_blocks}",
                    stage=stage,
                    exit_code=6,
                )
            _fault_point("after_target_validation")

            stage = "fingerprint"
            before = time.perf_counter()
            source_fingerprint = build_logical_fingerprint(layout.parsed_blocks, source_arrays)
            target_fingerprint = _target_fingerprint(temp_path)
            fingerprint_seconds = time.perf_counter() - before
            if source_fingerprint.sha256 != target_fingerprint.sha256:
                raise MigrationError(
                    "LOGICAL_FINGERPRINT_MISMATCH",
                    (
                        f"source={source_fingerprint.sha256}, "
                        f"target={target_fingerprint.sha256}"
                    ),
                    stage=stage,
                    exit_code=7,
                )
            _fault_point("after_fingerprint")

            stage = "source_recheck"
            identity_after = _identity(source_path)
            source_sha256_after = _hash_file(source_path)
            if identity_after != identity_before or source_sha256_after != source_sha256:
                raise MigrationError(
                    "SOURCE_CHANGED",
                    "source identity or SHA-256 changed before commit",
                    stage=stage,
                    exit_code=8,
                )
            target_size = temp_path.stat().st_size
            target_sha256 = _hash_file(temp_path)

            stage = "commit"
            _fault_point("before_commit")
            if target_path.exists():
                raise MigrationError(
                    "DESTINATION_APPEARED",
                    str(target_path),
                    stage=stage,
                    exit_code=8,
                )
            os.replace(temp_path, target_path)
            committed = True
            _fsync_directory(target_path.parent)

        total_seconds = time.perf_counter() - started
        return MigrationResult(
            source_path=source_path,
            target_path=target_path,
            source_version=ZP_VERSION_V1,
            target_version=ZP_VERSION_V2,
            source_size=identity_before.size,
            target_size=target_size,
            source_sha256=source_sha256,
            target_sha256=target_sha256,
            source_logical_fingerprint=source_fingerprint.sha256,
            target_logical_fingerprint=target_fingerprint.sha256,
            source_checked_blocks=source_validation.checked_blocks,
            target_checked_blocks=target_validation.checked_blocks,
            array_count=source_fingerprint.array_count,
            numeric_value_count=source_fingerprint.numeric_value_count,
            arrays_scan_count=arrays_reader.metrics.scan_count,
            max_live_array_count=arrays_reader.metrics.max_live_array_count,
            max_single_array_value_count=arrays_reader.metrics.max_single_array_value_count,
            payload_spool_bytes=arrays_result.payload_spool_bytes,
            payload_copy_bytes=arrays_result.payload_copy_bytes,
            source_validation_seconds=round(source_validation_seconds, 6),
            conversion_seconds=round(conversion_seconds, 6),
            target_validation_seconds=round(target_validation_seconds, 6),
            fingerprint_seconds=round(fingerprint_seconds, 6),
            total_seconds=round(total_seconds, 6),
            source_validator_peak_rss=source_rss.peak,
            conversion_peak_rss=conversion_peak_rss,
            target_validator_peak_rss=target_rss.peak,
            peak_rss=max(
                overall_rss.peak,
                source_rss.peak,
                conversion_peak_rss,
                target_rss.peak,
            ),
            temporary_disk_peak=temporary_disk_peak,
            disk_free_bytes=disk_free_bytes,
            disk_required_bytes=disk_required_bytes,
            created_at=layout.created_at,
            source_identity_before=identity_before,
            source_identity_after=identity_after,
        )
    except MigrationError:
        raise
    except KeyboardInterrupt as exc:
        raise MigrationError(
            "MIGRATION_INTERRUPTED",
            "migration was interrupted",
            stage=stage,
            exit_code=5,
        ) from exc
    except (OSError, V1ArraysStreamError, V2ArraysMigrationWriteError, ValueError) as exc:
        code = getattr(exc, "code", "MIGRATION_IO_ERROR")
        exit_code = {
            "source_identity": 3,
            "source_validation": 3,
            "source_layout": 3,
            "conversion": 4,
            "target_validation": 6,
            "fingerprint": 7,
            "source_recheck": 8,
            "commit": 8,
        }.get(stage, 4)
        raise MigrationError(code, str(exc), stage=stage, exit_code=exit_code) from exc
    finally:
        _cleanup(temporary_paths)


def _error_report(error: MigrationError) -> dict[str, object]:
    return {
        "success": False,
        "error_code": error.code,
        "error_message": error.message,
        "stage": error.stage,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely migrate one validated ZP v1 file to ZP v2")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = migrate_v1_to_v2(args.input, args.output)
    except MigrationError as exc:
        if not args.quiet:
            report = _error_report(exc)
            if args.json_output:
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            else:
                print(
                    f"migration failed: {exc.code} ({exc.stage}): {exc.message}",
                    file=sys.stderr,
                )
        return exc.exit_code
    except Exception as exc:
        if not args.quiet:
            report = {
                "success": False,
                "error_code": "UNEXPECTED_MIGRATION_ERROR",
                "error_message": str(exc),
                "stage": "cli",
            }
            if args.json_output:
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            else:
                print(
                    f"migration failed: UNEXPECTED_MIGRATION_ERROR: {exc}",
                    file=sys.stderr,
                )
        return 4
    if not args.quiet:
        report = {"success": True, **result.as_dict()}
        if args.json_output:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            print(f"migrated {result.source_path} -> {result.target_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
