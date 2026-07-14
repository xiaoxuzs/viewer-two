from __future__ import annotations

import json
from pathlib import Path

import pytest

from binary_layer.exceptions import MzmlParseError
from binary_layer.mzml_adapter import parse_mzml
from binary_layer.mzml_admission import evaluate_mzml_admission
from binary_layer.mzml_schema import MzmlMetadataV1

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


def test_indexed_float64_zlib_ms1_document_is_parsed_once_into_plain_values() -> None:
    document = parse_mzml(FIXTURE_DIR / "accept_ms1_only_indexed_float64_zlib.mzML")

    assert document.feature_profile.indexed is True
    assert document.run.run_id == "run1"
    assert len(document.spectra) == 2
    first = document.spectra[0]
    assert first.source_index == 0
    assert first.scan_number == 1
    assert first.scan_number_proven is True
    assert first.rt_seconds == 30.0
    assert first.source_rt_value == 0.5
    assert first.source_rt_unit_accession == "UO:0000031"
    assert first.source_rt_unit_name == "minute"
    assert first.source_mz_dtype == "float64"
    assert first.source_intensity_dtype == "float64"
    assert first.source_mz_compression == "zlib"
    assert first.source_intensity_compression == "zlib"
    assert first.mz_values == (100.0, 200.0)
    assert first.intensity_values == (10.0, 20.0)
    assert type(first.mz_values) is tuple
    assert all(type(value) is float for value in (*first.mz_values, *first.intensity_values))
    assert not hasattr(first, "keys")
    assert evaluate_mzml_admission(document.feature_profile).accepted is True
    assert isinstance(document.metadata_schema, MzmlMetadataV1)
    assert MzmlMetadataV1.from_payload(document.metadata_schema.to_payload()) == document.metadata_schema


def test_nonindexed_float32_uncompressed_ms1_document_normalizes_rt_only() -> None:
    document = parse_mzml(FIXTURE_DIR / "accept_ms1_only_nonindexed_float32_uncompressed.mzML")

    assert document.feature_profile.indexed is False
    first = document.spectra[0]
    assert first.rt_seconds == 0.5
    assert first.source_rt_value == 0.5
    assert first.source_rt_unit_accession == "UO:0000010"
    assert first.source_rt_unit_name == "second"
    assert first.source_mz_dtype == "float32"
    assert first.source_intensity_dtype == "float32"
    assert first.source_mz_compression == "none"
    assert first.source_intensity_compression == "none"
    assert first.mz_values == (100.0, 200.0)


def test_adapter_result_is_deterministic_and_profile_uses_parsed_spectrum_facts() -> None:
    path = FIXTURE_DIR / "accept_ms1_only_indexed_float64_zlib.mzML"
    first = parse_mzml(path)
    second = parse_mzml(path)

    assert first == second
    assert tuple(item.native_id for item in first.feature_profile.spectra) == tuple(
        item.native_id for item in first.spectra
    )
    assert tuple(item.scan_number for item in first.feature_profile.spectra) == tuple(
        item.scan_number for item in first.spectra
    )
    assert tuple(item.mz_array_length for item in first.feature_profile.spectra) == tuple(
        len(item.mz_values) for item in first.spectra
    )


def test_unproven_scan_and_unknown_rt_remain_admission_facts() -> None:
    missing_scan = parse_mzml(FIXTURE_DIR / "reject_missing_scan_number.mzML")
    unknown_rt = parse_mzml(FIXTURE_DIR / "reject_unknown_rt_unit.mzML")

    assert missing_scan.spectra[0].scan_number is None
    assert missing_scan.spectra[0].scan_number_proven is False
    assert {issue.code for issue in evaluate_mzml_admission(missing_scan.feature_profile).issues} == {
        "MISSING_SCAN_NUMBER"
    }
    assert unknown_rt.spectra[0].rt_seconds is None
    assert {issue.code for issue in evaluate_mzml_admission(unknown_rt.feature_profile).issues} == {
        "UNSUPPORTED_RT_UNIT"
    }


def test_adapter_preserves_every_frozen_fixture_admission_decision() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))

    for entry in manifest["fixtures"]:
        if entry["expected_adapter_error"] is not None:
            with pytest.raises(MzmlParseError) as captured:
                parse_mzml(FIXTURE_DIR / entry["fixture_name"])
            assert captured.value.code == entry["expected_adapter_error"]
            continue
        document = parse_mzml(FIXTURE_DIR / entry["fixture_name"])
        result = evaluate_mzml_admission(document.feature_profile)
        assert result.accepted is entry["expected_admission"]
        assert {issue.code for issue in result.issues} == set(entry["expected_issue_codes"])
