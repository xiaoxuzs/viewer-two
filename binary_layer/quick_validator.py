from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    BLOCK_NAMES,
    DIRECTORY_LENGTH_STRUCT,
    HEADER_SIZE,
    HEADER_STRUCT,
    SUPPORTED_ZP_VALIDATE_VERSIONS,
    ZP_ENDIANNESS_LITTLE,
    ZP_EXTENSION,
    ZP_MAGIC,
    ZP_VERSION_V1,
    ZP_VERSION_V2,
)
from .models import BlockDirectoryEntry, ValidationIssue, ValidationResult
from .serialization import canonical_json_bytes


VALIDATOR_CONTRACT_VERSION = "p2-c2.1-v1"
CERTIFICATE_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOP_ENTRY_FIELDS = frozenset(
    {"block_name", "offset", "length", "encoding", "checksum"}
)
_MAX_DIRECTORY_LENGTH = 64 * 1024 * 1024
_HASH_CHUNK_SIZE = 8 * 1024 * 1024


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _QuickEvidence:
    version: int | None
    file_size: int
    file_sha256: str | None
    directory_checksum: str | None
    entries: tuple[BlockDirectoryEntry, ...]
    issues: tuple[ValidationIssue, ...]
    checked_blocks: int
    bytes_read: int
    loop_count: int
    wall_seconds: float
    cpu_seconds: float


def default_certificate_path(path: str | Path) -> Path:
    zp_path = Path(path)
    return zp_path.with_name(zp_path.name + ".deep-validation.json")


def validate_quick(
    path: str | Path,
    *,
    certificate_path: str | Path | None = None,
) -> ValidationResult:
    zp_path = Path(path)
    explicit_certificate = certificate_path is not None
    certificate = (
        Path(certificate_path)
        if certificate_path is not None
        else default_certificate_path(zp_path)
    )
    evidence = _collect_quick_evidence(zp_path)
    issues = list(evidence.issues)
    certificate_valid: bool | None = None
    reused = False
    summary: dict[str, object] = {}
    top_down_valid: bool | None = None
    bottom_up_valid: bool | None = None

    if not issues and (explicit_certificate or certificate.exists()):
        certificate_valid, certificate_issues, payload = _check_certificate(
            certificate,
            evidence,
        )
        issues.extend(certificate_issues)
        if certificate_valid and payload is not None:
            reused = True
            deep_result = payload["deep_validation_result"]
            top_down_valid = deep_result["top_down_valid"]
            bottom_up_valid = deep_result["bottom_up_valid"]
            summary = {
                "entity_counts": dict(payload["entity_counts"]),
                "array_count": payload["array_count"],
                "array_value_count": payload["array_value_count"],
                "bottom_up_schema_version": payload["bottom_up_schema_version"],
            }

    metrics: dict[str, object] = {
        "wall_seconds": evidence.wall_seconds,
        "cpu_seconds": evidence.cpu_seconds,
        "bytes_read": evidence.bytes_read,
        "bytes_written": 0,
        "loop_count": evidence.loop_count,
        "file_size": evidence.file_size,
        "hash_chunk_size": _HASH_CHUNK_SIZE,
        "extension_json_parsed": False,
        "array_values_visited": 0,
    }
    return ValidationResult(
        valid=not issues,
        issues=issues,
        checked_blocks=evidence.checked_blocks,
        file_path=zp_path,
        version=evidence.version,
        top_down_valid=top_down_valid,
        bottom_up_valid=bottom_up_valid,
        mode="quick",
        file_sha256=evidence.file_sha256,
        certificate_valid=certificate_valid,
        deep_validation_reused=reused,
        certificate_summary=summary,
        metrics=metrics,
    )


def write_deep_validation_certificate(
    path: str | Path,
    validation: ValidationResult,
    *,
    certificate_path: str | Path | None = None,
) -> Path:
    zp_path = Path(path)
    if not validation.valid or validation.mode != "deep":
        raise ValueError("A successful deep validation result is required")
    evidence = _collect_quick_evidence(zp_path)
    if evidence.issues or evidence.file_sha256 is None or evidence.directory_checksum is None:
        codes = ", ".join(item.code for item in evidence.issues)
        raise ValueError(f"Quick physical evidence failed after deep validation: {codes}")

    entity_counts = validation.metrics.get("entity_counts", {})
    if not isinstance(entity_counts, dict):
        entity_counts = {}
    array_count = validation.metrics.get("entry_count")
    array_value_count = validation.metrics.get("numeric_value_count")
    if not isinstance(array_count, int) or not isinstance(array_value_count, int):
        from .reader import ZpReader

        arrays = ZpReader(zp_path).read_arrays()
        array_count = len(arrays)
        array_value_count = sum(len(item.values) for item in arrays)

    payload: dict[str, object] = {
        "certificate_schema_version": CERTIFICATE_SCHEMA_VERSION,
        "validator_version": VALIDATOR_CONTRACT_VERSION,
        "zp_file_sha256": evidence.file_sha256,
        "file_size": evidence.file_size,
        "format_version": evidence.version,
        "directory_checksum": evidence.directory_checksum,
        "block_checksums": {
            entry.block_name: entry.checksum for entry in evidence.entries
        },
        "bottom_up_schema_version": 1 if validation.bottom_up_valid is not None else None,
        "entity_counts": dict(entity_counts),
        "array_count": array_count,
        "array_value_count": array_value_count,
        "deep_validation_result": {
            "physical_valid": validation.valid,
            "top_down_valid": validation.top_down_valid,
            "bottom_up_valid": validation.bottom_up_valid,
        },
    }
    target = (
        Path(certificate_path)
        if certificate_path is not None
        else default_certificate_path(zp_path)
    )
    temporary = target.with_name(target.name + ".tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = canonical_json_bytes(payload) + b"\n"
        with temporary.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def _collect_quick_evidence(path: Path) -> _QuickEvidence:
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    issues: list[ValidationIssue] = []
    version: int | None = None
    file_size = 0
    directory_checksum: str | None = None
    entries: tuple[BlockDirectoryEntry, ...] = ()
    checked_blocks = 0
    bytes_read = 0
    loop_count = 0

    def add(code: str, message: str, block_name: str | None = None) -> None:
        issues.append(ValidationIssue(code, message, "error", block_name))

    if path.suffix != ZP_EXTENSION:
        add("INVALID_EXTENSION", f"File extension must be exactly {ZP_EXTENSION}")
    if not path.exists():
        add("FILE_NOT_FOUND", f"File does not exist: {path}")
        return _evidence_result(
            version, file_size, None, directory_checksum, entries, issues,
            checked_blocks, bytes_read, loop_count, wall_started, cpu_started,
        )
    if not path.is_file():
        add("NOT_REGULAR_FILE", f"Path is not a regular file: {path}")
        return _evidence_result(
            version, file_size, None, directory_checksum, entries, issues,
            checked_blocks, bytes_read, loop_count, wall_started, cpu_started,
        )

    try:
        stat_before = path.stat()
        file_size = stat_before.st_size
        if file_size < HEADER_SIZE:
            add("FILE_TOO_SMALL", f"File is smaller than {HEADER_SIZE}-byte header")
            return _evidence_result(
                version, file_size, None, directory_checksum, entries, issues,
                checked_blocks, bytes_read, loop_count, wall_started, cpu_started,
            )
        with path.open("rb") as stream:
            header_raw = stream.read(HEADER_SIZE)
            bytes_read += len(header_raw)
            magic, version, endianness, flags, _created_at, directory_offset = (
                HEADER_STRUCT.unpack(header_raw)
            )
            if magic != ZP_MAGIC:
                add("INVALID_MAGIC", f"Expected {ZP_MAGIC!r}, got {magic!r}")
            if version not in SUPPORTED_ZP_VALIDATE_VERSIONS:
                add("UNSUPPORTED_VERSION", f"Unsupported version: {version}")
            if endianness != ZP_ENDIANNESS_LITTLE:
                add("UNSUPPORTED_ENDIANNESS", f"Unsupported endianness: {endianness}")
            if flags != 0:
                add("UNSUPPORTED_TOP_LEVEL_FLAGS", "Header flags must be zero", "header.flags")
            if issues:
                return _evidence_result(
                    version, file_size, None, directory_checksum, entries, issues,
                    checked_blocks, bytes_read, loop_count, wall_started, cpu_started,
                )
            if (
                directory_offset < HEADER_SIZE
                or directory_offset + DIRECTORY_LENGTH_STRUCT.size > file_size
            ):
                add("INVALID_DIRECTORY_OFFSET", "Directory offset is outside the file")
                return _evidence_result(
                    version, file_size, None, directory_checksum, entries, issues,
                    checked_blocks, bytes_read, loop_count, wall_started, cpu_started,
                )
            stream.seek(directory_offset)
            length_raw = stream.read(DIRECTORY_LENGTH_STRUCT.size)
            bytes_read += len(length_raw)
            if len(length_raw) != DIRECTORY_LENGTH_STRUCT.size:
                add("TRUNCATED_DIRECTORY_LENGTH", "Directory length is truncated")
                return _evidence_result(
                    version, file_size, None, directory_checksum, entries, issues,
                    checked_blocks, bytes_read, loop_count, wall_started, cpu_started,
                )
            directory_length = DIRECTORY_LENGTH_STRUCT.unpack(length_raw)[0]
            if directory_length > _MAX_DIRECTORY_LENGTH:
                add("TOP_DIRECTORY_TOO_LARGE", "Directory exceeds the quick-validation limit")
                return _evidence_result(
                    version, file_size, None, directory_checksum, entries, issues,
                    checked_blocks, bytes_read, loop_count, wall_started, cpu_started,
                )
            if directory_offset + DIRECTORY_LENGTH_STRUCT.size + directory_length != file_size:
                add("INVALID_DIRECTORY_LENGTH", "Directory must end exactly at EOF")
                return _evidence_result(
                    version, file_size, None, directory_checksum, entries, issues,
                    checked_blocks, bytes_read, loop_count, wall_started, cpu_started,
                )
            directory_raw = stream.read(directory_length)
            bytes_read += len(directory_raw)
            if len(directory_raw) != directory_length:
                add("TRUNCATED_DIRECTORY", "Directory JSON is truncated")
                return _evidence_result(
                    version, file_size, None, directory_checksum, entries, issues,
                    checked_blocks, bytes_read, loop_count, wall_started, cpu_started,
                )
            directory_checksum = hashlib.sha256(directory_raw).hexdigest()
            parsed = _parse_directory(directory_raw, version, add)
            if parsed is not None:
                entries = _validate_directory_entries(
                    parsed,
                    version,
                    directory_offset,
                    add,
                )
    except (OSError, struct.error) as exc:
        add("FILE_READ_ERROR", str(exc))

    file_sha256: str | None = None
    if not issues and len(entries) == len(BLOCK_NAMES):
        try:
            file_digest = hashlib.sha256()
            block_digests = {
                entry.block_name: hashlib.sha256() for entry in entries
            }
            with path.open("rb") as stream:
                absolute_offset = 0
                while True:
                    chunk = stream.read(_HASH_CHUNK_SIZE)
                    if not chunk:
                        break
                    loop_count += 1
                    bytes_read += len(chunk)
                    file_digest.update(chunk)
                    chunk_end = absolute_offset + len(chunk)
                    view = memoryview(chunk)
                    for entry in entries:
                        start = max(absolute_offset, entry.offset)
                        end = min(chunk_end, entry.offset + entry.length)
                        if start < end:
                            block_digests[entry.block_name].update(
                                view[start - absolute_offset : end - absolute_offset]
                            )
                    absolute_offset = chunk_end
            if absolute_offset != file_size:
                add("FILE_CHANGED_DURING_VALIDATION", "File length changed while hashing")
            else:
                file_sha256 = file_digest.hexdigest()
                for entry in entries:
                    checked_blocks += 1
                    actual = block_digests[entry.block_name].hexdigest()
                    if actual != entry.checksum:
                        add(
                            "BLOCK_CHECKSUM_MISMATCH",
                            "Block checksum does not match raw stored bytes",
                            entry.block_name,
                        )
            stat_after = path.stat()
            if (
                stat_after.st_size != stat_before.st_size
                or stat_after.st_mtime_ns != stat_before.st_mtime_ns
                or stat_after.st_ctime_ns != stat_before.st_ctime_ns
            ):
                add("FILE_CHANGED_DURING_VALIDATION", "File identity changed during validation")
        except OSError as exc:
            add("FILE_READ_ERROR", str(exc))

    return _evidence_result(
        version, file_size, file_sha256, directory_checksum, entries, issues,
        checked_blocks, bytes_read, loop_count, wall_started, cpu_started,
    )


def _parse_directory(
    raw: bytes,
    version: int,
    add: Any,
) -> list[object] | None:
    def pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJsonKey(key)
            result[key] = value
        return result

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs_without_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, _DuplicateJsonKey) as exc:
        add("INVALID_DIRECTORY_JSON", str(exc), "directory")
        return None
    if not isinstance(parsed, list):
        add("INVALID_DIRECTORY_SCHEMA", "Directory JSON must be a list", "directory")
        return None
    if version == ZP_VERSION_V2:
        try:
            canonical = canonical_json_bytes(parsed)
        except (TypeError, ValueError) as exc:
            add("INVALID_DIRECTORY_SCHEMA", str(exc), "directory")
            return None
        if canonical != raw:
            add("NONCANONICAL_TOP_DIRECTORY", "v2 directory JSON is not canonical", "directory")
            return None
    return parsed


def _validate_directory_entries(
    parsed: list[object],
    version: int,
    directory_offset: int,
    add: Any,
) -> tuple[BlockDirectoryEntry, ...]:
    entries: list[BlockDirectoryEntry] = []
    for position, item in enumerate(parsed):
        if not isinstance(item, dict) or frozenset(item) != _TOP_ENTRY_FIELDS:
            add("INVALID_DIRECTORY_ENTRY", f"Entry {position} has an invalid field set", "directory")
            continue
        if (
            not isinstance(item["block_name"], str)
            or not _plain_nonnegative_int(item["offset"])
            or not _plain_nonnegative_int(item["length"])
            or not isinstance(item["encoding"], str)
            or not isinstance(item["checksum"], str)
        ):
            add("INVALID_DIRECTORY_ENTRY", f"Entry {position} has invalid field types", "directory")
            continue
        entries.append(BlockDirectoryEntry(**item))

    names = tuple(entry.block_name for entry in entries)
    if names != BLOCK_NAMES:
        add("INVALID_BLOCK_ORDER", "Directory must contain the nine blocks in canonical order", "directory")
    expected_encodings = {
        name: (
            "json"
            if version == ZP_VERSION_V1
            else "zp-arrays-v2" if name == "arrays" else "utf-8-json"
        )
        for name in BLOCK_NAMES
    }
    previous_end = HEADER_SIZE
    for entry in entries:
        if entry.offset < HEADER_SIZE or entry.offset + entry.length > directory_offset:
            add("BLOCK_OUT_OF_BOUNDS", "Block range is outside the payload region", entry.block_name)
        if entry.offset < previous_end:
            add("OVERLAPPING_BLOCKS", "Block overlaps the preceding block", entry.block_name)
        previous_end = max(previous_end, entry.offset + entry.length)
        if entry.encoding != expected_encodings.get(entry.block_name):
            add("UNSUPPORTED_ENCODING", f"Unexpected encoding: {entry.encoding}", entry.block_name)
        if _SHA256_RE.fullmatch(entry.checksum) is None:
            add("INVALID_CHECKSUM_FORMAT", "Checksum must be lowercase SHA-256", entry.block_name)
    return tuple(entries)


def _check_certificate(
    certificate_path: Path,
    evidence: _QuickEvidence,
) -> tuple[bool, list[ValidationIssue], dict[str, Any] | None]:
    issues: list[ValidationIssue] = []

    def add(code: str, message: str) -> None:
        issues.append(ValidationIssue(code, message, "error", "certificate"))

    if not certificate_path.is_file():
        add("DEEP_VALIDATION_CERTIFICATE_NOT_FOUND", "Deep-validation certificate is missing")
        return False, issues, None
    try:
        payload = json.loads(
            certificate_path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        add("DEEP_VALIDATION_CERTIFICATE_INVALID", str(exc))
        return False, issues, None
    if not isinstance(payload, dict):
        add("DEEP_VALIDATION_CERTIFICATE_INVALID", "Certificate must be a JSON object")
        return False, issues, None
    if (
        payload.get("certificate_schema_version") != CERTIFICATE_SCHEMA_VERSION
        or payload.get("validator_version") != VALIDATOR_CONTRACT_VERSION
    ):
        add(
            "DEEP_VALIDATION_CERTIFICATE_VERSION_INCOMPATIBLE",
            "Certificate and Validator contract versions are incompatible",
        )
        return False, issues, None
    expected_blocks = {entry.block_name: entry.checksum for entry in evidence.entries}
    if (
        payload.get("zp_file_sha256") != evidence.file_sha256
        or payload.get("file_size") != evidence.file_size
        or payload.get("format_version") != evidence.version
        or payload.get("directory_checksum") != evidence.directory_checksum
        or payload.get("block_checksums") != expected_blocks
    ):
        add(
            "DEEP_VALIDATION_CERTIFICATE_FILE_MISMATCH",
            "Certificate does not bind to the current file bytes and directory",
        )
        return False, issues, None
    deep_result = payload.get("deep_validation_result")
    if (
        not isinstance(deep_result, dict)
        or deep_result.get("physical_valid") is not True
        or deep_result.get("top_down_valid") not in {True, None}
        or deep_result.get("bottom_up_valid") not in {True, None}
        or not isinstance(payload.get("entity_counts"), dict)
        or not _plain_nonnegative_int(payload.get("array_count"))
        or not _plain_nonnegative_int(payload.get("array_value_count"))
    ):
        add("DEEP_VALIDATION_CERTIFICATE_INVALID", "Certificate result or count fields are invalid")
        return False, issues, None
    return True, issues, payload


def _plain_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _evidence_result(
    version: int | None,
    file_size: int,
    file_sha256: str | None,
    directory_checksum: str | None,
    entries: tuple[BlockDirectoryEntry, ...],
    issues: list[ValidationIssue],
    checked_blocks: int,
    bytes_read: int,
    loop_count: int,
    wall_started: float,
    cpu_started: float,
) -> _QuickEvidence:
    return _QuickEvidence(
        version=version,
        file_size=file_size,
        file_sha256=file_sha256,
        directory_checksum=directory_checksum,
        entries=entries,
        issues=tuple(issues),
        checked_blocks=checked_blocks,
        bytes_read=bytes_read,
        loop_count=loop_count,
        wall_seconds=time.perf_counter() - wall_started,
        cpu_seconds=time.process_time() - cpu_started,
    )
