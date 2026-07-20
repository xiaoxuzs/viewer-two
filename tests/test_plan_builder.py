from pathlib import Path

import pytest

from binary_layer.exceptions import UnsupportedSourceError
from binary_layer.inspector import SourceInspector
from binary_layer.models import SourceProfile
from binary_layer.plan import MOCK_MZML_STEPS, RAW_STEPS, REAL_MZML_STEPS, REAL_THERMO_RAW_STEPS, REAL_TOP_DOWN_STEPS, PlanBuilder


def mock_mzml_profile(path: Path) -> SourceProfile:
    return SourceProfile("mock_mzml", (path,), 1, True, False, False, False, False)


def mock_raw_profile(path: Path) -> SourceProfile:
    return SourceProfile("mock_raw", (path,), 1, True, False, False, False, True)


def test_exact_plans_and_extension() -> None:
    builder = PlanBuilder()
    mzml = builder.build(SourceInspector().inspect([Path("a.mzML")]))
    mock = builder.build(mock_mzml_profile(Path("mock.mzML")))
    raw = builder.build(SourceInspector().inspect([Path("a.raw")]))
    mock_raw = builder.build(mock_raw_profile(Path("mock.raw")))
    assert mzml.source_type == "real_mzml"
    assert mzml.required_steps == REAL_MZML_STEPS
    assert "real_mzml_parse" in mzml.required_steps
    assert "mock_mzml_parse" not in mzml.required_steps
    assert mock.required_steps == MOCK_MZML_STEPS
    assert "real_mzml_parse" not in mock.required_steps
    assert raw.source_type == "real_thermo_raw"
    assert raw.required_steps == REAL_THERMO_RAW_STEPS
    assert "real_thermo_raw_parse" in raw.required_steps
    assert mock_raw.required_steps == RAW_STEPS
    assert mzml.output_extension == mock.output_extension == raw.output_extension == ".zp"


def test_unknown_is_rejected_without_side_effect(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedSourceError):
        PlanBuilder().build(SourceInspector().inspect([tmp_path / "a.txt"]))
    assert list(tmp_path.iterdir()) == []


def test_plan_builder_has_no_file_side_effects(tmp_path: Path) -> None:
    source = tmp_path / "sample.mzML"
    source.write_bytes(b"unchanged")
    before = source.read_bytes()
    plan = PlanBuilder().build(SourceInspector().inspect([source]))
    assert plan.required_steps == REAL_MZML_STEPS
    assert source.read_bytes() == before
    assert list(tmp_path.iterdir()) == [source]


def test_top_down_plan_uses_named_registry_step_only(tmp_path: Path) -> None:
    profile = SourceProfile(
        "real_top_down_bundle",
        (tmp_path,),
        1,
        True,
        False,
        True,
        True,
        False,
    )

    plan = PlanBuilder().build(profile)

    assert plan.required_steps == REAL_TOP_DOWN_STEPS
    assert plan.required_steps.count("real_top_down") == 1
