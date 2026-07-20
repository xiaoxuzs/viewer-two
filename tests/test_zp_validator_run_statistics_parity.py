from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from conftest import load_raw_zp, rewrite_zp
from test_zp_v1_run_statistics_validation import _build_multi_run_blocks
from zp_v2_validator_support import mutate_json_block, top_layout
from zp_v2_writer_support import build_real_blocks


FAILURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "failures" / "candidate_parity"
EXPECTED_FAILURE_SHA256 = {
    1: "eef0bd6cfdf3601bff3e8bb6d740f3cea7d2394f60184ec1bed76c2e36687a92",
    2: "40668d81689d0fe389e7739c6a04c6eec5cad3222849c30019436b05a32bb4e3",
}
REAL_FIXTURE = "accept_tic_bpc_chromatograms.mzML"
RUN_STATISTICS = (
    ("spectrum_count", "spectra"),
    ("chromatogram_count", "chromatograms"),
)


def _write_pair(tmp_path: Path, blocks=None) -> tuple[dict[int, Path], object]:
    blocks = blocks or build_real_blocks(REAL_FIXTURE)
    paths = {}
    for version in (1, 2):
        path = tmp_path / f"run-statistics-v{version}.zp"
        ZpWriter().write(path, blocks, format_version=version)
        paths[version] = path
    return paths, blocks


def _mutate_runs(path: Path, version: int, mutation) -> None:
    if version == 1:
        rewrite_zp(path, lambda payloads: mutation(payloads["core_runs"]))
    else:
        mutate_json_block(path, "core_runs", mutation)


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def _logical_run_statistic(path: Path, version: int, field: str, run_position: int = 0) -> tuple[str, str, object, int]:
    if version == 1:
        _header, _directory, payloads = load_raw_zp(path)
    else:
        _header, _directory, raw_payloads = top_layout(path)
        payloads = {
            name: json.loads(payload.decode("utf-8"))
            for name, payload in raw_payloads.items()
            if name != "arrays"
        }
    run = payloads["core_runs"][run_position]
    source_block = "core_spectra" if field == "spectrum_count" else "core_chromatograms"
    actual = sum(record.get("run_id") == run["run_id"] for record in payloads[source_block])
    return run["run_id"], field, run[field], actual


def test_committed_run_count_failure_fixtures_have_parity_without_byte_drift() -> None:
    results = {}
    for version in (1, 2):
        path = FAILURE_DIR / f"run_count_mismatch_v{version}.zp"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_FAILURE_SHA256[version]
        results[version] = ZpValidator().validate(path)

    assert results[1].valid is results[2].valid is False
    assert _codes(results[1]) == _codes(results[2]) == ["COUNT_MISMATCH"]
    assert results[1].checked_blocks == results[2].checked_blocks == 9
    assert results[1].issues[0].block_name == "core_runs[0].spectrum_count"
    assert _logical_run_statistic(
        FAILURE_DIR / "run_count_mismatch_v1.zp", 1, "spectrum_count"
    ) == _logical_run_statistic(
        FAILURE_DIR / "run_count_mismatch_v2.zp", 2, "spectrum_count"
    ) == ("run_000001", "spectrum_count", 1, 2)
    for expected in ("run_000001", "spectrum_count", "1", "2"):
        assert expected in results[1].issues[0].message
    assert "run_000001" in results[2].issues[0].message


def test_correct_run_statistics_are_valid_in_both_versions(tmp_path: Path) -> None:
    paths, _blocks = _write_pair(tmp_path)
    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert results[1].valid is results[2].valid is True
    assert results[1].issues == results[2].issues == []
    assert results[1].checked_blocks == results[2].checked_blocks == 9


@pytest.mark.parametrize(("field", "attribute"), RUN_STATISTICS)
@pytest.mark.parametrize("direction", [-1, 1], ids=["declared_too_small", "declared_too_large"])
def test_each_run_statistic_mismatch_has_v1_v2_domain_parity(
    field: str,
    attribute: str,
    direction: int,
    tmp_path: Path,
) -> None:
    paths, blocks = _write_pair(tmp_path)
    actual = len(getattr(blocks, attribute))
    declared = actual + direction
    for version, path in paths.items():
        _mutate_runs(path, version, lambda runs: runs[0].update({field: declared}))

    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert results[1].valid is results[2].valid is False
    assert _codes(results[1]) == _codes(results[2]) == ["COUNT_MISMATCH"]
    assert results[1].checked_blocks == results[2].checked_blocks == 9
    assert _logical_run_statistic(paths[1], 1, field) == _logical_run_statistic(
        paths[2], 2, field
    ) == ("run1", field, declared, actual)
    issue = results[1].issues[0]
    assert issue.block_name == f"core_runs[0].{field}"
    for expected in ("run1", field, str(declared), str(actual)):
        assert expected in issue.message
    assert "run1" in results[2].issues[0].message


@pytest.mark.parametrize(("field", "_attribute"), RUN_STATISTICS)
@pytest.mark.parametrize(
    "mutation",
    [
        lambda record, field: record.pop(field),
        lambda record, field: record.update({field: "1"}),
        lambda record, field: record.update({field: 1.0}),
        lambda record, field: record.update({field: True}),
        lambda record, field: record.update({field: -1}),
    ],
    ids=["missing", "string", "float", "bool", "negative"],
)
def test_run_statistic_schema_errors_precede_count_issues_in_both_versions(
    field: str,
    _attribute: str,
    mutation,
    tmp_path: Path,
) -> None:
    paths, _blocks = _write_pair(tmp_path)
    for version, path in paths.items():
        _mutate_runs(path, version, lambda runs: mutation(runs[0], field))

    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert results[1].valid is results[2].valid is False
    for version in (1, 2):
        assert results[version].issues[0].code != "COUNT_MISMATCH"


def test_correct_multi_run_statistics_have_v1_v2_parity(tmp_path: Path) -> None:
    paths, _blocks = _write_pair(tmp_path, _build_multi_run_blocks())
    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert results[1].valid is results[2].valid is True
    assert results[1].issues == results[2].issues == []
    assert results[1].checked_blocks == results[2].checked_blocks == 9


def test_multiple_run_errors_have_stable_v1_v2_code_and_run_order(tmp_path: Path) -> None:
    paths, _blocks = _write_pair(tmp_path, _build_multi_run_blocks())

    def mutate(runs) -> None:
        runs[0]["spectrum_count"] += 1
        runs[1]["chromatogram_count"] += 1

    for version, path in paths.items():
        _mutate_runs(path, version, mutate)
    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert _codes(results[1]) == _codes(results[2]) == ["COUNT_MISMATCH", "COUNT_MISMATCH"]
    assert results[1].checked_blocks == results[2].checked_blocks == 9
    assert [issue.block_name for issue in results[1].issues] == [
        "core_runs[0].spectrum_count",
        "core_runs[1].chromatogram_count",
    ]
    assert _logical_run_statistic(paths[1], 1, "spectrum_count", 0) == _logical_run_statistic(
        paths[2], 2, "spectrum_count", 0
    ) == ("run1", "spectrum_count", 2, 1)
    assert _logical_run_statistic(paths[1], 1, "chromatogram_count", 1) == _logical_run_statistic(
        paths[2], 2, "chromatogram_count", 1
    ) == ("run2", "chromatogram_count", 2, 1)
    assert "run1" in results[2].issues[0].message
    assert "run2" in results[2].issues[1].message
