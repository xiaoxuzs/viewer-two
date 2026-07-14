import pytest

from binary_layer.exceptions import DuplicateStepError, StepNotFoundError
from binary_layer.registry import StepRegistry, build_default_registry
from binary_layer.models import PipelineContext
from binary_layer.tools.base import BasePipelineStep
from binary_layer.tools.common import FileValidateStep
from binary_layer.tools.real_mzml import RealMzmlParseTool


class FutureRealStep(BasePipelineStep):
    name = "real_mzml_parse"

    def run(self, context: PipelineContext) -> None:
        context.metadata["test_only_real_step_ran"] = True


def test_register_and_get() -> None:
    registry = StepRegistry()
    step = FileValidateStep()
    registry.register(step)
    assert registry.get("file_validate") is step


def test_duplicate_and_missing_fail() -> None:
    registry = StepRegistry()
    registry.register(FileValidateStep())
    with pytest.raises(DuplicateStepError):
        registry.register(FileValidateStep())
    with pytest.raises(StepNotFoundError):
        registry.get("missing")


def test_default_registry_has_all_steps() -> None:
    registry = build_default_registry()
    assert set(registry.names()) == {
        "file_validate", "hash_input", "mock_raw_to_mzml", "mock_mzml_parse",
        "real_mzml_parse", "string_pool_build", "index_build", "zp_write", "zp_validate",
    }
    assert type(registry.get("real_mzml_parse")) is RealMzmlParseTool


def test_registry_can_register_future_step_by_name_without_source_context() -> None:
    registry = StepRegistry()
    step = FutureRealStep()
    registry.register(step)
    assert registry.get("real_mzml_parse") is step
