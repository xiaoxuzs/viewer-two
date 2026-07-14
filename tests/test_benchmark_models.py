from __future__ import annotations

from dataclasses import fields

import pytest

from benchmarks.models import BENCHMARK_VERSION, BenchmarkResult


def valid_result(**overrides):
    values = {
        field.name: None
        for field in fields(BenchmarkResult)
        if field.default is field.default_factory
    }
    values.update(
        benchmark_version=BENCHMARK_VERSION,
        timestamp="2026-07-14T00:00:00Z",
        python_version="3.12.7",
        platform="win32",
        cpu_count=8,
        physical_memory=16_000_000_000,
        pyteomics_version="4.7.5",
        input_path="sample.mzML",
        input_kind="real",
        input_size=100,
        indexed=True,
        spectrum_count=2,
        ms1_count=1,
        ms2_count=1,
        precursor_count=1,
        chromatogram_count=0,
        array_count=4,
        peak_count=2,
        file_validate_seconds=0.1,
        hash_seconds=0.1,
        parse_seconds=0.1,
        block_build_seconds=0.1,
        string_pool_seconds=0.1,
        index_seconds=0.1,
        writer_seconds=0.1,
        validator_seconds=0.1,
        reader_seconds=0.1,
        pipeline_seconds=0.7,
        tracemalloc_peak_bytes=1000,
        rss_start_bytes=1000,
        rss_peak_bytes=2000,
        rss_end_bytes=1500,
        temporary_file_peak_bytes=100,
        zp_size=200,
        size_ratio=2.0,
        bytes_per_peak=100.0,
        arrays_block_size=100,
        arrays_fraction_of_zp=0.5,
        valid=True,
        failure_code=None,
        failure_message=None,
    )
    values.update(overrides)
    return BenchmarkResult(**values)


def test_benchmark_result_json_roundtrip_and_required_fields() -> None:
    result = valid_result()
    assert BenchmarkResult.from_json(result.to_json()).to_dict() == result.to_dict()
    with pytest.raises(TypeError):
        BenchmarkResult.from_dict({"benchmark_version": BENCHMARK_VERSION})


@pytest.mark.parametrize("field,value", [("writer_seconds", -0.1), ("zp_size", -1), ("size_ratio", float("inf"))])
def test_benchmark_result_rejects_invalid_numeric_values(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        valid_result(**{field: value})


def test_missing_measurement_is_none_with_reason_not_zero() -> None:
    result = valid_result(rss_peak_bytes=None, metric_unavailable_reasons={"rss_peak_bytes": "psutil unavailable"})
    assert result.rss_peak_bytes is None
    assert result.metric_unavailable_reasons["rss_peak_bytes"]


def test_schema_version_is_fixed() -> None:
    with pytest.raises(ValueError):
        valid_result(benchmark_version="future")

