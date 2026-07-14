from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import PipelineContext, PipelineRunner, PlanBuilder, SourceInspector, ZpReader, build_default_registry
from binary_layer.mzml_adapter import parse_mzml
from binary_layer.mzml_schema import MzmlAuxiliaryArraysV1, MzmlMetadataV1
from binary_layer.tools.real_mzml import RealMzmlParseTool
from binary_layer.validator import ZpValidator

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


def test_adapter_extracts_tic_bpc_and_auxiliary_values_once_as_plain_python() -> None:
    document = parse_mzml(FIXTURE_DIR / "accept_tic_bpc_chromatograms.mzML")

    assert [item.chromatogram_type for item in document.chromatograms] == ["tic", "bpc"]
    assert [item.native_id for item in document.chromatograms] == ["TIC", "BPC"]
    tic = document.chromatograms[0]
    assert tic.source_time_values == (0.0, 1.0)
    assert tic.time_values_seconds == (0.0, 1.0)
    assert tic.intensity_values == (100.0, 120.0)
    assert (tic.source_time_unit_accession, tic.source_time_unit_name) == ("UO:0000010", "second")
    assert (tic.source_time_dtype, tic.source_intensity_dtype) == ("float64", "float64")
    auxiliary = tic.auxiliary_arrays[0]
    assert (auxiliary.accession, auxiliary.name, auxiliary.dtype, auxiliary.values) == (
        "MS:1000786", "ms level", "int64", (1, 2)
    )
    assert all(type(value) in {int, float} for item in document.chromatograms for value in (*item.source_time_values, *item.intensity_values))
    assert document.metadata_schema is not None
    assert [item.chromatogram_id for item in document.metadata_schema.chromatograms] == [
        "chromatogram_000001", "chromatogram_000002"
    ]


@pytest.mark.parametrize(
    ("fixture_name", "expected_type", "expected_time", "source_dtype", "source_compression"),
    [
        ("accept_indexed_tic_minutes_float64_zlib.mzML", "tic", [0.0, 60.0], "float64", "zlib"),
        ("accept_nonindexed_bpc_seconds_float32_uncompressed.mzML", "bpc", [0.0, 1.0], "float32", "none"),
    ],
)
def test_real_tool_maps_one_chromatogram_and_normalizes_arrays(
    fixture_name: str,
    expected_type: str,
    expected_time: list[float],
    source_dtype: str,
    source_compression: str,
) -> None:
    path = FIXTURE_DIR / fixture_name
    context = PipelineContext(SourceInspector().inspect([path]), metadata={"input_sha256": "c" * 64})

    RealMzmlParseTool().run(context)

    assert len(context.blocks.chromatograms) == 1
    chromatogram = context.blocks.chromatograms[0]
    assert (chromatogram.chromatogram_id, chromatogram.chromatogram_type) == ("chromatogram_000001", expected_type)
    arrays = {item.array_id: item for item in context.blocks.arrays}
    assert arrays[chromatogram.time_array_id].values == expected_time
    assert arrays[chromatogram.intensity_array_id].values == [100.0, 120.0]
    assert arrays[chromatogram.time_array_id].dtype == arrays[chromatogram.intensity_array_id].dtype == "float64"
    metadata = MzmlMetadataV1.from_payload(context.blocks.extensions[0].payload)
    assert metadata.chromatograms[0].source_time_dtype.value == source_dtype
    assert metadata.chromatograms[0].source_time_compression.value == source_compression
    assert len(context.blocks.spectra) == 2
    assert len(context.blocks.precursors) == 1
    assert len(context.blocks.arrays) == 6


def test_tic_bpc_auxiliary_extension_uses_stable_core_owner_id() -> None:
    path = FIXTURE_DIR / "accept_tic_bpc_chromatograms.mzML"
    context = PipelineContext(SourceInspector().inspect([path]), metadata={"input_sha256": "e" * 64})

    RealMzmlParseTool().run(context)

    assert [item.chromatogram_type for item in context.blocks.chromatograms] == ["tic", "bpc"]
    assert [item.chromatogram_id for item in context.blocks.chromatograms] == ["chromatogram_000001", "chromatogram_000002"]
    assert len(context.blocks.arrays) == 6
    assert [item.extension_type for item in context.blocks.extensions] == ["mzml_metadata", "mzml_auxiliary_arrays"]
    auxiliary = MzmlAuxiliaryArraysV1.from_payload(context.blocks.extensions[1].payload)
    assert len(auxiliary.arrays) == 1
    assert auxiliary.arrays[0].owner_id == "chromatogram_000001"
    assert auxiliary.arrays[0].values == (1, 2)


@pytest.mark.parametrize(
    ("fixture_name", "expected_types", "expected_arrays", "expected_extensions"),
    [
        ("accept_indexed_tic_minutes_float64_zlib.mzML", ["tic"], 6, 1),
        ("accept_nonindexed_bpc_seconds_float32_uncompressed.mzML", ["bpc"], 6, 1),
        ("accept_tic_bpc_chromatograms.mzML", ["tic", "bpc"], 6, 2),
    ],
)
def test_chromatogram_pipeline_writer_reader_validator_roundtrip(
    fixture_name: str,
    expected_types: list[str],
    expected_arrays: int,
    expected_extensions: int,
    tmp_path: Path,
) -> None:
    source = FIXTURE_DIR / fixture_name
    profile = SourceInspector().inspect([source])
    output = tmp_path / f"{source.stem}.zp"
    context = PipelineContext(profile, metadata={"output_path": output})

    PipelineRunner().run(PlanBuilder().build(profile), build_default_registry(), context)

    assert output.exists()
    assert context.artifacts["validation_result"].valid is True
    reader = ZpReader(output)
    chromatograms = reader.read_chromatograms()
    arrays = {item.array_id: item for item in reader.read_arrays()}
    assert [item.chromatogram_type for item in chromatograms] == expected_types
    assert len(arrays) == expected_arrays
    assert len(reader.read_extensions()) == expected_extensions
    for item in chromatograms:
        assert arrays[item.time_array_id].array_type == "time"
        assert arrays[item.intensity_array_id].array_type == "intensity"
        assert len(arrays[item.time_array_id].values) == len(arrays[item.intensity_array_id].values) == 2
    assert ZpValidator().validate(output).valid is True


def test_chromatogram_ids_are_deterministic() -> None:
    source = FIXTURE_DIR / "accept_tic_bpc_chromatograms.mzML"
    contexts = [
        PipelineContext(SourceInspector().inspect([source]), metadata={"input_sha256": "f" * 64})
        for _ in range(2)
    ]
    for context in contexts:
        RealMzmlParseTool().run(context)
    assert contexts[0].blocks.chromatograms == contexts[1].blocks.chromatograms
    assert contexts[0].blocks.arrays == contexts[1].blocks.arrays
