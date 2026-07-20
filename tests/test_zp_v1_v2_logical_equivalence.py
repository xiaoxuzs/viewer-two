from __future__ import annotations

from pathlib import Path

import pytest

from specs.zp_full.inspect_full_zp import inspect_full_zp
from specs.zp_full.logical_model import LogicalZpDocument, logical_equivalence


FIXTURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


@pytest.mark.parametrize("kind", ["full", "minimal"])
def test_paired_golden_documents_are_exactly_logically_equal(kind: str) -> None:
    v1 = LogicalZpDocument.from_inspection(inspect_full_zp(FIXTURE_DIR / f"valid_{kind}_v1.zp"))
    v2 = LogicalZpDocument.from_inspection(inspect_full_zp(FIXTURE_DIR / f"valid_{kind}_v2.zp"))
    result = logical_equivalence(v1, v2)

    assert all(result.values()), result
    assert all(
        left["values"] == right["values"] and left["logical_sha256"] == right["logical_sha256"]
        for left, right in zip(v1.arrays, v2.arrays)
    )
