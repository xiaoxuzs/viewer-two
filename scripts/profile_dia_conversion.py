from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_layer.service import convert_source_to_zp
from scripts.profile_zp_validation import _windows_process_counters


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    _peak_before, read_before, write_before = _windows_process_counters()
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    result = convert_source_to_zp(
        args.bundle.resolve(),
        args.target.resolve(),
        format_version=2,
    )
    wall_seconds = time.perf_counter() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    peak_rss, read_after, write_after = _windows_process_counters()
    report = {
        "valid": result.validation.valid,
        "checked_blocks": result.validation.checked_blocks,
        "bottom_up_valid": result.validation.bottom_up_valid,
        "issues": [item.code for item in result.validation.issues],
        "bottom_up_issues": [
            item.code for item in result.validation.bottom_up_issues
        ],
        "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds,
        "peak_rss": peak_rss,
        "read_bytes": (
            read_after - read_before
            if read_after is not None and read_before is not None
            else None
        ),
        "write_bytes": (
            write_after - write_before
            if write_after is not None and write_before is not None
            else None
        ),
        "source_before": {
            "file_size": result.source_before.file_size,
            "sha256": result.source_before.sha256,
            "mtime_ns": result.source_before.mtime_ns,
        },
        "source_after": {
            "file_size": result.source_after.file_size,
            "sha256": result.source_after.sha256,
            "mtime_ns": result.source_after.mtime_ns,
        },
        "output_file_size": result.output_file_size,
        "output_sha256": result.output_sha256,
        "performance": result.performance,
        "validation_metrics": result.validation.metrics,
    }
    raw = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(raw, encoding="utf-8")
    print(raw, end="", flush=True)
    return 0 if result.validation.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
