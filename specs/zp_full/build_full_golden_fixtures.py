from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
for location in (ROOT, TESTS):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from specs.zp_full.inspect_full_zp import inspect_full_zp  # noqa: E402
from zp_compatibility_support import (  # noqa: E402
    FIXED_CREATED_AT,
    build_full_blocks,
    build_minimal_blocks,
    write_zp,
)


FIXTURE_DIR = Path(__file__).with_name("fixtures")
MANIFEST_NAME = "manifest.json"
FIXTURES = (
    ("valid_full_v1.zp", 1, build_full_blocks),
    ("valid_full_v2.zp", 2, build_full_blocks),
    ("valid_minimal_v1.zp", 1, build_minimal_blocks),
    ("valid_minimal_v2.zp", 2, build_minimal_blocks),
)


def _record(report: dict[str, object]) -> dict[str, object]:
    header = report["header"]
    directory = report["directory"]
    statistics = report["statistics"]
    assert isinstance(header, dict) and isinstance(directory, list) and isinstance(statistics, dict)
    return {
        "file": report["file"],
        "format_version": header["version"],
        "file_size": report["file_size"],
        "sha256": report["sha256"],
        "created_at": header["created_at"],
        "directory_offset": header["directory_offset"],
        "block_count": len(directory),
        "block_order": [item["block_name"] for item in directory],
        "block_offsets": {item["block_name"]: item["offset"] for item in directory},
        "block_lengths": {item["block_name"]: item["length"] for item in directory},
        "block_encodings": {item["block_name"]: item["encoding"] for item in directory},
        "block_checksums": {item["block_name"]: item["checksum"] for item in directory},
        **statistics,
        "arrays": report["arrays"],
    }


def build_into(directory: Path) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for name, version, builder in FIXTURES:
        path = directory / name
        write_zp(path, builder(), version)
        records.append(_record(inspect_full_zp(path)))
    manifest = {
        "format": "zp-full-v1-v2-golden",
        "fixed_created_at": FIXED_CREATED_AT.isoformat().replace("+00:00", "Z"),
        "fixtures": records,
    }
    (directory / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def check() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        generated = Path(temporary)
        build_into(generated)
        expected_names = {name for name, _version, _builder in FIXTURES} | {MANIFEST_NAME}
        actual_names = {item.name for item in FIXTURE_DIR.iterdir() if item.is_file()}
        if actual_names != expected_names:
            raise SystemExit(f"full Golden file set drifted: {sorted(actual_names)}")
        for name in sorted(expected_names):
            if (generated / name).read_bytes() != (FIXTURE_DIR / name).read_bytes():
                raise SystemExit(f"full Golden drifted: {name}")
    print(json.dumps({"full_golden_deterministic": True}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
        return
    manifest = build_into(FIXTURE_DIR)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
