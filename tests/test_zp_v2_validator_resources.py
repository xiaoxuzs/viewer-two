from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from binary_layer import ZpV2ValidationLimits, ZpValidator
from zp_v2_reader_support import build_complete_v2


RESOURCE_CASES = (
    ("max_arrays_block_length", 1, "ARRAYS_RESOURCE_LIMIT_EXCEEDED"),
    ("max_array_directory_length", 1, "ARRAY_DIRECTORY_TOO_LARGE"),
    ("max_entry_count", 1, "ARRAY_COUNT_TOO_LARGE"),
    ("max_array_value_count", 1, "ARRAY_VALUE_COUNT_TOO_LARGE"),
    ("max_array_id_utf8_length", 1, "ARRAY_ID_TOO_LONG"),
    ("max_payload_length", 1, "ARRAY_PAYLOAD_TOO_LARGE"),
)


@pytest.mark.parametrize(("field", "limit", "expected_code"), RESOURCE_CASES)
def test_resource_limits_fail_before_oversized_arrays_reads(
    field: str,
    limit: int,
    expected_code: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "resource.zp"
    build_complete_v2(path)
    validator = ZpValidator()
    validator.v2_limits = replace(ZpV2ValidationLimits(), **{field: limit})

    result = validator.validate(path)

    issue = next(item for item in result.issues if item.code == expected_code)
    assert result.valid is False
    assert "location=" in issue.message
    assert "actual=" in issue.message
    assert "limit=" in issue.message


def test_work_memory_limit_is_enforced_before_large_json_read(tmp_path: Path) -> None:
    path = tmp_path / "work-memory.zp"
    build_complete_v2(path)
    validator = ZpValidator()
    validator.v2_limits = replace(
        ZpV2ValidationLimits(),
        max_work_memory=128,
        chunk_size=8,
    )
    result = validator.validate(path)
    assert result.issues[0].code == "VALIDATION_WORK_MEMORY_EXCEEDED"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_payload_length": 0},
        {"max_entry_count": True},
        {"chunk_size": 7},
        {"max_work_memory": 8, "chunk_size": 16},
    ],
)
def test_invalid_validation_limit_configuration_is_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ZpV2ValidationLimits(**kwargs)


def test_validation_limits_expose_frozen_safe_defaults() -> None:
    limits = ZpV2ValidationLimits()
    assert limits.max_arrays_block_length == 512 * 1024 * 1024
    assert limits.max_array_directory_length == 64 * 1024 * 1024
    assert limits.max_entry_count == 100_000
    assert limits.max_array_value_count == 16_000_000
    assert limits.max_array_id_utf8_length == 4096
    assert limits.max_payload_length == 448 * 1024 * 1024
    assert limits.max_work_memory == 64 * 1024 * 1024

