from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from binary_layer import ZpValidator
from specs.zp_full.inspect_full_zp import inspect_full_zp


FIXTURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


@pytest.mark.parametrize(
    ("name", "version", "spectra", "precursors", "chromatograms", "arrays", "values"),
    [
        ("valid_full_v1.zp", 1, 2, 1, 1, 6, 22),
        ("valid_full_v2.zp", 2, 2, 1, 1, 6, 22),
        ("valid_minimal_v1.zp", 1, 1, 0, 0, 2, 2),
        ("valid_minimal_v2.zp", 2, 1, 0, 0, 2, 2),
    ],
)
def test_complete_file_golden_matches_manifest_and_both_checkers(
    name: str,
    version: int,
    spectra: int,
    precursors: int,
    chromatograms: int,
    arrays: int,
    values: int,
) -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    record = next(item for item in manifest["fixtures"] if item["file"] == name)
    path = FIXTURE_DIR / name
    report = inspect_full_zp(path)
    result = ZpValidator().validate(path)

    assert path.stat().st_size < 100 * 1024
    assert report["sha256"] == record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["file_size"] == record["file_size"] == path.stat().st_size
    assert report["header"]["version"] == record["format_version"] == version
    assert report["header"]["created_at"] == record["created_at"] == 1784160000000
    assert report["header"]["directory_offset"] == record["directory_offset"]
    assert [item["block_name"] for item in report["directory"]] == record["block_order"]
    assert report["statistics"] == {
        "run_count": record["run_count"],
        "spectrum_count": spectra,
        "ms1_count": record["ms1_count"],
        "ms2_count": record["ms2_count"],
        "precursor_count": precursors,
        "chromatogram_count": chromatograms,
        "array_count": arrays,
        "numeric_value_count": values,
        "extension_count": record["extension_count"],
    }
    assert report["arrays"] == record["arrays"]
    assert result.valid is True
    assert result.issues == []
    assert result.checked_blocks == 9


def test_full_golden_covers_frozen_numeric_and_domain_content() -> None:
    report = inspect_full_zp(FIXTURE_DIR / "valid_full_v1.zp")
    arrays = {item["array_id"]: item for item in report["arrays"]}
    all_values = [value for item in arrays.values() for value in item["values"]]

    assert {item["array_type"] for item in arrays.values()} == {"mz", "intensity", "time"}
    assert 0.0 in all_values
    assert any(value < 0 for item in arrays.values() if item["array_type"] == "intensity" for value in item["values"])
    assert any(item["value_count"] > 3 for item in arrays.values())
    assert report["blocks"]["extensions"][1]["payload"]["arrays"][0]["owner_id"] == "chromatogram_000001"
