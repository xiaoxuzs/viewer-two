from __future__ import annotations

from pathlib import Path

from benchmarks.benchmark_array_encodings import benchmark_array_encodings, representative_arrays


def test_array_encoding_benchmark_measures_roundtrip_size_and_float32_error() -> None:
    arrays = representative_arrays()
    measurements = {item.encoding: item for item in benchmark_array_encodings(arrays, repeats=1)}
    assert set(measurements) == {"json_float64", "binary_float64", "binary_float32", "zlib_float64", "zlib_float32"}
    assert measurements["json_float64"].numeric_roundtrip is True
    assert measurements["binary_float64"].numeric_roundtrip is True
    assert measurements["zlib_float64"].numeric_roundtrip is True
    assert measurements["binary_float32"].affected_value_count > 0
    assert measurements["binary_float32"].max_absolute_error > 0
    assert all(item.encoded_size > 0 for item in measurements.values())
    assert measurements["binary_float64"].size_ratio_vs_json == (
        measurements["binary_float64"].encoded_size / measurements["json_float64"].encoded_size
    )


def test_array_encoding_benchmark_does_not_use_production_writer() -> None:
    source = (Path(__file__).parents[1] / "benchmarks" / "benchmark_array_encodings.py").read_text(encoding="utf-8")
    assert "ZpWriter" not in source
