from __future__ import annotations

from .exceptions import DuplicateStepError, StepNotFoundError
from .tools.base import BasePipelineStep


class StepRegistry:
    def __init__(self) -> None:
        self._steps: dict[str, BasePipelineStep] = {}

    def register(self, step: BasePipelineStep) -> None:
        if step.name in self._steps:
            raise DuplicateStepError(f"Step already registered: {step.name}")
        self._steps[step.name] = step

    def get(self, name: str) -> BasePipelineStep:
        try:
            return self._steps[name]
        except KeyError as exc:
            raise StepNotFoundError(f"Step is not registered: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._steps)


def build_default_registry() -> StepRegistry:
    from .tools.common import FileValidateStep, HashInputStep, IndexBuildTool, StringPoolBuildTool, ZpValidateStep, ZpWriteStep
    from .tools.mzml_mock import MockMzmlParseTool
    from .tools.raw_mock import MockRawToMzmlTool
    from .tools.real_mzml import RealMzmlParseTool

    registry = StepRegistry()
    for step in (
        FileValidateStep(),
        HashInputStep(),
        MockRawToMzmlTool(),
        MockMzmlParseTool(),
        RealMzmlParseTool(),
        StringPoolBuildTool(),
        IndexBuildTool(),
        ZpWriteStep(),
        ZpValidateStep(),
    ):
        registry.register(step)
    return registry
