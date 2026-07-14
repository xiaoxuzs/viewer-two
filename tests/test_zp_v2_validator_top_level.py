from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpValidator
from test_zp_v2_reader_corruption import _top_cases
from zp_v2_reader_support import build_complete_v2


_VALIDATOR_CODE = {
    "ZP_READ_ERROR": None,
    "UNSUPPORTED_ZP_VERSION": "UNSUPPORTED_VERSION",
    "INVALID_TOP_DIRECTORY_SCHEMA": "MISSING_REQUIRED_BLOCK",
}


def _cases(path: Path) -> list[tuple[str, bytes, str]]:
    result = []
    for name, raw, reader_code, _block_name in _top_cases(path):
        expected = _VALIDATOR_CODE.get(reader_code, reader_code)
        if expected is None:
            expected = {
                "top magic": "INVALID_MAGIC",
                "top endianness": "UNSUPPORTED_ENDIANNESS",
            }[name]
        result.append((name, raw, expected))
    return result


def test_top_level_corruption_matrix_has_13_distinct_cases(tmp_path: Path) -> None:
    path = tmp_path / "valid.zp"
    build_complete_v2(path)
    cases = _cases(path)
    assert len(cases) == 13
    assert len({item[0] for item in cases}) == 13


@pytest.mark.parametrize("case_position", range(13))
def test_v2_validator_rejects_top_level_real_byte_corruption(
    case_position: int, tmp_path: Path
) -> None:
    valid = tmp_path / "valid.zp"
    corrupted = tmp_path / "corrupted.zp"
    build_complete_v2(valid)
    name, raw, expected = _cases(valid)[case_position]
    corrupted.write_bytes(raw)

    result = ZpValidator().validate(corrupted)

    assert result.valid is False, name
    assert result.issues[0].code == expected, (name, [item.code for item in result.issues])

