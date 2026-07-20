from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from conftest import rewrite_zp
from zp_v2_writer_support import build_real_blocks


FAILURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "failures" / "candidate_parity"
EXPECTED_FAILURE_SHA256 = "7e92ab9a34c578a791ca6bbebe5546f8fcc228b778535e8d77bf9cd9795eb3ca"
MS1_FIXTURE = "accept_ms1_only_indexed_float64_zlib.mzML"
MS2_FIXTURE = "accept_ms2_precursor_metadata.mzML"


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def _write_v1(tmp_path: Path, blocks=None, name: str = "precursor-v1.zp") -> Path:
    path = tmp_path / name
    ZpWriter().write(path, blocks or build_real_blocks(MS2_FIXTURE), format_version=1)
    return path


def _build_two_ms2_blocks():
    blocks = build_real_blocks(MS2_FIXTURE)
    source_spectrum = blocks.spectra[1]
    source_precursor = blocks.precursors[0]
    added_spectrum = deepcopy(source_spectrum)
    added_spectrum.spectrum_id = "spectrum_000003"
    added_spectrum.scan_number = 3
    added_spectrum.native_id = "controllerType=0 controllerNumber=1 scan=3"
    added_spectrum.rt += 1.0
    added_spectrum.precursor_id = "precursor_000002"
    added_spectrum.mz_array_id = "spectrum_000003:mz"
    added_spectrum.intensity_array_id = "spectrum_000003:intensity"

    added_precursor = deepcopy(source_precursor)
    added_precursor.precursor_id = "precursor_000002"
    added_precursor.spectrum_id = added_spectrum.spectrum_id

    arrays_by_id = {item.array_id: item for item in blocks.arrays}
    added_mz = deepcopy(arrays_by_id[source_spectrum.mz_array_id])
    added_mz.array_id = added_spectrum.mz_array_id
    added_intensity = deepcopy(arrays_by_id[source_spectrum.intensity_array_id])
    added_intensity.array_id = added_spectrum.intensity_array_id

    blocks.spectra.append(added_spectrum)
    blocks.precursors.append(added_precursor)
    blocks.arrays.extend((added_mz, added_intensity))
    blocks.string_pool.strings.append(added_spectrum.native_id)
    blocks.runs[0].spectrum_count += 1
    blocks.global_meta.spectrum_count += 1
    blocks.global_meta.array_count += 2
    blocks.indexes.scan_index.append(
        {"scan_number": added_spectrum.scan_number, "spectrum_id": added_spectrum.spectrum_id}
    )
    blocks.indexes.rt_index.append(
        {"rt": added_spectrum.rt, "spectrum_id": added_spectrum.spectrum_id}
    )
    blocks.indexes.spectrum_id_index.append(
        {"position": 2, "spectrum_id": added_spectrum.spectrum_id}
    )
    return blocks


def _mutate_relationship(payloads, scenario: str) -> None:
    spectra = payloads["core_spectra"]
    precursors = payloads["core_precursors"]
    if scenario == "spectrum_dangling":
        spectra[1]["precursor_id"] = "missing_precursor"
    elif scenario == "precursor_dangling":
        precursors[0]["spectrum_id"] = "missing_spectrum"
    elif scenario == "bidirectional_mismatch":
        precursors[0]["spectrum_id"] = spectra[0]["spectrum_id"]
    elif scenario == "ms1_has_precursor":
        spectra[0]["precursor_id"] = precursors[0]["precursor_id"]
    elif scenario == "ms2_missing_precursor":
        spectra[1]["precursor_id"] = None
    elif scenario == "shared_precursor":
        spectra[2]["precursor_id"] = precursors[0]["precursor_id"]
        del precursors[1]
    elif scenario == "two_precursors_one_spectrum":
        precursors[1]["spectrum_id"] = spectra[1]["spectrum_id"]
    elif scenario == "multiple_errors":
        spectra[0]["precursor_id"] = precursors[0]["precursor_id"]
        precursors[1]["spectrum_id"] = spectra[1]["spectrum_id"]
    else:
        raise AssertionError(scenario)


def test_committed_precursor_failure_fixture_rejects_without_byte_drift() -> None:
    path = FAILURE_DIR / "precursor_bidirectional_mismatch_v1.zp"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_FAILURE_SHA256

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert result.checked_blocks == 9
    assert _codes(result) == ["INVALID_REFERENCE", "INVALID_REFERENCE"]
    assert [issue.block_name for issue in result.issues] == [
        "core_spectra[1].precursor_id",
        "core_precursors[0].spectrum_id",
    ]
    for issue in result.issues:
        for expected in (
            "spectrum_000001",
            "spectrum_000002",
            "precursor_000001",
            "expected",
            "actual",
        ):
            assert expected in issue.message


@pytest.mark.parametrize("fixture", [MS1_FIXTURE, MS2_FIXTURE])
def test_v1_accepts_legal_ms1_only_and_ms1_ms2_relationships(
    fixture: str,
    tmp_path: Path,
) -> None:
    result = ZpValidator().validate(_write_v1(tmp_path, build_real_blocks(fixture)))

    assert result.valid is True
    assert result.issues == []
    assert result.checked_blocks == 9


def test_v1_accepts_multiple_one_to_one_ms2_precursor_relationships(tmp_path: Path) -> None:
    result = ZpValidator().validate(_write_v1(tmp_path, _build_two_ms2_blocks()))

    assert result.valid is True
    assert result.issues == []
    assert result.checked_blocks == 9


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
def test_v1_rejects_precursor_relationship_corruption_in_stable_direction_order(
    scenario: str,
    multiple: bool,
    expected_count: int,
    tmp_path: Path,
) -> None:
    blocks = _build_two_ms2_blocks() if multiple else build_real_blocks(MS2_FIXTURE)
    path = _write_v1(tmp_path, blocks, f"{scenario}.zp")
    rewrite_zp(path, lambda payloads: _mutate_relationship(payloads, scenario))

    first = ZpValidator().validate(path)
    second = ZpValidator().validate(path)

    assert first.valid is second.valid is False
    assert _codes(first) == _codes(second) == ["INVALID_REFERENCE"] * expected_count
    assert [issue.block_name for issue in first.issues] == [
        issue.block_name for issue in second.issues
    ]
    assert first.checked_blocks == second.checked_blocks == 9
    assert all("expected" in issue.message and "actual" in issue.message for issue in first.issues)


def test_v1_precursor_duplicate_id_remains_a_schema_identity_issue(tmp_path: Path) -> None:
    path = _write_v1(tmp_path)

    def duplicate(payloads) -> None:
        payloads["core_precursors"].append(deepcopy(payloads["core_precursors"][0]))

    rewrite_zp(path, duplicate)
    result = ZpValidator().validate(path)

    assert _codes(result) == ["DUPLICATE_ID"]


@pytest.mark.parametrize(
    ("block_name", "mutation", "expected_code"),
    [
        ("core_spectra", lambda records: records[1].pop("precursor_id"), "MISSING_FIELD"),
        (
            "core_precursors",
            lambda records: records[0].update(spectrum_id=7),
            "INVALID_FIELD_TYPE",
        ),
    ],
)
def test_v1_precursor_schema_errors_remain_schema_first(
    block_name: str,
    mutation,
    expected_code: str,
    tmp_path: Path,
) -> None:
    path = _write_v1(tmp_path)
    rewrite_zp(path, lambda payloads: mutation(payloads[block_name]))

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert _codes(result)[0] == expected_code


def test_precursor_relationship_indexes_traverse_each_record_class_once() -> None:
    class CountingList(list):
        def __init__(self, values) -> None:
            super().__init__(values)
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    count = 10_000
    spectra = CountingList(
        {
            "spectrum_id": f"spectrum_{position:06d}",
            "ms_level": 2,
            "precursor_id": f"precursor_{position:06d}",
        }
        for position in range(count)
    )
    precursors = CountingList(
        {
            "precursor_id": f"precursor_{position:06d}",
            "spectrum_id": f"spectrum_{position:06d}",
        }
        for position in range(count)
    )

    state = ZpValidator._build_precursor_relationship_state(spectra, precursors)

    assert state is not None
    spectrum_by_id, precursor_by_id, precursor_use, precursor_first_user = state
    assert spectra.iterations == precursors.iterations == 1
    assert len(spectrum_by_id) == len(precursor_by_id) == len(precursor_use) == count
    assert len(precursor_first_user) == count
    assert all(value == 1 for value in precursor_use.values())
