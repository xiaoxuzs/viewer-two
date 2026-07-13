from pathlib import Path

import pytest

from binary_layer.exceptions import UnsupportedSourceError
from binary_layer.inspector import SourceInspector
from binary_layer.plan import MZML_STEPS, RAW_STEPS, PlanBuilder


def test_exact_plans_and_extension() -> None:
    builder = PlanBuilder()
    mzml = builder.build(SourceInspector().inspect([Path("a.mzML")]))
    raw = builder.build(SourceInspector().inspect([Path("a.raw")]))
    assert mzml.required_steps == MZML_STEPS
    assert raw.required_steps == RAW_STEPS
    assert mzml.output_extension == raw.output_extension == ".zp"


def test_unknown_is_rejected_without_side_effect(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedSourceError):
        PlanBuilder().build(SourceInspector().inspect([tmp_path / "a.txt"]))
    assert list(tmp_path.iterdir()) == []

