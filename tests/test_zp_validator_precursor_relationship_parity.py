from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from conftest import load_raw_zp, rewrite_zp
from test_zp_v1_precursor_relationship_validation import (
    FAILURE_DIR,
    MS2_FIXTURE,
    _build_two_ms2_blocks,
    _mutate_relationship,
)
from zp_v2_validator_support import mutate_json_block, top_layout
from zp_v2_writer_support import build_real_blocks


EXPECTED_FAILURE_SHA256 = {
    1: "7e92ab9a34c578a791ca6bbebe5546f8fcc228b778535e8d77bf9cd9795eb3ca",
    2: "11424420c9c32cd335a059474b24b54a874dc41aba403d3c0165065528d83d0d",
}


def _write_pair(tmp_path: Path, blocks=None) -> dict[int, Path]:
    blocks = blocks or build_real_blocks(MS2_FIXTURE)
    paths = {}
    for version in (1, 2):
        path = tmp_path / f"precursor-v{version}.zp"
        ZpWriter().write(path, blocks, format_version=version)
        paths[version] = path
    return paths


def _mutate_pair(paths: dict[int, Path], scenario: str) -> None:
    rewrite_zp(paths[1], lambda payloads: _mutate_relationship(payloads, scenario))
    touched = (
        ("core_spectra", "core_precursors")
        if scenario in {"shared_precursor", "multiple_errors"}
        else ("core_spectra",)
        if scenario in {"spectrum_dangling", "ms1_has_precursor", "ms2_missing_precursor"}
        else ("core_precursors",)
    )
    for block_name in touched:
        mutate_json_block(
            paths[2],
            block_name,
            lambda records, block_name=block_name: _mutate_one_block(
                records,
                block_name,
                scenario,
                paths[2],
            ),
        )


def _mutate_one_block(records, block_name: str, scenario: str, path: Path) -> None:
    _header, _directory, raw_payloads = top_layout(path)
    payloads = {
        name: json.loads(payload.decode("utf-8"))
        for name, payload in raw_payloads.items()
        if name != "arrays"
    }
    payloads[block_name] = records
    _mutate_relationship(payloads, scenario)
    records[:] = payloads[block_name]


def _blocks(path: Path, version: int) -> dict[str, object]:
    if version == 1:
        _header, _directory, payloads = load_raw_zp(path)
        return payloads
    _header, _directory, raw_payloads = top_layout(path)
    return {
        name: json.loads(payload.decode("utf-8"))
        for name, payload in raw_payloads.items()
        if name != "arrays"
    }


def _logical_violations(path: Path, version: int) -> list[tuple[object, ...]]:
    blocks = _blocks(path, version)
    spectra = blocks["core_spectra"]
    precursors = blocks["core_precursors"]
    spectrum_by_id = {record["spectrum_id"]: record for record in spectra}
    precursor_by_id = {record["precursor_id"]: record for record in precursors}
    precursor_use: dict[str, int] = {}
    violations: list[tuple[object, ...]] = []
    for spectrum in spectra:
        spectrum_id = spectrum["spectrum_id"]
        precursor_id = spectrum["precursor_id"]
        if spectrum["ms_level"] == 1 and precursor_id is not None:
            violations.append(
                ("Spectrum->Precursor", spectrum_id, precursor_id, None, precursor_id)
            )
        if spectrum["ms_level"] == 2:
            precursor = precursor_by_id.get(precursor_id)
            if precursor is None:
                violations.append(
                    ("Spectrum->Precursor", spectrum_id, precursor_id, "existing", "missing")
                )
            elif precursor["spectrum_id"] != spectrum_id:
                violations.append(
                    (
                        "Spectrum->Precursor",
                        spectrum_id,
                        precursor_id,
                        spectrum_id,
                        precursor["spectrum_id"],
                    )
                )
            if isinstance(precursor_id, str):
                precursor_use[precursor_id] = precursor_use.get(precursor_id, 0) + 1
    for precursor in precursors:
        precursor_id = precursor["precursor_id"]
        spectrum_id = precursor["spectrum_id"]
        spectrum = spectrum_by_id.get(spectrum_id)
        actual = None if spectrum is None else (spectrum["ms_level"], spectrum["precursor_id"])
        if spectrum is None or spectrum["ms_level"] != 2 or spectrum["precursor_id"] != precursor_id:
            violations.append(
                (
                    "Precursor->Spectrum",
                    spectrum_id,
                    precursor_id,
                    (2, precursor_id),
                    actual,
                )
            )
        use_count = precursor_use.get(precursor_id, 0)
        if use_count != 1:
            violations.append(
                ("MS2 Spectrum->Precursor use", spectrum_id, precursor_id, 1, use_count)
            )
    return violations


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def test_committed_precursor_failure_fixtures_have_parity_without_byte_drift() -> None:
    results = {}
    for version in (1, 2):
        path = FAILURE_DIR / f"precursor_bidirectional_mismatch_v{version}.zp"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_FAILURE_SHA256[version]
        results[version] = ZpValidator().validate(path)

    assert results[1].valid is results[2].valid is False
    assert _codes(results[1]) == _codes(results[2]) == [
        "INVALID_REFERENCE",
        "INVALID_REFERENCE",
    ]
    assert results[1].checked_blocks == results[2].checked_blocks == 9
    assert _logical_violations(
        FAILURE_DIR / "precursor_bidirectional_mismatch_v1.zp", 1
    ) == _logical_violations(
        FAILURE_DIR / "precursor_bidirectional_mismatch_v2.zp", 2
    ) == [
        (
            "Spectrum->Precursor",
            "spectrum_000002",
            "precursor_000001",
            "spectrum_000002",
            "spectrum_000001",
        ),
        (
            "Precursor->Spectrum",
            "spectrum_000001",
            "precursor_000001",
            (2, "precursor_000001"),
            (1, None),
        ),
    ]


def test_legal_precursor_relationships_have_v1_v2_parity(tmp_path: Path) -> None:
    for blocks in (build_real_blocks(MS2_FIXTURE), _build_two_ms2_blocks()):
        paths = _write_pair(tmp_path, blocks)
        results = {version: ZpValidator().validate(path) for version, path in paths.items()}
        assert results[1].valid is results[2].valid is True
        assert results[1].issues == results[2].issues == []
        assert results[1].checked_blocks == results[2].checked_blocks == 9


@pytest.mark.parametrize(
    ("scenario", "multiple", "expected_count"),
    [
        ("spectrum_dangling", False, 3),
        ("precursor_dangling", False, 2),
        ("bidirectional_mismatch", False, 2),
        ("ms1_has_precursor", False, 1),
        ("ms2_missing_precursor", False, 3),
        ("shared_precursor", True, 2),
        ("two_precursors_one_spectrum", True, 2),
        ("multiple_errors", True, 3),
    ],
)
def test_invalid_precursor_relationships_have_v1_v2_domain_parity(
    scenario: str,
    multiple: bool,
    expected_count: int,
    tmp_path: Path,
) -> None:
    paths = _write_pair(tmp_path, _build_two_ms2_blocks() if multiple else None)
    _mutate_pair(paths, scenario)
    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert _logical_violations(paths[1], 1) == _logical_violations(paths[2], 2)
    assert len(_logical_violations(paths[1], 1)) == expected_count
    assert results[1].valid is results[2].valid is False
    assert _codes(results[1]) == _codes(results[2]) == ["INVALID_REFERENCE"] * expected_count
    assert results[1].checked_blocks == results[2].checked_blocks == 9


@pytest.mark.parametrize(
    ("block_name", "mutation"),
    [
        ("core_spectra", lambda records: records[1].pop("precursor_id")),
        ("core_precursors", lambda records: records[0].update(spectrum_id=7)),
    ],
)
def test_precursor_schema_errors_remain_schema_first_in_both_versions(
    block_name: str,
    mutation,
    tmp_path: Path,
) -> None:
    paths = _write_pair(tmp_path)
    rewrite_zp(paths[1], lambda payloads: mutation(payloads[block_name]))
    mutate_json_block(paths[2], block_name, mutation)
    results = {version: ZpValidator().validate(path) for version, path in paths.items()}

    assert results[1].valid is results[2].valid is False
    assert results[1].issues[0].code not in {"INVALID_REFERENCE"}
    assert results[2].issues[0].code not in {"INVALID_REFERENCE"}

