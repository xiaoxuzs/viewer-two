from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpValidator


FIXTURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


@pytest.mark.parametrize("name", ["valid_full_v1.zp", "valid_full_v2.zp", "valid_minimal_v1.zp", "valid_minimal_v2.zp"])
def test_production_validator_accepts_every_complete_file_golden(name: str) -> None:
    result = ZpValidator().validate(FIXTURE_DIR / name)

    assert result.valid is True
    assert result.issues == []
    assert result.checked_blocks == 9
