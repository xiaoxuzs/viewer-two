from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpReader, ZpV2ArrayReadLimits
from binary_layer.exceptions import ZpV2ArrayReadError
from zp_v2_reader_support import build_complete_v2


@pytest.mark.parametrize(
    ("limits", "operation", "code"),
    [
        (ZpV2ArrayReadLimits(max_arrays_block_length=100), "single", "ARRAYS_RESOURCE_LIMIT_EXCEEDED"),
        (ZpV2ArrayReadLimits(max_directory_length=100), "single", "ARRAY_DIRECTORY_TOO_LARGE"),
        (ZpV2ArrayReadLimits(max_entry_count=1), "single", "ARRAY_COUNT_TOO_LARGE"),
        (ZpV2ArrayReadLimits(max_array_value_count=1), "single", "ARRAY_VALUE_COUNT_TOO_LARGE"),
        (ZpV2ArrayReadLimits(max_array_id_utf8_length=3), "single", "ARRAY_ID_TOO_LONG"),
        (ZpV2ArrayReadLimits(max_payload_length=8), "single", "ARRAY_PAYLOAD_TOO_LARGE"),
        (ZpV2ArrayReadLimits(max_decoded_memory=1), "all", "ARRAY_DECODE_BUDGET_EXCEEDED"),
    ],
)
def test_v2_reader_limits_fail_with_structured_errors(
    limits: ZpV2ArrayReadLimits, operation: str, code: str, tmp_path: Path
) -> None:
    path = tmp_path / f"{code}.zp"
    blocks = build_complete_v2(path)
    reader = ZpReader(path, v2_limits=limits)
    with pytest.raises(ZpV2ArrayReadError) as captured:
        if operation == "all":
            reader.read_arrays()
        else:
            reader.read_array(blocks.arrays[0].array_id)
    assert captured.value.code == code
    assert captured.value.location
    assert captured.value.actual > captured.value.limit


@pytest.mark.parametrize("field", ZpV2ArrayReadLimits.__dataclass_fields__)
def test_invalid_read_limit_configuration_is_rejected(field: str) -> None:
    with pytest.raises(ZpV2ArrayReadError) as captured:
        ZpV2ArrayReadLimits(**{field: 0})
    assert captured.value.code == "INVALID_ARRAY_READ_LIMITS"
