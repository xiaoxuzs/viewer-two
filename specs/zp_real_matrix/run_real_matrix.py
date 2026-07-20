from __future__ import annotations

import argparse
import gc
import hashlib
import json
import random
import re
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_layer import (  # noqa: E402
    PipelineContext,
    PipelineRunner,
    PlanBuilder,
    SourceInspector,
    ZpReader,
    ZpValidator,
    ZpWriter,
    build_default_registry,
    migrate_v1_to_v2,
)
from binary_layer.constants import (  # noqa: E402
    DEFAULT_ZP_WRITE_VERSION,
    KNOWN_ZP_VERSIONS,
    SUPPORTED_ZP_READ_VERSIONS,
    SUPPORTED_ZP_VALIDATE_VERSIONS,
    SUPPORTED_ZP_WRITE_VERSIONS,
    ZP_VERSION,
)
from binary_layer.logical_fingerprint import (  # noqa: E402
    LogicalArrayFingerprint,
    LogicalFingerprint,
    build_logical_fingerprint,
)
from binary_layer.v2_arrays_reader import ZpV2ArraysReader  # noqa: E402
from specs.zp_full.compatibility_gate import _ResourceMonitor  # noqa: E402
from specs.zp_real_matrix.inspection import inspect_sample, sha256_file  # noqa: E402


RESULT_PATH = Path(__file__).with_name("results") / "real_matrix_summary.json"
RANDOM_SEED = 8707
SAMPLE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
REQUIRED_COVERAGE = (
    "indexed_mzml",
    "nonindexed_mzml",
    "float32_arrays",
    "float64_arrays",
    "zlib_compressed",
    "uncompressed",
    "ms1_only",
    "ms1_ms2",
    "precursor",
    "tic_or_bpc_chromatogram",
    "over_30mb",
)
NON_ARRAY_BLOCKS = (
    "global_meta",
    "string_pool",
    "core_runs",
    "core_spectra",
    "core_precursors",
    "core_chromatograms",
    "indexes",
    "extensions",
)


def _progress(stage: str, sample_id: str | None = None) -> None:
    payload = {"stage": stage}
    if sample_id is not None:
        payload["sample_id"] = sample_id
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def _production_hashes() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in sorted((ROOT / "binary_layer").rglob("*.py"))
    }


def _array_hash(values: list[float]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(struct.pack("<d", value))
    return digest.hexdigest()


def _logical_fingerprint(path: Path) -> LogicalFingerprint:
    reader = ZpReader(path)
    blocks = {name: reader.read_block(name) for name in NON_ARRAY_BLOCKS}
    if reader.read_header().version == 1:
        arrays = (
            LogicalArrayFingerprint(
                array_id=item.array_id,
                array_type=item.array_type,
                value_count=len(item.values),
                logical_sha256=_array_hash(item.values),
            )
            for item in reader.read_arrays()
        )
        return build_logical_fingerprint(blocks, arrays)
    arrays_entry = next(item for item in reader.read_directory() if item.block_name == "arrays")
    with path.open("rb") as stream:
        directory = ZpV2ArraysReader().read_directory(
            stream,
            block_offset=arrays_entry.offset,
            block_length=arrays_entry.length,
        )
    arrays = (
        LogicalArrayFingerprint(item.array_id, item.array_type, item.value_count, item.checksum)
        for item in directory.entries
    )
    return build_logical_fingerprint(blocks, arrays)


def _files_equal(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            left_chunk = left_stream.read(1024 * 1024)
            right_chunk = right_stream.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _statistics(blocks: object) -> dict[str, int]:
    spectra = blocks.spectra
    return {
        "run_count": len(blocks.runs),
        "spectrum_count": len(spectra),
        "ms1_count": sum(item.ms_level == 1 for item in spectra),
        "ms2_count": sum(item.ms_level == 2 for item in spectra),
        "precursor_count": len(blocks.precursors),
        "chromatogram_count": len(blocks.chromatograms),
        "array_count": len(blocks.arrays),
        "numeric_value_count": sum(len(item.values) for item in blocks.arrays),
        "extension_count": len(blocks.extensions),
    }


def _build_blocks(source: Path) -> tuple[object, float, list[str]]:
    profile = SourceInspector().inspect([source])
    full_plan = PlanBuilder().build(profile)
    expected = (
        "file_validate",
        "hash_input",
        "real_mzml_parse",
        "string_pool_build",
        "index_build",
    )
    if full_plan.required_steps[: len(expected)] != expected:
        raise RuntimeError("real mzML conversion plan prefix changed")
    registry = build_default_registry()
    if registry.get("real_mzml_parse").name != "real_mzml_parse":
        raise RuntimeError("Registry did not select RealMzmlParseTool")
    block_plan = replace(full_plan, required_steps=expected)
    context = PipelineContext(profile)
    PipelineRunner().run(block_plan, registry, context)
    parse_log = next(
        item
        for item in context.logs
        if item.step_name == "real_mzml_parse" and item.status == "completed"
    )
    if parse_log.finished_at is None:
        raise RuntimeError("real_mzml_parse did not finish")
    parse_seconds = (parse_log.finished_at - parse_log.started_at).total_seconds()
    completed = [item.step_name for item in context.logs if item.status == "completed"]
    return context.blocks, parse_seconds, completed


def _reader_matrix(v1_path: Path, v2_path: Path, migrated_path: Path) -> dict[str, object]:
    started = time.perf_counter()
    readers = {"v1": ZpReader(v1_path), "v2": ZpReader(v2_path), "migrated_v2": ZpReader(migrated_path)}
    spectra = {name: reader.read_spectra() for name, reader in readers.items()}
    precursors = {name: reader.read_precursors() for name, reader in readers.items()}
    chromatograms = {name: reader.read_chromatograms() for name, reader in readers.items()}

    rng = random.Random(RANDOM_SEED)
    spectrum_ids = sorted(item.spectrum_id for item in spectra["v1"])
    sampled_spectrum_ids = sorted(rng.sample(spectrum_ids, min(100, len(spectrum_ids))))
    precursor_ids = sorted(item.precursor_id for item in precursors["v1"])
    sampled_precursor_ids = sorted(rng.sample(precursor_ids, min(100, len(precursor_ids))))
    all_chromatogram_ids = sorted(item.chromatogram_id for item in chromatograms["v1"])
    sampled_chromatogram_ids = (
        all_chromatogram_ids
        if len(all_chromatogram_ids) <= 100
        else sorted(rng.sample(all_chromatogram_ids, 100))
    )

    v1_all_arrays = readers["v1"].read_arrays()
    all_array_ids = sorted(item.array_id for item in v1_all_arrays)
    sampled_array_ids = sorted(rng.sample(all_array_ids, min(100, len(all_array_ids))))
    spectra_by_id = {
        name: {item.spectrum_id: item for item in values}
        for name, values in spectra.items()
    }
    chrom_by_id = {
        name: {item.chromatogram_id: item for item in values}
        for name, values in chromatograms.items()
    }
    needed_array_ids = set(sampled_array_ids)
    for spectrum_id in sampled_spectrum_ids:
        item = spectra_by_id["v1"][spectrum_id]
        needed_array_ids.update((item.mz_array_id, item.intensity_array_id))
    for chromatogram_id in sampled_chromatogram_ids:
        item = chrom_by_id["v1"][chromatogram_id]
        needed_array_ids.update((item.time_array_id, item.intensity_array_id))
    v1_arrays = {item.array_id: item for item in v1_all_arrays if item.array_id in needed_array_ids}
    del v1_all_arrays
    gc.collect()
    arrays = {
        "v1": v1_arrays,
        "v2": {array_id: readers["v2"].read_array(array_id) for array_id in sorted(needed_array_ids)},
        "migrated_v2": {
            array_id: readers["migrated_v2"].read_array(array_id)
            for array_id in sorted(needed_array_ids)
        },
    }
    precursor_by_id = {
        name: {item.precursor_id: item for item in values}
        for name, values in precursors.items()
    }
    spectrum_equal = all(
        spectra_by_id["v1"][item] == spectra_by_id["v2"][item] == spectra_by_id["migrated_v2"][item]
        for item in sampled_spectrum_ids
    )
    precursor_equal = all(
        precursor_by_id["v1"][item]
        == precursor_by_id["v2"][item]
        == precursor_by_id["migrated_v2"][item]
        for item in sampled_precursor_ids
    )
    chromatogram_equal = all(
        chrom_by_id["v1"][item] == chrom_by_id["v2"][item] == chrom_by_id["migrated_v2"][item]
        for item in sampled_chromatogram_ids
    )
    arrays_equal = all(
        arrays["v1"][item] == arrays["v2"][item] == arrays["migrated_v2"][item]
        for item in needed_array_ids
    )
    return {
        "seed": RANDOM_SEED,
        "spectrum_sample_count": len(sampled_spectrum_ids),
        "array_sample_count": len(sampled_array_ids),
        "precursor_sample_count": len(sampled_precursor_ids),
        "chromatogram_sample_count": len(sampled_chromatogram_ids),
        "all_chromatograms_sampled": len(sampled_chromatogram_ids) == len(all_chromatogram_ids),
        "spectrum_equal": spectrum_equal,
        "array_equal": arrays_equal,
        "precursor_equal": precursor_equal,
        "chromatogram_equal": chromatogram_equal,
        "reader_equal": spectrum_equal and arrays_equal and precursor_equal and chromatogram_equal,
        "reader_sample_seconds": round(time.perf_counter() - started, 6),
    }


def _accepted_flow(path: Path, inspection: dict[str, object], directory: Path) -> dict[str, object]:
    started = time.perf_counter()
    blocks, parse_seconds, pipeline_steps = _build_blocks(path)
    statistics = _statistics(blocks)
    v1_path = directory / "direct-v1.zp"
    v2_path = directory / "direct-v2.zp"
    migrated_path = directory / "migrated-v2.zp"
    fixed_header_time = int(time.time() * 1000) / 1000
    with mock.patch("binary_layer.writer.time.time", return_value=fixed_header_time):
        before = time.perf_counter()
        ZpWriter().write(v1_path, blocks, format_version=1)
        v1_write_seconds = time.perf_counter() - before
        before = time.perf_counter()
        ZpWriter().write(v2_path, blocks, format_version=2)
        v2_write_seconds = time.perf_counter() - before
    del blocks
    gc.collect()

    before = time.perf_counter()
    v1_validation = ZpValidator().validate(v1_path)
    v1_validate_seconds = time.perf_counter() - before
    before = time.perf_counter()
    v2_validation = ZpValidator().validate(v2_path)
    v2_validate_seconds = time.perf_counter() - before

    source_v1_sha_before = sha256_file(v1_path)
    before = time.perf_counter()
    migration = migrate_v1_to_v2(v1_path, migrated_path)
    migration_seconds = time.perf_counter() - before
    source_v1_sha_after = sha256_file(v1_path)
    migrated_validation = ZpValidator().validate(migrated_path)

    v1_fingerprint = _logical_fingerprint(v1_path)
    v2_fingerprint = _logical_fingerprint(v2_path)
    migrated_fingerprint = _logical_fingerprint(migrated_path)
    logical_equal = v1_fingerprint.sha256 == v2_fingerprint.sha256 == migrated_fingerprint.sha256
    array_hashes_equal = v1_fingerprint.arrays == v2_fingerprint.arrays == migrated_fingerprint.arrays
    reader = _reader_matrix(v1_path, v2_path, migrated_path)
    direct_v2_sha256 = sha256_file(v2_path)
    migrated_v2_sha256 = sha256_file(migrated_path)
    byte_equal = _files_equal(v2_path, migrated_path)
    source_unchanged = source_v1_sha_before == source_v1_sha_after
    valid_results = (v1_validation, v2_validation, migrated_validation)
    complete_validation = all(
        item.valid and item.checked_blocks == 9 and item.issues == []
        for item in valid_results
    )
    passed = all(
        (
            complete_validation,
            logical_equal,
            array_hashes_equal,
            byte_equal,
            source_unchanged,
            migration.source_logical_fingerprint == migration.target_logical_fingerprint,
            reader["reader_equal"],
        )
    )
    return {
        **inspection,
        "statistics": statistics,
        "v1_size": v1_path.stat().st_size,
        "v2_size": v2_path.stat().st_size,
        "migrated_v2_size": migrated_path.stat().st_size,
        "size_ratio": round(v2_path.stat().st_size / v1_path.stat().st_size, 6),
        "validation": {
            "v1_valid": v1_validation.valid,
            "v2_valid": v2_validation.valid,
            "migrated_v2_valid": migrated_validation.valid,
            "v1_checked_blocks": v1_validation.checked_blocks,
            "v2_checked_blocks": v2_validation.checked_blocks,
            "migrated_v2_checked_blocks": migrated_validation.checked_blocks,
            "v1_issues": [item.code for item in v1_validation.issues],
            "v2_issues": [item.code for item in v2_validation.issues],
            "migrated_v2_issues": [item.code for item in migrated_validation.issues],
        },
        "logical_equal": logical_equal,
        "all_array_hashes_equal": array_hashes_equal,
        "migration_result": "passed" if byte_equal and source_unchanged else "failed",
        "direct_v2_sha256": direct_v2_sha256,
        "migrated_v2_sha256": migrated_v2_sha256,
        "migrated_equals_direct_v2": byte_equal and direct_v2_sha256 == migrated_v2_sha256,
        "source_unchanged": source_unchanged,
        "source_logical_fingerprint": v1_fingerprint.sha256,
        "migrated_logical_fingerprint": migrated_fingerprint.sha256,
        "reader": reader,
        "reader_equal": reader["reader_equal"],
        "pipeline_steps": pipeline_steps,
        "timings": {
            "inspection_seconds": inspection["inspection_seconds"],
            "admission_seconds": inspection["admission_seconds"],
            "parse_seconds": round(parse_seconds, 6),
            "v1_write_seconds": round(v1_write_seconds, 6),
            "v2_write_seconds": round(v2_write_seconds, 6),
            "v1_validate_seconds": round(v1_validate_seconds, 6),
            "v2_validate_seconds": round(v2_validate_seconds, 6),
            "migration_seconds": round(migration_seconds, 6),
            "reader_sample_seconds": reader["reader_sample_seconds"],
            "total_seconds": round(time.perf_counter() - started, 6),
        },
        "migration_memory": {
            "source_validation_peak_rss": migration.source_validator_peak_rss,
            "stream_conversion_peak_rss": migration.conversion_peak_rss,
            "destination_validation_peak_rss": migration.target_validator_peak_rss,
            "whole_migration_peak_rss": migration.peak_rss,
        },
        "migration_temporary_disk_peak": migration.temporary_disk_peak,
        "passed": passed,
    }


def _run_json_command(command: list[str]) -> tuple[bool, dict[str, object], str]:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        report = {}
    return completed.returncode == 0, report, completed.stderr[-2000:]


def _regression_gates(run_pytest: bool, run_existing_gates: bool) -> dict[str, object]:
    result: dict[str, object] = {
        "pytest": {"passed": False, "status": "not_run"},
        "b8_5": {"release_gate": False, "status": "not_run"},
        "b8_6": {"release_gate": False, "status": "not_run"},
    }
    if run_pytest:
        _progress("pytest")
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        lines = (completed.stdout + completed.stderr).strip().splitlines()
        result["pytest"] = {
            "passed": completed.returncode == 0,
            "returncode": completed.returncode,
            "summary": lines[-1] if lines else "",
        }
    if run_existing_gates:
        _progress("b8_5_gate")
        ok, report, stderr = _run_json_command([sys.executable, "specs/zp_full/compatibility_gate.py"])
        result["b8_5"] = {
            "release_gate": ok and report.get("release_gate") is True,
            "golden_determinism": bool(
                report.get("p1_b7_golden_frozen")
                and report.get("full_logical_equal", {}).get("logical_equal")
                and report.get("minimal_logical_equal", {}).get("logical_equal")
            ),
            "failure_fixture_determinism": bool(report.get("failure_fixture_parity", {}).get("passed")),
            "stderr_tail": stderr,
        }
        _progress("b8_6_gate")
        ok, report, stderr = _run_json_command([sys.executable, "specs/zp_migration/compatibility_gate.py"])
        result["b8_6"] = {
            "release_gate": ok and report.get("release_gate") is True,
            "b8_5_release_gate": report.get("b8_5_release_gate") is True,
            "migration_golden_determinism": bool(report.get("golden_migration_matrix", {}).get("passed")),
            "stderr_tail": stderr,
        }
    return result


def _version_state() -> dict[str, object]:
    return {
        "ZP_VERSION": ZP_VERSION,
        "DEFAULT_ZP_WRITE_VERSION": DEFAULT_ZP_WRITE_VERSION,
        "SUPPORTED_ZP_WRITE_VERSIONS": sorted(SUPPORTED_ZP_WRITE_VERSIONS),
        "SUPPORTED_ZP_READ_VERSIONS": sorted(SUPPORTED_ZP_READ_VERSIONS),
        "SUPPORTED_ZP_VALIDATE_VERSIONS": sorted(SUPPORTED_ZP_VALIDATE_VERSIONS),
        "KNOWN_ZP_VERSIONS": sorted(KNOWN_ZP_VERSIONS),
        "default_format_remains_v1": ZP_VERSION == DEFAULT_ZP_WRITE_VERSION == 1,
        "viewer_integration_started": False,
        "performance_tuning_started": False,
    }


def evaluate_matrix(samples: list[dict[str, object]], production_frozen: bool, gates: dict[str, object]) -> dict[str, object]:
    accepted = [item for item in samples if item.get("admission") == "accepted"]
    rejected = [item for item in samples if item.get("admission") == "rejected"]
    failed = [item for item in samples if item.get("admission") == "inspection_failed" or item.get("passed") is False]
    unique_hashes = {item.get("source_sha256") for item in samples if item.get("source_sha256")}
    covered = sorted({tag for item in accepted for tag in item.get("coverage_tags", [])})
    missing = [tag for tag in REQUIRED_COVERAGE if tag not in covered]
    count_sufficient = len(unique_hashes) >= 3
    matrix_sufficient = count_sufficient and not missing
    pytest_passed = gates.get("pytest", {}).get("passed") is True
    b8_5_passed = gates.get("b8_5", {}).get("release_gate") is True
    b8_6_passed = gates.get("b8_6", {}).get("release_gate") is True
    rejected_stable = all(
        item.get("admission_stable") is True
        and item.get("conversion_attempted") is False
        and item.get("artifacts_created") is False
        for item in rejected
    )
    accepted_passed = bool(accepted) and all(item.get("passed") is True for item in accepted)
    release_gate = all(
        (
            matrix_sufficient,
            accepted_passed,
            rejected_stable,
            not failed,
            production_frozen,
            pytest_passed,
            b8_5_passed,
            b8_6_passed,
            _version_state()["default_format_remains_v1"],
        )
    )
    if not matrix_sufficient:
        reason = "insufficient_real_sample_matrix"
    elif failed:
        reason = "sample_failure"
    elif not production_frozen:
        reason = "production_code_changed"
    elif not (pytest_passed and b8_5_passed and b8_6_passed):
        reason = "regression_gate_failed"
    else:
        reason = None
    return {
        "release_gate": release_gate,
        "status": "passed" if release_gate else "failed",
        "reason": reason,
        "counts": {
            "provided": len(samples),
            "available": len(unique_hashes),
            "missing": max(0, 3 - len(unique_hashes)),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "failed": len(failed),
        },
        "covered_coverage": covered,
        "missing_coverage": missing,
        "minimum_real_file_count_met": count_sufficient,
        "accepted_samples_passed": accepted_passed,
        "rejected_samples_stable": rejected_stable,
    }


def run(
    sample_specs: list[tuple[str, Path]],
    *,
    run_pytest: bool,
    run_existing_gates: bool,
) -> dict[str, object]:
    started = time.perf_counter()
    production_before = _production_hashes()
    samples: list[dict[str, object]] = []
    for sample_id, path in sample_specs:
        _progress("inspection", sample_id)
        with tempfile.TemporaryDirectory(prefix=f"zp-b8-7-{sample_id}-", dir=ROOT) as temporary:
            directory = Path(temporary)
            with _ResourceMonitor(directory) as monitor:
                try:
                    inspection = inspect_sample(sample_id, path)
                    if inspection["admission"] == "accepted":
                        _progress("accepted_flow", sample_id)
                        sample = _accepted_flow(path.resolve(strict=True), inspection, directory)
                        sample["timings"]["total_seconds"] = round(
                            inspection["inspection_seconds"] + sample["timings"]["total_seconds"],
                            6,
                        )
                    else:
                        sample = {
                            **inspection,
                            "conversion_attempted": False,
                            "artifacts_created": any(directory.iterdir()),
                            "migration_temp_files_created": False,
                            "statistics": None,
                            "v1_size": None,
                            "v2_size": None,
                            "migrated_v2_size": None,
                            "migration_result": "not_run",
                            "logical_equal": None,
                            "all_array_hashes_equal": None,
                            "reader_equal": None,
                            "timings": {
                                "inspection_seconds": inspection["inspection_seconds"],
                                "admission_seconds": inspection["admission_seconds"],
                                "total_seconds": inspection["inspection_seconds"],
                            },
                            "passed": True,
                        }
                except Exception as exc:
                    sample = {
                        "sample_id": sample_id,
                        "file_name": path.name,
                        "admission": "inspection_failed",
                        "admission_reasons": [],
                        "inspection_error_type": type(exc).__name__,
                        "inspection_error": str(exc),
                        "coverage_tags": [],
                        "passed": False,
                    }
            sample["peak_rss"] = monitor.peak_rss
            sample["temporary_disk_peak"] = monitor.temporary_disk_peak
            samples.append(sample)

    gates = _regression_gates(run_pytest, run_existing_gates)
    production_after = _production_hashes()
    production_frozen = production_before == production_after
    evaluation = evaluate_matrix(samples, production_frozen, gates)
    return {
        "schema_version": 1,
        "stage": "P1-B8.7",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "random_seed": RANDOM_SEED,
        **evaluation,
        "required_coverage": list(REQUIRED_COVERAGE),
        "samples": samples,
        "production_freeze": {
            "passed": production_frozen,
            "before": production_before,
            "after": production_after,
        },
        "existing_gates": gates,
        "version_state": _version_state(),
        "total_seconds": round(time.perf_counter() - started, 6),
    }


def _parse_sample(value: str) -> tuple[str, Path]:
    try:
        sample_id, raw_path = value.split("=", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("sample must use SAMPLE_ID=PATH") from exc
    if SAMPLE_ID_RE.fullmatch(sample_id) is None:
        raise argparse.ArgumentTypeError("sample_id must use lowercase letters, digits, '-' or '_'")
    path = Path(raw_path)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"sample is not a readable file: {path}")
    return sample_id, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the P1-B8.7 real mzML matrix")
    parser.add_argument("--sample", action="append", type=_parse_sample, required=True, metavar="ID=PATH")
    parser.add_argument("--result", type=Path, default=RESULT_PATH)
    parser.add_argument("--run-pytest", action="store_true")
    parser.add_argument("--run-existing-gates", action="store_true")
    args = parser.parse_args(argv)
    ids = [sample_id for sample_id, _path in args.sample]
    if len(ids) != len(set(ids)):
        parser.error("sample_id values must be unique")
    report = run(
        args.sample,
        run_pytest=args.run_pytest,
        run_existing_gates=args.run_existing_gates,
    )
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["release_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
