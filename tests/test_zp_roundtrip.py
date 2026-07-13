import hashlib
import json
import struct

from binary_layer.constants import HEADER_SIZE, REQUIRED_BLOCK_NAMES, ZP_MAGIC, ZP_VERSION
from binary_layer.reader import ZpReader


def test_roundtrip_reads_header_directory_spectrum_and_arrays(valid_zp) -> None:
    reader = ZpReader(valid_zp)
    header = reader.read_header()
    assert header.magic == ZP_MAGIC
    assert header.version == ZP_VERSION
    assert HEADER_SIZE == 24
    assert {entry.block_name for entry in reader.read_directory()} == REQUIRED_BLOCK_NAMES
    assert reader.read_extensions() == []
    spectrum, mz_array, intensity_array = reader.read_spectrum_arrays("spectrum_2")
    assert spectrum.mz_array_id == mz_array.array_id
    assert spectrum.intensity_array_id == intensity_array.array_id
    assert mz_array.values == [110.0, 210.0, 310.0]
    assert intensity_array.values == [15.0, 55.0, 25.0]
    raw_spectrum = reader.read_block("core_spectra")[1]
    assert "values" not in raw_spectrum
    assert "mz_values" not in raw_spectrum


def test_external_byte_contract_without_reader_helpers(valid_zp) -> None:
    raw = valid_zp.read_bytes()
    header_struct = struct.Struct("<4sHBBQQ")
    assert header_struct.size == 24
    magic, version, endianness, flags, created_at, directory_offset = header_struct.unpack(raw[:24])
    assert (magic, version, endianness, flags) == (b"ZPMS", 1, 1, 0)
    assert created_at > 0
    assert 24 <= directory_offset < len(raw)

    directory_length = int.from_bytes(raw[directory_offset:directory_offset + 8], "little")
    directory_end = directory_offset + 8 + directory_length
    assert directory_end == len(raw)
    directory = json.loads(raw[directory_offset + 8:directory_end].decode("utf-8"))
    assert [entry["block_name"] for entry in directory] == [
        "global_meta",
        "string_pool",
        "core_runs",
        "core_spectra",
        "core_precursors",
        "core_chromatograms",
        "arrays",
        "indexes",
        "extensions",
    ]

    decoded_blocks = {}
    for entry in directory:
        block_raw = raw[entry["offset"]:entry["offset"] + entry["length"]]
        assert entry["offset"] >= 24
        assert entry["offset"] + entry["length"] <= directory_offset
        assert hashlib.sha256(block_raw).hexdigest() == entry["checksum"]
        decoded_blocks[entry["block_name"]] = json.loads(block_raw.decode("utf-8"))

    assert decoded_blocks["extensions"] == []
    assert decoded_blocks["core_chromatograms"] == []
    assert isinstance(decoded_blocks["arrays"], list)
    array_ids = [item["array_id"] for item in decoded_blocks["arrays"]]
    assert len(array_ids) == len(set(array_ids)) == 6
