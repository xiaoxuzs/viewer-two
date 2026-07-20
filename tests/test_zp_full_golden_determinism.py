from __future__ import annotations

import hashlib
from pathlib import Path

from specs.zp_full.build_full_golden_fixtures import build_into, check


ROOT = Path(__file__).parents[1]


def _hashes(directory: Path) -> dict[str, str]:
    return {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in directory.iterdir()
        if item.is_file()
    }


def test_complete_file_golden_regeneration_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    build_into(first)
    build_into(second)

    assert _hashes(first) == _hashes(second)
    assert {item.name for item in first.iterdir()} == {
        "valid_full_v1.zp",
        "valid_full_v2.zp",
        "valid_minimal_v1.zp",
        "valid_minimal_v2.zp",
        "manifest.json",
    }


def test_complete_file_golden_check_is_read_only_and_p1_b7_hashes_are_frozen() -> None:
    fixture_dir = ROOT / "specs" / "zp_v2" / "fixtures"
    before = _hashes(fixture_dir)
    check()
    after = _hashes(fixture_dir)

    assert before == after == {
        "manifest.json": "280c48d13d163880ab370ddaeb1889de547475c94b882f213b77cadac3aa4c61",
        "valid_arrays_v2.bin": "fc08d7123bd5abcb811d6fdbe5fff06b2250cb7e92727f5275d16cdb70cf7a5c",
        "valid_empty_arrays_v2.bin": "a81b75aaa9e6f59ea15b9b3fe9bb4cb386e0ca30db253d196c852151a8d46616",
    }
