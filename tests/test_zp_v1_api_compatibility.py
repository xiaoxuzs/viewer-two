from inspect import Parameter, signature
from pathlib import Path

from binary_layer import (
    PipelineContext,
    PipelineRunner,
    PlanBuilder,
    SourceInspector,
    ZpReader,
    ZpValidator,
    ZpWriter,
    build_default_registry,
)
from binary_layer.constants import ZP_VERSION_V1


def test_writer_version_is_an_optional_keyword_only_api() -> None:
    parameter = signature(ZpWriter.write).parameters["format_version"]
    assert parameter.kind is Parameter.KEYWORD_ONLY
    assert parameter.default == ZP_VERSION_V1


def test_existing_mock_pipeline_call_path_still_defaults_to_v1(pipeline_factory) -> None:
    context = pipeline_factory(".mzML")
    path = Path(context.artifacts["output_zp_path"])
    assert ZpReader(path).read_header().version == ZP_VERSION_V1
    assert ZpValidator().validate(path).valid is True
    assert "format_version" not in context.metadata


def test_existing_real_mzml_pipeline_call_path_still_defaults_to_v1(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "mzml" / "accept_ms1_only_nonindexed_float32_uncompressed.mzML"
    profile = SourceInspector().inspect([source])
    plan = PlanBuilder().build(profile)
    output = tmp_path / "real.zp"
    context = PipelineContext(profile, metadata={"output_path": output})

    PipelineRunner().run(plan, build_default_registry(), context)

    assert ZpReader(output).read_header().version == ZP_VERSION_V1
    assert context.artifacts["validation_result"].valid is True
    assert "format_version" not in context.metadata
    assert all("version" not in step_name for step_name in plan.required_steps)
