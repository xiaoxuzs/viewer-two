from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
import tracemalloc
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any, Iterator

from binary_layer import PipelineContext, PlanBuilder, SourceInspector, ZpReader, build_default_registry
from binary_layer.constants import HEADER_STRUCT
from binary_layer.serialization import canonical_json_bytes

from benchmarks.models import BENCHMARK_VERSION, BenchmarkResult
from benchmarks.monitor import ProcessMonitor, monitor_child_limits, physical_memory_bytes


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _snapshot(name: str, target: list[dict[str, Any]]) -> None:
    current, peak = tracemalloc.get_traced_memory()
    snapshot = tracemalloc.take_snapshot()
    top = []
    for item in snapshot.statistics("lineno")[:20]:
        frame = item.traceback[0]
        top.append({"file": str(frame.filename), "line": frame.lineno, "size_bytes": item.size, "count": item.count})
    target.append({"name": name, "current_bytes": current, "peak_bytes": peak, "top_allocations": top})


def _capture_snapshot(name: str, target: list[dict[str, Any]], stages: dict[str, float], enabled: bool) -> None:
    if not enabled:
        return
    started = time.perf_counter()
    _snapshot(name, target)
    elapsed = time.perf_counter() - started
    stages[f"snapshot_{name}_seconds"] = elapsed
    stages["snapshot_seconds"] = stages.get("snapshot_seconds", 0.0) + elapsed


@contextmanager
def _instrument(stages: dict[str, float], snapshots: list[dict[str, Any]], capture_snapshots: bool) -> Iterator[None]:
    import binary_layer.tools.real_mzml as real_module
    import binary_layer.validator as validator_module
    import binary_layer.writer as writer_module

    original_parse = real_module.parse_mzml
    original_admission = real_module.evaluate_mzml_admission
    original_candidate = real_module._build_candidate
    original_serialize = writer_module.ZpWriter.__dict__["_serialize_blocks"]
    original_parse_json = validator_module.parse_json_bytes
    original_schema = validator_module.ZpValidator.__dict__["_validate_schema"]
    original_references = validator_module.ZpValidator.__dict__["_validate_references"]

    def timed_parse(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        result = original_parse(*args, **kwargs)
        stages["parse_seconds"] = time.perf_counter() - started
        _capture_snapshot("after_parse", snapshots, stages, capture_snapshots)
        return result

    def timed_admission(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        result = original_admission(*args, **kwargs)
        stages["admission_seconds"] = time.perf_counter() - started
        return result

    def timed_candidate(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        result = original_candidate(*args, **kwargs)
        stages["candidate_block_seconds"] = time.perf_counter() - started
        _capture_snapshot("after_candidate_blocks", snapshots, stages, capture_snapshots)
        return result

    def timed_serialize(blocks: Any) -> Any:
        started = time.perf_counter()
        result = original_serialize.__func__(blocks)
        stages["json_serialize_seconds"] = time.perf_counter() - started
        _capture_snapshot("after_writer_serialization", snapshots, stages, capture_snapshots)
        return result

    def timed_parse_json(payload: bytes) -> Any:
        started = time.perf_counter()
        result = original_parse_json(payload)
        stages["validator_json_seconds"] = stages.get("validator_json_seconds", 0.0) + time.perf_counter() - started
        return result

    def timed_schema(cls: Any, blocks: Any, add: Any) -> None:
        started = time.perf_counter()
        original_schema.__func__(cls, blocks, add)
        stages["validator_schema_seconds"] = time.perf_counter() - started

    def timed_references(cls: Any, blocks: Any, add: Any) -> None:
        started = time.perf_counter()
        original_references.__func__(cls, blocks, add)
        stages["validator_relationship_seconds"] = time.perf_counter() - started

    real_module.parse_mzml = timed_parse
    real_module.evaluate_mzml_admission = timed_admission
    real_module._build_candidate = timed_candidate
    writer_module.ZpWriter._serialize_blocks = staticmethod(timed_serialize)
    validator_module.parse_json_bytes = timed_parse_json
    validator_module.ZpValidator._validate_schema = classmethod(timed_schema)
    validator_module.ZpValidator._validate_references = classmethod(timed_references)
    try:
        yield
    finally:
        real_module.parse_mzml = original_parse
        real_module.evaluate_mzml_admission = original_admission
        real_module._build_candidate = original_candidate
        writer_module.ZpWriter._serialize_blocks = original_serialize
        validator_module.parse_json_bytes = original_parse_json
        validator_module.ZpValidator._validate_schema = original_schema
        validator_module.ZpValidator._validate_references = original_references


def _block_analysis(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], float]:
    reader = ZpReader(path)
    directory = reader.read_directory()
    block_stats: list[dict[str, Any]] = []
    arrays_payload: bytes | None = None
    checksum_total = 0.0
    file_size = path.stat().st_size
    with path.open("rb") as stream:
        for entry in directory:
            stream.seek(entry.offset)
            payload = stream.read(entry.length)
            started = time.perf_counter()
            hashlib.sha256(payload).hexdigest()
            checksum_seconds = time.perf_counter() - started
            checksum_total += checksum_seconds
            started = time.perf_counter()
            decoded = json.loads(payload.decode("utf-8"))
            decode_seconds = time.perf_counter() - started
            record_count = len(decoded) if isinstance(decoded, list) else 1
            block_stats.append({
                "block_name": entry.block_name,
                "offset": entry.offset,
                "length": entry.length,
                "fraction_of_file": entry.length / file_size,
                "record_count": record_count,
                "checksum_seconds": checksum_seconds,
                "json_decode_seconds": decode_seconds,
            })
            if entry.block_name == "arrays":
                arrays_payload = payload
    if arrays_payload is None:
        raise RuntimeError("arrays block missing from valid output")
    arrays = json.loads(arrays_payload.decode("utf-8"))
    lengths = [len(item["values"]) for item in arrays]
    type_counts = {
        kind: sum(len(item["values"]) for item in arrays if item["array_type"] == kind)
        for kind in ("mz", "intensity", "time")
    }
    numeric_bytes = 0
    structural_bytes = 0
    array_id_value_bytes = 0
    for item in arrays:
        values_bytes = canonical_json_bytes(item["values"])
        numeric_bytes += max(0, len(values_bytes) - 2)
        structural_bytes += len(canonical_json_bytes(item)) - max(0, len(values_bytes) - 2)
        array_id_value_bytes += len(canonical_json_bytes(item["array_id"]))
    total_values = sum(lengths)
    array_stats = {
        "array_count": len(arrays),
        "total_numeric_values": total_values,
        "mz_values": type_counts["mz"],
        "intensity_values": type_counts["intensity"],
        "time_values": type_counts["time"],
        "json_bytes": len(arrays_payload),
        "average_bytes_per_numeric_value": len(arrays_payload) / total_values if total_values else None,
        "average_bytes_per_array": len(arrays_payload) / len(arrays) if arrays else None,
        "largest_array_length": max(lengths) if lengths else None,
        "smallest_array_length": min(lengths) if lengths else None,
        "median_array_length": statistics.median(lengths) if lengths else None,
        "numeric_decimal_bytes": numeric_bytes,
        "record_structure_and_metadata_bytes": structural_bytes,
        "array_id_value_bytes": array_id_value_bytes,
        "array_id_key_bytes": arrays_payload.count(b'\"array_id\"') * len(b'\"array_id\":'),
        "array_type_key_bytes": arrays_payload.count(b'\"array_type\"') * len(b'\"array_type\":'),
        "dtype_key_bytes": arrays_payload.count(b'\"dtype\"') * len(b'\"dtype\":'),
        "values_key_bytes": arrays_payload.count(b'\"values\"') * len(b'\"values\":'),
    }
    return block_stats, array_stats, checksum_total


def _environment() -> dict[str, Any]:
    disk = shutil.disk_usage(str(Path.cwd().anchor or Path.cwd()))
    try:
        psutil_version = version("psutil")
    except Exception:
        psutil_version = None
    return {
        "cpu": platform.processor() or None,
        "cpu_count_logical": os.cpu_count(),
        "physical_memory_bytes": physical_memory_bytes(),
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "disk_type": None,
        "disk_type_reason": "Windows volume API does not expose reliable SSD/HDD media type without elevated WMI access",
        "psutil_version": psutil_version,
        "rss_backend": "psutil" if psutil_version else "Windows GetProcessMemoryInfo" if sys.platform == "win32" else "/proc when available",
    }


def _run_worker(args: argparse.Namespace) -> BenchmarkResult:
    source = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(character if character.isalnum() or character in "-_" else "_" for character in args.run_label)
    output = output_dir / f"{source.stem}.{safe_label}.zp"
    temporary = output.with_name(output.name + ".tmp")
    snapshots: list[dict[str, Any]] = []
    stages: dict[str, float] = {}
    monitor = ProcessMonitor(temporary)
    tracemalloc.start()
    _capture_snapshot("start", snapshots, stages, args.capture_snapshots)
    monitor.start()
    started_pipeline = time.perf_counter()
    context: PipelineContext | None = None
    failure: Exception | None = None
    try:
        profile = SourceInspector().inspect([source])
        plan = PlanBuilder().build(profile)
        context = PipelineContext(profile, metadata={"output_path": output})
        registry = build_default_registry()
        with _instrument(stages, snapshots, args.capture_snapshots):
            for step_name in plan.required_steps:
                started = time.perf_counter()
                registry.get(step_name).run(context)
                elapsed = time.perf_counter() - started
                if step_name == "zp_write":
                    elapsed = max(0.0, elapsed - stages.get("snapshot_after_writer_serialization_seconds", 0.0))
                stages[step_name] = elapsed
                if step_name == "zp_write":
                    stages["writer_disk_seconds"] = max(0.0, elapsed - stages.get("json_serialize_seconds", 0.0))
                    _capture_snapshot("after_write", snapshots, stages, args.capture_snapshots)
                elif step_name == "zp_validate":
                    _capture_snapshot("after_validator", snapshots, stages, args.capture_snapshots)
        reader_started = time.perf_counter()
        reader = ZpReader(output)
        reader.read_header()
        reader.read_directory()
        spectra = reader.read_spectra()
        reader.read_arrays()
        if spectra:
            reader.read_spectrum_arrays(spectra[0].spectrum_id)
        if context.blocks.chromatograms:
            reader.read_chromatograms()
        stages["reader_seconds"] = time.perf_counter() - reader_started
        _capture_snapshot("after_reader", snapshots, stages, args.capture_snapshots)
    except Exception as exc:
        failure = exc
    instrumented_pipeline_seconds = time.perf_counter() - started_pipeline
    pipeline_seconds = max(0.0, instrumented_pipeline_seconds - stages.get("snapshot_seconds", 0.0))
    monitor.stop()
    current, traced_peak = tracemalloc.get_traced_memory()
    peak = max((item["peak_bytes"] for item in snapshots), default=traced_peak)
    tracemalloc.stop()

    counts: dict[str, int | None] = {name: None for name in ("spectrum", "ms1", "ms2", "precursor", "chromatogram", "array", "peak")}
    block_stats: list[dict[str, Any]] = []
    array_stats: dict[str, Any] = {}
    checksum_seconds: float | None = None
    valid = False
    if context is not None:
        blocks = context.blocks
        counts.update(
            spectrum=len(blocks.spectra),
            ms1=sum(item.ms_level == 1 for item in blocks.spectra),
            ms2=sum(item.ms_level == 2 for item in blocks.spectra),
            precursor=len(blocks.precursors),
            chromatogram=len(blocks.chromatograms),
            array=len(blocks.arrays),
            peak=sum(len(item.values) for item in blocks.arrays if item.array_type == "mz"),
        )
        validation = context.artifacts.get("validation_result")
        valid = bool(getattr(validation, "valid", False)) and failure is None
    if output.exists() and valid:
        block_stats, array_stats, checksum_seconds = _block_analysis(output)
    input_size = source.stat().st_size if source.exists() else None
    zp_size = output.stat().st_size if output.exists() else None
    peak_count = counts["peak"]
    reasons: dict[str, str] = {}
    if not monitor.available:
        for field in ("rss_start_bytes", "rss_peak_bytes", "rss_end_bytes", "windows_peak_working_set_bytes"):
            reasons[field] = "neither psutil nor a supported native process-memory backend was available"
    if failure is not None:
        reasons["incomplete_metrics"] = "conversion stopped before all stages completed"
    indexed = None
    if context is not None and context.blocks.extensions:
        try:
            indexed = bool(context.blocks.extensions[0].payload["source"]["indexed"])
        except (KeyError, TypeError):
            pass
    result = BenchmarkResult(
        benchmark_version=BENCHMARK_VERSION,
        timestamp=_now(),
        python_version=platform.python_version(),
        platform=platform.platform(),
        cpu_count=os.cpu_count(),
        physical_memory=physical_memory_bytes(),
        pyteomics_version=version("pyteomics"),
        input_path=str(source),
        input_kind=args.input_kind,
        input_size=input_size,
        indexed=indexed,
        spectrum_count=counts["spectrum"],
        ms1_count=counts["ms1"],
        ms2_count=counts["ms2"],
        precursor_count=counts["precursor"],
        chromatogram_count=counts["chromatogram"],
        array_count=counts["array"],
        peak_count=peak_count,
        file_validate_seconds=stages.get("file_validate"),
        hash_seconds=stages.get("hash_input"),
        parse_seconds=stages.get("parse_seconds"),
        block_build_seconds=stages.get("candidate_block_seconds"),
        string_pool_seconds=stages.get("string_pool_build"),
        index_seconds=stages.get("index_build"),
        writer_seconds=stages.get("zp_write"),
        validator_seconds=stages.get("zp_validate"),
        reader_seconds=stages.get("reader_seconds"),
        pipeline_seconds=pipeline_seconds,
        tracemalloc_peak_bytes=peak,
        rss_start_bytes=monitor.rss_start_bytes,
        rss_peak_bytes=monitor.rss_peak_bytes,
        rss_end_bytes=monitor.rss_end_bytes,
        temporary_file_peak_bytes=max(monitor.temporary_file_peak_bytes, zp_size or 0),
        zp_size=zp_size,
        size_ratio=zp_size / input_size if zp_size is not None and input_size else None,
        bytes_per_peak=zp_size / peak_count if zp_size is not None and peak_count else None,
        arrays_block_size=array_stats.get("json_bytes"),
        arrays_fraction_of_zp=array_stats.get("json_bytes") / zp_size if zp_size and array_stats.get("json_bytes") is not None else None,
        valid=valid,
        failure_code=None if valid else type(failure).__name__ if failure is not None else "VALIDATION_FAILED",
        failure_message=None if valid else str(failure) if failure is not None else "generated output did not validate",
        run_label=args.run_label,
        tracemalloc_current_bytes=current,
        windows_peak_working_set_bytes=monitor.windows_peak_working_set_bytes,
        admission_seconds=stages.get("admission_seconds"),
        candidate_block_seconds=stages.get("candidate_block_seconds"),
        json_serialize_seconds=stages.get("json_serialize_seconds"),
        writer_disk_seconds=stages.get("writer_disk_seconds"),
        validator_checksum_seconds=checksum_seconds,
        validator_json_seconds=stages.get("validator_json_seconds"),
        validator_relationship_seconds=stages.get("validator_relationship_seconds"),
        metric_unavailable_reasons=reasons,
        tracemalloc_snapshots=snapshots,
        block_stats=block_stats,
        array_stats=array_stats,
        environment={
            **_environment(),
            "capture_tracemalloc_snapshots": args.capture_snapshots,
            "snapshot_instrumentation_seconds": stages.get("snapshot_seconds", 0.0),
            "instrumented_pipeline_wall_seconds": instrumented_pipeline_seconds,
        },
    )
    args.worker_result.write_text(result.to_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    if output.exists() and not args.keep_zp:
        output.unlink()
    temporary.unlink(missing_ok=True)
    return result


def _resource_failure(args: argparse.Namespace, message: str) -> BenchmarkResult:
    source = args.input.resolve()
    return BenchmarkResult(
        benchmark_version=BENCHMARK_VERSION,
        timestamp=_now(),
        python_version=platform.python_version(),
        platform=platform.platform(),
        cpu_count=os.cpu_count(),
        physical_memory=physical_memory_bytes(),
        pyteomics_version=version("pyteomics"),
        input_path=str(source),
        input_kind=args.input_kind,
        input_size=source.stat().st_size if source.exists() else None,
        indexed=None,
        spectrum_count=None,
        ms1_count=None,
        ms2_count=None,
        precursor_count=None,
        chromatogram_count=None,
        array_count=None,
        peak_count=None,
        file_validate_seconds=None,
        hash_seconds=None,
        parse_seconds=None,
        block_build_seconds=None,
        string_pool_seconds=None,
        index_seconds=None,
        writer_seconds=None,
        validator_seconds=None,
        reader_seconds=None,
        pipeline_seconds=None,
        tracemalloc_peak_bytes=None,
        rss_start_bytes=None,
        rss_peak_bytes=None,
        rss_end_bytes=None,
        temporary_file_peak_bytes=None,
        zp_size=None,
        size_ratio=None,
        bytes_per_peak=None,
        arrays_block_size=None,
        arrays_fraction_of_zp=None,
        valid=False,
        failure_code="RESOURCE_LIMIT_REACHED",
        failure_message=message,
        run_label=args.run_label,
        metric_unavailable_reasons={"incomplete_metrics": "worker was terminated by the parent resource guard"},
        environment=_environment(),
    )


def _run_parent(args: argparse.Namespace) -> BenchmarkResult:
    args.result.parent.mkdir(parents=True, exist_ok=True)
    worker_result = args.result.with_suffix(args.result.suffix + ".tmp.json")
    output = args.output_dir.resolve() / f"{args.input.stem}.{args.run_label}.zp"
    command = [
        sys.executable,
        "-m",
        "benchmarks.benchmark_mzml_conversion",
        "--worker",
        "--input", str(args.input),
        "--input-kind", args.input_kind,
        "--output-dir", str(args.output_dir),
        "--run-label", args.run_label,
        "--worker-result", str(worker_result),
    ]
    if args.keep_zp:
        command.append("--keep-zp")
    if not args.capture_snapshots:
        command.append("--no-capture-snapshots")
    started = time.perf_counter()
    process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1])
    limit_message: str | None = None
    while process.poll() is None:
        limit_message = monitor_child_limits(
            process.pid,
            started_at=started,
            max_rss_bytes=int(args.max_rss_gb * 1024 ** 3),
            max_runtime_seconds=args.max_runtime_seconds,
            max_output_bytes=int(args.max_output_gb * 1024 ** 3),
            output_paths=(output, output.with_name(output.name + ".tmp")),
        )
        if limit_message is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            break
        time.sleep(0.05)
    if limit_message is not None:
        result = _resource_failure(args, limit_message)
    elif process.returncode != 0 or not worker_result.exists():
        result = _resource_failure(args, f"worker exited with code {process.returncode} without a result")
        result.failure_code = "WORKER_FAILED"
    else:
        result = BenchmarkResult.from_json(worker_result.read_text(encoding="utf-8"))
    args.result.write_text(result.to_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    worker_result.unlink(missing_ok=True)
    if not args.keep_zp or not result.valid:
        output.unlink(missing_ok=True)
    output.with_name(output.name + ".tmp").unlink(missing_ok=True)
    print(result.to_json())
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P1-B6 isolated mzML conversion benchmark")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--input-kind", default="real")
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks/generated"))
    parser.add_argument("--result", type=Path, default=Path("benchmarks/results/conversion.json"))
    parser.add_argument("--run-label", default="run1")
    parser.add_argument("--keep-zp", action="store_true")
    parser.add_argument("--capture-snapshots", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-rss-gb", type=float, default=6.0)
    parser.add_argument("--max-runtime-seconds", type=float, default=600.0)
    parser.add_argument("--max-output-gb", type=float, default=2.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-result", type=Path, help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.worker:
        if args.worker_result is None:
            raise SystemExit("--worker-result is required in worker mode")
        result = _run_worker(args)
    else:
        result = _run_parent(args)
    return 0 if result.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
