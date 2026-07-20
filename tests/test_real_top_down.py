from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from binary_layer import (
    ConversionOptions,
    TopDownReader,
    convert_source_to_zp,
    get_fragment_matches,
    get_proteoform,
    get_prsm,
    get_prsms_for_spectrum,
    get_top_down_summary,
    get_top_down_interpretation_provenance,
    open_zp,
    validate_zp,
)
from binary_layer.constants import DEFAULT_ZP_WRITE_VERSION
from binary_layer.conversion_exceptions import SourceConversionError, TopDownConversionError
from binary_layer.top_down_schema import TOP_DOWN_EXTENSION_TYPES
from binary_layer.top_down_interpretation_schema import (
    TOP_DOWN_INTERPRETATION_EXTENSION_TYPE,
)
from binary_layer.thermo_raw_schema import THERMO_RAW_CONVERSION_EXTENSION_TYPE
from top_down_support import MZML_FIXTURE, build_top_down_bundle, write_prsm


def test_mzml_bundle_converts_via_unified_service_and_high_level_reader(tmp_path: Path) -> None:
    source = build_top_down_bundle(tmp_path / "中文 数据包")
    target = tmp_path / "output" / "top-down.zp"

    result = convert_source_to_zp(source, target, format_version=2)

    assert result.plan.source_type == "real_top_down_bundle"
    assert "real_top_down" in result.plan.required_steps
    assert result.validation.valid is True
    assert result.validation.checked_blocks == 9
    assert result.validation.issues == []
    assert result.validation.top_down_valid is True
    assert result.validation.top_down_issues == []
    assert result.source_before == result.source_after
    assert open_zp(target).read_header().version == 2
    summary = get_top_down_summary(target)
    assert summary == {
        "schema_name": "top_down_summary",
        "schema_version": 1,
        "run_name": "run",
        "spectrum_source_type": "mzml",
        "proteoform_count": 1,
        "prsm_count": 1,
        "modification_count": 1,
        "fragment_match_count": 1,
        "feature_count": 1,
        "peak_count": 1,
        "associated_spectrum_count": 1,
        "unique_protein_count": 1,
        "modified_proteoform_count": 1,
        "prsm_with_fragment_match_count": 1,
    }
    prsm = get_prsm(target, "1")
    assert prsm["spectrum_id"] == "spectrum_000002"
    assert get_proteoform(target, "1")["best_prsm_id"] == "1"
    assert get_prsms_for_spectrum(target, prsm["spectrum_id"])[0]["prsm_id"] == "1"
    assert get_fragment_matches(target, "1")[0]["ion_type"] == "B"
    assert TopDownReader(target).get_metadata()["source_tables"][0]["rows"][0]["Unknown column"] == "preserved-value"
    provenance = get_top_down_interpretation_provenance(target)
    assert provenance is not None
    assert provenance["interpretation_origin"] == "precomputed_prsm_js"


def test_top_down_default_format_remains_v1(tmp_path: Path) -> None:
    source = build_top_down_bundle(tmp_path / "bundle")
    target = tmp_path / "default.zp"

    result = convert_source_to_zp(source, target)

    assert DEFAULT_ZP_WRITE_VERSION == 1
    assert result.format_version == 1
    assert open_zp(target).read_header().version == 1


def test_extension_records_have_stable_order_and_identity(tmp_path: Path) -> None:
    source = build_top_down_bundle(tmp_path / "bundle")
    write_prsm(
        source / "data" / "prsms" / "prsm2.js",
        prsm_id=2,
        spectrum_file_name="run.mzML",
        scan_number=2,
    )
    # The fixture intentionally shares a Proteoform only when IDs match the PrSM.
    target = tmp_path / "ordered.zp"

    convert_source_to_zp(source, target, format_version=2)
    extensions = {
        item.extension_type: item for item in open_zp(target).read_extensions()
        if item.extension_type.startswith("top_down_")
    }

    assert tuple(extensions) == (
        *TOP_DOWN_EXTENSION_TYPES,
        TOP_DOWN_INTERPRETATION_EXTENSION_TYPE,
    )
    for extension_type, extension in extensions.items():
        assert extension.extension_version == "1"
        assert extension.payload["owner"] == "top_down"
        assert extension.payload["schema_name"] == extension_type
        assert extension.payload["schema_version"] == 1
    assert [item["prsm_id"] for item in extensions["top_down_prsms"].payload["records"]] == ["1", "2"]


def test_missing_prsm_spectrum_reference_fails_without_pseudo_success_file(tmp_path: Path) -> None:
    source = build_top_down_bundle(tmp_path / "bundle", scan_number=999)
    target = tmp_path / "failed.zp"

    with pytest.raises(TopDownConversionError) as captured:
        convert_source_to_zp(source, target, format_version=2)

    assert captured.value.code == "TOP_DOWN_SPECTRUM_REFERENCE_NOT_FOUND"
    assert not target.exists()
    assert not list(tmp_path.glob(".*.partial.zp"))


def test_existing_target_is_not_overwritten(tmp_path: Path) -> None:
    source = build_top_down_bundle(tmp_path / "bundle")
    target = tmp_path / "existing.zp"
    target.write_bytes(b"existing")

    with pytest.raises(SourceConversionError) as captured:
        convert_source_to_zp(source, target, format_version=2)

    assert captured.value.code == "TARGET_ALREADY_EXISTS"
    assert target.read_bytes() == b"existing"


def test_raw_bundle_reuses_existing_thermo_and_mzml_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = build_top_down_bundle(tmp_path / "bundle", spectrum_suffix=".raw")
    converter = tmp_path / "ThermoRawFileParser.exe"
    converter.write_bytes(b"fake")

    def run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="1.4.5\n", stderr="")
        output = Path(next(item[3:] for item in command if item.startswith("-b=")))
        shutil.copyfile(
            MZML_FIXTURE.parent / "accept_indexed_float64_zlib.mzML",
            output,
        )
        return SimpleNamespace(returncode=0, stdout="converted", stderr="")

    monkeypatch.setattr("binary_layer.thermo_raw_adapter.subprocess.run", run)
    target = tmp_path / "raw-top-down.zp"

    result = convert_source_to_zp(
        source,
        target,
        format_version=2,
        options=ConversionOptions(
            converter_path=converter,
            temporary_directory=tmp_path / "temporary",
            timeout_seconds=10,
        ),
    )

    extension_types = {item.extension_type for item in open_zp(target).read_extensions()}
    assert THERMO_RAW_CONVERSION_EXTENSION_TYPE in extension_types
    assert set(TOP_DOWN_EXTENSION_TYPES) <= extension_types
    assert result.converter_name == "ThermoRawFileParser"
    assert result.cleanup_result == "removed"
    assert validate_zp(target, mode="deep").top_down_valid is True
