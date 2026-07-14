from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ArrayBlock, ZpV2ArrayWriteLimits, ZpWriter
from binary_layer.exceptions import ZpV2ArrayWriteError, ZpV2ResourceLimitError
from binary_layer.v2_arrays_writer import prepare_v2_arrays_layout


@pytest.mark.parametrize(
    ("limits", "arrays", "code"),
    [
        (ZpV2ArrayWriteLimits(max_entry_count=1), [ArrayBlock("a", "mz", "float64", []), ArrayBlock("b", "mz", "float64", [])], "ARRAY_COUNT_TOO_LARGE"),
        (ZpV2ArrayWriteLimits(max_array_value_count=1), [ArrayBlock("a", "mz", "float64", [1.0, 2.0])], "ARRAY_VALUE_COUNT_TOO_LARGE"),
        (ZpV2ArrayWriteLimits(max_array_id_utf8_length=1), [ArrayBlock("é", "mz", "float64", [])], "ARRAY_ID_TOO_LONG"),
        (ZpV2ArrayWriteLimits(max_payload_length=8), [ArrayBlock("a", "mz", "float64", [1.0, 2.0])], "ARRAY_PAYLOAD_TOO_LARGE"),
        (ZpV2ArrayWriteLimits(max_directory_length=13), [], "ARRAY_DIRECTORY_TOO_LARGE"),
        (ZpV2ArrayWriteLimits(max_arrays_block_length=79), [], "ARRAYS_RESOURCE_LIMIT_EXCEEDED"),
    ],
)
def test_each_write_limit_has_a_stable_error(limits: ZpV2ArrayWriteLimits, arrays: list[ArrayBlock], code: str) -> None:
    with pytest.raises(ZpV2ResourceLimitError) as captured:
        prepare_v2_arrays_layout(arrays, limits=limits)
    error = captured.value
    assert (error.code, error.actual > error.limit, bool(error.location)) == (code, True, True)


@pytest.mark.parametrize("field", ZpV2ArrayWriteLimits.__dataclass_fields__)
def test_limit_configuration_rejects_zero_negative_bool_and_non_integer(field: str) -> None:
    for invalid in (0, -1, True, 1.5):
        with pytest.raises(ZpV2ArrayWriteError) as captured:
            ZpV2ArrayWriteLimits(**{field: invalid})
        assert captured.value.code == "INVALID_ARRAY_WRITE_LIMITS"


def test_preflight_resource_failure_creates_no_directory_tmp_or_target(pipeline_factory, tmp_path: Path) -> None:
    blocks = pipeline_factory(".mzML").blocks
    target = tmp_path / "not-created" / "resource.zp"
    with pytest.raises(ZpV2ResourceLimitError):
        ZpWriter().write(
            target,
            blocks,
            format_version=2,
            v2_limits=ZpV2ArrayWriteLimits(max_entry_count=1),
        )
    assert not target.parent.exists()
    assert not target.exists()
    assert not target.with_name(target.name + ".tmp").exists()
