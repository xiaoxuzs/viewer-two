from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from zp_compatibility_support import (
    mutate_pair,
    resize_array,
    validation_summary,
    write_pair,
)


def _global(field: str):
    return lambda blocks: blocks["global_meta"].update({field: blocks["global_meta"][field] + 1})


def _run(field: str):
    return lambda blocks: blocks["core_runs"][0].update({field: blocks["core_runs"][0][field] + 1})


def _remove_pool_value(value: str):
    return lambda blocks: blocks["string_pool"]["strings"].remove(value)


def _missing_extension_owner(blocks) -> None:
    blocks["extensions"][1]["payload"]["arrays"][0]["owner_id"] = "missing_chromatogram"


def _wrong_extension_owner_type(blocks) -> None:
    record = blocks["extensions"][1]["payload"]["arrays"][0]
    record["owner_kind"] = "spectrum"
    record["owner_id"] = "spectrum_000001"


def _missing_spectrum_mz(blocks) -> None:
    blocks["core_spectra"][0]["mz_array_id"] = "missing:mz"


def _wrong_spectrum_array_type(blocks) -> None:
    blocks["core_spectra"][0]["mz_array_id"] = "spectrum_000001:intensity"


def _missing_chromatogram_time(blocks) -> None:
    blocks["core_chromatograms"][0]["time_array_id"] = "missing:time"


def _missing_index_record(blocks) -> None:
    blocks["indexes"]["scan_index"][0]["spectrum_id"] = "missing_spectrum"


def _spectrum_dangling_precursor(blocks) -> None:
    blocks["core_spectra"][1]["precursor_id"] = "missing_precursor"


def _precursor_dangling_spectrum(blocks) -> None:
    blocks["core_precursors"][0]["spectrum_id"] = "missing_spectrum"


def _precursor_bidirectional(blocks) -> None:
    blocks["core_precursors"][0]["spectrum_id"] = "spectrum_000001"


def _ms1_has_precursor(blocks) -> None:
    blocks["core_spectra"][0]["precursor_id"] = "precursor_000001"


def _ms2_missing_precursor(blocks) -> None:
    blocks["core_spectra"][1]["precursor_id"] = None


def _shared_precursor(blocks) -> None:
    blocks["core_spectra"][0]["ms_level"] = 2
    blocks["core_spectra"][0]["precursor_id"] = "precursor_000001"


def _two_precursors_one_spectrum(blocks) -> None:
    added = deepcopy(blocks["core_precursors"][0])
    added["precursor_id"] = "precursor_000002"
    blocks["core_precursors"].append(added)


def _logical_issue_targets(result: dict[str, object]) -> list[str | None]:
    locations = result["locations"]
    assert isinstance(locations, list)
    return [
        None if location is None else str(location).split("[", 1)[0].split(".", 1)[0]
        for location in locations
    ]


DOMAIN_CASES = (
    ("global_run_count", _global("run_count"), "COUNT_MISMATCH"),
    ("global_spectrum_count", _global("spectrum_count"), "COUNT_MISMATCH"),
    ("global_chromatogram_count", _global("chromatogram_count"), "COUNT_MISMATCH"),
    ("global_array_count", _global("array_count"), "COUNT_MISMATCH"),
    ("run_spectrum_count", _run("spectrum_count"), "COUNT_MISMATCH"),
    ("run_chromatogram_count", _run("chromatogram_count"), "COUNT_MISMATCH"),
    ("string_pool_required_reference", _remove_pool_value("full-run"), "INVALID_REFERENCE"),
    ("extension_owner_missing", _missing_extension_owner, "INVALID_REFERENCE"),
    ("extension_owner_type", _wrong_extension_owner_type, "INVALID_EXTENSION_SCHEMA"),
    ("spectrum_missing_mz", _missing_spectrum_mz, "INVALID_REFERENCE"),
    ("spectrum_wrong_array_type", _wrong_spectrum_array_type, "ARRAY_TYPE_MISMATCH"),
    ("chromatogram_missing_time", _missing_chromatogram_time, "INVALID_REFERENCE"),
    ("index_missing_record", _missing_index_record, "INVALID_REFERENCE"),
    ("spectrum_dangling_precursor", _spectrum_dangling_precursor, "INVALID_REFERENCE"),
    ("precursor_dangling_spectrum", _precursor_dangling_spectrum, "INVALID_REFERENCE"),
    ("precursor_bidirectional", _precursor_bidirectional, "INVALID_REFERENCE"),
    ("ms1_has_precursor", _ms1_has_precursor, "INVALID_REFERENCE"),
    ("ms2_missing_precursor", _ms2_missing_precursor, "INVALID_REFERENCE"),
    ("shared_precursor", _shared_precursor, "INVALID_REFERENCE"),
    ("two_precursors_one_spectrum", _two_precursors_one_spectrum, "INVALID_REFERENCE"),
)


@pytest.mark.parametrize(("_name", "mutation", "expected_primary"), DOMAIN_CASES, ids=[item[0] for item in DOMAIN_CASES])
def test_json_domain_error_code_sequences_match_between_versions(
    _name: str,
    mutation,
    expected_primary: str,
    tmp_path: Path,
) -> None:
    paths = write_pair(tmp_path)
    mutate_pair(paths, mutation)
    results = {version: validation_summary(path) for version, path in paths.items()}

    assert results[1]["valid"] is results[2]["valid"] is False
    assert results[1]["codes"] == results[2]["codes"]
    assert _logical_issue_targets(results[1]) == _logical_issue_targets(results[2])
    assert results[1]["codes"][0] == expected_primary
    assert results[1]["checked_blocks"] == results[2]["checked_blocks"] == 9


@pytest.mark.parametrize(
    ("name", "array_id"),
    [
        ("spectrum_array_length", "spectrum_000001:intensity"),
        ("chromatogram_array_length", "chromatogram_000001:intensity"),
    ],
)
def test_array_length_domain_error_code_sequences_match_between_versions(
    name: str,
    array_id: str,
    tmp_path: Path,
) -> None:
    paths = write_pair(tmp_path)
    for path in paths.values():
        resize_array(path, array_id, 3)
    results = {version: validation_summary(path) for version, path in paths.items()}

    assert name
    assert results[1]["valid"] is results[2]["valid"] is False
    assert results[1]["codes"] == results[2]["codes"] == ["ARRAY_LENGTH_MISMATCH"]
    assert _logical_issue_targets(results[1]) == _logical_issue_targets(results[2])
    assert results[1]["checked_blocks"] == results[2]["checked_blocks"] == 9
