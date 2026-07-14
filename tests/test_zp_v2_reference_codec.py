import hashlib
import json
import struct
from pathlib import Path

from specs.zp_v2.arrays_reference_codec import (
    ReferenceArray,
    ResourceLimits,
    decode_arrays_block,
    encode_arrays_block,
    read_array,
    validate_arrays_block,
)


FIXTURE = Path(__file__).parents[1] / "specs" / "zp_v2" / "fixtures" / "valid_arrays_v2.bin"


def sample_arrays() -> tuple[ReferenceArray, ...]:
    return (
        ReferenceArray("spectrum_000001:mz", "mz", (0.0, 100.125, 2500.75)),
        ReferenceArray("chromatogram_000001:time", "time", (0.0, 0.125, 12.75)),
        ReferenceArray("spectrum_000001:intensity", "intensity", (0.0, -2.5, 1500.25)),
    )


def test_reference_codec_is_deterministic_and_matches_golden_fixture() -> None:
    first = encode_arrays_block(sample_arrays())
    second = encode_arrays_block(tuple(reversed(sample_arrays())))
    assert first == second == FIXTURE.read_bytes()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_reference_codec_roundtrip_and_validation() -> None:
    raw = encode_arrays_block(sample_arrays())
    decoded = decode_arrays_block(raw)
    assert [item.array_id for item in decoded.arrays] == [
        "chromatogram_000001:time",
        "spectrum_000001:intensity",
        "spectrum_000001:mz",
    ]
    assert decoded.arrays[1].values == (0.0, -2.5, 1500.25)
    result = validate_arrays_block(raw)
    assert (result.valid, result.error_code, result.entry_count) == (True, None, 3)


def test_random_read_slices_only_target_and_does_not_validate_other_payload() -> None:
    raw = bytearray(FIXTURE.read_bytes())
    _, _, _, _, _, directory_offset, directory_length, payload_offset, _, _ = struct.Struct("<8sHBBIQQQQ16s").unpack(raw[:64])
    directory = json.loads(raw[directory_offset:directory_offset + directory_length].decode("utf-8"))
    target = next(item for item in directory["entries"] if item["array_id"] == "spectrum_000001:mz")
    other = next(item for item in directory["entries"] if item["array_id"] == "spectrum_000001:intensity")
    raw[payload_offset + other["data_offset"]] ^= 1
    calls: list[tuple[int, int]] = []

    def payload_reader(start: int, length: int) -> bytes:
        calls.append((start, length))
        return bytes(raw[start:start + length])

    values = read_array(raw, target["array_id"], payload_reader=payload_reader)
    assert values == tuple(struct.unpack("<3d", raw[payload_offset + target["data_offset"]:payload_offset + target["data_offset"] + 24]))
    assert calls == [(payload_offset + target["data_offset"], target["byte_length"])]
    assert validate_arrays_block(raw).error_code == "ARRAY_CHECKSUM_MISMATCH"


def test_empty_arrays_block_is_valid_at_format_layer() -> None:
    raw = encode_arrays_block(())
    decoded = decode_arrays_block(raw)
    assert decoded.arrays == ()
    assert decoded.directory == {"entries": []}
    assert decoded.payload_length == 0
    assert decoded.payload_offset == 80
    assert len(raw) == 80


def test_reference_decoder_enforces_each_configurable_resource_limit_before_deep_decode() -> None:
    raw = FIXTURE.read_bytes()
    cases = (
        (ResourceLimits(max_arrays_block_length=100), "ARRAYS_RESOURCE_LIMIT_EXCEEDED"),
        (ResourceLimits(max_directory_length=100), "ARRAY_DIRECTORY_TOO_LARGE"),
        (ResourceLimits(max_entry_count=2), "ARRAY_COUNT_TOO_LARGE"),
        (ResourceLimits(max_array_value_count=2), "ARRAY_VALUE_COUNT_TOO_LARGE"),
        (ResourceLimits(max_array_id_utf8_length=8), "ARRAY_ID_TOO_LONG"),
        (ResourceLimits(max_payload_length=64), "ARRAYS_RESOURCE_LIMIT_EXCEEDED"),
        (ResourceLimits(max_decoded_memory=64), "ARRAYS_RESOURCE_LIMIT_EXCEEDED"),
    )
    for limits, expected_code in cases:
        result = validate_arrays_block(raw, limits=limits)
        assert result.error_code == expected_code
