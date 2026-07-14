from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from .arrays_reference_codec import decode_arrays_block, validate_arrays_block
except ImportError:
    from arrays_reference_codec import decode_arrays_block, validate_arrays_block


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect one P1-B7 ZP v2 arrays block fixture")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    data = args.path.read_bytes()
    result = validate_arrays_block(data)
    report: dict[str, object] = {
        "file": str(args.path),
        "block_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "valid": result.valid,
        "error_code": result.error_code,
    }
    if result.valid:
        decoded = decode_arrays_block(data)
        report.update(
            {
                "entry_count": len(decoded.arrays),
                "directory_length": decoded.directory_length,
                "payload_offset": decoded.payload_offset,
                "payload_length": decoded.payload_length,
                "entries": decoded.directory["entries"],
            }
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
