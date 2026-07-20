from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from conftest import rewrite_zp
from zp_v2_validator_support import mutate_json_block
from zp_v2_writer_support import build_real_blocks


FAILURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "failures" / "candidate_parity"
EXPECTED_FAILURE_SHA256 = {
    1: "2d4b74af4dc24f7be53727ef16cfc514fb122c43c694ed3041ac5872e19a46bd",
    2: "ef3cf3a0f0d396b69dc49d6c27e6b127287d042e0a759f2a0ac2b8a4135471e6",
}
REAL_FIXTURE = "accept_tic_bpc_chromatograms.mzML"
REQUIRED_REFERENCES = (
    ("runs", "source_file"),
    ("runs", "run_name"),
    ("spectra", "native_id"),
    ("chromatograms", "chromatogram_type"),
    ("chromatograms", "native_id"),
)


def _write_pair(tmp_path: Path):
    blocks = build_real_blocks(REAL_FIXTURE)
    paths = {}
    for version in (1, 2):
        path = tmp_path / f"string-pool-v{version}.zp"
        ZpWriter().write(path, blocks, format_version=version)
        paths[version] = path
    return paths, blocks


def _mutate_pool(path: Path, version: int, mutation) -> None:
    if version == 1:
        rewrite_zp(path, lambda payloads: mutation(payloads["string_pool"]["strings"]))
    else:
        mutate_json_block(path, "string_pool", lambda value: mutation(value["strings"]))


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def test_committed_string_pool_failure_fixtures_have_parity_without_byte_drift() -> None:
    results = {}
    for version in (1, 2):
        path = FAILURE_DIR / f"string_pool_mismatch_v{version}.zp"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_FAILURE_SHA256[version]
        results[version] = ZpValidator().validate(path)

    assert results[1].valid is results[2].valid is False
    assert _codes(results[1]) == _codes(results[2]) == ["INVALID_REFERENCE"]
    assert results[1].checked_blocks == results[2].checked_blocks == 9
    assert results[1].issues[0].block_name == results[2].issues[0].block_name == "string_pool"


def test_complete_required_string_pool_is_valid_in_both_versions(tmp_path: Path) -> None:
    paths, _blocks = _write_pair(tmp_path)
    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert results[1].valid is results[2].valid is True
    assert results[1].issues == results[2].issues == []
    assert results[1].checked_blocks == results[2].checked_blocks == 9


@pytest.mark.parametrize(("attribute", "field"), REQUIRED_REFERENCES)
def test_each_missing_required_string_has_v1_v2_domain_parity(
    attribute: str,
    field: str,
    tmp_path: Path,
) -> None:
    paths, blocks = _write_pair(tmp_path)
    missing = getattr(getattr(blocks, attribute)[0], field)
    for version, path in paths.items():
        _mutate_pool(path, version, lambda strings: strings.remove(missing))

    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert results[1].valid is results[2].valid is False
    assert _codes(results[1]) == _codes(results[2]) == ["INVALID_REFERENCE"]
    assert results[1].checked_blocks == results[2].checked_blocks == 9
    assert results[1].issues[0].block_name == results[2].issues[0].block_name == "string_pool"


def test_multiple_missing_string_references_have_stable_v1_v2_order(tmp_path: Path) -> None:
    paths, _blocks = _write_pair(tmp_path)
    for version, path in paths.items():
        _mutate_pool(path, version, list.clear)

    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert _codes(results[1]) == _codes(results[2])
    assert len(results[1].issues) > 1
    assert [issue.block_name for issue in results[1].issues] == [
        issue.block_name for issue in results[2].issues
    ] == ["string_pool"] * len(results[1].issues)
