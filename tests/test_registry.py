import pytest

from binary_layer.exceptions import DuplicateStepError, StepNotFoundError
from binary_layer.registry import StepRegistry, build_default_registry
from binary_layer.tools.common import FileValidateStep


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
    assert set(build_default_registry().names()) == {
        "file_validate", "hash_input", "mock_raw_to_mzml", "mock_mzml_parse",
        "string_pool_build", "index_build", "zp_write", "zp_validate",
    }

