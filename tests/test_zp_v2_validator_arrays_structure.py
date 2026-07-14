from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpValidator
from test_zp_v2_reference_corruption import CASES
from zp_v2_reader_support import build_complete_v2
from zp_v2_validator_support import replace_arrays_block


def test_arrays_corruption_matrix_has_32_distinct_cases() -> None:
    assert len(CASES) == 32
    assert len({item[0] for item in CASES}) == 32


@pytest.mark.parametrize(
    ("name", "arrays_raw", "expected_code"),
    CASES,
    ids=[item[0] for item in CASES],
)
def test_v2_validator_rejects_arrays_real_byte_corruption(
    name: str,
    arrays_raw: bytes,
    expected_code: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "corrupted.zp"
    build_complete_v2(path)
    replace_arrays_block(path, arrays_raw)

    result = ZpValidator().validate(path)

    assert result.valid is False, name
    assert expected_code in {item.code for item in result.issues}, (
        name,
        [item.code for item in result.issues],
    )
