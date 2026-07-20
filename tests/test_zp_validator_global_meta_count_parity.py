from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from conftest import rewrite_zp
from zp_v2_validator_support import mutate_json_block
from zp_v2_writer_support import build_real_blocks


FAILURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "failures" / "global_meta_count"
REAL_FIXTURE = "accept_tic_bpc_chromatograms.mzML"
EXPECTED_FAILURE_SHA256 = {
    1: "91f18933482bd78daae281fe386c256fb5a31e4c19e34f197753b214019ea6e8",
    2: "4e52767898b8bd12a0b74ca32e793c3185376f914f3c0c79f9951b0adfde263c",
}
COUNT_FIELDS = (
    ("run_count", "core_runs", "runs"),
    ("spectrum_count", "core_spectra", "spectra"),
    ("chromatogram_count", "core_chromatograms", "chromatograms"),
    ("array_count", "arrays", "arrays"),
)


def _write_pair(tmp_path: Path) -> tuple[dict[int, Path], object]:
    blocks = build_real_blocks(REAL_FIXTURE)
    paths = {}
    for version in (1, 2):
        path = tmp_path / f"case-v{version}.zp"
        ZpWriter().write(path, blocks, format_version=version)
        paths[version] = path
    return paths, blocks


def _mutate_global_meta(path: Path, version: int, mutation) -> None:
    if version == 1:
        rewrite_zp(path, lambda payloads: mutation(payloads["global_meta"]))
    else:
        mutate_json_block(path, "global_meta", mutation)


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def test_committed_global_meta_count_failure_fixtures_have_parity_without_byte_drift() -> None:
    results = {}
    for version in (1, 2):
        path = FAILURE_DIR / f"global_meta_count_mismatch_v{version}.zp"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_FAILURE_SHA256[version]
        results[version] = ZpValidator().validate(path)

    assert results[1].valid is results[2].valid is False
    assert _codes(results[1]) == _codes(results[2]) == ["COUNT_MISMATCH"]
    assert results[1].checked_blocks == results[2].checked_blocks == 9
    for version in (1, 2):
        issue = results[version].issues[0]
        assert issue.block_name == "global_meta"
        assert "run_count" in issue.message
        assert "0" in issue.message
        assert "1" in issue.message


def test_correct_global_meta_counts_are_valid_in_both_versions(tmp_path: Path) -> None:
    paths, _blocks = _write_pair(tmp_path)
    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert results[1].valid is results[2].valid is True
    assert results[1].issues == results[2].issues == []
    assert results[1].checked_blocks == results[2].checked_blocks == 9


@pytest.mark.parametrize(("field", "block_name", "attribute"), COUNT_FIELDS)
@pytest.mark.parametrize("direction", [-1, 1], ids=["declared_too_small", "declared_too_large"])
def test_each_global_meta_count_mismatch_has_v1_v2_domain_parity(
    field: str,
    block_name: str,
    attribute: str,
    direction: int,
    tmp_path: Path,
) -> None:
    paths, blocks = _write_pair(tmp_path)
    actual = len(getattr(blocks, attribute))
    declared = actual + direction
    for version, path in paths.items():
        _mutate_global_meta(path, version, lambda value: value.update({field: declared}))

    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert results[1].valid is results[2].valid is False
    assert _codes(results[1]) == _codes(results[2]) == ["COUNT_MISMATCH"]
    assert results[1].checked_blocks == results[2].checked_blocks == 9
    for version in (1, 2):
        issue = results[version].issues[0]
        assert issue.block_name == "global_meta"
        assert field in issue.message
        assert str(declared) in issue.message
        assert str(actual) in issue.message
    assert block_name in results[1].issues[0].message


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.pop("run_count"),
        lambda value: value.update(run_count="1"),
        lambda value: value.update(run_count=1.0),
        lambda value: value.update(run_count=True),
        lambda value: value.update(run_count=-1),
    ],
    ids=["missing", "string", "float", "bool", "negative"],
)
def test_global_meta_count_schema_errors_precede_count_issues_in_both_versions(
    mutation,
    tmp_path: Path,
) -> None:
    paths, _blocks = _write_pair(tmp_path)
    for version, path in paths.items():
        _mutate_global_meta(path, version, mutation)

    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert results[1].valid is results[2].valid is False
    assert results[1].issues[0].code != "COUNT_MISMATCH"
    assert results[2].issues[0].code != "COUNT_MISMATCH"
    assert results[1].issues[0].block_name == results[2].issues[0].block_name == "global_meta"


def test_multiple_global_meta_count_issue_order_matches_between_versions(tmp_path: Path) -> None:
    paths, _blocks = _write_pair(tmp_path)

    def mutate(value) -> None:
        for field, _block_name, _attribute in COUNT_FIELDS:
            value[field] += 1

    for version, path in paths.items():
        _mutate_global_meta(path, version, mutate)
    results = {version: ZpValidator().validate(path) for version, path in paths.items()}
    fields = [field for field, _block_name, _attribute in COUNT_FIELDS]

    for version in (1, 2):
        assert _codes(results[version]) == ["COUNT_MISMATCH"] * len(fields)
        assert fields == [
            next(field for field in fields if field in issue.message)
            for issue in results[version].issues
        ]

