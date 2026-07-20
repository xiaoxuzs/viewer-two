from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from conftest import rewrite_zp
from zp_v2_writer_support import build_real_blocks


FAILURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "failures" / "candidate_parity"
EXPECTED_FAILURE_SHA256 = "2d4b74af4dc24f7be53727ef16cfc514fb122c43c694ed3041ac5872e19a46bd"
REAL_FIXTURE = "accept_tic_bpc_chromatograms.mzML"
REQUIRED_REFERENCES = (
    ("runs", "source_file"),
    ("runs", "run_name"),
    ("spectra", "native_id"),
    ("chromatograms", "chromatogram_type"),
    ("chromatograms", "native_id"),
)


def _write_v1(tmp_path: Path, blocks=None) -> Path:
    path = tmp_path / "string-pool-v1.zp"
    ZpWriter().write(path, blocks or build_real_blocks(REAL_FIXTURE), format_version=1)
    return path


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def test_committed_v1_string_pool_failure_rejects_without_byte_drift() -> None:
    path = FAILURE_DIR / "string_pool_mismatch_v1.zp"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_FAILURE_SHA256

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert result.checked_blocks == 9
    assert _codes(result) == ["INVALID_REFERENCE"]
    assert result.issues[0].block_name == "string_pool"
    assert "run" in result.issues[0].message


def test_v1_accepts_complete_required_string_pool(tmp_path: Path) -> None:
    result = ZpValidator().validate(_write_v1(tmp_path))

    assert result.valid is True
    assert result.issues == []
    assert result.checked_blocks == 9


@pytest.mark.parametrize(("attribute", "field"), REQUIRED_REFERENCES)
def test_v1_rejects_each_missing_required_string(
    attribute: str,
    field: str,
    tmp_path: Path,
) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    missing = getattr(getattr(blocks, attribute)[0], field)
    path = _write_v1(tmp_path, blocks)

    def remove_reference(payloads) -> None:
        strings = payloads["string_pool"]["strings"]
        strings[:] = [value for value in strings if value != missing]

    rewrite_zp(path, remove_reference)
    result = ZpValidator().validate(path)

    assert result.valid is False
    assert _codes(result) == ["INVALID_REFERENCE"]
    assert result.checked_blocks == 9
    assert result.issues[0].block_name == "string_pool"
    assert repr(missing) in result.issues[0].message


def test_v1_missing_string_issue_order_follows_required_reference_order(tmp_path: Path) -> None:
    blocks = build_real_blocks(REAL_FIXTURE)
    expected = [
        *(value for run in blocks.runs for value in (run.source_file, run.run_name)),
        *(spectrum.native_id for spectrum in blocks.spectra),
        *(
            value
            for chromatogram in blocks.chromatograms
            for value in (chromatogram.chromatogram_type, chromatogram.native_id)
        ),
    ]
    path = _write_v1(tmp_path, blocks)
    rewrite_zp(path, lambda payloads: payloads["string_pool"].update(strings=[]))

    result = ZpValidator().validate(path)

    assert _codes(result) == ["INVALID_REFERENCE"] * len(expected)
    assert all(issue.block_name == "string_pool" for issue in result.issues)
    assert [issue.message for issue in result.issues] == [
        f"string_pool is missing referenced string {value!r}" for value in expected
    ]


def test_string_pool_reference_validation_uses_set_membership_and_one_record_pass() -> None:
    class CountingList(list):
        def __init__(self, values) -> None:
            super().__init__(values)
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    class NoLinearMembershipList(CountingList):
        def __contains__(self, value) -> bool:
            raise AssertionError(f"linear membership lookup attempted for {value!r}")

    strings = NoLinearMembershipList(["source", "run", "scan", "tic", "chrom"])
    runs = CountingList([{"source_file": "source", "run_name": "run"}])
    spectra = CountingList([{"native_id": "scan"}])
    chromatograms = CountingList([{"chromatogram_type": "tic", "native_id": "chrom"}])
    issues = []

    ZpValidator._validate_string_pool_references(
        {"strings": strings},
        runs,
        spectra,
        chromatograms,
        lambda code, message, block: issues.append((code, message, block)),
    )

    assert issues == []
    assert strings.iterations == runs.iterations == spectra.iterations == chromatograms.iterations == 1
