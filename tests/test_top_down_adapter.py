from __future__ import annotations

from pathlib import Path
import json

import pytest

from binary_layer.conversion_exceptions import TopDownConversionError
from binary_layer.inspector import SourceInspector
from binary_layer.top_down_adapter import TopDownAdapter
from top_down_support import build_top_down_bundle, write_prsm


def test_inspector_discovers_content_based_single_run_bundle(tmp_path: Path) -> None:
    bundle_path = build_top_down_bundle(tmp_path / "bundle")

    profile = SourceInspector().inspect([bundle_path])

    assert profile.source_type == "real_top_down_bundle"
    assert profile.run_count == 1
    assert profile.spectrum_source_type == "mzml"
    assert profile.missing_required_roles == ()
    assert profile.ambiguous_roles == ()
    assert set(profile.detected_roles) >= {
        "spectrum_source",
        "prsm_result",
        "proteoform_result",
        "fragment_match_result",
    }


def test_adapter_preserves_unknown_columns_and_detail_fields(tmp_path: Path) -> None:
    bundle = TopDownAdapter().inspect_bundle(build_top_down_bundle(tmp_path / "bundle"))

    document = TopDownAdapter().load(bundle)

    assert document.prsms[0].source_fields["prsm_detail"]["value"]["viewer_unused_field"] == "preserved-detail-value"
    assert document.source_tables[0].rows[0]["Unknown column"] == "preserved-value"
    assert document.source_tables[1].rows[0]["Unexpected proteoform column"] == "also-preserved"
    assert document.modifications[0].source_fields["prsm_detail"]["columns"]["unknown_modification_field"] == "retained"
    assert document.fragment_matches[0].source_fields["prsm_detail"]["columns"]["unknown_ion_field"] == "retained"


def test_adapter_treats_viewer_multivalue_numeric_as_null_and_keeps_raw(tmp_path: Path) -> None:
    root = build_top_down_bundle(tmp_path / "bundle")
    write_prsm(
        root / "data" / "prsms" / "prsm1.js",
        prsm_id=1,
        spectrum_file_name="run.mzML",
        scan_number=2,
        precursor_charge="12:6:12",
    )

    prsm = TopDownAdapter().load(TopDownAdapter().inspect_bundle(root)).prsms[0]

    assert prsm.charge is None
    assert prsm.source_fields["prsm_detail"]["value"]["ms"]["ms_header"]["precursor_charge"] == "12:6:12"


def test_bundle_rejects_missing_spectrum_source(tmp_path: Path) -> None:
    root = build_top_down_bundle(tmp_path / "bundle")
    (root / "run.mzML").unlink()

    with pytest.raises(TopDownConversionError) as captured:
        TopDownAdapter().inspect_bundle(root)

    assert captured.value.code == "TOP_DOWN_SPECTRUM_SOURCE_MISSING"


def test_bundle_rejects_multiple_run_references(tmp_path: Path) -> None:
    root = build_top_down_bundle(tmp_path / "bundle")
    write_prsm(
        root / "data" / "prsms" / "prsm2.js",
        prsm_id=2,
        spectrum_file_name="other.mzML",
        scan_number=2,
    )

    with pytest.raises(TopDownConversionError) as captured:
        TopDownAdapter().inspect_bundle(root)

    assert captured.value.code == "TOP_DOWN_MULTIPLE_RUNS_NOT_SUPPORTED"


def test_bundle_rejects_duplicate_prsm_file_ids(tmp_path: Path) -> None:
    root = build_top_down_bundle(tmp_path / "bundle")
    source = root / "data" / "prsms" / "prsm1.js"
    (source.parent / "prsm01.js").write_bytes(source.read_bytes())

    with pytest.raises(TopDownConversionError) as captured:
        TopDownAdapter().inspect_bundle(root)

    assert captured.value.code == "TOP_DOWN_DUPLICATE_PRSM_ID"


def test_explicit_manifest_resolves_roles_relative_to_manifest(tmp_path: Path) -> None:
    root = build_top_down_bundle(tmp_path / "bundle")
    manifest = root / "top-down-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_name": "top_down_bundle_manifest",
                "schema_version": 1,
                "run_name": "run",
                "roles": {
                    "spectrum_source": "run.mzML",
                    "prsm_result": "data/prsms",
                    "fragment_match_result": "data/prsms",
                    "proteoform_result": "run_ms2_toppic_proteoform.tsv",
                    "prsm_summary_result": "run_ms2_toppic_prsm.tsv",
                },
            }
        ),
        encoding="utf-8",
    )

    profile = SourceInspector().inspect([manifest])

    assert profile.source_type == "real_top_down_bundle"
    assert profile.top_down_bundle is not None
    assert profile.top_down_bundle.manifest_path == manifest
