from ..models import PipelineContext
from .base import BasePipelineStep


class MockRawToMzmlTool(BasePipelineStep):
    name = "mock_raw_to_mzml"
    category = "pre_conversion"
    input_kinds = ("mock_raw",)
    output_kinds = ("mock_mzml_state",)

    def run(self, context: PipelineContext) -> None:
        context.metadata["raw_converted_to_mock_mzml"] = True
        context.metadata["pre_conversion_note"] = "RAW conversion is simulated; no mzML file was created."

