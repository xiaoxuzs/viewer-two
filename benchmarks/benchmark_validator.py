from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from binary_layer.validator import ZpValidator

from benchmarks.benchmark_mzml_conversion import _block_analysis, _instrument
from benchmarks.models import BENCHMARK_VERSION


def main() -> int:
    parser = argparse.ArgumentParser(description="P1-B6 Validator stage attribution")
    parser.add_argument("--zp", type=Path, required=True)
    parser.add_argument("--result", type=Path, default=Path("benchmarks/results/validator.json"))
    args = parser.parse_args()
    path = args.zp.resolve()
    stages: dict[str, float] = {}
    started = time.perf_counter()
    with _instrument(stages, [], False):
        validation = ZpValidator().validate(path)
    wall = time.perf_counter() - started
    block_stats, _array_stats, checksum_replay = _block_analysis(path)
    report = {
        "benchmark_version": BENCHMARK_VERSION,
        "zp_path": str(path),
        "valid": validation.valid,
        "validator_wall_seconds": wall,
        "validator_json_parse_seconds": stages.get("validator_json_seconds"),
        "validator_schema_seconds": stages.get("validator_schema_seconds"),
        "validator_relationship_seconds": stages.get("validator_relationship_seconds"),
        "checksum_replay_seconds": checksum_replay,
        "block_stats": block_stats,
        "notes": "checksum replay times the same SHA-256 operation per block outside the production Validator for isolated attribution; the full production Validator ran first",
    }
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if validation.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())

