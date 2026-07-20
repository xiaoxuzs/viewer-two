from __future__ import annotations

import argparse
import contextlib
import ctypes
import gc
import hashlib
import io
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
for location in (ROOT, TESTS):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from binary_layer import (  # noqa: E402
    PipelineContext,
    SourceInspector,
    ZpReader,
    ZpValidator,
    build_default_registry,
)
from binary_layer.constants import DEFAULT_ZP_WRITE_VERSION, ZP_VERSION  # noqa: E402
from specs.zp_full.build_full_golden_fixtures import check as check_full_golden  # noqa: E402
from specs.zp_full.inspect_full_zp import inspect_full_zp  # noqa: E402
from specs.zp_full.logical_model import LogicalZpDocument, logical_equivalence  # noqa: E402
from zp_compatibility_support import build_full_blocks, write_pair  # noqa: E402
from zp_v2_writer_support import build_real_blocks  # noqa: E402


FIXTURE_DIR = Path(__file__).with_name("fixtures")
PRODUCTION_HASH_PATH = Path(__file__).with_name("production_sha256.json")
DEFAULT_LARGE_SAMPLE = Path(
    r"E:\viewer-TD\test\xzx_PXD045330\20191118_rvg262_LT_110516-13_1000-1100_Techrep01.mzML"
)
REAL_FIXTURES = (
    ("ms1_only", "accept_ms1_only_indexed_float64_zlib.mzML"),
    ("ms1_ms2", "accept_ms2_precursor_metadata.mzML"),
    ("tic_bpc", "accept_tic_bpc_chromatograms.mzML"),
)


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


def _golden_summary(report: dict[str, object]) -> dict[str, object]:
    header = report["header"]
    assert isinstance(header, dict)
    return {
        "file": report["file"],
        "size": report["file_size"],
        "sha256": report["sha256"],
        "version": header["version"],
        "directory_offset": header["directory_offset"],
        "block_count": len(report["directory"]),
        **report["statistics"],
    }


def _writer_matrix(directory: Path) -> dict[str, object]:
    paths = write_pair(directory, build_full_blocks())
    default = directory / "default.zp"
    from zp_compatibility_support import FIXED_EPOCH_SECONDS
    from unittest.mock import patch
    from binary_layer import ZpWriter

    with patch("binary_layer.writer.time.time", return_value=FIXED_EPOCH_SECONDS):
        ZpWriter().write(default, build_full_blocks())
    v1 = inspect_full_zp(paths[1])
    v2 = inspect_full_zp(paths[2])
    passed = (
        default.read_bytes() == paths[1].read_bytes()
        and v1["header"]["version"] == 1
        and v2["header"]["version"] == 2
        and v1["blocks"]["global_meta"]["format_version"] == 1
        and v2["blocks"]["global_meta"]["format_version"] == 2
    )
    return {
        "passed": passed,
        "default_version": 1,
        "default_equals_explicit_v1": default.read_bytes() == paths[1].read_bytes(),
        "explicit_v2": v2["header"]["version"] == 2,
    }


def _reader_matrix() -> dict[str, object]:
    results: dict[str, bool] = {}
    for kind in ("full", "minimal"):
        readers = {
            version: ZpReader(FIXTURE_DIR / f"valid_{kind}_v{version}.zp")
            for version in (1, 2)
        }
        passed = (
            readers[1].read_spectra() == readers[2].read_spectra()
            and readers[1].read_spectrum_arrays("spectrum_000001")
            == readers[2].read_spectrum_arrays("spectrum_000001")
            and sorted(readers[1].read_arrays(), key=lambda item: item.array_id)
            == sorted(readers[2].read_arrays(), key=lambda item: item.array_id)
        )
        if kind == "full":
            passed = passed and readers[1].read_chromatogram_arrays(
                "chromatogram_000001"
            ) == readers[2].read_chromatogram_arrays("chromatogram_000001")
        results[kind] = passed
    return {"passed": all(results.values()), **results}


def _validator_matrix() -> dict[str, object]:
    results: dict[str, dict[str, object]] = {}
    for name in (
        "valid_full_v1.zp",
        "valid_full_v2.zp",
        "valid_minimal_v1.zp",
        "valid_minimal_v2.zp",
    ):
        result = ZpValidator().validate(FIXTURE_DIR / name)
        results[name] = {
            "valid": result.valid,
            "checked_blocks": result.checked_blocks,
            "issue_codes": [issue.code for issue in result.issues],
        }
    return {
        "passed": all(
            item["valid"] and item["checked_blocks"] == 9 and item["issue_codes"] == []
            for item in results.values()
        ),
        "files": results,
    }


def _failure_fixture_parity() -> dict[str, object]:
    roots = (
        ROOT / "specs" / "zp_full" / "failures" / "manifest.json",
        ROOT / "specs" / "zp_full" / "failures" / "global_meta_count" / "manifest.json",
    )
    records: list[dict[str, object]] = []
    for manifest_path in roots:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for record in manifest["fixtures"]:
            path = manifest_path.parent / record["file"]
            result = ZpValidator().validate(path)
            records.append(
                {
                    "file": record["file"],
                    "hash_equal": _sha256(path) == record["sha256"],
                    "valid_equal": result.valid is record["validator_valid"],
                    "codes_equal": [issue.code for issue in result.issues]
                    == record["validator_issue_codes"],
                }
            )
    candidate_path = ROOT / "specs" / "zp_full" / "failures" / "candidate_parity" / "manifest.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    for probe in candidate["probes"]:
        for record in probe["fixtures"]:
            path = candidate_path.parent / record["file"]
            result = ZpValidator().validate(path)
            records.append(
                {
                    "file": record["file"],
                    "hash_equal": _sha256(path) == record["sha256"],
                    "valid_equal": result.valid is record["validator_valid"],
                    "codes_equal": [issue.code for issue in result.issues]
                    == record["validator_issue_codes"],
                }
            )
    return {"passed": all(all(value for key, value in item.items() if key != "file") for item in records), "files": records}


def _real_fixture_matrix(directory: Path) -> dict[str, object]:
    results: dict[str, object] = {}
    for kind, fixture in REAL_FIXTURES:
        output = directory / kind
        output.mkdir()
        paths = write_pair(output, build_real_blocks(fixture))
        reports = {version: inspect_full_zp(path) for version, path in paths.items()}
        models = {version: LogicalZpDocument.from_inspection(report) for version, report in reports.items()}
        validations = {version: ZpValidator().validate(path) for version, path in paths.items()}
        equivalence = logical_equivalence(models[1], models[2])
        results[kind] = {
            "v1_size": paths[1].stat().st_size,
            "v2_size": paths[2].stat().st_size,
            "v1_valid": validations[1].valid,
            "v2_valid": validations[2].valid,
            "logical_equal": equivalence["logical_equal"],
            "array_values_equal": equivalence["array_values_equal"],
        }
    return {
        "passed": all(
            item["v1_valid"] and item["v2_valid"] and item["logical_equal"] and item["array_values_equal"]
            for item in results.values()
        ),
        **results,
    }


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
    if os.name != "nt":
        if sys.platform.startswith("linux"):
            try:
                resident_pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
                return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
            except (IndexError, OSError, ValueError):
                pass
        try:
            import resource

            peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            return peak if sys.platform == "darwin" else peak * 1024
        except (ImportError, OSError, ValueError):
            return 0
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
    handle = kernel32.GetCurrentProcess()
    success = psapi.GetProcessMemoryInfo(
        handle,
        ctypes.byref(counters),
        counters.cb,
    )
    return int(counters.WorkingSetSize) if success else 0


class _ResourceMonitor:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.peak_rss = 0
        self.temporary_disk_peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.05):
            self.peak_rss = max(self.peak_rss, _working_set())
            try:
                disk = sum(item.stat().st_size for item in self.directory.rglob("*") if item.is_file())
            except OSError:
                disk = 0
            self.temporary_disk_peak = max(self.temporary_disk_peak, disk)

    def __enter__(self) -> "_ResourceMonitor":
        self.peak_rss = _working_set()
        self._thread.start()
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self._stop.set()
        self._thread.join()
        self.peak_rss = max(self.peak_rss, _working_set())
        try:
            disk = sum(item.stat().st_size for item in self.directory.rglob("*") if item.is_file())
        except OSError:
            disk = 0
        self.temporary_disk_peak = max(self.temporary_disk_peak, disk)


def _build_blocks_from_path(source: Path):
    profile = SourceInspector().inspect([source])
    context = PipelineContext(profile)
    registry = build_default_registry()
    for step in ("file_validate", "hash_input", "real_mzml_parse", "string_pool_build", "index_build"):
        registry.get(step).run(context)
    return context.blocks


def _array_signature(item) -> tuple[str, int, str, tuple[float, ...]]:
    digest = hashlib.sha256()
    for value in item.values:
        digest.update(struct.pack("<d", value))
    return item.array_type, len(item.values), digest.hexdigest(), tuple(item.values)


def _non_array_summary(reader: ZpReader) -> dict[str, object]:
    names = (
        "global_meta",
        "string_pool",
        "core_runs",
        "core_spectra",
        "core_precursors",
        "core_chromatograms",
        "indexes",
        "extensions",
    )
    result = {name: reader.read_block(name) for name in names}
    result["global_meta"].pop("format_version", None)
    return result


def run_large_sample(sample_path: Path) -> dict[str, object]:
    if not sample_path.is_file():
        return {"passed": False, "status": "missing", "sample_path": str(sample_path)}
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="zp-b8-5-large-", dir=ROOT) as temporary:
        directory = Path(temporary)
        with _ResourceMonitor(directory) as monitor:
            _progress("large_parse")
            blocks = _build_blocks_from_path(sample_path)
            spectrum_count = len(blocks.spectra)
            precursor_count = len(blocks.precursors)
            chromatogram_count = len(blocks.chromatograms)
            array_count = len(blocks.arrays)
            numeric_value_count = sum(len(item.values) for item in blocks.arrays)
            array_ids = sorted(item.array_id for item in blocks.arrays)
            sampled_ids = sorted(random.Random(8505).sample(array_ids, min(100, len(array_ids))))
            v1_path = directory / "large-v1.zp"
            v2_path = directory / "large-v2.zp"
            _progress("large_write_v1")
            from binary_layer import ZpWriter
            ZpWriter().write(v1_path, blocks, format_version=1)
            _progress("large_write_v2")
            ZpWriter().write(v2_path, blocks, format_version=2)
            del blocks
            gc.collect()

            _progress("large_validate_v1")
            before = time.perf_counter()
            v1_validation = ZpValidator().validate(v1_path)
            v1_validator_seconds = time.perf_counter() - before
            _progress("large_validate_v2")
            before = time.perf_counter()
            v2_validation = ZpValidator().validate(v2_path)
            v2_validator_seconds = time.perf_counter() - before

            _progress("large_reader_sample")
            before = time.perf_counter()
            v1_reader = ZpReader(v1_path)
            v2_reader = ZpReader(v2_path)
            decoded_v1 = v1_reader.read_arrays()
            selected = set(sampled_ids)
            v1_samples = {
                item.array_id: _array_signature(item)
                for item in decoded_v1
                if item.array_id in selected
            }
            del decoded_v1
            gc.collect()
            sampled_arrays_equal = all(
                v1_samples[array_id] == _array_signature(v2_reader.read_array(array_id))
                for array_id in sampled_ids
            )
            spectra_v1 = v1_reader.read_spectra()
            spectra_v2 = v2_reader.read_spectra()
            first_ms1_v1 = next(item for item in spectra_v1 if item.ms_level == 1)
            first_ms1_v2 = next(item for item in spectra_v2 if item.ms_level == 1)
            first_ms2_v1 = next(item for item in spectra_v1 if item.ms_level == 2)
            first_ms2_v2 = next(item for item in spectra_v2 if item.ms_level == 2)
            precursors_v1 = v1_reader.read_precursors()
            precursors_v2 = v2_reader.read_precursors()
            chromatograms_v1 = v1_reader.read_chromatograms()
            chromatograms_v2 = v2_reader.read_chromatograms()
            business_summary_equal = _non_array_summary(v1_reader) == _non_array_summary(v2_reader)
            reader_sample_seconds = time.perf_counter() - before

        passed = (
            sample_path.stat().st_size == 31_408_514
            and v1_validation.valid
            and v2_validation.valid
            and v1_validation.checked_blocks == v2_validation.checked_blocks == 9
            and spectrum_count == 2_048
            and precursor_count == 1_051
            and chromatogram_count == 1
            and array_count == 4_098
            and len(sampled_ids) == 100
            and sampled_arrays_equal
            and first_ms1_v1 == first_ms1_v2
            and first_ms2_v1 == first_ms2_v2
            and precursors_v1[0] == precursors_v2[0]
            and chromatograms_v1[0] == chromatograms_v2[0]
            and business_summary_equal
            and monitor.peak_rss > 0
            and monitor.peak_rss < 32 * 1024**3
        )
        return {
            "passed": passed,
            "status": "completed",
            "input_size": sample_path.stat().st_size,
            "v1_size": v1_path.stat().st_size,
            "v2_size": v2_path.stat().st_size,
            "v1_valid": v1_validation.valid,
            "v2_valid": v2_validation.valid,
            "v1_checked_blocks": v1_validation.checked_blocks,
            "v2_checked_blocks": v2_validation.checked_blocks,
            "spectrum_count": spectrum_count,
            "precursor_count": precursor_count,
            "chromatogram_count": chromatogram_count,
            "array_count": array_count,
            "numeric_value_count": numeric_value_count,
            "sampled_array_count": len(sampled_ids),
            "sampled_arrays_equal": sampled_arrays_equal,
            "first_ms1_equal": first_ms1_v1 == first_ms1_v2,
            "first_ms2_equal": first_ms2_v1 == first_ms2_v2,
            "first_precursor_equal": precursors_v1[0] == precursors_v2[0],
            "first_chromatogram_equal": chromatograms_v1[0] == chromatograms_v2[0],
            "business_summary_equal": business_summary_equal,
            "v1_validator_seconds": round(v1_validator_seconds, 6),
            "v2_validator_seconds": round(v2_validator_seconds, 6),
            "reader_sample_seconds": round(reader_sample_seconds, 6),
            "peak_rss": monitor.peak_rss,
            "temporary_disk_peak": monitor.temporary_disk_peak,
            "total_seconds": round(time.perf_counter() - started, 6),
            "validators_serial": True,
            "simultaneous_full_arrays": False,
            "tracemalloc_enabled": False,
            "target_cpu_cores": 8,
            "target_memory_bytes": 32 * 1024**3,
        }


def run_gate(
    *,
    large_sample: Path = DEFAULT_LARGE_SAMPLE,
    skip_large: bool = False,
    run_pytest: bool = True,
) -> dict[str, object]:
    started = time.perf_counter()
    _progress("golden_and_logical")
    reports = {
        name: inspect_full_zp(FIXTURE_DIR / name)
        for name in (
            "valid_full_v1.zp",
            "valid_full_v2.zp",
            "valid_minimal_v1.zp",
            "valid_minimal_v2.zp",
        )
    }
    full_equivalence = logical_equivalence(
        LogicalZpDocument.from_inspection(reports["valid_full_v1.zp"]),
        LogicalZpDocument.from_inspection(reports["valid_full_v2.zp"]),
    )
    minimal_equivalence = logical_equivalence(
        LogicalZpDocument.from_inspection(reports["valid_minimal_v1.zp"]),
        LogicalZpDocument.from_inspection(reports["valid_minimal_v2.zp"]),
    )
    with contextlib.redirect_stdout(io.StringIO()):
        check_full_golden()
    with tempfile.TemporaryDirectory(prefix="zp-b8-5-small-", dir=ROOT) as temporary:
        directory = Path(temporary)
        _progress("writer_reader_validator")
        writer_matrix = _writer_matrix(directory)
        reader_matrix = _reader_matrix()
        validator_matrix = _validator_matrix()
        _progress("real_fixtures")
        real_fixture_matrix = _real_fixture_matrix(directory)

    if run_pytest:
        _progress("compatibility_pytest_matrices")
        domain_matrix = _pytest_gate(["tests/test_zp_v1_v2_domain_error_matrix.py"])
        domain_matrix["case_count"] = 22
        encoding_matrix = _pytest_gate(["tests/test_zp_version_encoding_matrix.py"])
        encoding_matrix["case_count"] = 8
        corruption_matrix = _pytest_gate(["tests/test_zp_full_golden_corruption.py"])
        corruption_matrix["v1_case_count"] = 11
        corruption_matrix["v2_case_count"] = 14
        independence = _pytest_gate(["tests/test_zp_cross_implementation_independence.py"])
    else:
        domain_matrix = {"passed": False, "status": "not_run", "case_count": 22}
        encoding_matrix = {"passed": False, "status": "not_run", "case_count": 8}
        corruption_matrix = {"passed": False, "status": "not_run", "v1_case_count": 11, "v2_case_count": 14}
        independence = {"passed": False, "status": "not_run"}

    failure_fixture_parity = _failure_fixture_parity()
    expected_hashes = json.loads(PRODUCTION_HASH_PATH.read_text(encoding="utf-8"))
    actual_hashes = _production_hashes()
    production_code_changed = actual_hashes != expected_hashes
    p1_b7 = {
        "valid_arrays_v2.bin": _sha256(ROOT / "specs" / "zp_v2" / "fixtures" / "valid_arrays_v2.bin"),
        "valid_empty_arrays_v2.bin": _sha256(ROOT / "specs" / "zp_v2" / "fixtures" / "valid_empty_arrays_v2.bin"),
        "manifest.json": _sha256(ROOT / "specs" / "zp_v2" / "fixtures" / "manifest.json"),
    }
    p1_b7_frozen = p1_b7 == {
        "valid_arrays_v2.bin": "fc08d7123bd5abcb811d6fdbe5fff06b2250cb7e92727f5275d16cdb70cf7a5c",
        "valid_empty_arrays_v2.bin": "a81b75aaa9e6f59ea15b9b3fe9bb4cb386e0ca30db253d196c852151a8d46616",
        "manifest.json": "280c48d13d163880ab370ddaeb1889de547475c94b882f213b77cadac3aa4c61",
    }
    if skip_large:
        large_sample_matrix = {"passed": False, "status": "skipped"}
    else:
        _progress("large_sample")
        large_sample_matrix = run_large_sample(large_sample)

    required = (
        full_equivalence["logical_equal"],
        minimal_equivalence["logical_equal"],
        writer_matrix["passed"],
        reader_matrix["passed"],
        validator_matrix["passed"],
        domain_matrix["passed"],
        encoding_matrix["passed"],
        corruption_matrix["passed"],
        independence["passed"],
        failure_fixture_parity["passed"],
        real_fixture_matrix["passed"],
        large_sample_matrix["passed"],
        not production_code_changed,
        p1_b7_frozen,
        ZP_VERSION == DEFAULT_ZP_WRITE_VERSION == 1,
    )
    return {
        "golden_full_v1": _golden_summary(reports["valid_full_v1.zp"]),
        "golden_full_v2": _golden_summary(reports["valid_full_v2.zp"]),
        "golden_minimal_v1": _golden_summary(reports["valid_minimal_v1.zp"]),
        "golden_minimal_v2": _golden_summary(reports["valid_minimal_v2.zp"]),
        "full_logical_equal": full_equivalence,
        "minimal_logical_equal": minimal_equivalence,
        "writer_matrix": writer_matrix,
        "reader_matrix": reader_matrix,
        "validator_matrix": validator_matrix,
        "domain_error_matrix": domain_matrix,
        "encoding_matrix": encoding_matrix,
        "golden_corruption_matrix": corruption_matrix,
        "failure_fixture_parity": failure_fixture_parity,
        "cross_implementation_independence": independence,
        "production_code_changed": production_code_changed,
        "production_sha256": actual_hashes,
        "p1_b7_golden_frozen": p1_b7_frozen,
        "real_fixture_matrix": real_fixture_matrix,
        "large_sample_matrix": large_sample_matrix,
        "zp_version": ZP_VERSION,
        "default_zp_write_version": DEFAULT_ZP_WRITE_VERSION,
        "total_gate_seconds": round(time.perf_counter() - started, 6),
        "release_gate": all(required),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the P1-B8.5 unified compatibility gate")
    parser.add_argument("--large-sample", type=Path, default=DEFAULT_LARGE_SAMPLE)
    parser.add_argument("--skip-large", action="store_true")
    args = parser.parse_args()
    report = run_gate(large_sample=args.large_sample, skip_large=args.skip_large)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["release_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
