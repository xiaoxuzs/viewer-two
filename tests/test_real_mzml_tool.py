from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from binary_layer.blocks import ExtensionBlock
from binary_layer.inspector import SourceInspector
from binary_layer.models import PipelineContext
from binary_layer.mzml_schema import MzmlMetadataV1
from binary_layer.tools.base import BaseBlockTool
from binary_layer.tools.real_mzml import RealMzmlParseTool

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


def test_real_tool_builds_complete_ms1_candidate_and_only_commits_blocks() -> None:
    path = FIXTURE_DIR / "accept_ms1_only_indexed_float64_zlib.mzML"
    profile = SourceInspector().inspect([path])
    context = PipelineContext(
        profile,
        metadata={"input_sha256": "b" * 64},
        artifacts={"existing": "artifact"},
    )
    before_profile = deepcopy(context.source_profile)
    before_metadata = deepcopy(context.metadata)
    before_artifacts = deepcopy(context.artifacts)
    before_logs = deepcopy(context.logs)
    tool = RealMzmlParseTool()

    tool.run(context)

    assert isinstance(tool, BaseBlockTool)
    assert tool.name == "real_mzml_parse"
    assert tool.category == "block_tool"
    assert context.source_profile == before_profile
    assert context.metadata == before_metadata
    assert context.artifacts == before_artifacts
    assert context.logs == before_logs
    assert context.blocks.global_meta is not None
    assert context.blocks.global_meta.source_type == "real_mzml"
    assert context.blocks.global_meta.source_file_hash == "b" * 64
    assert context.blocks.global_meta.run_count == 1
    assert context.blocks.global_meta.spectrum_count == 2
    assert context.blocks.global_meta.chromatogram_count == 0
    assert context.blocks.global_meta.array_count == 4
    assert len(context.blocks.runs) == 1
    assert context.blocks.runs[0].run_id == "run1"
    assert context.blocks.runs[0].start_rt == 30.0
    assert context.blocks.runs[0].end_rt == 90.0
    assert [item.spectrum_id for item in context.blocks.spectra] == ["spectrum_000001", "spectrum_000002"]
    assert [item.scan_number for item in context.blocks.spectra] == [1, 2]
    assert all(item.ms_level == 1 and item.precursor_id is None for item in context.blocks.spectra)
    assert context.blocks.precursors == []
    assert context.blocks.chromatograms == []
    assert len(context.blocks.arrays) == 4
    assert len({item.array_id for item in context.blocks.arrays}) == 4
    assert all(item.dtype == "float64" for item in context.blocks.arrays)
    assert context.blocks.arrays[0].values == [100.0, 200.0]
    assert context.blocks.arrays[1].values == [10.0, 20.0]
    assert all(type(value) is float for array in context.blocks.arrays for value in array.values)
    assert context.blocks.string_pool is None
    assert context.blocks.indexes is None
    assert len(context.blocks.extensions) == 1
    extension = context.blocks.extensions[0]
    assert isinstance(extension, ExtensionBlock)
    assert extension.extension_type == "mzml_metadata"
    assert extension.extension_version == "1"
    metadata = MzmlMetadataV1.from_payload(extension.payload)
    assert [item.spectrum_id for item in metadata.spectra] == ["spectrum_000001", "spectrum_000002"]
    assert metadata.spectra[0].source_rt_value == 0.5
    assert metadata.spectra[0].source_rt_unit_name == "minute"
    assert metadata.spectra[0].source_mz_dtype.value == "float64"
    assert metadata.spectra[0].source_mz_compression.value == "zlib"


def test_float32_source_is_widened_without_changing_provenance() -> None:
    path = FIXTURE_DIR / "accept_ms1_only_nonindexed_float32_uncompressed.mzML"
    context = PipelineContext(SourceInspector().inspect([path]), metadata={"input_sha256": "c" * 64})

    RealMzmlParseTool().run(context)

    assert [item.rt for item in context.blocks.spectra] == [0.5, 1.5]
    assert all(item.dtype == "float64" for item in context.blocks.arrays)
    metadata = MzmlMetadataV1.from_payload(context.blocks.extensions[0].payload)
    assert all(item.source_mz_dtype.value == "float32" for item in metadata.spectra)
    assert all(item.source_mz_compression.value == "none" for item in metadata.spectra)

