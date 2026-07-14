import json
import struct
from pathlib import Path


FIXTURE = Path(__file__).parents[1] / "specs" / "zp_v2" / "fixtures" / "valid_arrays_v2.bin"


def test_arrays_header_literal_struct_is_exactly_64_bytes_with_frozen_offsets() -> None:
    header_struct = struct.Struct("<8sHBBIQQQQ16s")
    assert header_struct.size == 64
    packed = header_struct.pack(
        b"ZPARRV2\0", 0x0201, 3, 4, 0x08070605,
        0x100F0E0D0C0B0A09, 0x1817161514131211,
        0x201F1E1D1C1B1A19, 0x2827262524232221, bytes(range(16)),
    )
    assert packed[0:8] == b"ZPARRV2\0"
    assert packed[8:10] == b"\x01\x02"
    assert packed[10] == 3
    assert packed[11] == 4
    assert packed[12:16] == b"\x05\x06\x07\x08"
    assert packed[16:24] == bytes(range(9, 17))
    assert packed[24:32] == bytes(range(17, 25))
    assert packed[32:40] == bytes(range(25, 33))
    assert packed[40:48] == bytes(range(33, 41))
    assert packed[48:64] == bytes(range(16))


def test_golden_header_has_frozen_values_alignment_and_zero_reserved() -> None:
    raw = FIXTURE.read_bytes()
    fields = struct.Struct("<8sHBBIQQQQ16s").unpack(raw[:64])
    magic, version, endianness, flags, count, directory_offset, directory_length, payload_offset, payload_length, reserved = fields
    assert magic == b"ZPARRV2\0"
    assert (version, endianness, flags, count) == (2, 1, 0, 3)
    assert directory_offset == 64
    assert payload_offset == (directory_offset + directory_length + 7) & ~7
    assert payload_offset % 8 == 0
    assert reserved == b"\0" * 16
    assert payload_offset + payload_length == len(raw)
    directory = json.loads(raw[directory_offset:directory_offset + directory_length].decode("utf-8"))
    assert list(directory) == ["entries"]

