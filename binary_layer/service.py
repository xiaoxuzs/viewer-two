from __future__ import annotations

import hashlib
import json
import os
import struct
import time
from pathlib import Path
from uuid import uuid4

from .bottom_up_validator import combine_bottom_up_validation
from .constants import DEFAULT_ZP_WRITE_VERSION, ZP_EXTENSION
from .conversion_exceptions import SourceConversionError, ThermoRawConversionError
from .dia_resource_limits import (
    DIA_V2_ARRAY_WRITE_LIMITS,
    DIA_V2_VALIDATION_LIMITS,
)
from .inspector import SourceInspector
from .models import (
    ConversionOptions,
    ConversionResult,
    PipelineContext,
    SourceFileIdentity,
    SourceProfile,
    ValidationIssue,
    ValidationResult,
)
from .plan import PlanBuilder
from .quick_validator import validate_quick, write_deep_validation_certificate
from .reader import ZpReader
from .registry import StepRegistry, build_default_registry
from .runner import PipelineRunner
from .tools.real_thermo_raw import RealThermoRawExecutionReport
from .tools.real_top_down_intermediate import RealTopDownIntermediateExecutionReport
from .tools.real_dia_result import RealDiaResultExecutionReport
from .top_down_validator import combine_top_down_validation
from .validator import ZpValidator


def inspect_source(
    source_path: str | Path,
    *,
    requested_conversion_kind: str | None = None,
) -> SourceProfile:
    return SourceInspector().inspect(
        (Path(source_path),),
        requested_conversion_kind=requested_conversion_kind,
    )


def validate_zp(
    path: str | Path,
    *,
    mode: str = "quick",
    certificate_path: str | Path | None = None,
) -> ValidationResult:
    zp_path = Path(path)
    if mode == "quick":
        return validate_quick(zp_path, certificate_path=certificate_path)
    if mode != "deep":
        raise ValueError("mode must be 'quick' or 'deep'")
    validator = ZpValidator()
    if zp_path.is_file() and zp_path.stat().st_size > 512 * 1024 * 1024:
        validator.v2_limits = DIA_V2_VALIDATION_LIMITS
    physical_result = validator.validate(zp_path)
    cached_extensions = getattr(validator, "_last_v2_extensions", None)
    physical = combine_top_down_validation(
        zp_path,
        physical_result,
        extensions=cached_extensions,
    )
    physical.metrics.update(getattr(validator, "_last_v2_metrics", {}))
    result = combine_bottom_up_validation(
        zp_path,
        physical,
        extensions=cached_extensions,
    )
    validator._last_v2_extensions = None
    result.mode = "deep"
    if result.valid:
        try:
            written = write_deep_validation_certificate(
                zp_path,
                result,
                certificate_path=certificate_path,
            )
            certificate = json.loads(written.read_text(encoding="utf-8"))
            result.file_sha256 = certificate["zp_file_sha256"]
            result.certificate_valid = True
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            result.valid = False
            result.certificate_valid = False
            result.issues.append(
                ValidationIssue(
                    "DEEP_VALIDATION_CERTIFICATE_WRITE_FAILED",
                    str(exc),
                    "error",
                    "certificate",
                )
            )
    return result


def open_zp(path: str | Path) -> ZpReader:
    return ZpReader(Path(path))


def convert_source_to_zp(
    source_path: str | Path,
    target_path: str | Path,
    *,
    format_version: int = DEFAULT_ZP_WRITE_VERSION,
    options: ConversionOptions | None = None,
) -> ConversionResult:
    if type(format_version) is not int:
        raise TypeError("format_version must be a plain integer")
    conversion_options = options or ConversionOptions()
    if not isinstance(conversion_options, ConversionOptions):
        raise TypeError("options must be a ConversionOptions instance or None")

    source = Path(source_path).resolve(strict=False)
    target = Path(target_path).resolve(strict=False)
    if target.suffix != ZP_EXTENSION:
        raise SourceConversionError(
            "INVALID_TARGET_EXTENSION",
            f"Target extension must be exactly {ZP_EXTENSION}: {target}",
        )
    if source == target:
        raise SourceConversionError("SOURCE_TARGET_COLLISION", "Source and target paths must differ")
    if target.exists():
        raise SourceConversionError("TARGET_ALREADY_EXISTS", f"Target already exists: {target}")

    total_started = time.perf_counter()
    inspect_started = time.perf_counter()
    profile = inspect_source(
        source,
        requested_conversion_kind=conversion_options.requested_conversion_kind,
    )
    inspect_seconds = time.perf_counter() - inspect_started
    source_before = _source_identity(source, profile)
    plan = PlanBuilder().build(profile)
    target.parent.mkdir(parents=True, exist_ok=True)
    working_zp = target.with_name(f".{target.name}.{uuid4().hex}.partial.zp")
    registry = build_default_registry()
    context = PipelineContext(
        profile,
        metadata={
            "output_path": working_zp,
            "format_version": format_version,
            "conversion_options": conversion_options,
            "inspect_seconds": inspect_seconds,
            "release_blocks_after_write": True,
        },
    )
    if profile.source_type == "real_dia_result_bundle":
        context.metadata["v2_array_write_limits"] = DIA_V2_ARRAY_WRITE_LIMITS
        context.metadata["v2_validation_limits"] = DIA_V2_VALIDATION_LIMITS

    try:
        PipelineRunner().run(plan, registry, context)
        validation = context.artifacts.get("validation_result")
        if not isinstance(validation, ValidationResult) or not validation.valid:
            raise SourceConversionError(
                "ZP_VALIDATION_FAILED",
                "Pipeline did not return a successful unified ZpValidator result",
            )
        source_after = _source_identity(source, profile)
        if source_after != source_before:
            raise SourceConversionError(
                "SOURCE_CHANGED_DURING_CONVERSION",
                "Source file size, SHA-256, or mtime_ns changed during conversion",
            )
        working_size = working_zp.stat().st_size
        _commit_without_overwrite(working_zp, target)
    except Exception as exc:
        _cleanup_working_zp(working_zp)
        stable_error = _find_source_conversion_error(exc)
        if stable_error is not None and stable_error is not exc:
            raise stable_error from exc
        raise

    output_checksum_started = time.perf_counter()
    output_sha256 = _sha256(target)
    output_checksum_seconds = time.perf_counter() - output_checksum_started
    raw_report = _find_raw_report(plan.required_steps, registry)
    interpretation_report = _find_interpretation_report(plan.required_steps, registry)
    dia_report = _find_dia_report(plan.required_steps, registry)
    final_validation = ValidationResult(
        valid=validation.valid,
        issues=list(validation.issues),
        checked_blocks=validation.checked_blocks,
        file_path=target,
        version=validation.version,
        top_down_valid=validation.top_down_valid,
        top_down_issues=list(validation.top_down_issues),
        bottom_up_valid=validation.bottom_up_valid,
        bottom_up_issues=list(validation.bottom_up_issues),
        mode=validation.mode,
        file_sha256=output_sha256,
        certificate_valid=validation.certificate_valid,
        deep_validation_reused=validation.deep_validation_reused,
        certificate_summary=dict(validation.certificate_summary),
        metrics=dict(validation.metrics),
    )
    performance = _performance_metrics(
        context,
        raw_report,
        dia_report,
        output_size=working_size,
        keep_intermediate=conversion_options.keep_intermediate,
    )
    performance["total_seconds"] = time.perf_counter() - total_started
    performance["file_checksum_seconds"] = output_checksum_seconds

    if interpretation_report is not None:
        interpretation = interpretation_report.interpretation_result
        executions = interpretation.executions
        performance.update(
            {
                "interpretation_seconds": interpretation.duration_seconds,
                "interpretation_generated_prsm_count": len(
                    interpretation.generated_prsm_artifacts
                ),
                "interpretation_python_version": interpretation.python_version,
                "prsmup_script_sha256": interpretation.script_sha256,
            }
        )
        return ConversionResult(
            source_path=source,
            target_path=target,
            source_profile=profile,
            plan=plan,
            format_version=format_version,
            validation=final_validation,
            source_before=source_before,
            source_after=source_after,
            output_file_size=working_size,
            output_sha256=output_sha256,
            converter_path=interpretation.script_path,
            converter_name="prsmup.py",
            converter_exit_code=(executions[-1].exit_code if executions else None),
            converter_command=(executions[0].command if executions else ()),
            converter_stdout="\n".join(item.stdout for item in executions),
            converter_stderr="\n".join(item.stderr for item in executions),
            intermediate_path=interpretation_report.retained_directory,
            intermediate_file_size=sum(
                item.size for item in interpretation.generated_prsm_artifacts
            ),
            intermediate_sha256=_generated_artifact_sha256(interpretation_report),
            cleanup_result=interpretation_report.cleanup_result,
            performance=performance,
        )

    if raw_report is None:
        return ConversionResult(
            source_path=source,
            target_path=target,
            source_profile=profile,
            plan=plan,
            format_version=format_version,
            validation=final_validation,
            source_before=source_before,
            source_after=source_after,
            output_file_size=working_size,
            output_sha256=output_sha256,
            performance=performance,
        )

    adapter_result = raw_report.adapter_result
    return ConversionResult(
        source_path=source,
        target_path=target,
        source_profile=profile,
        plan=plan,
        format_version=format_version,
        validation=final_validation,
        source_before=source_before,
        source_after=source_after,
        output_file_size=working_size,
        output_sha256=output_sha256,
        converter_path=adapter_result.converter_path,
        converter_name=adapter_result.converter_name,
        converter_version=adapter_result.converter_version,
        converter_exit_code=adapter_result.exit_code,
        converter_command=adapter_result.command,
        converter_stdout=adapter_result.stdout,
        converter_stderr=adapter_result.stderr,
        intermediate_path=adapter_result.mzml_path if raw_report.intermediate_retained else None,
        intermediate_file_size=adapter_result.intermediate_file_size,
        intermediate_sha256=adapter_result.intermediate_sha256,
        cleanup_result=raw_report.cleanup_result,
        performance=performance,
    )


def _file_identity(path: Path) -> SourceFileIdentity:
    try:
        before = path.stat()
        digest = _sha256(path)
        after = path.stat()
    except OSError as exc:
        raise SourceConversionError("SOURCE_NOT_READABLE", f"Source file cannot be read: {path}") from exc
    if not path.is_file():
        raise SourceConversionError("SOURCE_NOT_REGULAR_FILE", f"Source is not a regular file: {path}")
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SourceConversionError(
            "SOURCE_CHANGED_DURING_FINGERPRINT",
            f"Source changed while computing SHA-256: {path}",
        )
    return SourceFileIdentity(before.st_size, digest, before.st_mtime_ns)


def _source_identity(path: Path, profile: SourceProfile) -> SourceFileIdentity:
    if not profile.identity_files:
        return _file_identity(path)
    digest = hashlib.sha256()
    total_size = 0
    mtimes: list[int] = []
    for item in sorted(
        profile.identity_files,
        key=lambda candidate: profile.relative_label(candidate).encode("utf-8"),
    ):
        try:
            before = item.stat()
            label = profile.relative_label(item).encode("utf-8")
            digest.update(struct.pack("<Q", len(label)))
            digest.update(label)
            digest.update(struct.pack("<Q", before.st_size))
            with item.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            after = item.stat()
        except OSError as exc:
            raise SourceConversionError(
                "SOURCE_NOT_READABLE",
                f"Bundle file cannot be read: {profile.relative_label(item)}",
            ) from exc
        if not item.is_file():
            raise SourceConversionError(
                "SOURCE_NOT_REGULAR_FILE",
                f"Bundle role is not a regular file: {profile.relative_label(item)}",
            )
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SourceConversionError(
                "SOURCE_CHANGED_DURING_FINGERPRINT",
                f"Bundle file changed while hashing: {profile.relative_label(item)}",
            )
        total_size += before.st_size
        mtimes.append(before.st_mtime_ns)
    return SourceFileIdentity(total_size, digest.hexdigest(), max(mtimes))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit_without_overwrite(working_zp: Path, target: Path) -> None:
    try:
        os.link(working_zp, target)
    except FileExistsError as exc:
        raise SourceConversionError("TARGET_ALREADY_EXISTS", f"Target already exists: {target}") from exc
    except OSError as exc:
        raise SourceConversionError(
            "ATOMIC_COMMIT_FAILED",
            f"Validated .zp could not be atomically committed without overwrite: {exc}",
        ) from exc
    try:
        working_zp.unlink()
    except OSError as exc:
        try:
            target.unlink()
        except OSError:
            pass
        raise SourceConversionError(
            "ATOMIC_COMMIT_CLEANUP_FAILED",
            f"Validated temporary .zp could not be removed after commit: {working_zp}",
        ) from exc


def _cleanup_working_zp(working_zp: Path) -> None:
    for path in (working_zp, working_zp.with_name(working_zp.name + ".tmp")):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _find_source_conversion_error(exc: BaseException) -> SourceConversionError | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ThermoRawConversionError, SourceConversionError)):
            return current
        current = current.__cause__ or current.__context__
    return None


def _find_raw_report(
    required_steps: tuple[str, ...],
    registry: StepRegistry,
) -> RealThermoRawExecutionReport | None:
    for step_name in required_steps:
        step = registry.get(step_name)
        report = getattr(step, "last_report", None)
        if isinstance(report, RealThermoRawExecutionReport):
            return report
        nested_report = getattr(report, "raw_report", None)
        if isinstance(nested_report, RealThermoRawExecutionReport):
            return nested_report
    return None


def _find_interpretation_report(
    required_steps: tuple[str, ...],
    registry: StepRegistry,
) -> RealTopDownIntermediateExecutionReport | None:
    for step_name in required_steps:
        report = getattr(registry.get(step_name), "last_report", None)
        if isinstance(report, RealTopDownIntermediateExecutionReport):
            return report
    return None


def _find_dia_report(
    required_steps: tuple[str, ...],
    registry: StepRegistry,
) -> RealDiaResultExecutionReport | None:
    for step_name in required_steps:
        report = getattr(registry.get(step_name), "last_report", None)
        if isinstance(report, RealDiaResultExecutionReport):
            return report
    return None


def _generated_artifact_sha256(
    report: RealTopDownIntermediateExecutionReport,
) -> str:
    digest = hashlib.sha256()
    for artifact in sorted(
        report.interpretation_result.generated_prsm_artifacts,
        key=lambda item: item.file_name.encode("utf-8"),
    ):
        name = artifact.file_name.encode("utf-8")
        digest.update(struct.pack("<Q", len(name)))
        digest.update(name)
        digest.update(bytes.fromhex(artifact.sha256))
    return digest.hexdigest()


def _performance_metrics(
    context: PipelineContext,
    raw_report: RealThermoRawExecutionReport | None,
    dia_report: RealDiaResultExecutionReport | None,
    *,
    output_size: int,
    keep_intermediate: bool,
) -> dict[str, int | float | str]:
    durations = {
        entry.step_name: (entry.finished_at - entry.started_at).total_seconds()
        for entry in context.logs
        if entry.status == "completed" and entry.finished_at is not None
    }
    metrics: dict[str, int | float | str] = {
        "zp_write_seconds": durations.get("zp_write", 0.0),
        "zp_validate_seconds": durations.get("zp_validate", 0.0),
        "parent_process_peak_rss": _parent_peak_rss(),
        "converter_process_peak_rss": "not_measured",
        "inspect_seconds": float(context.metadata.get("inspect_seconds", 0.0)),
        "input_size": int(context.metadata.get("input_file_size", 0)),
        "output_size": output_size,
    }
    writer_metrics = context.artifacts.get("zp_writer_metrics")
    if isinstance(writer_metrics, dict):
        for key, value in writer_metrics.items():
            if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                metrics[f"writer_{key}"] = value
    validation = context.artifacts.get("validation_result")
    if isinstance(validation, ValidationResult):
        for key, value in validation.metrics.items():
            if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                metrics[f"validator_{key}"] = value
    if dia_report is not None:
        metrics.update(
            {
                "mzml_admission_seconds": dia_report.mzml_admission_seconds,
                "mzml_parse_seconds": dia_report.mzml_parse_seconds,
                "mzml_block_build_seconds": dia_report.mzml_block_build_seconds,
                "mzml_parse_cpu_seconds": dia_report.mzml_parse_cpu_seconds,
                "mzml_admission_cpu_seconds": dia_report.mzml_admission_cpu_seconds,
                "mzml_block_build_cpu_seconds": dia_report.mzml_block_build_cpu_seconds,
                "parquet_parse_seconds": dia_report.parquet_parse_seconds,
                "parquet_parse_cpu_seconds": dia_report.parquet_parse_cpu_seconds,
                "association_seconds": dia_report.association_seconds,
                "association_cpu_seconds": dia_report.association_cpu_seconds,
                "extension_build_seconds": dia_report.extension_build_seconds,
                "extension_build_cpu_seconds": dia_report.extension_build_cpu_seconds,
                "parquet_batch_count": dia_report.parquet_batch_count,
                "parquet_row_count": dia_report.parquet_row_count,
                "spectrum_count": dia_report.spectrum_count,
                "array_count": dia_report.array_count,
                "array_value_count": dia_report.array_value_count,
                "temporary_disk_peak": output_size,
            }
        )
        return metrics
    if raw_report is None:
        metrics.update(
            {
                "raw_to_mzml_seconds": "not_applicable",
                "mzml_admission_seconds": "not_applicable",
                "mzml_parse_seconds": "not_applicable",
                "temporary_disk_peak": output_size,
            }
        )
        return metrics

    intermediate_size = raw_report.adapter_result.intermediate_file_size
    metrics.update(
        {
            "raw_to_mzml_seconds": raw_report.adapter_result.raw_to_mzml_seconds,
            "mzml_admission_seconds": raw_report.mzml_report.admission_seconds,
            "mzml_parse_seconds": raw_report.mzml_report.parse_seconds + raw_report.mzml_report.block_build_seconds,
            "temporary_disk_peak": intermediate_size + output_size if keep_intermediate else max(intermediate_size, output_size),
        }
    )
    return metrics


def _parent_peak_rss() -> int | str:
    try:
        import psutil  # type: ignore[import-not-found]

        memory = psutil.Process().memory_info()
        peak = getattr(memory, "peak_wset", None)
        if isinstance(peak, int) and peak > 0:
            return peak
    except (ImportError, OSError):
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = (
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                )

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = (
                wintypes.HANDLE,
                ctypes.POINTER(ProcessMemoryCounters),
                wintypes.DWORD,
            )
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            process = kernel32.GetCurrentProcess()
            if psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
                return int(counters.PeakWorkingSetSize)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    else:
        try:
            import resource
            import sys

            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if isinstance(peak, int) and peak > 0:
                return peak if sys.platform == "darwin" else peak * 1024
        except (ImportError, OSError, ValueError):
            pass
    return "not_measured"
