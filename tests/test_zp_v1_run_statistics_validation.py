from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from binary_layer import RunBlock, ZpValidator, ZpWriter
from conftest import rewrite_zp
from zp_v2_writer_support import build_real_blocks


FAILURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "failures" / "candidate_parity"
EXPECTED_FAILURE_SHA256 = "eef0bd6cfdf3601bff3e8bb6d740f3cea7d2394f60184ec1bed76c2e36687a92"
REAL_FIXTURE = "accept_tic_bpc_chromatograms.mzML"
RUN_STATISTICS = (
    ("spectrum_count", "core_spectra", "spectra"),
    ("chromatogram_count", "core_chromatograms", "chromatograms"),
)


def _write_v1(tmp_path: Path, blocks=None) -> Path:
    path = tmp_path / "run-statistics-v1.zp"
    ZpWriter().write(path, blocks or build_real_blocks(REAL_FIXTURE), format_version=1)
    return path


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def _build_multi_run_blocks():
    blocks = build_real_blocks(REAL_FIXTURE)
    first = blocks.runs[0]
    first.chromatogram_count = 1
    blocks.chromatograms[1].run_id = "run2"
    blocks.runs.append(
        RunBlock(
            "run2",
            first.source_file,
            first.run_name,
            0,
            1,
            first.start_rt,
            first.end_rt,
        )
    )
    blocks.global_meta.run_count = 2
    return blocks


def test_committed_v1_run_count_fixture_rejects_without_byte_drift() -> None:
    path = FAILURE_DIR / "run_count_mismatch_v1.zp"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_FAILURE_SHA256

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert result.checked_blocks == 9
    assert _codes(result) == ["COUNT_MISMATCH"]
    issue = result.issues[0]
    assert issue.block_name == "core_runs[0].spectrum_count"
    for expected in ("run_000001", "spectrum_count", "1", "2"):
        assert expected in issue.message


def test_v1_accepts_correct_run_statistics(tmp_path: Path) -> None:
    result = ZpValidator().validate(_write_v1(tmp_path))

    assert result.valid is True
    assert result.issues == []
    assert result.checked_blocks == 9


@pytest.mark.parametrize(("field", "block_name", "attribute"), RUN_STATISTICS)
@pytest.mark.parametrize("direction", [-1, 1], ids=["declared_too_small", "declared_too_large"])
def test_v1_rejects_each_run_statistic_mismatch(
    field: str,
    block_name: str,
    attribute: str,
    direction: int,
    tmp_path: Path,
) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    actual = len(getattr(blocks, attribute))
    declared = actual + direction
    path = _write_v1(tmp_path, blocks)
    rewrite_zp(path, lambda payloads: payloads["core_runs"][0].update({field: declared}))

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert _codes(result) == ["COUNT_MISMATCH"]
    assert result.checked_blocks == 9
    issue = result.issues[0]
    assert issue.block_name == f"core_runs[0].{field}"
    for expected in ("run1", field, str(declared), str(actual), block_name):
        assert expected in issue.message


@pytest.mark.parametrize(("field", "_block_name", "attribute"), RUN_STATISTICS)
def test_v1_rejects_zero_declared_for_nonzero_owned_records(
    field: str,
    _block_name: str,
    attribute: str,
    tmp_path: Path,
) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    assert len(getattr(blocks, attribute)) > 0
    path = _write_v1(tmp_path, blocks)
    rewrite_zp(path, lambda payloads: payloads["core_runs"][0].update({field: 0}))

    result = ZpValidator().validate(path)

    assert _codes(result) == ["COUNT_MISMATCH"]


@pytest.mark.parametrize(("field", "_block_name", "_attribute"), RUN_STATISTICS)
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda record, field: record.pop(field), "MISSING_FIELD"),
        (lambda record, field: record.update({field: "1"}), "INVALID_FIELD_TYPE"),
        (lambda record, field: record.update({field: 1.0}), "INVALID_FIELD_TYPE"),
        (lambda record, field: record.update({field: True}), "INVALID_FIELD_TYPE"),
        (lambda record, field: record.update({field: -1}), "INVALID_FIELD_TYPE"),
    ],
    ids=["missing", "string", "float", "bool", "negative"],
)
def test_v1_run_statistic_schema_errors_remain_schema_errors(
    field: str,
    _block_name: str,
    _attribute: str,
    mutation,
    expected_code: str,
    tmp_path: Path,
) -> None:
    path = _write_v1(tmp_path)
    rewrite_zp(path, lambda payloads: mutation(payloads["core_runs"][0], field))

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert _codes(result)[0] == expected_code


@pytest.mark.parametrize(
    ("field", "block_name"),
    [("spectrum_count", "core_spectra"), ("chromatogram_count", "core_chromatograms")],
)
def test_v1_does_not_derive_run_statistics_from_unparseable_records(
    field: str,
    block_name: str,
    tmp_path: Path,
) -> None:
    path = _write_v1(tmp_path)
    rewrite_zp(path, lambda payloads: payloads[block_name].__setitem__(0, "not-a-record"))

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert "INVALID_RECORD_SCHEMA" in _codes(result)
    assert not any(issue.code == "COUNT_MISMATCH" and field in issue.message for issue in result.issues)


def test_v1_does_not_derive_run_statistics_from_unparseable_run_block(tmp_path: Path) -> None:
    path = _write_v1(tmp_path)
    rewrite_zp(path, lambda payloads: payloads.update(core_runs={}))

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert "INVALID_BLOCK_SCHEMA" in _codes(result)
    assert "COUNT_MISMATCH" not in _codes(result)


def test_v1_missing_run_reference_precedes_owned_count_mismatch(tmp_path: Path) -> None:
    path = _write_v1(tmp_path)
    rewrite_zp(path, lambda payloads: payloads["core_spectra"][0].update(run_id="missing"))

    result = ZpValidator().validate(path)

    assert _codes(result)[:2] == ["INVALID_REFERENCE", "COUNT_MISMATCH"]
    assert result.issues[1].block_name == "core_runs[0].spectrum_count"


def test_v1_accepts_correct_multi_run_statistics_and_zero_spectrum_run(tmp_path: Path) -> None:
    result = ZpValidator().validate(_write_v1(tmp_path, _build_multi_run_blocks()))

    assert result.valid is True
    assert result.issues == []


def test_v1_multi_run_issue_order_is_run_then_schema_field(tmp_path: Path) -> None:
    path = _write_v1(tmp_path, _build_multi_run_blocks())

    def mutate(payloads) -> None:
        payloads["core_runs"][0]["spectrum_count"] += 1
        payloads["core_runs"][0]["chromatogram_count"] += 1
        payloads["core_runs"][1]["chromatogram_count"] += 1

    rewrite_zp(path, mutate)
    result = ZpValidator().validate(path)

    assert _codes(result) == ["COUNT_MISMATCH"] * 3
    assert [issue.block_name for issue in result.issues] == [
        "core_runs[0].spectrum_count",
        "core_runs[0].chromatogram_count",
        "core_runs[1].chromatogram_count",
    ]


def test_v1_duplicate_run_id_precedes_and_suppresses_ambiguous_statistics(tmp_path: Path) -> None:
    path = _write_v1(tmp_path)

    def mutate(payloads) -> None:
        payloads["core_runs"].append(copy.deepcopy(payloads["core_runs"][0]))
        payloads["global_meta"]["run_count"] = 2

    rewrite_zp(path, mutate)
    result = ZpValidator().validate(path)

    assert "DUPLICATE_ID" in _codes(result)
    assert "COUNT_MISMATCH" not in _codes(result)


def test_run_statistics_aggregation_traverses_each_record_class_once() -> None:
    class CountingList(list):
        def __init__(self, values) -> None:
            super().__init__(values)
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    spectra = CountingList({"run_id": f"run{index % 100}"} for index in range(10_000))
    chromatograms = CountingList({"run_id": f"run{index % 100}"} for index in range(2_000))

    spectrum_counts = ZpValidator._build_counts_by_run(spectra)
    chromatogram_counts = ZpValidator._build_counts_by_run(chromatograms)

    assert spectra.iterations == chromatograms.iterations == 1
    assert isinstance(spectrum_counts, dict) and isinstance(chromatogram_counts, dict)
    assert sum(spectrum_counts.values()) == 10_000
    assert sum(chromatogram_counts.values()) == 2_000
