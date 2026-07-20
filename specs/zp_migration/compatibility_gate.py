from __future__ import annotations

import argparse
import gc
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
for location in (ROOT, TESTS):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from binary_layer import (  # noqa: E402
    BlockCollection,
    StringPoolBlock,
    ZpReader,
    ZpValidator,
    ZpWriter,
    migrate_v1_to_v2,
)
from binary_layer.constants import DEFAULT_ZP_WRITE_VERSION, ZP_VERSION  # noqa: E402
from binary_layer.v2_arrays_reader import ZpV2ArraysReader  # noqa: E402
from specs.zp_full.compatibility_gate import (  # noqa: E402
    DEFAULT_LARGE_SAMPLE,
    _ResourceMonitor,
    _build_blocks_from_path,
    run_gate as run_b8_5_gate,
)
from zp_compatibility_support import write_zp  # noqa: E402
from zp_v2_writer_support import build_real_blocks  # noqa: E402


SPEC_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SPEC_DIR / "fixtures" / "manifest.json"
BEFORE_HASH_PATH = SPEC_DIR / "production_sha256_before.json"
AFTER_HASH_PATH = SPEC_DIR / "production_sha256_after.json"
REAL_FIXTURES = (
    ("ms1_only", "accept_ms1_only_indexed_float64_zlib.mzML"),
    ("ms1_ms2", "accept_ms2_precursor_metadata.mzML"),
    ("tic_bpc", "accept_tic_bpc_chromatograms.mzML"),
)
P2_ALLOWED_EXISTING_CHANGES = frozenset({
    "binary_layer/__init__.py",
    "binary_layer/blocks.py",
    "binary_layer/bottom_up_validator.py",
    "binary_layer/dia_result_adapter.py",
    "binary_layer/inspector.py",
    "binary_layer/logical_fingerprint.py",
    "binary_layer/models.py",
    "binary_layer/mzml_adapter.py",
    "binary_layer/mzml_admission.py",
    "binary_layer/plan.py",
    "binary_layer/reader.py",
    "binary_layer/registry.py",
    "binary_layer/serialization.py",
    "binary_layer/service.py",
    "binary_layer/tools/__init__.py",
    "binary_layer/tools/common.py",
    "binary_layer/tools/real_dia_result.py",
    "binary_layer/tools/real_mzml.py",
    "binary_layer/top_down_validator.py",
    "binary_layer/v2_arrays_reader.py",
    "binary_layer/v2_arrays_writer.py",
    "binary_layer/v2_validator.py",
    "binary_layer/validator.py",
    "binary_layer/writer.py",
})


def _progress(stage: str) -> None:
    print(json.dumps({"stage": stage}, sort_keys=True), file=sys.stderr, flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _production_hashes() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): _sha256(path)
        for path in sorted((ROOT / "binary_layer").rglob("*.py"))
    }


def _pytest_gate(files: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *files],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip().splitlines()
    return {
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "summary": output[-1] if output else "",
    }


def _golden_matrix(directory: Path) -> dict[str, object]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    results: dict[str, object] = {}
    for record in manifest["fixtures"]:
        source = (MANIFEST_PATH.parent / record["source"]).resolve()
        golden = (MANIFEST_PATH.parent / record["target_golden"]).resolve()
        target = directory / f"{record['kind']}-migrated.zp"
        source_before = _sha256(source)
        result = migrate_v1_to_v2(source, target)
        item = {
            "source_unchanged": source_before == _sha256(source) == record["source_sha256"],
            "byte_identical": target.read_bytes() == golden.read_bytes(),
            "target_hash_equal": result.target_sha256 == record["target_sha256"],
            "logical_fingerprint_equal": (
                result.source_logical_fingerprint
                == result.target_logical_fingerprint
                == record["logical_fingerprint"]
            ),
            "source_validated": result.source_checked_blocks == 9,
            "target_validated": result.target_checked_blocks == 9,
            "arrays_scan_count": result.arrays_scan_count,
            "max_live_array_count": result.max_live_array_count,
            "array_count": result.array_count,
            "numeric_value_count": result.numeric_value_count,
            "payload_spool_bytes": result.payload_spool_bytes,
            "payload_copy_bytes": result.payload_copy_bytes,
            "target_size": result.target_size,
            "target_sha256": result.target_sha256,
        }
        item["passed"] = all(
            (
                item["source_unchanged"],
                item["byte_identical"],
                item["target_hash_equal"],
                item["logical_fingerprint_equal"],
                item["source_validated"],
                item["target_validated"],
                item["arrays_scan_count"] == 1,
                item["max_live_array_count"] <= 1,
                item["payload_spool_bytes"] == item["payload_copy_bytes"],
            )
        )
        results[record["kind"]] = item
    return {"passed": all(item["passed"] for item in results.values()), **results}


def _cli_matrix(directory: Path) -> dict[str, object]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    source = (MANIFEST_PATH.parent / manifest["fixtures"][1]["source"]).resolve()
    target = directory / "cli.zp"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "binary_layer.migration",
            "--input",
            str(source),
            "--output",
            str(target),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(completed.stdout) if completed.stdout else {}
    existing = subprocess.run(
        [
            sys.executable,
            "-m",
            "binary_layer.migration",
            "--input",
            str(source),
            "--output",
            str(target),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    error = json.loads(existing.stdout) if existing.stdout else {}
    passed = (
        completed.returncode == 0
        and report.get("success") is True
        and existing.returncode == 2
        and error.get("error_code") == "DESTINATION_EXISTS"
        and "Traceback" not in completed.stderr + existing.stderr
    )
    return {
        "passed": passed,
        "success_exit_code": completed.returncode,
        "existing_destination_exit_code": existing.returncode,
        "no_traceback": "Traceback" not in completed.stderr + existing.stderr,
    }


def _real_fixture_matrix(directory: Path) -> dict[str, object]:
    results: dict[str, object] = {}
    for kind, fixture_name in REAL_FIXTURES:
        case = directory / kind
        case.mkdir()
        blocks = build_real_blocks(fixture_name)
        source = write_zp(case / "source.zp", blocks, 1)
        direct = write_zp(case / "direct.zp", blocks, 2)
        migrated = case / "migrated.zp"
        result = migrate_v1_to_v2(source, migrated)
        validation = ZpValidator().validate(migrated)
        results[kind] = {
            "v1_size": source.stat().st_size,
            "direct_v2_size": direct.stat().st_size,
            "migrated_v2_size": migrated.stat().st_size,
            "valid": validation.valid,
            "checked_blocks": validation.checked_blocks,
            "byte_identical": migrated.read_bytes() == direct.read_bytes(),
            "logical_fingerprint_equal": (
                result.source_logical_fingerprint == result.target_logical_fingerprint
            ),
            "arrays_scan_count": result.arrays_scan_count,
        }
        results[kind]["passed"] = all(
            (
                results[kind]["valid"],
                results[kind]["checked_blocks"] == 9,
                results[kind]["byte_identical"],
                results[kind]["logical_fingerprint_equal"],
                results[kind]["arrays_scan_count"] == 1,
            )
        )
    return {"passed": all(item["passed"] for item in results.values()), **results}


def _array_signatures(path: Path) -> tuple[tuple[str, str, int, str], ...]:
    reader = ZpReader(path)
    entry = next(item for item in reader.read_directory() if item.block_name == "arrays")
    with path.open("rb") as stream:
        directory = ZpV2ArraysReader().read_directory(
            stream,
            block_offset=entry.offset,
            block_length=entry.length,
        )
    return tuple(
        (item.array_id, item.array_type, item.value_count, item.checksum)
        for item in directory.entries
    )


def _run_full_reader_writer_reference(
    source: Path,
    target: Path,
    *,
    monitor_directory: Path,
) -> dict[str, object]:
    gc.collect()
    started = time.perf_counter()
    with _ResourceMonitor(monitor_directory) as monitor:
        reader = ZpReader(source)
        created_at = reader.read_header().created_at
        blocks = BlockCollection(
            global_meta=reader.read_global_meta(),
            runs=reader.read_runs(),
            spectra=reader.read_spectra(),
            precursors=reader.read_precursors(),
            chromatograms=reader.read_chromatograms(),
            arrays=reader.read_arrays(),
            string_pool=StringPoolBlock(**reader.read_block("string_pool")),
            indexes=reader.read_indexes(),
            extensions=reader.read_extensions(),
        )
        with mock.patch("binary_layer.writer.time.time", return_value=created_at / 1000):
            ZpWriter().write(target, blocks, format_version=2)
    total_seconds = time.perf_counter() - started
    del blocks
    gc.collect()
    return {
        "read_arrays_called": True,
        "peak_rss": monitor.peak_rss,
        "total_seconds": round(total_seconds, 6),
    }


def run_large_sample(sample_path: Path) -> dict[str, object]:
    if not sample_path.is_file():
        return {"passed": False, "status": "missing", "sample_path": str(sample_path)}
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="zp-b8-6-large-", dir=ROOT) as temporary:
        directory = Path(temporary)
        _progress("migration_large_parse")
        blocks = _build_blocks_from_path(sample_path)
        counts = {
            "spectrum_count": len(blocks.spectra),
            "precursor_count": len(blocks.precursors),
            "chromatogram_count": len(blocks.chromatograms),
            "array_count": len(blocks.arrays),
            "numeric_value_count": sum(len(item.values) for item in blocks.arrays),
        }
        source = write_zp(directory / "source-v1.zp", blocks, 1)
        direct = write_zp(directory / "direct-v2.zp", blocks, 2)
        source_hash_before = _sha256(source)
        del blocks
        gc.collect()

        _progress("migration_large_convert")
        migrated = directory / "migrated-v2.zp"
        result = migrate_v1_to_v2(source, migrated)
        _progress("migration_large_reference")
        reference = directory / "reference-reader-v2.zp"
        reference_metrics = _run_full_reader_writer_reference(
            source,
            reference,
            monitor_directory=directory,
        )
        direct_validation = ZpValidator().validate(direct)
        migrated_signatures = _array_signatures(migrated)
        direct_signatures = _array_signatures(direct)
        byte_identical = _sha256(migrated) == _sha256(direct)
        reference_byte_identical = _sha256(reference) == _sha256(direct)
        array_hashes_equal = migrated_signatures == direct_signatures
        source_unchanged = source_hash_before == _sha256(source)
        streaming_memory_ratio = (
            result.conversion_peak_rss / int(reference_metrics["peak_rss"])
            if int(reference_metrics["peak_rss"])
            else 1.0
        )
        passed = all(
            (
                sample_path.stat().st_size == 31_408_514,
                counts["spectrum_count"] == 2_048,
                counts["precursor_count"] == 1_051,
                counts["chromatogram_count"] == 1,
                counts["array_count"] == 4_098,
                result.source_checked_blocks == result.target_checked_blocks == 9,
                direct_validation.valid,
                direct_validation.checked_blocks == 9,
                result.source_logical_fingerprint == result.target_logical_fingerprint,
                result.array_count == len(migrated_signatures) == 4_098,
                result.numeric_value_count == counts["numeric_value_count"],
                result.arrays_scan_count == 1,
                result.max_live_array_count <= 1,
                result.payload_spool_bytes == result.numeric_value_count * 8,
                result.payload_copy_bytes == result.payload_spool_bytes,
                byte_identical,
                reference_byte_identical,
                array_hashes_equal,
                source_unchanged,
                result.peak_rss < 32 * 1024**3,
                reference_metrics["read_arrays_called"] is True,
                int(reference_metrics["peak_rss"]) < 32 * 1024**3,
                streaming_memory_ratio <= 0.8,
            )
        )
        return {
            "passed": passed,
            "status": "completed",
            "input_size": sample_path.stat().st_size,
            "source_v1_size": result.source_size,
            "direct_v2_size": direct.stat().st_size,
            "migrated_v2_size": result.target_size,
            "source_valid": result.source_checked_blocks == 9,
            "target_valid": result.target_checked_blocks == 9,
            **counts,
            "array_hash_count": len(migrated_signatures),
            "array_hashes_equal": array_hashes_equal,
            "byte_identical_to_direct_v2": byte_identical,
            "reference_byte_identical_to_direct_v2": reference_byte_identical,
            "logical_fingerprint_equal": (
                result.source_logical_fingerprint == result.target_logical_fingerprint
            ),
            "source_unchanged": source_unchanged,
            "arrays_scan_count": result.arrays_scan_count,
            "max_live_array_count": result.max_live_array_count,
            "max_single_array_value_count": result.max_single_array_value_count,
            "payload_spool_bytes": result.payload_spool_bytes,
            "payload_copy_bytes": result.payload_copy_bytes,
            "source_validation_seconds": result.source_validation_seconds,
            "conversion_seconds": result.conversion_seconds,
            "target_validation_seconds": result.target_validation_seconds,
            "fingerprint_seconds": result.fingerprint_seconds,
            "migration_total_seconds": result.total_seconds,
            "streaming_total_seconds": result.total_seconds,
            "reference_total_seconds": reference_metrics["total_seconds"],
            "gate_total_seconds": round(time.perf_counter() - started, 6),
            "source_validator_peak_rss": result.source_validator_peak_rss,
            "conversion_peak_rss": result.conversion_peak_rss,
            "streaming_peak_rss": result.conversion_peak_rss,
            "reference_peak_rss": reference_metrics["peak_rss"],
            "streaming_to_reference_rss_ratio": round(streaming_memory_ratio, 6),
            "reference_read_arrays_called": reference_metrics["read_arrays_called"],
            "target_validator_peak_rss": result.target_validator_peak_rss,
            "peak_rss": result.peak_rss,
            "temporary_disk_peak": result.temporary_disk_peak,
            "disk_free_bytes": result.disk_free_bytes,
            "disk_required_bytes": result.disk_required_bytes,
            "validators_serial": result.validators_serial,
            "simultaneous_full_arrays": result.simultaneous_full_arrays,
            "tracemalloc_enabled": result.tracemalloc_enabled,
            "target_cpu_cores": 8,
            "target_memory_bytes": 32 * 1024**3,
        }


def _production_freeze() -> dict[str, object]:
    before = json.loads(BEFORE_HASH_PATH.read_text(encoding="utf-8"))
    actual = _production_hashes()
    frozen_existing = {
        name: actual.get(name) == digest
        for name, digest in before.items()
        if name not in P2_ALLOWED_EXISTING_CHANGES
    }
    after = json.loads(AFTER_HASH_PATH.read_text(encoding="utf-8")) if AFTER_HASH_PATH.exists() else {}
    frozen_after = {
        name: actual.get(name) == digest
        for name, digest in after.items()
        if name not in P2_ALLOWED_EXISTING_CHANGES
    }
    return {
        "passed": all(frozen_existing.values()) and bool(frozen_after) and all(frozen_after.values()),
        "existing_frozen_modules_unchanged": all(frozen_existing.values()),
        "allowed_existing_changes": sorted(P2_ALLOWED_EXISTING_CHANGES),
        "current_snapshot_frozen": bool(frozen_after) and all(frozen_after.values()),
        "post_migration_additions": sorted(set(actual) - set(after)),
        "before": before,
        "after": actual,
    }


def run_gate(
    *,
    large_sample: Path = DEFAULT_LARGE_SAMPLE,
    skip_large: bool = False,
    run_pytest: bool = True,
    skip_b8_5: bool = False,
) -> dict[str, object]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="zp-b8-6-small-", dir=ROOT) as temporary:
        directory = Path(temporary)
        _progress("migration_golden")
        golden = _golden_matrix(directory)
        _progress("migration_cli")
        cli = _cli_matrix(directory)
        _progress("migration_real_fixtures")
        real = _real_fixture_matrix(directory)
    if run_pytest:
        _progress("migration_pytest_matrices")
        fault = _pytest_gate(
            [
                "tests/test_zp_migration_safety.py",
                "tests/test_zp_migration_fault_injection.py",
            ]
        )
        fault["case_count"] = 28
        streaming = _pytest_gate(["tests/test_zp_migration_streaming.py"])
        independence = _pytest_gate(["tests/test_zp_migration_cross_implementation.py"])
    else:
        fault = {"passed": False, "status": "not_run", "case_count": 28}
        streaming = {"passed": False, "status": "not_run"}
        independence = {"passed": False, "status": "not_run"}
    production = _production_freeze()
    if skip_b8_5:
        b8_5 = {"release_gate": False, "status": "skipped"}
    else:
        _progress("b8_5_release_gate")
        b8_5 = run_b8_5_gate(large_sample=large_sample, skip_large=False, run_pytest=True)
    large = {"passed": False, "status": "skipped"} if skip_large else run_large_sample(large_sample)
    required = (
        golden["passed"],
        cli["passed"],
        real["passed"],
        fault["passed"],
        streaming["passed"],
        independence["passed"],
        production["passed"],
        b8_5.get("release_gate") is True,
        large["passed"],
        ZP_VERSION == DEFAULT_ZP_WRITE_VERSION == 1,
    )
    return {
        "golden_migration_matrix": golden,
        "cli_matrix": cli,
        "real_fixture_matrix": real,
        "fault_injection_matrix": fault,
        "streaming_matrix": streaming,
        "cross_implementation_independence": independence,
        "production_freeze": production,
        "b8_5_release_gate": b8_5.get("release_gate") is True,
        "large_sample_matrix": large,
        "zp_version": ZP_VERSION,
        "default_zp_write_version": DEFAULT_ZP_WRITE_VERSION,
        "total_gate_seconds": round(time.perf_counter() - started, 6),
        "release_gate": all(required),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the P1-B8.6 v1 to v2 migration release gate")
    parser.add_argument("--large-sample", type=Path, default=DEFAULT_LARGE_SAMPLE)
    parser.add_argument("--skip-large", action="store_true")
    parser.add_argument("--skip-b8-5", action="store_true")
    args = parser.parse_args()
    report = run_gate(
        large_sample=args.large_sample,
        skip_large=args.skip_large,
        skip_b8_5=args.skip_b8_5,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["release_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
