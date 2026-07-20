from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .conversion_exceptions import ThermoRawConversionError

THERMO_RAW_CONVERTER_NOT_FOUND = "THERMO_RAW_CONVERTER_NOT_FOUND"
THERMO_RAW_CONVERTER_FAILED = "THERMO_RAW_CONVERTER_FAILED"
THERMO_RAW_CONVERSION_TIMEOUT = "THERMO_RAW_CONVERSION_TIMEOUT"
THERMO_RAW_OUTPUT_MISSING = "THERMO_RAW_OUTPUT_MISSING"
THERMO_RAW_OUTPUT_EMPTY = "THERMO_RAW_OUTPUT_EMPTY"
THERMO_RAW_OUTPUT_NOT_MZML = "THERMO_RAW_OUTPUT_NOT_MZML"
THERMO_RAW_OUTPUT_NOT_INDEXED = "THERMO_RAW_OUTPUT_NOT_INDEXED"
THERMO_RAW_TEMP_CLEANUP_FAILED = "THERMO_RAW_TEMP_CLEANUP_FAILED"

CONVERTER_NAME = "ThermoRawFileParser"
_INDEX_OFFSET_PATTERN = re.compile(rb"<indexListOffset>\s*([0-9]+)\s*</indexListOffset>")
_MZML_CHILD_PATTERN = re.compile(rb"<mzML(?:\s|>)")


@dataclass(frozen=True, slots=True)
class ThermoRawAdapterResult:
    converter_path: Path
    converter_name: str
    converter_version: str
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    mzml_path: Path
    work_directory: Path
    raw_to_mzml_seconds: float
    intermediate_file_size: int
    intermediate_sha256: str
    intermediate_indexed: bool = True


def discover_thermo_raw_file_parser(configured_path: Path | None = None) -> Path:
    if configured_path is not None:
        if configured_path.is_file():
            return configured_path.resolve()
        raise ThermoRawConversionError(
            THERMO_RAW_CONVERTER_NOT_FOUND,
            f"Configured ThermoRawFileParser does not exist: {configured_path}",
        )

    for command_name in ("ThermoRawFileParser", "ThermoRawFileParser.exe"):
        discovered = shutil.which(command_name)
        if discovered:
            return Path(discovered).resolve()

    candidates = _default_converter_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    searched = "; ".join(str(path) for path in candidates)
    raise ThermoRawConversionError(
        THERMO_RAW_CONVERTER_NOT_FOUND,
        f"ThermoRawFileParser was not found; searched PATH and limited local candidates: {searched}",
    )


def _default_converter_candidates() -> tuple[Path, ...]:
    project_root = Path(__file__).resolve().parent.parent
    roots = [project_root]
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    candidates: list[Path] = []
    for root in roots:
        candidates.extend(
            (
                root / "ThermoRawFileParser1.4.5" / "ThermoRawFileParser.exe",
                root / "ThermoRawFileParser" / "ThermoRawFileParser.exe",
            )
        )
        try:
            candidates.extend(sorted(root.glob("ThermoRawFileParser*/ThermoRawFileParser.exe")))
        except OSError:
            continue
    return tuple(dict.fromkeys(candidates))


def read_converter_version(executable: Path, *, timeout_seconds: float = 30.0) -> str:
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ThermoRawConversionError(
            THERMO_RAW_CONVERTER_NOT_FOUND,
            f"ThermoRawFileParser disappeared before version inspection: {executable}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ThermoRawConversionError(
            THERMO_RAW_CONVERSION_TIMEOUT,
            "ThermoRawFileParser version inspection timed out",
        ) from exc
    except OSError as exc:
        raise ThermoRawConversionError(
            THERMO_RAW_CONVERTER_FAILED,
            f"ThermoRawFileParser version inspection failed: {exc}",
        ) from exc
    if completed.returncode != 0:
        raise ThermoRawConversionError(
            THERMO_RAW_CONVERTER_FAILED,
            f"ThermoRawFileParser --version exited with code {completed.returncode}",
            details={"exit_code": completed.returncode, "stderr": completed.stderr or ""},
        )
    version = (completed.stdout or completed.stderr or "").strip()
    if not version:
        raise ThermoRawConversionError(
            THERMO_RAW_CONVERTER_FAILED,
            "ThermoRawFileParser --version returned no version text",
        )
    return version


def build_thermo_raw_command(executable: Path, source_path: Path, output_path: Path) -> list[str]:
    return [
        str(executable),
        f"-i={source_path}",
        f"-b={output_path}",
        "-f=2",
        "-m=2",
    ]


def validate_indexed_mzml(path: Path) -> Path:
    if not path.exists() or not path.is_file():
        raise ThermoRawConversionError(
            THERMO_RAW_OUTPUT_MISSING,
            f"ThermoRawFileParser output is missing: {path}",
        )
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise ThermoRawConversionError(
            THERMO_RAW_OUTPUT_MISSING,
            f"ThermoRawFileParser output cannot be inspected: {path}",
        ) from exc
    if file_size == 0:
        raise ThermoRawConversionError(
            THERMO_RAW_OUTPUT_EMPTY,
            f"ThermoRawFileParser output is empty: {path}",
        )

    try:
        _, root = next(iter(ET.iterparse(path, events=("start",))))
    except (ET.ParseError, OSError, StopIteration) as exc:
        raise ThermoRawConversionError(
            THERMO_RAW_OUTPUT_NOT_MZML,
            f"ThermoRawFileParser output is not readable XML/mzML: {path}",
        ) from exc
    root_name = root.tag.rsplit("}", 1)[-1]
    if root_name not in {"mzML", "indexedmzML"}:
        raise ThermoRawConversionError(
            THERMO_RAW_OUTPUT_NOT_MZML,
            f"ThermoRawFileParser output root is not mzML: {root_name!r}",
        )
    if root_name != "indexedmzML":
        raise ThermoRawConversionError(
            THERMO_RAW_OUTPUT_NOT_INDEXED,
            "ThermoRawFileParser output is mzML but not indexed mzML",
        )

    try:
        with path.open("rb") as stream:
            prefix = stream.read(min(file_size, 1024 * 1024))
            stream.seek(max(0, file_size - 4 * 1024 * 1024))
            tail = stream.read()
            offset_match = _INDEX_OFFSET_PATTERN.search(tail)
            if _MZML_CHILD_PATTERN.search(prefix) is None or b"</indexedmzML>" not in tail or offset_match is None:
                raise ThermoRawConversionError(
                    THERMO_RAW_OUTPUT_NOT_INDEXED,
                    "indexed mzML wrapper is missing mzML content, indexListOffset, or closing structure",
                )
            index_offset = int(offset_match.group(1))
            if index_offset <= 0 or index_offset >= file_size:
                raise ThermoRawConversionError(
                    THERMO_RAW_OUTPUT_NOT_INDEXED,
                    f"indexed mzML indexListOffset is outside the output file: {index_offset}",
                )
            stream.seek(index_offset)
            if not stream.read(64).lstrip().startswith(b"<indexList"):
                raise ThermoRawConversionError(
                    THERMO_RAW_OUTPUT_NOT_INDEXED,
                    "indexed mzML indexListOffset does not point to indexList",
                )
    except ThermoRawConversionError:
        raise
    except OSError as exc:
        raise ThermoRawConversionError(
            THERMO_RAW_OUTPUT_NOT_MZML,
            f"ThermoRawFileParser output cannot be read: {path}",
        ) from exc
    return path.resolve()


class ThermoRawAdapter:
    def convert(
        self,
        source_path: Path,
        *,
        converter_path: Path | None,
        temporary_directory: Path | None,
        timeout_seconds: float,
    ) -> ThermoRawAdapterResult:
        source = Path(source_path)
        executable = discover_thermo_raw_file_parser(converter_path)
        version = read_converter_version(executable, timeout_seconds=min(timeout_seconds, 30.0))
        base_directory = temporary_directory or Path(tempfile.gettempdir()) / "zp-thermo-raw"
        base_directory.mkdir(parents=True, exist_ok=True)
        safe_stem = re.sub(r"[^0-9A-Za-z._-]+", "_", source.stem).strip("._") or "thermo-raw"
        work_directory = Path(tempfile.mkdtemp(prefix=f"{safe_stem}-", dir=base_directory))
        output_path = work_directory / f"{source.stem}.mzML"
        command = build_thermo_raw_command(executable, source, output_path)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
                check=False,
            )
            elapsed = time.perf_counter() - started
            if completed.returncode != 0:
                raise ThermoRawConversionError(
                    THERMO_RAW_CONVERTER_FAILED,
                    f"ThermoRawFileParser exited with code {completed.returncode}",
                    details={
                        "exit_code": completed.returncode,
                        "stdout": completed.stdout or "",
                        "stderr": completed.stderr or "",
                    },
                )
            validated_path = validate_indexed_mzml(output_path)
            return ThermoRawAdapterResult(
                converter_path=executable,
                converter_name=CONVERTER_NAME,
                converter_version=version,
                command=tuple(command),
                exit_code=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                mzml_path=validated_path,
                work_directory=work_directory.resolve(),
                raw_to_mzml_seconds=elapsed,
                intermediate_file_size=validated_path.stat().st_size,
                intermediate_sha256=_sha256(validated_path),
            )
        except subprocess.TimeoutExpired as exc:
            error = ThermoRawConversionError(
                THERMO_RAW_CONVERSION_TIMEOUT,
                f"ThermoRawFileParser timed out after {timeout_seconds} seconds",
                details={"stdout": _timeout_text(exc.stdout), "stderr": _timeout_text(exc.stderr)},
            )
            self._raise_after_failed_cleanup(work_directory, error)
            raise AssertionError("unreachable")
        except ThermoRawConversionError as error:
            self._raise_after_failed_cleanup(work_directory, error)
            raise AssertionError("unreachable")
        except OSError as exc:
            error = ThermoRawConversionError(
                THERMO_RAW_CONVERTER_FAILED,
                f"ThermoRawFileParser could not be executed: {exc}",
            )
            self._raise_after_failed_cleanup(work_directory, error)
            raise AssertionError("unreachable")

    def cleanup_intermediate(self, result: ThermoRawAdapterResult) -> str:
        try:
            if result.work_directory.exists():
                shutil.rmtree(result.work_directory)
                return "removed"
            return "already_absent"
        except OSError as exc:
            raise ThermoRawConversionError(
                THERMO_RAW_TEMP_CLEANUP_FAILED,
                f"Failed to remove Thermo RAW temporary directory: {result.work_directory}",
            ) from exc

    @staticmethod
    def _raise_after_failed_cleanup(work_directory: Path, error: ThermoRawConversionError) -> None:
        try:
            if work_directory.exists():
                shutil.rmtree(work_directory)
        except OSError as cleanup_exc:
            raise ThermoRawConversionError(
                THERMO_RAW_TEMP_CLEANUP_FAILED,
                f"Failed to clean incomplete Thermo RAW output: {work_directory}",
                details={"original_error_code": error.code},
            ) from cleanup_exc
        raise error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timeout_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
