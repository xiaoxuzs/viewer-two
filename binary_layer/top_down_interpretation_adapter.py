from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from .conversion_exceptions import TopDownConversionError
from .models import ConversionOptions
from .top_down_interpretation_schema import (
    GeneratedPrsmArtifact,
    PrsmupExecution,
    TopDownIntermediateBundle,
    TopDownInterpretationInputPair,
    TopDownInterpretationOptions,
    TopDownInterpretationResult,
)

_PRSM_FILE = re.compile(r"^prsm(\d+)\.js$", re.IGNORECASE)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")
_MZML_ALONE_MESSAGE = (
    "mzML alone cannot produce PrSM interpretation; "
    "TopPIC *_toppic_prsm.xml and TopFD *_ms2.msalign are required."
)


class TopDownInterpretationAdapter:
    """Discover and execute the existing prsmup.py interpreter safely."""

    def inspect_bundle(self, source: Path) -> TopDownIntermediateBundle:
        root = source.resolve(strict=False)
        if not root.is_dir():
            raise TopDownConversionError(
                "TOP_DOWN_INTERMEDIATE_BUNDLE_NOT_FOUND",
                f"Top-Down intermediate source must be a directory: {root}",
            )
        files = _files_at_depth_one(root)
        spectra = tuple(path for path in files if path.suffix.lower() == ".mzml")
        xml_files = tuple(
            path for path in files if path.name.lower().endswith("_toppic_prsm.xml")
        )
        msalign_files = tuple(
            path for path in files if path.name.lower().endswith("_ms2.msalign")
        )
        if not xml_files and not msalign_files:
            raise TopDownConversionError(
                "TOP_DOWN_INTERMEDIATE_BUNDLE_NOT_FOUND",
                "Directory has no TopPIC XML or TopFD MSALIGN intermediate inputs",
            )
        if not spectra:
            raise TopDownConversionError(
                "TOP_DOWN_SPECTRUM_SOURCE_MISSING",
                "Top-Down intermediate bundle requires one mzML spectrum source",
            )
        if len(spectra) != 1:
            raise TopDownConversionError(
                "TOP_DOWN_MULTIPLE_RUNS_NOT_SUPPORTED",
                "Top-Down intermediate bundle must contain exactly one mzML run",
                details={"spectrum_source_count": len(spectra)},
            )
        if not xml_files:
            raise TopDownConversionError(
                "PRSMUP_INPUT_XML_MISSING",
                "TopPIC *_toppic_prsm.xml input is required",
            )
        if not msalign_files:
            raise TopDownConversionError(
                "PRSMUP_INPUT_MSALIGN_MISSING",
                "TopFD *_ms2.msalign input is required",
            )

        spectrum = spectra[0]
        pairs = _pair_inputs(xml_files, msalign_files)
        run_name = _run_key(spectrum.name)
        referenced_runs: set[str] = set()
        for pair in pairs:
            referenced_runs.update(_msalign_spectrum_run_names(pair.topfd_ms2_msalign))
        if referenced_runs and referenced_runs != {run_name}:
            raise TopDownConversionError(
                "TOP_DOWN_MULTIPLE_RUNS_NOT_SUPPORTED",
                "MSALIGN spectrum file references do not match the single mzML run",
                details={"run_count": len(referenced_runs | {run_name})},
            )
        source_files = _unique_paths(
            (spectrum, *(pair.toppic_prsm_xml for pair in pairs), *(pair.topfd_ms2_msalign for pair in pairs))
        )
        return TopDownIntermediateBundle(
            schema_name="top_down_intermediate_bundle",
            schema_version=1,
            input_path=root,
            root=root,
            run_name=run_name,
            spectrum_source=spectrum,
            spectrum_source_type="mzml",
            input_pairs=pairs,
            detected_roles=("spectrum_source", "toppic_prsm_xml", "topfd_ms2_msalign"),
            source_files=source_files,
        )

    def options_from_conversion(
        self,
        options: ConversionOptions,
    ) -> TopDownInterpretationOptions:
        script = options.top_down_interpreter_script
        if script is None:
            raise TopDownConversionError(
                "PRSMUP_SCRIPT_NOT_FOUND",
                "ConversionOptions.top_down_interpreter_script is required",
            )
        python_executable = options.python_executable or Path(sys.executable)
        working_directory = options.temporary_directory or (
            Path(tempfile.gettempdir()) / "viewer-two-top-down-interpretation"
        )
        return TopDownInterpretationOptions(
            script_path=script,
            python_executable=python_executable,
            working_directory=working_directory,
            timeout_seconds=float(options.interpretation_timeout_seconds),
            keep_generated_files=options.keep_generated_interpretation,
            generated_directory=options.generated_interpretation_directory,
        )

    def generate(
        self,
        bundle: TopDownIntermediateBundle,
        options: TopDownInterpretationOptions,
    ) -> TopDownInterpretationResult:
        script = options.script_path.resolve(strict=False)
        python_executable = options.python_executable.resolve(strict=False)
        if not script.is_file():
            raise TopDownConversionError(
                "PRSMUP_SCRIPT_NOT_FOUND",
                f"prsmup.py script is missing: {script}",
            )
        if not python_executable.is_file():
            raise TopDownConversionError(
                "PRSMUP_EXECUTION_FAILED",
                f"Python executable is missing: {python_executable}",
            )
        for pair in bundle.input_pairs:
            if not pair.toppic_prsm_xml.is_file():
                raise TopDownConversionError(
                    "PRSMUP_INPUT_XML_MISSING",
                    f"TopPIC XML input is missing: {pair.toppic_prsm_xml.name}",
                )
            if not pair.topfd_ms2_msalign.is_file():
                raise TopDownConversionError(
                    "PRSMUP_INPUT_MSALIGN_MISSING",
                    f"TopFD MSALIGN input is missing: {pair.topfd_ms2_msalign.name}",
                )

        base = options.working_directory.resolve(strict=False)
        base.mkdir(parents=True, exist_ok=True)
        prefix = _aggregate_sha256(bundle.source_files)[:12]
        working = base / f"{prefix}-{uuid4().hex[:12]}"
        working.mkdir(parents=False, exist_ok=False)
        started = time.perf_counter()
        executions: list[PrsmupExecution] = []
        artifacts: list[GeneratedPrsmArtifact] = []
        script_sha256 = _sha256(script)
        try:
            python_version = self._python_version(
                python_executable,
                timeout_seconds=min(options.timeout_seconds, 30.0),
            )
            for index, pair in enumerate(bundle.input_pairs):
                output_directory = working / f"pair-{index:04d}"
                command = [
                    str(python_executable),
                    str(script),
                    "--prsm-xml",
                    str(pair.toppic_prsm_xml),
                    "--msalign",
                    str(pair.topfd_ms2_msalign),
                    "--out-dir",
                    str(output_directory),
                    "--limit",
                    str(pair.prsm_count),
                ]
                execution = self._run_prsmup(
                    command,
                    working,
                    timeout_seconds=options.timeout_seconds,
                )
                executions.append(execution)
                if execution.exit_code != 0:
                    raise TopDownConversionError(
                        "PRSMUP_EXECUTION_FAILED",
                        f"prsmup.py exited with code {execution.exit_code}",
                        details={"exit_code": execution.exit_code},
                    )
                for path in sorted(output_directory.glob("prsm*.js"), key=lambda item: item.name):
                    match = _PRSM_FILE.fullmatch(path.name)
                    if match is None:
                        continue
                    size = path.stat().st_size
                    if size == 0:
                        raise TopDownConversionError(
                            "PRSMUP_OUTPUT_EMPTY",
                            f"Generated PrSM output is empty: {path.name}",
                        )
                    text = _read_text(path)
                    if _WINDOWS_ABSOLUTE_PATH.search(text):
                        raise TopDownConversionError(
                            "PRSMUP_OUTPUT_MALFORMED",
                            f"Generated PrSM output leaks an absolute path: {path.name}",
                        )
                    artifacts.append(
                        GeneratedPrsmArtifact(
                            path=path,
                            file_name=path.name,
                            prsm_id=str(int(match.group(1))),
                            size=size,
                            sha256=_sha256(path),
                        )
                    )
            if not artifacts:
                raise TopDownConversionError(
                    "PRSMUP_OUTPUT_MISSING",
                    "prsmup.py produced no prsm*.js output",
                )
            ids = [item.prsm_id for item in artifacts]
            if len(ids) != len(set(ids)):
                duplicate = next(item for item in ids if ids.count(item) > 1)
                raise TopDownConversionError(
                    "PRSMUP_OUTPUT_DUPLICATE_ID",
                    f"Generated PrSM ID is duplicated: {duplicate}",
                )
            expected_ids = [
                prsm_id for pair in bundle.input_pairs for prsm_id in pair.prsm_ids
            ]
            if len(expected_ids) != len(set(expected_ids)):
                raise TopDownConversionError(
                    "PRSMUP_OUTPUT_DUPLICATE_ID",
                    "TopPIC XML contains duplicate PrSM IDs across input pairs",
                )
            if set(ids) != set(expected_ids) or len(ids) != len(expected_ids):
                raise TopDownConversionError(
                    "PRSMUP_OUTPUT_MISSING",
                    "Generated PrSM IDs do not completely cover TopPIC XML PrSM IDs",
                    details={
                        "expected_count": len(expected_ids),
                        "generated_count": len(ids),
                    },
                )
            if _sha256(script) != script_sha256:
                raise TopDownConversionError(
                    "PRSMUP_EXECUTION_FAILED",
                    "prsmup.py changed during interpretation execution",
                )
            return TopDownInterpretationResult(
                script_path=script,
                script_sha256=script_sha256,
                python_executable=python_executable,
                python_version=python_version,
                working_directory=working,
                generated_prsm_artifacts=tuple(
                    sorted(artifacts, key=lambda item: (int(item.prsm_id), item.file_name))
                ),
                executions=tuple(executions),
                duration_seconds=time.perf_counter() - started,
            )
        except Exception:
            self.cleanup(working, base)
            raise

    @staticmethod
    def cleanup(working: Path, base: Path) -> None:
        resolved_working = working.resolve(strict=False)
        resolved_base = base.resolve(strict=False)
        if resolved_working.parent != resolved_base or not resolved_working.name:
            raise TopDownConversionError(
                "PRSMUP_TEMP_CLEANUP_FAILED",
                "Refusing to clean a directory outside the configured temporary root",
            )
        try:
            shutil.rmtree(resolved_working)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise TopDownConversionError(
                "PRSMUP_TEMP_CLEANUP_FAILED",
                f"Generated interpretation cleanup failed: {exc}",
            ) from exc

    def finalize(
        self,
        result: TopDownInterpretationResult,
        options: TopDownInterpretationOptions,
    ) -> tuple[Path | None, str]:
        if not options.keep_generated_files:
            self.cleanup(result.working_directory, options.working_directory)
            return None, "removed"
        if options.generated_directory is None:
            return result.working_directory, "retained"
        destination_root = options.generated_directory.resolve(strict=False)
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / result.working_directory.name
        try:
            shutil.copytree(result.working_directory, destination)
        except OSError as exc:
            raise TopDownConversionError(
                "PRSMUP_TEMP_CLEANUP_FAILED",
                f"Generated interpretation could not be retained: {exc}",
            ) from exc
        self.cleanup(result.working_directory, options.working_directory)
        return destination, "retained"

    @staticmethod
    def _python_version(python_executable: Path, *, timeout_seconds: float) -> str:
        try:
            completed = subprocess.run(
                [str(python_executable), "--version"],
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise TopDownConversionError(
                "PRSMUP_EXECUTION_TIMEOUT",
                "Python version check timed out",
            ) from exc
        except OSError as exc:
            raise TopDownConversionError(
                "PRSMUP_EXECUTION_FAILED",
                f"Python executable could not be started: {exc}",
            ) from exc
        if completed.returncode != 0:
            raise TopDownConversionError(
                "PRSMUP_EXECUTION_FAILED",
                "Python version check failed",
                details={"exit_code": completed.returncode},
            )
        version = (completed.stdout or completed.stderr).strip()
        if not version:
            raise TopDownConversionError(
                "PRSMUP_EXECUTION_FAILED",
                "Python executable did not report a version",
            )
        return version

    @staticmethod
    def _run_prsmup(
        command: list[str],
        working_directory: Path,
        *,
        timeout_seconds: float,
    ) -> PrsmupExecution:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                cwd=working_directory,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise TopDownConversionError(
                "PRSMUP_EXECUTION_TIMEOUT",
                f"prsmup.py exceeded {timeout_seconds:g} seconds",
            ) from exc
        except OSError as exc:
            raise TopDownConversionError(
                "PRSMUP_EXECUTION_FAILED",
                f"prsmup.py could not be started: {exc}",
            ) from exc
        return PrsmupExecution(
            command=tuple(command),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=time.perf_counter() - started,
        )


def mzml_alone_error() -> TopDownConversionError:
    return TopDownConversionError(
        "TOP_DOWN_INTERPRETATION_INPUTS_MISSING",
        _MZML_ALONE_MESSAGE,
    )


def _files_at_depth_one(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    try:
        children = tuple(root.iterdir())
        files.extend(item for item in children if item.is_file())
        for directory in (item for item in children if item.is_dir()):
            files.extend(item for item in directory.iterdir() if item.is_file())
    except OSError as exc:
        raise TopDownConversionError(
            "TOP_DOWN_SOURCE_NOT_READABLE",
            f"Cannot inspect Top-Down intermediate bundle: {exc}",
        ) from exc
    return tuple(sorted(files, key=lambda item: item.as_posix().encode("utf-8")))


def _pair_inputs(
    xml_files: tuple[Path, ...],
    msalign_files: tuple[Path, ...],
) -> tuple[TopDownInterpretationInputPair, ...]:
    xml_info = {path: _xml_info(path) for path in xml_files}
    msalign_info = {path: _msalign_info(path) for path in msalign_files}
    unused = set(msalign_files)
    pairs: list[TopDownInterpretationInputPair] = []
    for xml in xml_files:
        (
            referenced_names,
            scans,
            prsm_ids,
            modification_count,
            modification_counts_by_prsm,
        ) = xml_info[xml]
        candidates = [
            path for path in unused if path.name.casefold() in referenced_names
        ]
        evidence = "xml_file_reference"
        if not candidates and scans:
            candidates = [path for path in unused if scans <= msalign_info[path][0]]
            evidence = "spectrum_scan_set"
        if not candidates:
            key = _intermediate_run_key(xml.name)
            candidates = [
                path for path in unused if _intermediate_run_key(path.name) == key
            ]
            evidence = "normalized_common_basename"
        if not candidates and len(unused) == 1 and len(xml_files) == 1:
            candidates = list(unused)
            evidence = "unique_candidate"
        if len(candidates) != 1:
            raise TopDownConversionError(
                "PRSMUP_INPUT_PAIR_AMBIGUOUS",
                f"TopPIC XML does not resolve to exactly one TopFD MSALIGN: {xml.name}",
                details={"candidate_count": len(candidates)},
            )
        msalign = candidates[0]
        unused.remove(msalign)
        pairs.append(
            TopDownInterpretationInputPair(
                toppic_prsm_xml=xml,
                topfd_ms2_msalign=msalign,
                pairing_evidence=evidence,
                prsm_count=len(prsm_ids),
                prsm_ids=prsm_ids,
                modification_count=modification_count,
                modification_counts_by_prsm=modification_counts_by_prsm,
            )
        )
    if unused:
        raise TopDownConversionError(
            "TOP_DOWN_AMBIGUOUS_INTERPRETATION_INPUT",
            "One or more TopFD MSALIGN files are not paired with TopPIC XML",
            details={"unpaired_msalign_count": len(unused)},
        )
    return tuple(pairs)


def _xml_info(
    path: Path,
) -> tuple[
    set[str],
    set[int],
    tuple[str, ...],
    int,
    tuple[tuple[str, int], ...],
]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise TopDownConversionError(
            "PRSMUP_INPUT_PAIR_AMBIGUOUS",
            f"TopPIC XML cannot be inspected: {path.name}",
        ) from exc
    prsms = root.findall("prsm")
    names = {
        value.casefold()
        for value in (item.findtext("file_name", "").strip() for item in prsms)
        if value
    }
    scans: set[int] = set()
    prsm_ids: list[str] = []
    modification_counts_by_prsm: list[tuple[str, int]] = []
    for item in prsms:
        raw_prsm_id = item.findtext("prsm_id", "").strip()
        try:
            prsm_id = str(int(raw_prsm_id))
        except ValueError as exc:
            raise TopDownConversionError(
                "PRSMUP_INPUT_PAIR_AMBIGUOUS",
                f"TopPIC XML contains an invalid PrSM ID: {path.name}",
            ) from exc
        prsm_ids.append(prsm_id)
        modification_counts_by_prsm.append(
            (
                prsm_id,
                len(item.findall("proteoform/mass_shift_list/mass_shift")),
            )
        )
        try:
            scans.add(int(item.findtext("spectrum_scan", "")))
        except ValueError:
            continue
    modification_count = sum(count for _, count in modification_counts_by_prsm)
    return (
        names,
        scans,
        tuple(prsm_ids),
        modification_count,
        tuple(modification_counts_by_prsm),
    )


def _msalign_info(path: Path) -> tuple[set[int], set[str]]:
    scans: set[int] = set()
    run_names: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n")
                if line.startswith("SCANS="):
                    try:
                        scans.add(int(line.split("=", 1)[1].strip()))
                    except ValueError:
                        continue
                elif line.startswith("FILE_NAME="):
                    value = line.split("=", 1)[1].strip()
                    if value:
                        run_names.add(_run_key(value))
                elif line.lower().startswith("#file name:"):
                    value = line.split(":", 1)[1].strip()
                    if value:
                        run_names.add(_run_key(value))
    except (OSError, UnicodeError) as exc:
        raise TopDownConversionError(
            "PRSMUP_INPUT_PAIR_AMBIGUOUS",
            f"TopFD MSALIGN cannot be inspected: {path.name}",
        ) from exc
    return scans, run_names


def _msalign_spectrum_run_names(path: Path) -> set[str]:
    return _msalign_info(path)[1]


def _intermediate_run_key(name: str) -> str:
    value = Path(name).stem.casefold()
    for suffix in ("_toppic_prsm", "_ms2"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    return _run_key(value)


def _run_key(value: str) -> str:
    name = Path(value).name.casefold()
    for suffix in (".mzml", ".raw", ".msalign", ".xml"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    for suffix in ("_toppic_prsm", "_ms2"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise TopDownConversionError(
        "PRSMUP_OUTPUT_MALFORMED",
        f"Generated PrSM output encoding is unsupported: {path.name}",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aggregate_sha256(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name.encode("utf-8")):
        label = path.name.encode("utf-8")
        digest.update(len(label).to_bytes(8, "little"))
        digest.update(label)
        digest.update(path.stat().st_size.to_bytes(8, "little"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _unique_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve(strict=False)
        if resolved not in seen:
            seen.add(resolved)
            result.append(path)
    return tuple(result)
