from datetime import datetime, timezone

from ..blocks import ArrayBlock, BlockCollection, GlobalMetaBlock, PrecursorBlock, RunBlock, SpectrumBlock
from ..constants import ZP_VERSION
from ..exceptions import BlockValidationError
from ..models import PipelineContext
from .base import BaseBlockTool


class MockMzmlParseTool(BaseBlockTool):
    name = "mock_mzml_parse"
    input_kinds = ("validated_source", "input_sha256")
    output_kinds = ("core_blocks", "arrays")

    def build_blocks(self, context: PipelineContext) -> None:
        digest = context.metadata.get("input_sha256")
        if not isinstance(digest, str):
            raise BlockValidationError("hash_input must run before mock_mzml_parse")

        source = context.source_profile.input_files[0]
        spectra = [
            SpectrumBlock("spectrum_1", "run_1", 1, 1, "controllerType=0 controllerNumber=1 scan=1", 0.5, None, "mz_1", "intensity_1"),
            SpectrumBlock("spectrum_2", "run_1", 2, 2, "controllerType=0 controllerNumber=1 scan=2", 1.0, "precursor_2", "mz_2", "intensity_2"),
            SpectrumBlock("spectrum_3", "run_1", 2, 3, "controllerType=0 controllerNumber=1 scan=3", 1.5, "precursor_3", "mz_3", "intensity_3"),
        ]
        arrays = [
            ArrayBlock("mz_1", "mz", "float64", [100.0, 200.0, 300.0]),
            ArrayBlock("intensity_1", "intensity", "float64", [10.0, 40.0, 20.0]),
            ArrayBlock("mz_2", "mz", "float64", [110.0, 210.0, 310.0]),
            ArrayBlock("intensity_2", "intensity", "float64", [15.0, 55.0, 25.0]),
            ArrayBlock("mz_3", "mz", "float64", [120.0, 220.0, 320.0]),
            ArrayBlock("intensity_3", "intensity", "float64", [12.0, 48.0, 22.0]),
        ]
        context.blocks = BlockCollection(
            global_meta=GlobalMetaBlock(
                format_version=ZP_VERSION,
                source_type=context.source_profile.source_type,
                source_file_name=source.name,
                source_file_hash=digest,
                run_count=1,
                spectrum_count=len(spectra),
                chromatogram_count=0,
                array_count=len(arrays),
                created_at=datetime.now(timezone.utc),
                generator_name="zp-binary-layer",
                generator_version="0.1.0",
                notes=["Mock data; source content was not parsed."],
            ),
            runs=[RunBlock("run_1", str(source), source.stem, 3, 0, 0.5, 1.5)],
            spectra=spectra,
            precursors=[
                PrecursorBlock("precursor_2", "spectrum_2", 500.2, 2, 1000.0),
                PrecursorBlock("precursor_3", "spectrum_3", 600.3, 3, 800.0),
            ],
            arrays=arrays,
        )
