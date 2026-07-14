from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from zp_v2_reader_support import build_complete_v2, raw_layout
from zp_v2_validator_support import (
    corrupt_top_block_without_checksum_update,
    mutate_array_value,
    replace_block_raw,
    top_layout,
)
from zp_v2_writer_support import build_real_blocks


JSON_BLOCKS = (
    "global_meta",
    "string_pool",
    "core_runs",
    "core_spectra",
    "core_precursors",
    "core_chromatograms",
    "indexes",
    "extensions",
)


@pytest.mark.parametrize("block_name", JSON_BLOCKS)
def test_each_non_arrays_top_level_checksum_is_verified(
    block_name: str, tmp_path: Path
) -> None:
    path = tmp_path / "checksum.zp"
    ZpWriter().write(
        path,
        build_real_blocks("accept_tic_bpc_chromatograms.mzML"),
        format_version=2,
    )
    corrupt_top_block_without_checksum_update(path, block_name)

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert result.issues[0].code == "BLOCK_CHECKSUM_MISMATCH"
    assert result.issues[0].block_name == block_name


def test_arrays_top_checksum_error_precedes_per_array_error(tmp_path: Path) -> None:
    path = tmp_path / "arrays-top.zp"
    build_complete_v2(path)
    layout = raw_layout(path)
    raw = bytearray(layout["raw"])
    arrays_entry = layout["arrays_entry"]
    arrays_header = layout["arrays_header"]
    position = arrays_entry["offset"] + arrays_header[7]
    raw[position] ^= 1
    path.write_bytes(raw)

    result = ZpValidator().validate(path)

    assert result.valid is False
    assert [item.code for item in result.issues[:2]] == [
        "BLOCK_CHECKSUM_MISMATCH",
        "ARRAY_CHECKSUM_MISMATCH",
    ]


def test_per_array_checksum_isolated_by_updating_top_checksum(tmp_path: Path) -> None:
    path = tmp_path / "per-array.zp"
    build_complete_v2(path)
    mutate_array_value(
        path,
        "spectrum_000001:intensity",
        0,
        12345.0,
        update_per_array_checksum=False,
    )

    result = ZpValidator().validate(path)

    assert [item.code for item in result.issues] == ["ARRAY_CHECKSUM_MISMATCH"]


def test_numeric_error_isolated_by_updating_both_checksum_layers(tmp_path: Path) -> None:
    path = tmp_path / "numeric.zp"
    build_complete_v2(path)
    mutate_array_value(
        path,
        "spectrum_000001:mz",
        0,
        -1.0,
        update_per_array_checksum=True,
    )

    result = ZpValidator().validate(path)

    assert [item.code for item in result.issues] == ["NEGATIVE_MZ_VALUE"]
    assert "values[0]" in result.issues[0].block_name


def test_duplicate_non_arrays_json_key_is_rejected_after_outer_checksum_update(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate-key.zp"
    build_complete_v2(path)
    _header, _directory, payloads = top_layout(path)
    duplicate = payloads["global_meta"].replace(
        b'"format_version":2',
        b'"format_version":2,"format_version":2',
        1,
    )
    replace_block_raw(path, "global_meta", duplicate)

    result = ZpValidator().validate(path)

    assert result.issues[0].code == "INVALID_BLOCK_JSON"


def test_noncanonical_and_invalid_json_are_distinct(tmp_path: Path) -> None:
    for name, payload, expected in (
        ("noncanonical", None, "NONCANONICAL_BLOCK_JSON"),
        ("invalid", b"{", "INVALID_BLOCK_JSON"),
    ):
        path = tmp_path / f"{name}.zp"
        build_complete_v2(path)
        _header, _directory, payloads = top_layout(path)
        replace_block_raw(
            path,
            "global_meta",
            payloads["global_meta"] + b" " if payload is None else payload,
        )
        result = ZpValidator().validate(path)
        assert result.issues[0].code == expected
