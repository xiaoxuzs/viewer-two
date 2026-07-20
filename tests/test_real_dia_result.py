from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from binary_layer import (
    BottomUpReader,
    BottomUpExtensionValidator,
    DiaResultBundleInspector,
    PipelineContext,
    SourceInspector,
    ZpReader,
    convert_source_to_zp,
    migrate_v1_to_v2,
    validate_zp,
)
from binary_layer.blocks import ISOLATION_WINDOW_KIND
from binary_layer.bottom_up_exceptions import DiaResultConversionError
from binary_layer.bottom_up_schema import DIANN_COLUMN_NAMES
from binary_layer.exceptions import ZpWriteError
from binary_layer.tools.common import (
    FileValidateStep,
    HashInputStep,
    IndexBuildTool,
    StringPoolBuildTool,
)
from binary_layer.tools.real_dia_result import RealDiaResultTool
from binary_layer.writer import ZpWriter
from scripts.run_dia_result_acceptance import (
    _load_checkpoint,
    compare_core_and_arrays,
    run as run_acceptance,
)

from bottom_up_support import build_dia_bundle


def test_frozen_diann_contract_has_69_unique_columns() -> None:
    assert len(DIANN_COLUMN_NAMES) == 69
    assert len(set(DIANN_COLUMN_NAMES)) == 69


def test_inspector_prefers_all_report_and_matches_one_run(tmp_path: Path) -> None:
    bundle = build_dia_bundle(tmp_path)
    profile = SourceInspector().inspect((bundle,))
    assert profile.source_type == "real_dia_result_bundle"
    assert profile.dia_result_bundle is not None
    assert profile.dia_result_bundle.report_role == "all_report"
    assert profile.dia_result_bundle.optional_report is not None
    assert profile.dia_result_bundle.report_columns == DIANN_COLUMN_NAMES


def test_inspector_falls_back_to_target_report(tmp_path: Path) -> None:
    bundle = build_dia_bundle(tmp_path, report_role="target_report")
    profile = SourceInspector().inspect((bundle,))
    assert profile.dia_result_bundle is not None
    assert profile.dia_result_bundle.report_role == "target_report"


def test_inspector_rejects_report_without_spectrum(tmp_path: Path) -> None:
    bundle = build_dia_bundle(tmp_path)
    (bundle / "spectra" / "run1.mzML").unlink()
    with pytest.raises(DiaResultConversionError) as caught:
        SourceInspector().inspect((bundle,))
    assert caught.value.code == "MISSING_SPECTRUM_SOURCE"


def test_inspector_rejects_missing_and_ambiguous_reports(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(DiaResultConversionError) as missing:
        DiaResultBundleInspector().inspect_bundle(empty)
    assert missing.value.code == "MISSING_DIANN_REPORT"

    bundle = build_dia_bundle(tmp_path / "ambiguous")
    duplicate = bundle / "duplicate"
    duplicate.mkdir()
    shutil.copyfile(
        bundle / "diann" / "all_report.parquet",
        duplicate / "all_report.parquet",
    )
    with pytest.raises(DiaResultConversionError) as ambiguous:
        SourceInspector().inspect((bundle,))
    assert ambiguous.value.code == "AMBIGUOUS_DIANN_REPORT"


def test_inspector_rejects_ambiguous_spectrum_and_run_mismatch(tmp_path: Path) -> None:
    ambiguous_bundle = build_dia_bundle(tmp_path / "ambiguous")
    duplicate = ambiguous_bundle / "duplicate"
    duplicate.mkdir()
    shutil.copyfile(
        ambiguous_bundle / "spectra" / "run1.mzML",
        duplicate / "run1.mzML",
    )
    with pytest.raises(DiaResultConversionError) as ambiguous:
        SourceInspector().inspect((ambiguous_bundle,))
    assert ambiguous.value.code == "AMBIGUOUS_SPECTRUM_SOURCE"

    mismatch_bundle = build_dia_bundle(tmp_path / "mismatch")
    _replace_column(mismatch_bundle, "Run", ["another-run"] * 3)
    with pytest.raises(DiaResultConversionError) as mismatch:
        SourceInspector().inspect((mismatch_bundle,))
    assert mismatch.value.code == "DIANN_RUN_NOT_MATCHED"


def test_inspector_rejects_multi_run_report(tmp_path: Path) -> None:
    bundle = build_dia_bundle(tmp_path)
    _replace_column(bundle, "Run", ["run1", "run2", "run1"])
    with pytest.raises(DiaResultConversionError) as caught:
        SourceInspector().inspect((bundle,))
    assert caught.value.code == "MULTI_RUN_BUNDLE_NOT_SUPPORTED"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (("drop", "RT", None), "DIANN_REQUIRED_COLUMN_MISSING"),
        (("string", "Precursor.Charge", ["2", "3", "2"]), "DIANN_COLUMN_TYPE_INVALID"),
        (("value", "Stripped.Sequence", ["", "ACDE", "CPEP"]), "DIANN_ROW_MALFORMED"),
        (("value", "Precursor.Charge", [0, 3, 2]), "DIANN_ROW_MALFORMED"),
        (("value", "Precursor.Mz", [float("nan"), 500.5, 700.0]), "DIANN_ROW_MALFORMED"),
        (("value", "Q.Value", [-0.1, 0.005, 0.005]), "DIANN_ROW_MALFORMED"),
        (("value", "RT", [None, 1.5, 2.5]), "DIANN_ROW_MALFORMED"),
        (("value", "Precursor.Mz", [900.0, 500.5, 700.0]), "IDENTIFICATION_SPECTRUM_NOT_FOUND"),
        (("value", "Precursor.Id", ["ACDE2", "ACDE2", "CPEP2"]), "IDENTIFICATION_ID_CONFLICT"),
        (("value", "Modified.Sequence", ["A(UniMod:4)CDE", "AC(UniMod:4)DE", "C(UniMod:4)PEP"]), "MODIFICATION_POSITION_INVALID"),
    ],
)
def test_malformed_report_fails_before_atomic_commit(
    tmp_path: Path,
    mutation: tuple[str, str, object],
    expected_code: str,
) -> None:
    bundle = build_dia_bundle(tmp_path)
    kind, column, values = mutation
    report = bundle / "diann" / "all_report.parquet"
    table = pq.read_table(report)
    if kind == "drop":
        table = table.drop([column])
    else:
        dtype = pa.string() if kind == "string" else table.schema.field(column).type
        table = table.set_column(
            table.schema.get_field_index(column),
            column,
            pa.array(values, type=dtype),
        )
    pq.write_table(table, report, row_group_size=2)
    target = tmp_path / "must-not-exist.zp"
    with pytest.raises(DiaResultConversionError) as caught:
        convert_source_to_zp(bundle, target, format_version=2)
    assert caught.value.code == expected_code
    assert not target.exists()
    assert not list(target.parent.glob(f".{target.name}.*.partial.zp*"))


def test_unknown_parquet_column_is_preserved_and_reported(tmp_path: Path) -> None:
    bundle = build_dia_bundle(tmp_path)
    report = bundle / "diann" / "all_report.parquet"
    table = pq.read_table(report).append_column(
        "Future.Score",
        pa.array([1.0, 2.0, 3.0], type=pa.float64()),
    )
    pq.write_table(table, report, row_group_size=2)
    target = tmp_path / "unknown-column.zp"
    convert_source_to_zp(bundle, target, format_version=2)
    reader = BottomUpReader(target)
    metadata = reader.get_metadata()
    assert metadata["field_coverage"]["unknown_columns"] == ["Future.Score"]
    first = reader._records("bottom_up_identifications")[0]
    assert "Future.Score" in first["source_fields"]


def test_unsafe_pfmb_pickle_is_never_deserialized(tmp_path: Path) -> None:
    bundle = build_dia_bundle(tmp_path)
    unsafe = bundle / "diann" / "run1.mzML.pos.pkl"
    unsafe.write_bytes(b"cos\nsystem\n(S'never execute'\ntR.")
    target = tmp_path / "unsafe-pickle-preserved.zp"
    convert_source_to_zp(bundle, target, format_version=2)
    metadata = BottomUpReader(target).get_metadata()
    assert metadata["fragment_support"] == {
        "status": "not_available",
        "reason": "fragment_source_not_loaded",
        "unsafe_pickle_deserialization_used": False,
    }
    assert metadata["optional_roles"]["pfmb_pickle"] == "present"
    assert any(
        item["source_file"].endswith(".pos.pkl")
        and item["processing_status"] == "unsafe_preserved_not_loaded"
        for item in metadata["source_files"]
    )


@pytest.mark.parametrize(
    ("extension_type", "mutate", "expected_code"),
    [
        (
            "bottom_up_identifications",
            lambda records: records[0].__setitem__("spectrum_id", "missing-spectrum"),
            "IDENTIFICATION_SPECTRUM_NOT_FOUND",
        ),
        (
            "bottom_up_identifications",
            lambda records: records[0].__setitem__("peptide_id", "missing-peptide"),
            "BOTTOM_UP_REFERENCE_MISSING",
        ),
        (
            "bottom_up_identifications",
            lambda records: records[0].__setitem__("protein_group_id", "missing-group"),
            "BOTTOM_UP_REFERENCE_MISSING",
        ),
        (
            "bottom_up_modifications",
            lambda records: records[0].__setitem__("position", 0),
            "MODIFICATION_POSITION_INVALID",
        ),
    ],
)
def test_bottom_up_validator_rejects_dangling_or_invalid_entities(
    tmp_path: Path,
    extension_type: str,
    mutate: object,
    expected_code: str,
) -> None:
    context = _build_fixture_blocks(tmp_path)
    extension = next(
        item for item in context.blocks.extensions if item.extension_type == extension_type
    )
    mutate(extension.payload["records"])  # type: ignore[operator]
    target = tmp_path / f"invalid-{extension_type}.zp"
    ZpWriter().write(
        target,
        context.blocks,
        format_version=2,
        created_at_millis=context.source_profile.output_created_at_millis,
    )
    result = BottomUpExtensionValidator().validate(target)
    assert result.valid is False
    assert expected_code in {item.code for item in result.issues}


def test_bottom_up_validator_rejects_count_mismatch(tmp_path: Path) -> None:
    context = _build_fixture_blocks(tmp_path)
    extension = next(
        item
        for item in context.blocks.extensions
        if item.extension_type == "bottom_up_identifications"
    )
    extension.payload["record_count"] += 1
    target = tmp_path / "count-mismatch.zp"
    ZpWriter().write(
        target,
        context.blocks,
        format_version=2,
        created_at_millis=context.source_profile.output_created_at_millis,
    )
    result = BottomUpExtensionValidator().validate(target)
    assert result.valid is False
    assert "BOTTOM_UP_COUNT_MISMATCH" in {item.code for item in result.issues}


def test_nonserializable_source_fields_fail_writer_front_gate(tmp_path: Path) -> None:
    context = _build_fixture_blocks(tmp_path)
    extension = next(
        item
        for item in context.blocks.extensions
        if item.extension_type == "bottom_up_identifications"
    )
    extension.payload["records"][0]["source_fields"]["bad"] = object()
    target = tmp_path / "not-serializable.zp"
    with pytest.raises(ZpWriteError):
        ZpWriter().write(
            target,
            context.blocks,
            format_version=2,
            created_at_millis=context.source_profile.output_created_at_millis,
        )
    assert not target.exists()
    assert not target.with_name(target.name + ".tmp").exists()


@pytest.mark.parametrize("format_version", [1, 2])
def test_service_reader_validator_and_dia_core_contract(
    tmp_path: Path,
    format_version: int,
) -> None:
    bundle = build_dia_bundle(tmp_path)
    output = tmp_path / f"result-v{format_version}.zp"
    result = convert_source_to_zp(bundle, output, format_version=format_version)
    assert result.validation.valid is True
    assert result.validation.checked_blocks == 9
    assert result.validation.bottom_up_valid is True
    reader = ZpReader(output)
    spectra = reader.read_spectra()
    precursors = reader.read_precursors()
    assert len(spectra) == 3
    assert len(precursors) == 2
    assert all(item.effective_precursor_kind == ISOLATION_WINDOW_KIND for item in precursors)
    assert all(item.charge is None and item.precursor_mz is None for item in precursors)
    assert {(item.isolation_lower_mz, item.isolation_upper_mz) for item in precursors} == {
        (499.3, 501.3),
        (699.3, 701.3),
    }

    bottom_up = BottomUpReader(output)
    summary = bottom_up.get_bottom_up_summary()
    assert summary["identification"] == 3
    assert summary["peptide"] == 2
    assert summary["protein_group"] == 2
    assert summary["distinct_ms2_count"] == 2
    ids = bottom_up._records("bottom_up_identifications")
    matches_by_spectrum = {
        item["spectrum_id"]: bottom_up.get_bottom_up_identifications_for_spectrum(
            item["spectrum_id"]
        )
        for item in ids
    }
    assert sorted(len(matches) for matches in matches_by_spectrum.values()) == [1, 2]
    assert bottom_up.get_bottom_up_fragment_matches(ids[0]["identification_id"]) == []
    assert bottom_up.get_bottom_up_quantification_summary()["record_count"] >= 3
    assert validate_zp(output).valid is True


def test_fixture_output_is_byte_deterministic_from_source_run_time(tmp_path: Path) -> None:
    bundle = build_dia_bundle(tmp_path)
    first = tmp_path / "first.zp"
    second = tmp_path / "second.zp"
    convert_source_to_zp(bundle, first, format_version=2)
    convert_source_to_zp(bundle, second, format_version=2)
    assert first.read_bytes() == second.read_bytes()


def test_independent_array_acceptance_includes_chromatograms(tmp_path: Path) -> None:
    bundle = build_dia_bundle(tmp_path)
    spectrum_source = bundle / "spectra" / "run1.mzML"
    tree = ET.parse(spectrum_source)
    run = tree.getroot().find("{http://psi.hupo.org/ms/mzml}run")
    reference = ET.parse(
        Path(__file__).parent
        / "fixtures"
        / "mzml"
        / "accept_tic_bpc_chromatograms.mzML"
    )
    reference_run = reference.getroot().find("{http://psi.hupo.org/ms/mzml}run")
    assert run is not None and reference_run is not None
    chromatograms = reference_run.find(
        "{http://psi.hupo.org/ms/mzml}chromatogramList"
    )
    assert chromatograms is not None
    run.append(deepcopy(chromatograms))
    tree.write(spectrum_source, encoding="utf-8", xml_declaration=True)

    target = tmp_path / "with-chromatograms.zp"
    convert_source_to_zp(bundle, target, format_version=2)
    inspected = SourceInspector().inspect((bundle,)).dia_result_bundle
    assert inspected is not None
    result = compare_core_and_arrays(inspected, target)
    assert result["chromatogram_count"] == 2
    assert result["array_count"] == 10
    assert result["all_array_hashes_equal"] is True


def test_real_acceptance_checkpoint_skips_completed_deep_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = build_dia_bundle(tmp_path)
    target = tmp_path / "checkpointed.zp"
    checkpoint = tmp_path / "checkpoint.json"
    certificate = tmp_path / "certificate.json"
    convert_source_to_zp(bundle, target, format_version=2)

    first = run_acceptance(
        bundle,
        target,
        checkpoint_path=checkpoint,
        certificate_path=certificate,
    )
    assert all(first["checkpoint"]["stages"].values())

    monkeypatch.setattr(
        "scripts.run_dia_result_acceptance.ZpValidator.validate",
        lambda *_args, **_kwargs: pytest.fail("completed physical validation repeated"),
    )
    second = run_acceptance(
        bundle,
        target,
        checkpoint_path=checkpoint,
        certificate_path=certificate,
    )
    assert second["zp"]["sha256"] == first["zp"]["sha256"]
    invalidated = _load_checkpoint(checkpoint, "0" * 64)
    assert not any(invalidated["stages"].values())


def test_fixture_v1_migration_matches_direct_v2_byte_for_byte(tmp_path: Path) -> None:
    bundle = build_dia_bundle(tmp_path)
    v1 = tmp_path / "source-v1.zp"
    migrated = tmp_path / "migrated-v2.zp"
    direct = tmp_path / "direct-v2.zp"
    convert_source_to_zp(bundle, v1, format_version=1)
    migrate_v1_to_v2(v1, migrated)
    convert_source_to_zp(bundle, direct, format_version=2)
    assert migrated.read_bytes() == direct.read_bytes()
    assert BottomUpReader(migrated).get_bottom_up_summary() == BottomUpReader(
        direct
    ).get_bottom_up_summary()


def _replace_column(bundle: Path, column: str, values: list[object]) -> None:
    report = bundle / "diann" / "all_report.parquet"
    table = pq.read_table(report)
    table = table.set_column(
        table.schema.get_field_index(column),
        column,
        pa.array(values, type=table.schema.field(column).type),
    )
    pq.write_table(table, report, row_group_size=2)


def _build_fixture_blocks(tmp_path: Path) -> PipelineContext:
    bundle = build_dia_bundle(tmp_path)
    profile = SourceInspector().inspect((bundle,))
    context = PipelineContext(profile)
    FileValidateStep().run(context)
    HashInputStep().run(context)
    RealDiaResultTool().run(context)
    StringPoolBuildTool().run(context)
    IndexBuildTool().run(context)
    return context
