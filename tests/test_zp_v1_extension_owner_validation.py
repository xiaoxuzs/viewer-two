from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from binary_layer import ZpValidator, ZpWriter
from binary_layer.validator import ZpValidator as V1ValidatorImplementation
from zp_v2_writer_support import build_real_blocks


FAILURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "failures"
REAL_FIXTURE = "accept_tic_bpc_chromatograms.mzML"


def _auxiliary_extension(blocks):
    return next(item for item in blocks.extensions if item.extension_type == "mzml_auxiliary_arrays")


def _write_v1(tmp_path: Path, blocks, name: str = "case.zp"):
    path = tmp_path / name
    ZpWriter().write(path, blocks, format_version=1)
    return ZpValidator().validate(path)


def _codes(result) -> list[str]:
    return [item.code for item in result.issues]


def test_committed_v1_failure_fixture_rejects_missing_chromatogram_owner() -> None:
    result = ZpValidator().validate(FAILURE_DIR / "extension_owner_mismatch_v1.zp")

    assert result.valid is False
    assert result.checked_blocks == 9
    assert _codes(result) == ["INVALID_REFERENCE"]
    issue = result.issues[0]
    assert "owner_kind=chromatogram" in issue.message
    assert "owner_id='missing_chromatogram'" in issue.message
    assert issue.block_name == "extensions[0].payload.arrays[0].owner_id"


def test_legal_chromatogram_owner_remains_valid_for_v1(tmp_path: Path) -> None:
    result = _write_v1(tmp_path, build_real_blocks(REAL_FIXTURE))

    assert result.valid is True
    assert result.issues == []
    assert result.checked_blocks == 9


@pytest.mark.parametrize("owner_id", ["missing_chromatogram", "spectrum_000001"])
def test_v1_rejects_missing_or_wrong_entity_type_chromatogram_owner(
    owner_id: str,
    tmp_path: Path,
) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    _auxiliary_extension(blocks).payload["arrays"][0]["owner_id"] = owner_id

    result = _write_v1(tmp_path, blocks)

    assert result.valid is False
    assert _codes(result) == ["INVALID_REFERENCE"]
    assert owner_id in result.issues[0].message


def test_v1_reports_only_the_invalid_middle_owner_in_source_order(tmp_path: Path) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    auxiliary = _auxiliary_extension(blocks)
    template = auxiliary.payload["arrays"][0]
    auxiliary.payload["arrays"] = [
        {**deepcopy(template), "owner_id": "chromatogram_000001"},
        {**deepcopy(template), "owner_id": "missing_middle"},
        {**deepcopy(template), "owner_id": "chromatogram_000002"},
    ]

    result = _write_v1(tmp_path, blocks)

    assert _codes(result) == ["INVALID_REFERENCE"]
    assert "missing_middle" in result.issues[0].message
    assert result.issues[0].block_name == "extensions[1].payload.arrays[1].owner_id"


def test_v1_multiple_missing_owner_issues_are_stable(tmp_path: Path) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    auxiliary = _auxiliary_extension(blocks)
    template = auxiliary.payload["arrays"][0]
    owner_ids = ["missing_first", "missing_second", "missing_third"]
    auxiliary.payload["arrays"] = [
        {**deepcopy(template), "owner_id": owner_id}
        for owner_id in owner_ids
    ]

    result = _write_v1(tmp_path, blocks)

    assert _codes(result) == ["INVALID_REFERENCE"] * 3
    assert all(owner_id in issue.message for owner_id, issue in zip(owner_ids, result.issues))
    assert [issue.block_name for issue in result.issues] == [
        f"extensions[1].payload.arrays[{position}].owner_id"
        for position in range(3)
    ]


@pytest.mark.parametrize("mutation", ["unknown_kind", "missing_owner_id", "owner_id_type", "spectrum_owner"])
def test_v1_auxiliary_schema_errors_do_not_fall_through_to_reference_validation(
    mutation: str,
    tmp_path: Path,
) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    record = _auxiliary_extension(blocks).payload["arrays"][0]
    if mutation == "unknown_kind":
        record["owner_kind"] = "run"
    elif mutation == "missing_owner_id":
        record.pop("owner_id")
    elif mutation == "owner_id_type":
        record["owner_id"] = 7
    elif mutation == "spectrum_owner":
        record["owner_kind"] = "spectrum"
        record["owner_id"] = "spectrum_000001"

    result = _write_v1(tmp_path, blocks)

    assert "INVALID_EXTENSION_SCHEMA" in _codes(result)
    assert "INVALID_REFERENCE" not in _codes(result)


def test_v1_empty_auxiliary_array_list_uses_existing_schema_semantics(tmp_path: Path) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    _auxiliary_extension(blocks).payload["arrays"] = []

    result = _write_v1(tmp_path, blocks)

    assert result.valid is True
    assert result.issues == []


def test_v1_unrelated_extension_is_unchanged(tmp_path: Path) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    auxiliary = _auxiliary_extension(blocks)
    auxiliary.extension_type = "vendor_private"
    auxiliary.extension_version = "arbitrary"
    auxiliary.payload = {"owner_kind": "unknown", "owner_id": "missing"}

    result = _write_v1(tmp_path, blocks)

    assert result.valid is True
    assert result.issues == []


class _CountingSet(set[str]):
    def __init__(self, values: set[str]) -> None:
        super().__init__(values)
        self.contains_calls = 0

    def __contains__(self, value: object) -> bool:
        self.contains_calls += 1
        return super().__contains__(value)


def test_v1_owner_lookup_is_one_set_membership_per_auxiliary_record() -> None:
    count = 10_000
    owner_ids = {f"chromatogram_{position:05d}" for position in range(count)}
    counted_ids = _CountingSet(owner_ids)
    extension = {
        "extension_type": "mzml_auxiliary_arrays",
        "extension_version": "1",
        "payload": {
            "arrays": [
                {
                    "owner_kind": "chromatogram",
                    "owner_id": owner_id,
                    "array_accession": "MS:1000786",
                    "array_name": "ms level",
                    "dtype": "int64",
                    "values": [1],
                    "unit_accession": "UO:0000186",
                    "unit_name": "dimensionless unit",
                }
                for owner_id in sorted(owner_ids)
            ],
            "schema_version": 1,
        },
    }
    issues: list[tuple[Any, ...]] = []

    V1ValidatorImplementation._validate_extension_references(
        [extension],
        spectrum_ids=set(),
        chromatogram_ids=counted_ids,
        add=lambda *args: issues.append(args),
    )

    assert issues == []
    assert counted_ids.contains_calls == count
