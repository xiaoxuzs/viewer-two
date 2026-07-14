from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import struct
import time
import zlib
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from binary_layer.reader import ZpReader
from binary_layer.serialization import canonical_json_bytes

from benchmarks.models import BENCHMARK_VERSION, EncodingMeasurement


def representative_arrays(zp_path: Path | None = None) -> list[list[float]]:
    if zp_path is None:
        return [
            [100.0 + index * 0.1 for index in range(32)],
            [400.0 + index * 0.001 for index in range(4096)],
            [float((index * 17) % 997) for index in range(256)],
            [index * 0.5 for index in range(128)],
            [float(1000 + index * 3) for index in range(128)],
        ]
    arrays = ZpReader(zp_path).read_arrays()
    mz_arrays = sorted((item for item in arrays if item.array_type == "mz"), key=lambda item: len(item.values))
    intensity_arrays = [item for item in arrays if item.array_type == "intensity"]
    time_arrays = [item for item in arrays if item.array_type == "time"]
    selected = []
    if mz_arrays:
        selected.extend((mz_arrays[0].values, mz_arrays[-1].values))
    if intensity_arrays:
        selected.append(intensity_arrays[len(intensity_arrays) // 2].values)
    if time_arrays:
        selected.append(time_arrays[0].values)
        time_id = time_arrays[0].array_id.replace(":time", ":intensity")
        chromatogram_intensity = next((item for item in intensity_arrays if item.array_id == time_id), None)
        if chromatogram_intensity is not None:
            selected.append(chromatogram_intensity.values)
    if len(selected) < 5:
        selected.extend(item.values for item in arrays if item.values and item.values not in selected)
    return [list(values) for values in selected[:5]]


def _median_seconds(action: Callable[[], object], repeats: int) -> float:
    values = []
    for _ in range(repeats):
        started = time.perf_counter()
        action()
        values.append(time.perf_counter() - started)
    return statistics.median(values)


def _pack(values: list[float], code: str) -> bytes:
    return struct.pack("<" + code * len(values), *values)


def _unpack(payload: bytes, code: str) -> list[float]:
    width = 8 if code == "d" else 4
    return list(struct.unpack("<" + code * (len(payload) // width), payload))


def _errors(source: list[list[float]], decoded: list[list[float]]) -> tuple[float, float, int]:
    max_abs = 0.0
    max_rel = 0.0
    affected = 0
    for expected_array, actual_array in zip(source, decoded):
        for expected, actual in zip(expected_array, actual_array):
            absolute = abs(expected - actual)
            relative = absolute / abs(expected) if expected else absolute
            max_abs = max(max_abs, absolute)
            max_rel = max(max_rel, relative)
            if actual != expected:
                affected += 1
    return max_abs, max_rel, affected


def benchmark_array_encodings(arrays: list[list[float]], repeats: int = 5) -> list[EncodingMeasurement]:
    if not arrays or any(not values for values in arrays):
        raise ValueError("representative arrays must be nonempty")

    json_payload = canonical_json_bytes(arrays)
    json_decode = lambda: json.loads(json_payload.decode("utf-8"))
    json_full = json_decode()
    results = [
        EncodingMeasurement(
            encoding="json_float64",
            encoded_size=len(json_payload),
            size_ratio_vs_json=1.0,
            encode_seconds=_median_seconds(lambda: canonical_json_bytes(arrays), repeats),
            decode_seconds=_median_seconds(json_decode, repeats),
            single_array_access_seconds=_median_seconds(lambda: json_decode()[len(arrays) // 2], repeats),
            full_scan_seconds=_median_seconds(lambda: sum(len(item) for item in json_decode()), repeats),
            checksum_seconds=_median_seconds(lambda: hashlib.sha256(json_payload).digest(), repeats),
            numeric_roundtrip=json_full == arrays,
            max_absolute_error=0.0,
            max_relative_error=0.0,
            affected_value_count=0,
        )
    ]

    for name, code, compressed in (
        ("binary_float64", "d", False),
        ("binary_float32", "f", False),
        ("zlib_float64", "d", True),
        ("zlib_float32", "f", True),
    ):
        def encode_all() -> list[bytes]:
            payloads = [_pack(values, code) for values in arrays]
            return [zlib.compress(payload) for payload in payloads] if compressed else payloads

        payloads = encode_all()

        def decode_one(payload: bytes) -> list[float]:
            raw = zlib.decompress(payload) if compressed else payload
            return _unpack(raw, code)

        def decode_all() -> list[list[float]]:
            return [decode_one(payload) for payload in payloads]

        decoded = decode_all()
        max_abs, max_rel, affected = _errors(arrays, decoded)
        target = payloads[len(payloads) // 2]
        results.append(
            EncodingMeasurement(
                encoding=name,
                encoded_size=sum(len(payload) for payload in payloads),
                size_ratio_vs_json=sum(len(payload) for payload in payloads) / len(json_payload),
                encode_seconds=_median_seconds(encode_all, repeats),
                decode_seconds=_median_seconds(decode_all, repeats),
                single_array_access_seconds=_median_seconds(lambda: decode_one(target), repeats),
                full_scan_seconds=_median_seconds(lambda: sum(len(item) for item in decode_all()), repeats),
                checksum_seconds=_median_seconds(lambda: [hashlib.sha256(payload).digest() for payload in payloads], repeats),
                numeric_roundtrip=affected == 0,
                max_absolute_error=max_abs,
                max_relative_error=max_rel,
                affected_value_count=affected,
            )
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="P1-B6 independent array encoding microbenchmark")
    parser.add_argument("--zp", type=Path)
    parser.add_argument("--result", type=Path, default=Path("benchmarks/results/array_encodings.json"))
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    arrays = representative_arrays(args.zp.resolve() if args.zp else None)
    measurements = benchmark_array_encodings(arrays, args.repeats)
    result = {
        "benchmark_version": BENCHMARK_VERSION,
        "source_zp": str(args.zp.resolve()) if args.zp else None,
        "array_lengths": [len(item) for item in arrays],
        "total_values": sum(len(item) for item in arrays),
        "measurements": [asdict(item) for item in measurements],
        "notes": [
            "payload sizes exclude a future array directory and compare identical numeric values",
            "zlib measurements use per-array compression",
            "float32 is a precision candidate only and does not change the v1 float64 contract",
        ],
    }
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

