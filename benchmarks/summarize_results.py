from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from binary_layer.reader import ZpReader
from binary_layer.serialization import canonical_json_bytes

PRODUCTION_HASHES_BEFORE = {
    "binary_layer/blocks.py": "985f0a88e934a1c0e0dc6d7c93c80534fa1342e8cd7211a833f34a51adfe40c9",
    "binary_layer/constants.py": "6d0edb96dfd8c984d7e705eba9f65b3dda76bfdeb9e541aaaf8582292a428e51",
    "binary_layer/inspector.py": "7de6c0fb175240020f9af23a30b76e414a72046774cddd6d5acb5c7fd989dea1",
    "binary_layer/models.py": "7102853820619c89f99dc99e843b1686681d8b0d8847803e45e0308c633afec8",
    "binary_layer/mzml_adapter.py": "1ff9bff016627fe5f2d2f93029449630fd0ad8c091a322ac708ba4b8005e82ac",
    "binary_layer/mzml_admission.py": "776fe5d97d046718985bd8f52e0f58610a4c820ee0a3fef2ba24a97b2b20d88a",
    "binary_layer/mzml_schema.py": "ae156599d5c31f7a06fec64fe79505602ac59db3a5dc5074f0c67cf33a0d22fa",
    "binary_layer/plan.py": "e01fc73c9e69f0b9f9df6f0c3d80b376d6e6df6ac2bcc072de8db13eaf662b73",
    "binary_layer/reader.py": "c6535fc81796e8e7237006d48d6910479e30c0f00aab152aafbbcd6b18b5c70a",
    "binary_layer/registry.py": "4f4b91e7adc6b2a5ba267692f22cd232f9ce55f6efb854d39eea7f2acb811eb8",
    "binary_layer/runner.py": "8e4f1175f0ddc4cc62f45d6aaaf1578b31c4713658741a3b7c56116624890b99",
    "binary_layer/serialization.py": "fadda12fdf96e0fb0a059120cb1ffad3b4e04ee1b6d8f43dc1275829e48535c1",
    "binary_layer/tools/base.py": "1f7f65ad42848c2f655880e3edfd35afba8ca801101d1a7ae0adb96f935805a6",
    "binary_layer/tools/common.py": "d6cac268e9129d5cffe688d32546213bd5bdfbe4a40d8e6737dc85d4bc945d2f",
    "binary_layer/tools/mzml_mock.py": "fdf7855d21936648c2f4d8e4fc1424b56c1a37b64b4f53dce3d4fbd3400e6a12",
    "binary_layer/tools/raw_mock.py": "bab479295ef42a2c6eaa25d9228d7a3ad256c929cbf72f8b81f224190fe9e521",
    "binary_layer/tools/real_mzml.py": "f805755afc0234e3f35b1472c68c793ba481f55db5c06a3719d3b4ab4d9f84eb",
    "binary_layer/validator.py": "84f2a5b0aa5d471b15d449889ea10a289e4162eab4f0f31ca7faeab71f6e8103",
    "binary_layer/writer.py": "8cf4635db19eda9b1bcc0ecfb85b1232bf8961277e47e7ff4fffa84b6390ed48",
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _stats(values: list[float]) -> dict[str, float]:
    return {
        "minimum": min(values),
        "median": statistics.median(values),
        "maximum": max(values),
        "standard_deviation": statistics.pstdev(values),
    }


def _fit(points: list[tuple[float, float]]) -> dict[str, Any]:
    x_values = [item[0] for item in points]
    y_values = [item[1] for item in points]
    x_mean = statistics.mean(x_values)
    y_mean = statistics.mean(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    slope = sum((x - x_mean) * (y - y_mean) for x, y in points) / denominator
    intercept = y_mean - slope * x_mean
    predictions = [slope * value + intercept for value in x_values]
    residual = sum((actual - predicted) ** 2 for actual, predicted in zip(y_values, predictions))
    total = sum((actual - y_mean) ** 2 for actual in y_values)
    return {
        "slope": slope,
        "intercept": intercept,
        "r_squared": 1.0 - residual / total if total else 1.0,
        "sample_count": len(points),
        "measured_x_min": min(x_values),
        "measured_x_max": max(x_values),
    }


def _operation(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in report["measurements"] if item["operation"] == name)


def build_summary(
    results_dir: Path,
    root: Path,
    before_tests: int,
    after_tests: int,
    acceptance_status: str,
) -> dict[str, Any]:
    real_runs = [_read(results_dir / f"real_run{index}.json") for index in range(1, 4)]
    scales = [_read(results_dir / f"S{index}.json") for index in range(1, 4)]
    variant = _read(results_dir / "S1_variant.json")
    reader_real = _read(results_dir / "reader_real.json")
    reader_s1 = _read(results_dir / "reader_S1.json")
    encodings = _read(results_dir / "array_encodings_real.json")
    validator_breakdown = _read(results_dir / "validator_real.json")
    real_zp = root / "benchmarks/generated/20191118_rvg262_LT_110516-13_1000-1100_Techrep01.repeat2.zp"
    numeric_decimal_bytes_by_type = None
    if real_zp.exists():
        arrays = ZpReader(real_zp).read_arrays()
        numeric_decimal_bytes_by_type = {
            kind: sum(max(0, len(canonical_json_bytes(item.values)) - 2) for item in arrays if item.array_type == kind)
            for kind in ("mz", "intensity", "time")
        }

    real_metrics = (
        "parse_seconds", "writer_seconds", "validator_seconds", "pipeline_seconds", "reader_seconds",
        "tracemalloc_peak_bytes", "rss_peak_bytes", "zp_size",
    )
    real_statistics = {
        name: _stats([float(item[name]) for item in real_runs])
        for name in real_metrics
    }
    real_point = dict(real_runs[0])
    for name in ("writer_seconds", "validator_seconds", "rss_peak_bytes", "zp_size"):
        real_point[name] = real_statistics[name]["median"]
    fit_points = scales + [real_point]
    models = {
        "zp_size_by_peak_count": _fit([(item["peak_count"], item["zp_size"]) for item in fit_points]),
        "rss_peak_by_peak_count": _fit([(item["peak_count"], item["rss_peak_bytes"]) for item in fit_points]),
        "writer_seconds_by_peak_count": _fit([(item["peak_count"], item["writer_seconds"]) for item in fit_points]),
        "validator_seconds_by_peak_count": _fit([(item["peak_count"], item["validator_seconds"]) for item in fit_points]),
        "rss_peak_by_array_count": _fit([(item["array_count"], item["rss_peak_bytes"]) for item in fit_points]),
        "writer_seconds_by_array_count": _fit([(item["array_count"], item["writer_seconds"]) for item in fit_points]),
    }
    reader_fit = _fit([
        (reader_s1["zp_size"], _operation(reader_s1, "read_spectrum_arrays_middle")["seconds"]),
        (reader_real["zp_size"], _operation(reader_real, "read_spectrum_arrays_middle")["seconds"]),
    ])
    models["single_spectrum_read_by_zp_size"] = reader_fit
    extrapolations = []
    for peak_count in (5_000_000, 10_000_000, 50_000_000, 100_000_000):
        zp_size = models["zp_size_by_peak_count"]["slope"] * peak_count + models["zp_size_by_peak_count"]["intercept"]
        extrapolations.append({
            "peak_count": peak_count,
            "estimated_zp_size": max(0.0, zp_size),
            "estimated_peak_rss_bytes": max(0.0, models["rss_peak_by_peak_count"]["slope"] * peak_count + models["rss_peak_by_peak_count"]["intercept"]),
            "estimated_writer_seconds": max(0.0, models["writer_seconds_by_peak_count"]["slope"] * peak_count + models["writer_seconds_by_peak_count"]["intercept"]),
            "estimated_validator_seconds": max(0.0, models["validator_seconds_by_peak_count"]["slope"] * peak_count + models["validator_seconds_by_peak_count"]["intercept"]),
            "estimated_single_spectrum_read_seconds": max(0.0, reader_fit["slope"] * zp_size + reader_fit["intercept"]),
            "evidence_kind": "linear extrapolation, not measured",
        })

    hashes_after = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in PRODUCTION_HASHES_BEFORE
    }
    return {
        "benchmark_version": "p1-b6-v1",
        "acceptance_status": acceptance_status,
        "baseline": {
            "tests_before": before_tests,
            "tests_after_at_summary_time": after_tests,
            "production_sha256_before": PRODUCTION_HASHES_BEFORE,
            "production_sha256_after": hashes_after,
            "production_files_unchanged": hashes_after == PRODUCTION_HASHES_BEFORE,
        },
        "environment": real_runs[0]["environment"],
        "real_runs": real_runs,
        "real_statistics": real_statistics,
        "synthetic_scales": scales,
        "synthetic_variant": variant,
        "s4": {"status": "not run", "reason": "optional point skipped after S3 and real sample approached 2.1-2.38M peaks; resource safety took priority"},
        "reader_real": reader_real,
        "reader_s1": reader_s1,
        "array_encodings": encodings,
        "validator_breakdown": validator_breakdown,
        "real_block_stats": real_runs[0]["block_stats"],
        "real_array_stats": real_runs[0]["array_stats"],
        "real_array_numeric_decimal_bytes_by_type": numeric_decimal_bytes_by_type,
        "tracemalloc_snapshots": real_runs[0]["tracemalloc_snapshots"],
        "models": models,
        "model_limits": [
            "four fit points cover 32,768 to 2,379,436 peaks and mix synthetic with one real acquisition",
            "peak count, Spectrum count, array count, decimal shape, compression and metadata are confounded",
            "linear extrapolation does not prove behavior at 5M-100M peaks, OOM boundaries, filesystem limits or concurrency",
            "single-spectrum read fit has only two points and R² is not evidence of generality",
        ],
        "extrapolations": extrapolations,
        "v1_thresholds": {
            "recommended_max_input_bytes": 67_108_864,
            "recommended_max_peak_count": 5_000_000,
            "recommended_max_output_bytes": 209_715_200,
            "recommended_max_rss_bytes": 4_294_967_296,
            "warning": "warn if any of input >=32 MiB, peaks >=2M, predicted output >=80 MiB or predicted RSS >=1.5 GiB",
            "reject": "reject if any hard maximum is exceeded or free resources cannot reserve the predicted RSS plus one output-sized temporary file",
            "concurrency": "apply limits to aggregate concurrent imports; two allowed jobs must fit within 50% of physical RAM",
            "basis": "measured 2.10-2.38M peak cases used 1.51-1.72 GiB RSS; hard limits add roughly 2x headroom but remain prototype gates",
        },
        "decision": {
            "preferred": "ZP v2 single arrays region with an internal array directory and contiguous little-endian float64 payloads",
            "alternative": "ZP v2 per-array chunks with a block-internal subdirectory",
            "zp_version": "2 required: physical encoding and read/checksum semantics are incompatible with frozen v1 JSON arrays",
            "compatibility": "one version-dispatching Reader supporting frozen v1 and explicit v2; offline migration is optional, not a prerequisite",
            "next_stage": "P1-B7: ZP v2 binary array format design and compatibility plan",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate P1-B6 measured result files")
    parser.add_argument("--results-dir", type=Path, default=Path("benchmarks/results"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results/p1_b6_summary.json"))
    parser.add_argument("--before-tests", type=int, default=251)
    parser.add_argument("--after-tests", type=int, required=True)
    parser.add_argument("--acceptance-status", default="pending final regression and hash audit")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    summary = build_summary(
        args.results_dir.resolve(),
        root,
        args.before_tests,
        args.after_tests,
        args.acceptance_status,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
