from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BENCHMARK_VERSION = "p1-b6-v1"


@dataclass(slots=True)
class BenchmarkResult:
    benchmark_version: str
    timestamp: str
    python_version: str
    platform: str
    cpu_count: int | None
    physical_memory: int | None
    pyteomics_version: str | None
    input_path: str
    input_kind: str
    input_size: int | None
    indexed: bool | None
    spectrum_count: int | None
    ms1_count: int | None
    ms2_count: int | None
    precursor_count: int | None
    chromatogram_count: int | None
    array_count: int | None
    peak_count: int | None
    file_validate_seconds: float | None
    hash_seconds: float | None
    parse_seconds: float | None
    block_build_seconds: float | None
    string_pool_seconds: float | None
    index_seconds: float | None
    writer_seconds: float | None
    validator_seconds: float | None
    reader_seconds: float | None
    pipeline_seconds: float | None
    tracemalloc_peak_bytes: int | None
    rss_start_bytes: int | None
    rss_peak_bytes: int | None
    rss_end_bytes: int | None
    temporary_file_peak_bytes: int | None
    zp_size: int | None
    size_ratio: float | None
    bytes_per_peak: float | None
    arrays_block_size: int | None
    arrays_fraction_of_zp: float | None
    valid: bool
    failure_code: str | None
    failure_message: str | None
    run_label: str | None = None
    tracemalloc_current_bytes: int | None = None
    windows_peak_working_set_bytes: int | None = None
    admission_seconds: float | None = None
    candidate_block_seconds: float | None = None
    json_serialize_seconds: float | None = None
    writer_disk_seconds: float | None = None
    validator_checksum_seconds: float | None = None
    validator_json_seconds: float | None = None
    validator_relationship_seconds: float | None = None
    metric_unavailable_reasons: dict[str, str] = field(default_factory=dict)
    tracemalloc_snapshots: list[dict[str, Any]] = field(default_factory=list)
    block_stats: list[dict[str, Any]] = field(default_factory=list)
    array_stats: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.benchmark_version != BENCHMARK_VERSION:
            raise ValueError(f"benchmark_version must be {BENCHMARK_VERSION!r}")
        if not isinstance(self.valid, bool):
            raise ValueError("valid must be a bool")
        for name, value in asdict(self).items():
            if value is None or isinstance(value, (str, bool, dict, list)):
                continue
            if isinstance(value, (int, float)):
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(f"{name} must be finite")
                if value < 0:
                    raise ValueError(f"{name} must not be negative")
        if self.valid and (self.failure_code is not None or self.failure_message is not None):
            raise ValueError("valid results cannot carry a failure")
        if not self.valid and not self.failure_code:
            raise ValueError("failed results require failure_code")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent, allow_nan=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BenchmarkResult":
        return cls(**value)

    @classmethod
    def from_json(cls, value: str) -> "BenchmarkResult":
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("benchmark result JSON must be an object")
        return cls.from_dict(parsed)

    def write_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(indent=2) + "\n", encoding="utf-8", newline="\n")
        return target


@dataclass(frozen=True, slots=True)
class EncodingMeasurement:
    encoding: str
    encoded_size: int
    size_ratio_vs_json: float
    encode_seconds: float
    decode_seconds: float
    single_array_access_seconds: float
    full_scan_seconds: float
    checksum_seconds: float
    numeric_roundtrip: bool
    max_absolute_error: float
    max_relative_error: float
    affected_value_count: int

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                raise ValueError(f"{name} must not be negative")

