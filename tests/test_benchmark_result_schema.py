from __future__ import annotations

from dataclasses import fields

from benchmarks.models import BENCHMARK_VERSION, BenchmarkResult


def test_conversion_result_schema_contains_every_p1_b6_required_field() -> None:
    names = {item.name for item in fields(BenchmarkResult)}
    required = {
        "benchmark_version", "timestamp", "python_version", "platform", "cpu_count", "physical_memory",
        "pyteomics_version", "input_path", "input_kind", "input_size", "indexed", "spectrum_count",
        "ms1_count", "ms2_count", "precursor_count", "chromatogram_count", "array_count", "peak_count",
        "file_validate_seconds", "hash_seconds", "parse_seconds", "block_build_seconds", "string_pool_seconds",
        "index_seconds", "writer_seconds", "validator_seconds", "reader_seconds", "pipeline_seconds",
        "tracemalloc_peak_bytes", "rss_start_bytes", "rss_peak_bytes", "rss_end_bytes",
        "temporary_file_peak_bytes", "zp_size", "size_ratio", "bytes_per_peak", "arrays_block_size",
        "arrays_fraction_of_zp", "valid", "failure_code", "failure_message",
    }
    assert required <= names
    assert BENCHMARK_VERSION == "p1-b6-v1"

