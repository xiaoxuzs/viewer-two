from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from binary_layer.reader import ZpReader

from benchmarks.models import BENCHMARK_VERSION
from benchmarks.monitor import ProcessMonitor

COLD_OPERATIONS = (
    "read_header",
    "read_directory",
    "read_global_meta",
    "read_spectra",
    "read_arrays",
    "read_spectrum_first",
    "read_spectrum_middle",
    "read_spectrum_last",
    "read_spectrum_arrays_first",
    "read_spectrum_arrays_middle",
    "read_spectrum_arrays_last",
    "read_chromatograms",
    "read_chromatogram_arrays",
    "sequential_10_spectrum_arrays",
    "random_100_spectrum_arrays",
    "repeat_100_same_spectrum_arrays",
)


def _spectrum_ids(reader: ZpReader) -> list[str]:
    return [item.spectrum_id for item in reader.read_spectra()]


def _select(ids: list[str], position: str) -> str:
    if not ids:
        raise ValueError("output contains no spectra")
    indexes = {"first": 0, "middle": len(ids) // 2, "last": len(ids) - 1}
    return ids[indexes[position]]


def _execute(path: Path, operation: str) -> tuple[int | None, str]:
    reader = ZpReader(path)
    if operation == "read_header":
        return reader.read_header().version, "fixed 24-byte header only"
    if operation == "read_directory":
        return len(reader.read_directory()), "header plus trailing directory JSON"
    if operation == "read_global_meta":
        reader.read_global_meta()
        return 1, "directory plus global_meta JSON"
    if operation == "read_spectra":
        return len(reader.read_spectra()), "directory plus complete core_spectra JSON"
    if operation == "read_arrays":
        return len(reader.read_arrays()), "directory plus complete arrays JSON"
    if operation.startswith("read_spectrum_arrays_"):
        position = operation.rsplit("_", 1)[-1]
        spectrum_id = _select(_spectrum_ids(reader), position)
        _, mz_array, intensity_array = reader.read_spectrum_arrays(spectrum_id)
        return len(mz_array.values) + len(intensity_array.values), "full spectra parse, full arrays parse, and a new array_id map"
    if operation.startswith("read_spectrum_"):
        position = operation.rsplit("_", 1)[-1]
        spectrum_id = _select(_spectrum_ids(reader), position)
        reader.read_spectrum(spectrum_id)
        return 1, "complete core_spectra JSON then linear spectrum_id search"
    if operation == "read_chromatograms":
        return len(reader.read_chromatograms()), "directory plus complete core_chromatograms JSON"
    if operation == "read_chromatogram_arrays":
        chromatograms = reader.read_chromatograms()
        if not chromatograms:
            return None, "not measured: output contains no Chromatogram"
        arrays = {item.array_id: item for item in reader.read_arrays()}
        item = chromatograms[0]
        return len(arrays[item.time_array_id].values) + len(arrays[item.intensity_array_id].values), "complete chromatogram block, complete arrays block, and a new array_id map"
    ids = _spectrum_ids(reader)
    if operation == "sequential_10_spectrum_arrays":
        selected = ids[:10]
        for spectrum_id in selected:
            reader.read_spectrum_arrays(spectrum_id)
        return len(selected), "each call reparses complete spectra and arrays blocks"
    if operation == "random_100_spectrum_arrays":
        generator = random.Random(1729)
        selected = [ids[generator.randrange(len(ids))] for _ in range(100)]
        for spectrum_id in selected:
            reader.read_spectrum_arrays(spectrum_id)
        return len(selected), "fixed seed 1729; each call reparses complete spectra and arrays blocks"
    if operation == "repeat_100_same_spectrum_arrays":
        spectrum_id = ids[len(ids) // 2]
        for _ in range(100):
            reader.read_spectrum_arrays(spectrum_id)
        return 100, "no Reader cache; 100 complete spectra and arrays block parses"
    raise ValueError(f"unknown operation: {operation}")


def _worker(path: Path, operation: str) -> dict[str, Any]:
    monitor = ProcessMonitor()
    monitor.start()
    started = time.perf_counter()
    failure = None
    value_count = None
    notes = ""
    try:
        value_count, notes = _execute(path, operation)
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    monitor.stop()
    return {
        "operation": operation,
        "seconds": elapsed,
        "rss_start_bytes": monitor.rss_start_bytes,
        "rss_peak_bytes": monitor.rss_peak_bytes,
        "rss_end_bytes": monitor.rss_end_bytes,
        "windows_peak_working_set_bytes": monitor.windows_peak_working_set_bytes,
        "value_count": value_count,
        "notes": notes,
        "failure": failure,
    }


def _parent(path: Path, result_path: Path) -> dict[str, Any]:
    measurements = []
    root = Path(__file__).resolve().parents[1]
    for operation in COLD_OPERATIONS:
        completed = subprocess.run(
            [sys.executable, "-m", "benchmarks.benchmark_zp_read", "--zp", str(path), "--worker-operation", operation],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            measurements.append({"operation": operation, "failure": completed.stderr.strip() or f"worker exit {completed.returncode}"})
            continue
        measurements.append(json.loads(completed.stdout))
    report = {
        "benchmark_version": BENCHMARK_VERSION,
        "zp_path": str(path),
        "zp_size": path.stat().st_size,
        "cold_process_per_operation": True,
        "fixed_random_seed": 1729,
        "measurements": measurements,
        "reader_semantics": {
            "cache": "none",
            "read_spectrum": "reads and parses complete core_spectra block, then linear search",
            "read_spectrum_arrays": "calls read_spectrum, then reads/parses complete arrays block and rebuilds array_id dict",
            "disk_random_access": False,
            "repeat_100": "performs 100 complete spectra-block and arrays-block parses",
        },
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="P1-B6 cold-process .zp Reader benchmark")
    parser.add_argument("--zp", type=Path, required=True)
    parser.add_argument("--result", type=Path, default=Path("benchmarks/results/zp_read.json"))
    parser.add_argument("--worker-operation", choices=COLD_OPERATIONS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    path = args.zp.resolve()
    if args.worker_operation:
        print(json.dumps(_worker(path, args.worker_operation), ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    report = _parent(path, args.result)
    return 0 if all(item.get("failure") is None for item in report["measurements"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())

