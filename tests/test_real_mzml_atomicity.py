from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from binary_layer.blocks import BlockCollection, StringPoolBlock
from binary_layer import PipelineRunner, PlanBuilder, build_default_registry
from binary_layer.exceptions import MzmlAdmissionError, MzmlParseError, StepExecutionError
from binary_layer.models import PipelineContext
from binary_layer.inspector import SourceInspector
from binary_layer.tools.real_mzml import RealMzmlParseTool

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


def context_for(name: str, *, blocks: BlockCollection | None = None, with_hash: bool = True) -> PipelineContext:
    path = FIXTURE_DIR / name
    metadata = {"input_sha256": "a" * 64} if with_hash else {}
    return PipelineContext(SourceInspector().inspect([path]), metadata=metadata, blocks=blocks or BlockCollection())


@pytest.mark.parametrize(
    ("fixture_name", "expected_code"),
    [
        ("reject_missing_scan_number.mzML", "MISSING_SCAN_NUMBER"),
        ("reject_unknown_rt_unit.mzML", "UNSUPPORTED_RT_UNIT"),
        ("reject_missing_precursor.mzML", "MISSING_PRECURSOR"),
        ("reject_missing_selected_ion.mzML", "MISSING_SELECTED_ION"),
        ("reject_missing_selected_ion_mz.mzML", "MISSING_SELECTED_ION_MZ"),
        ("reject_zero_charge.mzML", "MISSING_PRECURSOR_CHARGE"),
        ("reject_missing_chromatogram_time_array.mzML", "MISSING_CHROMATOGRAM_ARRAY"),
        ("reject_missing_chromatogram_intensity_array.mzML", "MISSING_CHROMATOGRAM_ARRAY"),
        ("reject_chromatogram_array_length_mismatch.mzML", "CHROMATOGRAM_ARRAY_LENGTH_MISMATCH"),
        ("reject_unknown_chromatogram_time_unit.mzML", "UNSUPPORTED_RT_UNIT"),
        ("reject_srm_chromatogram.mzML", "UNSUPPORTED_CHROMATOGRAM_TYPE"),
        ("reject_chromatogram_precursor_semantics.mzML", "UNSUPPORTED_CHROMATOGRAM_SEMANTICS"),
        ("reject_chromatogram_product_semantics.mzML", "UNSUPPORTED_CHROMATOGRAM_SEMANTICS"),
        ("reject_unknown_chromatogram_auxiliary_array.mzML", "UNSUPPORTED_AUXILIARY_ARRAY"),
    ],
)
def test_admission_failure_uses_existing_issue_code_and_is_atomic(fixture_name: str, expected_code: str) -> None:
    context = context_for(fixture_name)
    before = deepcopy(context.blocks)

    with pytest.raises(MzmlAdmissionError, match=expected_code):
        RealMzmlParseTool().run(context)

    assert context.blocks == before
    assert context.blocks.global_meta is None
    assert context.blocks.arrays == []
    assert context.blocks.extensions == []
    assert "output_zp_path" not in context.artifacts


@pytest.mark.parametrize(
    ("fixture_name", "expected_code"),
    [
        ("reject_negative_isolation_offset.mzML", "NEGATIVE_ISOLATION_OFFSET"),
        ("reject_nonfinite_precursor_value.mzML", "NONFINITE_PRECURSOR_VALUE"),
    ],
)
def test_precursor_parse_failure_is_atomic(fixture_name: str, expected_code: str) -> None:
    context = context_for(fixture_name)
    before = deepcopy(context.blocks)

    with pytest.raises(MzmlParseError) as captured:
        RealMzmlParseTool().run(context)

    assert captured.value.code == expected_code
    assert context.blocks == before
    assert context.blocks.global_meta is None
    assert context.blocks.precursors == []
    assert context.blocks.extensions == []
    assert "output_zp_path" not in context.artifacts


@pytest.mark.parametrize(
    "fixture_name",
    [
        "reject_missing_precursor.mzML",
        "reject_multiple_precursors.mzML",
        "reject_missing_selected_ion.mzML",
        "reject_multiple_selected_ions.mzML",
        "reject_missing_selected_ion_mz.mzML",
        "reject_missing_charge.mzML",
        "reject_zero_charge.mzML",
        "reject_missing_selected_ion_intensity.mzML",
        "reject_negative_isolation_offset.mzML",
        "reject_nonfinite_precursor_value.mzML",
        "reject_missing_chromatogram_time_array.mzML",
        "reject_missing_chromatogram_intensity_array.mzML",
        "reject_chromatogram_array_length_mismatch.mzML",
        "reject_unknown_chromatogram_time_unit.mzML",
        "reject_srm_chromatogram.mzML",
        "reject_chromatogram_precursor_semantics.mzML",
        "reject_chromatogram_product_semantics.mzML",
        "reject_unknown_chromatogram_auxiliary_array.mzML",
    ],
)
def test_every_structural_rejection_stops_pipeline_before_writer(fixture_name: str, tmp_path: Path) -> None:
    source = FIXTURE_DIR / fixture_name
    profile = SourceInspector().inspect([source])
    output = tmp_path / "must-not-exist.zp"
    context = PipelineContext(profile, metadata={"output_path": output})

    with pytest.raises(StepExecutionError):
        PipelineRunner().run(PlanBuilder().build(profile), build_default_registry(), context)

    assert context.blocks == BlockCollection()
    assert "output_zp_path" not in context.artifacts
    assert output.exists() is False
    assert not any(item.step_name in {"string_pool_build", "index_build", "zp_write", "zp_validate"} for item in context.logs)


def test_nonempty_block_collection_is_rejected_without_overwrite() -> None:
    existing = BlockCollection(string_pool=StringPoolBlock(["existing business block"]))
    context = context_for("accept_ms1_only_indexed_float64_zlib.mzML", blocks=existing)

    with pytest.raises(MzmlParseError) as captured:
        RealMzmlParseTool().run(context)

    assert captured.value.code == "BLOCK_COLLECTION_NOT_EMPTY"
    assert context.blocks is existing
    assert context.blocks == BlockCollection(string_pool=StringPoolBlock(["existing business block"]))


def test_missing_input_hash_is_explicit_and_atomic() -> None:
    context = context_for("accept_ms1_only_indexed_float64_zlib.mzML", with_hash=False)
    before = deepcopy(context.blocks)

    with pytest.raises(MzmlParseError) as captured:
        RealMzmlParseTool().run(context)

    assert captured.value.code == "MISSING_INPUT_SHA256"
    assert context.blocks == before
