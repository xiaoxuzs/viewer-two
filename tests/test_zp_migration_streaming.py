from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from binary_layer import ZpReader
from binary_layer.serialization import canonical_json_bytes
from binary_layer.v1_arrays_stream_reader import V1ArraysStreamError, V1ArraysStreamReader
from zp_compatibility_support import top_layout


FIXTURES = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 31, 255, 4096, 1024 * 1024])
def test_v1_stream_parser_matches_full_reader_across_chunk_boundaries(chunk_size: int) -> None:
    source = FIXTURES / "valid_full_v1.zp"
    _header, directory, payloads = top_layout(source)
    entry = next(item for item in directory if item["block_name"] == "arrays")
    raw = payloads["arrays"]
    reader = V1ArraysStreamReader(
        io.BytesIO(raw),
        block_offset=0,
        block_length=len(raw),
        expected_checksum=entry["checksum"],
        chunk_size=chunk_size,
    )
    streamed = list(reader.iter_arrays())
    reference = ZpReader(source).read_arrays()
    assert [
        (item.array_id, item.array_type, item.dtype, item.values)
        for item in streamed
    ] == [
        (item.array_id, item.array_type, item.dtype, item.values)
        for item in reference
    ]
    assert reader.metrics.scan_count == 1
    assert reader.metrics.block_bytes_read == len(raw)
    assert reader.metrics.max_live_array_count == 1


def test_stream_parser_handles_large_single_array_without_retaining_other_arrays() -> None:
    values = [float(position) / 8.0 for position in range(20_001)]
    raw = canonical_json_bytes(
        [
            {
                "array_id": "large:mz",
                "array_type": "mz",
                "dtype": "float64",
                "values": values,
            },
            {
                "array_id": "small:intensity",
                "array_type": "intensity",
                "dtype": "float64",
                "values": [0.0, -1.0],
            },
        ]
    )
    reader = V1ArraysStreamReader(
        io.BytesIO(raw),
        block_offset=0,
        block_length=len(raw),
        expected_checksum=hashlib.sha256(raw).hexdigest(),
        chunk_size=7,
    )
    arrays = list(reader.iter_arrays())
    assert len(arrays) == 2
    assert reader.metrics.max_live_array_count == 1
    assert reader.metrics.max_single_array_value_count == 20_001
    assert reader.metrics.numeric_value_count == 20_003


@pytest.mark.parametrize(
    ("value", "expected_code"),
    [
        ([{"array_id": "a", "array_type": "mz", "dtype": "float32", "values": [1.0]}], "UNSUPPORTED_ARRAY_DTYPE"),
        ([{"array_id": "a", "array_type": "unknown", "dtype": "float64", "values": [1.0]}], "UNSUPPORTED_ARRAY_TYPE"),
        ([{"array_id": "a", "array_type": "mz", "dtype": "float64", "values": [-1.0]}], "NEGATIVE_ARRAY_VALUE"),
        ([{"array_id": "a", "array_type": "time", "dtype": "float64", "values": [-1.0]}], "NEGATIVE_ARRAY_VALUE"),
        ([{"array_id": "a", "array_type": "intensity", "dtype": "float64", "values": [True]}], "INVALID_ARRAY_VALUE"),
        ([{"array_id": "a", "array_type": "intensity", "dtype": "float64", "values": [1.0]}, {"array_id": "a", "array_type": "intensity", "dtype": "float64", "values": [2.0]}], "DUPLICATE_ARRAY_ID"),
    ],
)
def test_stream_parser_rejects_invalid_array_schema(value: object, expected_code: str) -> None:
    raw = canonical_json_bytes(value)
    reader = V1ArraysStreamReader(
        io.BytesIO(raw),
        block_offset=0,
        block_length=len(raw),
        expected_checksum=hashlib.sha256(raw).hexdigest(),
        chunk_size=2,
    )
    with pytest.raises(V1ArraysStreamError) as captured:
        list(reader.iter_arrays())
    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        (b'[{"array_id":"a","array_type":"mz","dtype":"float64","values":[1.0]}] ', "ARRAYS_TRAILING_DATA"),
        (b'[ {"array_id":"a","array_type":"mz","dtype":"float64","values":[1.0]}]', "INVALID_ARRAYS_JSON"),
        (b'[{"array_id":"a","array_type":"mz","dtype":"float64","values":[1.0]}', "TRUNCATED_ARRAYS_JSON"),
    ],
)
def test_stream_parser_rejects_noncanonical_or_truncated_top_level(
    raw: bytes,
    expected_code: str,
) -> None:
    reader = V1ArraysStreamReader(
        io.BytesIO(raw),
        block_offset=0,
        block_length=len(raw),
        expected_checksum=hashlib.sha256(raw).hexdigest(),
        chunk_size=1,
    )
    with pytest.raises(V1ArraysStreamError) as captured:
        list(reader.iter_arrays())
    assert captured.value.code == expected_code


def test_stream_parser_rejects_checksum_change_and_rescan() -> None:
    raw = b"[]"
    reader = V1ArraysStreamReader(
        io.BytesIO(raw),
        block_offset=0,
        block_length=len(raw),
        expected_checksum="0" * 64,
        chunk_size=1,
    )
    with pytest.raises(V1ArraysStreamError, match="ARRAYS_CHECKSUM_MISMATCH"):
        list(reader.iter_arrays())
    with pytest.raises(V1ArraysStreamError, match="ARRAYS_RESCANNED"):
        list(reader.iter_arrays())

