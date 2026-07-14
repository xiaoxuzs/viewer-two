from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from binary_layer import PipelineContext, PipelineRunner, PlanBuilder, SourceInspector, build_default_registry
from binary_layer.blocks import StringPoolBlock
from binary_layer.exceptions import BlockBoundaryViolationError, MzmlParseError, StepExecutionError
from binary_layer.models import ConversionPlan, PipelineLogEntry
from binary_layer.registry import StepRegistry
from binary_layer.tools.base import BaseBlockTool, BasePipelineStep
from conftest import mock_mzml_profile


@pytest.mark.parametrize("suffix", [".mzML", ".RAW"])
def test_complete_mock_pipelines(pipeline_factory, suffix: str) -> None:
    context = pipeline_factory(suffix)
    assert context.metadata["file_validated"] is True
    assert len(context.blocks.spectra) == 3
    assert len(context.blocks.arrays) == 6
    assert Path(context.artifacts["output_zp_path"]).exists()
    assert context.artifacts["validation_result"].valid is True
    if suffix.lower() == ".raw":
        assert context.metadata["raw_converted_to_mock_mzml"] is True
    else:
        assert context.source_profile.source_type == "mock_mzml"
    statuses = [entry.status for entry in context.logs]
    assert statuses == [status for _ in range(len(statuses) // 2) for status in ("started", "completed")]


def test_block_tools_do_not_create_zp(tmp_path: Path) -> None:
    source = tmp_path / "source.mzML"
    source.write_bytes(b"mock")
    profile = mock_mzml_profile(source)
    context = PipelineContext(profile, metadata={"output_dir": tmp_path})
    registry = build_default_registry()
    for name in ("file_validate", "hash_input", "mock_mzml_parse", "string_pool_build", "index_build"):
        registry.get(name).run(context)
        assert "output_zp_path" not in context.artifacts
        assert list(tmp_path.glob("*.zp")) == []
    registry.get("zp_write").run(context)
    assert Path(context.artifacts["output_zp_path"]).exists()


def test_invalid_real_mzml_entry_fails_closed_without_mock_fallback(tmp_path: Path) -> None:
    source = tmp_path / "real.mzML"
    source.write_bytes(b"minimal bytes are not parsed during B2")
    profile = SourceInspector().inspect([source])
    plan = PlanBuilder().build(profile)
    context = PipelineContext(profile, metadata={"output_dir": tmp_path})

    with pytest.raises(StepExecutionError) as captured:
        PipelineRunner().run(plan, build_default_registry(), context)

    assert profile.source_type == "real_mzml"
    assert "real_mzml_parse" in plan.required_steps
    assert "mock_mzml_parse" not in plan.required_steps
    assert isinstance(captured.value.__cause__, MzmlParseError)
    assert captured.value.__cause__.code == "MZML_READ_FAILED"
    assert context.metadata["file_validated"] is True
    assert isinstance(context.metadata["input_sha256"], str)
    log_pairs = [(item.step_name, item.status) for item in context.logs]
    assert log_pairs == [
        ("file_validate", "started"), ("file_validate", "completed"),
        ("hash_input", "started"), ("hash_input", "completed"),
        ("real_mzml_parse", "started"), ("real_mzml_parse", "failed"),
    ]
    assert all(item.step_name != "mock_mzml_parse" for item in context.logs)
    assert all(item.step_name not in {"zp_write", "zp_validate"} for item in context.logs)
    assert context.blocks.global_meta is None
    assert context.blocks.spectra == []
    assert context.blocks.arrays == []
    assert "output_zp_path" not in context.artifacts
    assert list(tmp_path.glob("*.zp")) == []


class FailingStep(BasePipelineStep):
    name = "fail"

    def run(self, context: PipelineContext) -> None:
        raise RuntimeError("original failure")


class MarkerStep(BasePipelineStep):
    name = "marker"

    def run(self, context: PipelineContext) -> None:
        context.metadata["marker_ran"] = True


def test_runner_stops_and_preserves_exception_chain() -> None:
    profile = SourceInspector().inspect(["unused.mzML"])
    context = PipelineContext(profile)
    plan = ConversionPlan("id", "unused", ("fail", "marker"), ".zp")
    registry = StepRegistry()
    registry.register(FailingStep())
    registry.register(MarkerStep())
    with pytest.raises(StepExecutionError) as captured:
        PipelineRunner().run(plan, registry, context)
    assert isinstance(captured.value.__cause__, RuntimeError)
    assert context.logs[-1].status == "failed"
    assert "marker_ran" not in context.metadata


class ProtectedFieldMutatingBlockTool(BaseBlockTool):
    name = "protected_field_mutator"

    def __init__(self, field: str) -> None:
        self.field = field

    def build_blocks(self, context: PipelineContext) -> None:
        context.blocks.string_pool = StringPoolBlock(["allowed block change"])
        if self.field == "metadata":
            context.metadata["hidden_block_data"] = [1, 2, 3]
        elif self.field == "artifacts":
            context.artifacts["output_zp_path"] = Path("forbidden.zp")
        elif self.field == "source_profile":
            context.source_profile = SourceInspector().inspect(["other.mzML"])
        elif self.field == "logs":
            now = datetime.now(timezone.utc)
            context.logs.append(PipelineLogEntry(self.name, "completed", now, now, "forbidden"))


@pytest.mark.parametrize("field", ["metadata", "artifacts", "source_profile", "logs"])
def test_block_tool_rejects_and_restores_protected_context_fields(field: str) -> None:
    profile = SourceInspector().inspect(["source.mzML"])
    original_log_time = datetime.now(timezone.utc)
    context = PipelineContext(
        profile,
        metadata={"input_sha256": "a" * 64},
        artifacts={"existing": "artifact"},
        logs=[PipelineLogEntry("existing", "completed", original_log_time, original_log_time)],
    )
    with pytest.raises(BlockBoundaryViolationError, match=field):
        ProtectedFieldMutatingBlockTool(field).run(context)
    assert context.source_profile == profile
    assert context.metadata == {"input_sha256": "a" * 64}
    assert context.artifacts == {"existing": "artifact"}
    assert len(context.logs) == 1
    assert context.blocks.string_pool == StringPoolBlock(["allowed block change"])


class FailingBoundaryBlockTool(BaseBlockTool):
    name = "failing_boundary_tool"

    def build_blocks(self, context: PipelineContext) -> None:
        context.metadata["forbidden"] = True
        raise RuntimeError("tool failed after crossing boundary")


def test_block_tool_restores_boundary_when_tool_also_fails() -> None:
    context = PipelineContext(SourceInspector().inspect(["source.mzML"]), metadata={"original": True})
    with pytest.raises(BlockBoundaryViolationError) as captured:
        FailingBoundaryBlockTool().run(context)
    assert isinstance(captured.value.__cause__, RuntimeError)
    assert context.metadata == {"original": True}
