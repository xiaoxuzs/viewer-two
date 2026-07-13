from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy

from ..exceptions import BlockBoundaryViolationError
from ..models import PipelineContext


class BasePipelineStep(ABC):
    name: str
    category: str = "system"
    input_kinds: tuple[str, ...] = ()
    output_kinds: tuple[str, ...] = ()

    @abstractmethod
    def run(self, context: PipelineContext) -> None:
        raise NotImplementedError


class BaseBlockTool(BasePipelineStep, ABC):
    category = "block_tool"

    def run(self, context: PipelineContext) -> None:
        protected = {
            "source_profile": deepcopy(context.source_profile),
            "metadata": deepcopy(context.metadata),
            "artifacts": deepcopy(context.artifacts),
            "logs": deepcopy(context.logs),
        }
        try:
            self.build_blocks(context)
        except Exception as exc:
            changed = self._protected_changes(context, protected)
            if changed:
                self._restore_protected(context, protected)
                raise BlockBoundaryViolationError(
                    f"BlockTool {self.name} modified protected context fields: {', '.join(changed)}"
                ) from exc
            raise

        changed = self._protected_changes(context, protected)
        if changed:
            self._restore_protected(context, protected)
            raise BlockBoundaryViolationError(
                f"BlockTool {self.name} modified protected context fields: {', '.join(changed)}"
            )

    @staticmethod
    def _protected_changes(context: PipelineContext, protected: dict[str, object]) -> list[str]:
        return [name for name, before in protected.items() if getattr(context, name) != before]

    @staticmethod
    def _restore_protected(context: PipelineContext, protected: dict[str, object]) -> None:
        context.source_profile = protected["source_profile"]  # type: ignore[assignment]
        context.metadata = protected["metadata"]  # type: ignore[assignment]
        context.artifacts = protected["artifacts"]  # type: ignore[assignment]
        context.logs = protected["logs"]  # type: ignore[assignment]

    @abstractmethod
    def build_blocks(self, context: PipelineContext) -> None:
        raise NotImplementedError
