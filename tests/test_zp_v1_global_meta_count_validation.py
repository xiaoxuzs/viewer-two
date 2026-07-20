from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from conftest import rewrite_zp
from zp_v2_writer_support import build_real_blocks


FAILURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "failures" / "global_meta_count"
REAL_FIXTURE = "accept_tic_bpc_chromatograms.mzML"
EXPECTED_FAILURE_SHA256 = "91f18933482bd78daae281fe386c256fb5a31e4c19e34f197753b214019ea6e8"
COUNT_FIELDS = (
    ("run_count", "core_runs", "runs"),
    ("spectrum_count", "core_spectra", "spectra"),
    ("chromatogram_count", "core_chromatograms", "chromatograms"),
    ("array_count", "arrays", "arrays"),
)


def _write_v1(tmp_path: Path) -> Path:
    path = tmp_path / "case-v1.zp"
    ZpWriter().write(path, build_real_blocks(REAL_FIXTURE), format_version=1)
    return path


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def _actual_count(blocks, attribute: str) -> int:
    return len(getattr(blocks, attribute))


def test_committed_v1_failure_fixture_rejects_count_mismatch_without_byte_drift() -> None:
    path = FAILURE_DIR / "global_meta_count_mismatch_v1.zp"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_FAILURE_SHA256

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert result.checked_blocks == 9
    assert _codes(result) == ["COUNT_MISMATCH"]
    assert result.issues[0].block_name == "global_meta"
    assert "run_count" in result.issues[0].message
    assert "0" in result.issues[0].message
    assert "1" in result.issues[0].message


def test_v1_accepts_all_correct_global_meta_counts(tmp_path: Path) -> None:
    result = ZpValidator().validate(_write_v1(tmp_path))

    assert result.valid is True
    assert result.issues == []
    assert result.checked_blocks == 9


@pytest.mark.parametrize(("field", "block_name", "attribute"), COUNT_FIELDS)
@pytest.mark.parametrize("direction", [-1, 1], ids=["declared_too_small", "declared_too_large"])
def test_v1_rejects_each_global_meta_count_mismatch(
    field: str,
    block_name: str,
    attribute: str,
    direction: int,
    tmp_path: Path,
) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    actual = _actual_count(blocks, attribute)
    declared = actual + direction
    path = _write_v1(tmp_path)
    rewrite_zp(path, lambda payloads: payloads["global_meta"].update({field: declared}))

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert _codes(result) == ["COUNT_MISMATCH"]
    assert result.checked_blocks == 9
    issue = result.issues[0]
    assert issue.block_name == "global_meta"
    assert field in issue.message
    assert block_name in issue.message
    assert str(declared) in issue.message
    assert str(actual) in issue.message


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda value: value.pop("run_count"), "MISSING_FIELD"),
        (lambda value: value.update(run_count="1"), "INVALID_FIELD_TYPE"),
        (lambda value: value.update(run_count=1.0), "INVALID_FIELD_TYPE"),
        (lambda value: value.update(run_count=True), "INVALID_FIELD_TYPE"),
        (lambda value: value.update(run_count=-1), "INVALID_FIELD_TYPE"),
    ],
    ids=["missing", "string", "float", "bool", "negative"],
)
def test_v1_global_meta_count_schema_errors_remain_schema_errors(
    mutation,
    expected_code: str,
    tmp_path: Path,
) -> None:
    path = _write_v1(tmp_path)
    rewrite_zp(path, lambda payloads: mutation(payloads["global_meta"]))

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert _codes(result)[0] == expected_code


def test_v1_does_not_report_count_from_unparseable_core_runs(tmp_path: Path) -> None:
    path = _write_v1(tmp_path)
    rewrite_zp(path, lambda payloads: payloads.update(core_runs={}))

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert "INVALID_BLOCK_SCHEMA" in _codes(result)
    assert "COUNT_MISMATCH" not in _codes(result)
    assert result.issues[0].block_name == "core_runs"


def test_v1_multiple_global_meta_count_issues_follow_schema_field_order(tmp_path: Path) -> None:
    path = _write_v1(tmp_path)

    def mutate(payloads) -> None:
        meta = payloads["global_meta"]
        for field, _block_name, _attribute in COUNT_FIELDS:
            meta[field] += 1

    rewrite_zp(path, mutate)
    result = ZpValidator().validate(path)

    assert _codes(result) == ["COUNT_MISMATCH"] * len(COUNT_FIELDS)
    assert [field for field, _block_name, _attribute in COUNT_FIELDS] == [
        next(field for field, _block_name, _attribute in COUNT_FIELDS if field in issue.message)
        for issue in result.issues
    ]
