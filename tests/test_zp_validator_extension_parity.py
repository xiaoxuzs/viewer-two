from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from zp_v2_writer_support import build_real_blocks


FAILURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "failures"
REAL_FIXTURE = "accept_tic_bpc_chromatograms.mzML"
EXPECTED_FAILURE_SHA256 = {
    1: "289553f0eb75b3734b50b85f00e1015614e2f545d180fbc7e5e65f7e22fa82f5",
    2: "2629f288d40f00ff42285270d98a8da6f1bb17fb2f1c680675813606b4da27de",
}


def _auxiliary_extension(blocks):
    return next(item for item in blocks.extensions if item.extension_type == "mzml_auxiliary_arrays")


def _validate_pair(tmp_path: Path, blocks) -> dict[int, object]:
    results = {}
    for version in (1, 2):
        path = tmp_path / f"case-v{version}.zp"
        ZpWriter().write(path, blocks, format_version=version)
        results[version] = ZpValidator().validate(path)
    return results


def _codes(result) -> list[str]:
    return [item.code for item in result.issues]


def test_committed_failure_fixtures_now_have_validator_parity_without_byte_drift() -> None:
    results = {}
    for version in (1, 2):
        path = FAILURE_DIR / f"extension_owner_mismatch_v{version}.zp"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_FAILURE_SHA256[version]
        results[version] = ZpValidator().validate(path)

    assert results[1].valid is results[2].valid is False
    assert _codes(results[1]) == _codes(results[2]) == ["INVALID_REFERENCE"]
    assert results[1].checked_blocks == results[2].checked_blocks == 9
    assert "missing_chromatogram" in results[1].issues[0].message
    assert "missing_chromatogram" in results[2].issues[0].message


@pytest.mark.parametrize(
    ("scenario", "expected_valid", "expected_codes"),
    [
        ("legal", True, []),
        ("missing_owner", False, ["INVALID_REFERENCE"]),
        ("wrong_entity_type", False, ["INVALID_REFERENCE"]),
        ("unknown_owner_kind", False, ["INVALID_EXTENSION_SCHEMA"]),
        ("missing_owner_id", False, ["INVALID_EXTENSION_SCHEMA"]),
    ],
)
def test_v1_v2_auxiliary_owner_domain_results_match(
    scenario: str,
    expected_valid: bool,
    expected_codes: list[str],
    tmp_path: Path,
) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    record = _auxiliary_extension(blocks).payload["arrays"][0]
    if scenario == "missing_owner":
        record["owner_id"] = "missing_chromatogram"
    elif scenario == "wrong_entity_type":
        record["owner_id"] = blocks.spectra[0].spectrum_id
    elif scenario == "unknown_owner_kind":
        record["owner_kind"] = "run"
    elif scenario == "missing_owner_id":
        record.pop("owner_id")

    results = _validate_pair(tmp_path, blocks)

    assert results[1].valid is results[2].valid is expected_valid
    assert _codes(results[1]) == _codes(results[2]) == expected_codes
    assert results[1].checked_blocks == results[2].checked_blocks == 9


def test_v1_v2_multiple_missing_owner_issue_order_matches(tmp_path: Path) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    auxiliary = _auxiliary_extension(blocks)
    template = auxiliary.payload["arrays"][0]
    owner_ids = ["missing_first", "missing_second", "missing_third"]
    auxiliary.payload["arrays"] = [
        {**deepcopy(template), "owner_id": owner_id}
        for owner_id in owner_ids
    ]

    results = _validate_pair(tmp_path, blocks)

    for version in (1, 2):
        assert _codes(results[version]) == ["INVALID_REFERENCE"] * 3
        assert all(
            owner_id in issue.message
            for owner_id, issue in zip(owner_ids, results[version].issues)
        )
