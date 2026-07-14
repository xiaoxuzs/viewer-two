from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_layer.mzml_admission import evaluate_mzml_admission
from mzml_test_support import summarize_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Test-side, read-only mzML admission check")
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    summary = summarize_profile(args.file.resolve())
    result = evaluate_mzml_admission(summary.pop("profile"))
    summary.update({
        "accepted": result.accepted,
        "issue_count": len(result.issues),
        "warning_count": len(result.warnings),
        "issues": [{"code": item.code, "location": item.location, "message": item.message} for item in result.issues],
    })
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
