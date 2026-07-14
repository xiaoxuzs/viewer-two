from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from binary_layer import ArrayBlock, ZpV2ArrayWriteLimits
from binary_layer.exceptions import ZpV2ArrayWriteError
from binary_layer.v2_arrays_writer import prepare_v2_arrays_layout, write_v2_arrays_block
from zp_v2_writer_support import parse_arrays_block


FIXTURE = Path(__file__).parents[1] / "specs" / "zp_v2" / "fixtures" / "valid_arrays_v2.bin"


def golden_arrays() -> list[ArrayBlock]:
    return [
        ArrayBlock("spectrum_000001:mz", "mz", "float64", [0.0, 100.125, 2500.75]),
        ArrayBlock("chromatogram_000001:time", "time", "float64", [0.0, 0.125, 12.75]),
        ArrayBlock("spectrum_000001:intensity", "intensity", "float64", [0.0, -2.5, 1500.25]),
    ]


def encode(arrays: list[ArrayBlock], limits: ZpV2ArrayWriteLimits | None = None) -> bytes:
    layout = prepare_v2_arrays_layout(arrays, limits=limits)
    stream = io.BytesIO()
    length, checksum = write_v2_arrays_block(stream, layout)
    raw = stream.getvalue()
    assert length == len(raw)
    assert checksum == hashlib.sha256(raw).hexdigest()
    return raw


def test_production_arrays_writer_matches_frozen_golden_byte_for_byte() -> None:
    raw = encode(golden_arrays())
    assert raw == FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "fc08d7123bd5abcb811d6fdbe5fff06b2250cb7e92727f5275d16cdb70cf7a5c"
    parsed = parse_arrays_block(raw)
    assert parsed["header"] == (b"ZPARRV2\0", 2, 1, 0, 3, 64, 688, 752, 72, b"\0" * 16)


def test_empty_arrays_match_frozen_empty_golden() -> None:
    raw = encode([])
    fixture = FIXTURE.with_name("valid_empty_arrays_v2.bin")
    assert raw == fixture.read_bytes()


def test_utf8_array_id_order_and_payload_order_are_input_independent() -> None:
    arrays = [
        ArrayBlock("é", "intensity", "float64", [2.0]),
        ArrayBlock("z", "intensity", "float64", [1.0]),
        ArrayBlock("中", "intensity", "float64", [3.0]),
    ]
    first = encode(arrays)
    second = encode(list(reversed(arrays)))
    assert first == second
    entries = parse_arrays_block(first)["directory"]["entries"]
    assert [entry["array_id"] for entry in entries] == sorted(["é", "z", "中"], key=lambda item: item.encode("utf-8"))
    assert [entry["data_offset"] for entry in entries] == [0, 8, 16]


@pytest.mark.parametrize(
    ("array", "code"),
    [
        (ArrayBlock("", "mz", "float64", [1.0]), "INVALID_ARRAY_ID"),
        (ArrayBlock("bad\0id", "mz", "float64", [1.0]), "INVALID_ARRAY_ID"),
        (ArrayBlock("a", "unknown", "float64", [1.0]), "UNSUPPORTED_ARRAY_TYPE"),
        (ArrayBlock("a", "mz", "float32", [1.0]), "UNSUPPORTED_ARRAY_DTYPE"),
        (ArrayBlock("a", "mz", "float64", [True]), "INVALID_ARRAY_VALUE"),
        (ArrayBlock("a", "mz", "float64", ["1.0"]), "INVALID_ARRAY_VALUE"),
        (ArrayBlock("a", "mz", "float64", [float("nan")]), "NONFINITE_ARRAY_VALUE"),
        (ArrayBlock("a", "mz", "float64", [float("inf")]), "NONFINITE_ARRAY_VALUE"),
        (ArrayBlock("a", "mz", "float64", [-1.0]), "NEGATIVE_MZ_VALUE"),
        (ArrayBlock("a", "time", "float64", [-1.0]), "NEGATIVE_TIME_VALUE"),
    ],
)
def test_invalid_array_fields_have_stable_structured_errors(array: ArrayBlock, code: str) -> None:
    with pytest.raises(ZpV2ArrayWriteError) as captured:
        prepare_v2_arrays_layout([array])
    assert captured.value.code == code
    assert captured.value.location


def test_duplicate_ids_reject_without_sorting_or_mutating_input() -> None:
    arrays = [ArrayBlock("same", "mz", "float64", [1.0]), ArrayBlock("same", "intensity", "float64", [2.0])]
    before = [(item.array_id, item.array_type, list(item.values)) for item in arrays]
    with pytest.raises(ZpV2ArrayWriteError) as captured:
        prepare_v2_arrays_layout(arrays)
    assert captured.value.code == "DUPLICATE_ARRAY_ID"
    assert [(item.array_id, item.array_type, item.values) for item in arrays] == before


def test_finite_negative_intensity_is_allowed() -> None:
    raw = encode([ArrayBlock("negative", "intensity", "float64", [-2.5])])
    assert parse_arrays_block(raw)["directory"]["entries"][0]["value_count"] == 1
