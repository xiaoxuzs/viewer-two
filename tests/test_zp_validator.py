from __future__ import annotations

from pathlib import Path

from binary_layer.constants import DIRECTORY_LENGTH_STRUCT, HEADER_SIZE
from binary_layer.validator import ZpValidator
from conftest import load_raw_zp, rewrite_directory, rewrite_zp


def codes(path: Path) -> set[str]:
    return {issue.code for issue in ZpValidator().validate(path).issues}


def test_valid_file_passes(valid_zp: Path) -> None:
    result = ZpValidator().validate(valid_zp)
    assert result.valid is True
    assert result.checked_blocks == 9


def test_corrupt_magic_is_reported(valid_zp: Path) -> None:
    with valid_zp.open("r+b") as stream:
        stream.seek(0)
        stream.write(b"BAD!")
    assert "INVALID_MAGIC" in codes(valid_zp)


def test_corrupt_real_block_bytes_breaks_checksum(valid_zp: Path) -> None:
    _header, directory, _payloads = load_raw_zp(valid_zp)
    entry = next(item for item in directory if item["block_name"] == "arrays")
    with valid_zp.open("r+b") as stream:
        stream.seek(entry["offset"])
        payload = bytearray(stream.read(entry["length"]))
        position = payload.index(b"100.0")
        payload[position:position + 5] = b"101.0"
        stream.seek(entry["offset"])
        stream.write(payload)
    assert "CHECKSUM_MISMATCH" in codes(valid_zp)


def test_missing_empty_extensions_is_reported(valid_zp: Path) -> None:
    rewrite_zp(valid_zp, omitted={"extensions"})
    assert "MISSING_REQUIRED_BLOCK" in codes(valid_zp)


def test_missing_array_reference_is_not_a_checksum_failure(valid_zp: Path) -> None:
    def mutate(payloads):
        payloads["core_spectra"][0]["mz_array_id"] = "missing_array"
    rewrite_zp(valid_zp, mutate)
    result_codes = codes(valid_zp)
    assert "INVALID_REFERENCE" in result_codes
    assert "CHECKSUM_MISMATCH" not in result_codes


def test_wrong_array_type_is_reported(valid_zp: Path) -> None:
    def mutate(payloads):
        payloads["core_spectra"][0]["mz_array_id"] = "intensity_1"
    rewrite_zp(valid_zp, mutate)
    assert "ARRAY_TYPE_MISMATCH" in codes(valid_zp)


def test_array_length_mismatch_is_reported(valid_zp: Path) -> None:
    def mutate(payloads):
        payloads["arrays"][1]["values"].pop()
    rewrite_zp(valid_zp, mutate)
    assert "ARRAY_LENGTH_MISMATCH" in codes(valid_zp)


def test_duplicate_array_id_is_reported(valid_zp: Path) -> None:
    def mutate(payloads):
        payloads["arrays"][1]["array_id"] = payloads["arrays"][0]["array_id"]
    rewrite_zp(valid_zp, mutate)
    assert "DUPLICATE_ID" in codes(valid_zp)


def test_block_offset_into_header_is_reported(valid_zp: Path) -> None:
    raw = bytearray(valid_zp.read_bytes())
    header, directory, _payloads = load_raw_zp(valid_zp)
    directory[0]["offset"] = HEADER_SIZE - 1
    from binary_layer.constants import DIRECTORY_LENGTH_STRUCT, HEADER_STRUCT
    from binary_layer.serialization import canonical_json_bytes
    directory_raw = canonical_json_bytes(directory)
    directory_offset = header[-1]
    raw[directory_offset:] = DIRECTORY_LENGTH_STRUCT.pack(len(directory_raw)) + directory_raw
    raw[:HEADER_SIZE] = HEADER_STRUCT.pack(*header)
    valid_zp.write_bytes(raw)
    assert "BLOCK_OVERLAPS_HEADER" in codes(valid_zp)


def test_trailing_data_after_directory_is_reported(valid_zp: Path) -> None:
    valid_zp.write_bytes(valid_zp.read_bytes() + b"trailing garbage")
    assert "TRAILING_DATA" in codes(valid_zp)


def test_overlapping_block_ranges_are_reported(valid_zp: Path) -> None:
    def overlap(directory):
        first, second = directory[0], directory[1]
        second["offset"] = first["offset"] + first["length"] - 1
    rewrite_directory(valid_zp, overlap)
    assert "OVERLAPPING_BLOCKS" in codes(valid_zp)


def test_duplicate_block_name_is_reported(valid_zp: Path) -> None:
    def duplicate(directory):
        directory.append(dict(next(item for item in directory if item["block_name"] == "core_spectra")))
    rewrite_directory(valid_zp, duplicate)
    assert "DUPLICATE_BLOCK_NAME" in codes(valid_zp)


def test_unsupported_version_is_reported(valid_zp: Path) -> None:
    raw = bytearray(valid_zp.read_bytes())
    raw[4:6] = (999).to_bytes(2, "little")
    valid_zp.write_bytes(raw)
    assert "UNSUPPORTED_VERSION" in codes(valid_zp)


def test_unsupported_endianness_is_reported(valid_zp: Path) -> None:
    raw = bytearray(valid_zp.read_bytes())
    raw[6] = 2
    valid_zp.write_bytes(raw)
    assert "UNSUPPORTED_ENDIANNESS" in codes(valid_zp)


def test_unsupported_encoding_is_reported(valid_zp: Path) -> None:
    def replace_encoding(directory):
        directory[0]["encoding"] = "msgpack"
    rewrite_directory(valid_zp, replace_encoding)
    assert "UNSUPPORTED_ENCODING" in codes(valid_zp)


def test_invalid_checksum_format_is_reported(valid_zp: Path) -> None:
    def replace_checksum(directory):
        directory[0]["checksum"] = "not-a-sha256"
    rewrite_directory(valid_zp, replace_checksum)
    assert "INVALID_CHECKSUM_FORMAT" in codes(valid_zp)


def test_directory_offset_beyond_file_is_reported(valid_zp: Path) -> None:
    raw = bytearray(valid_zp.read_bytes())
    raw[16:24] = (len(raw) + 1).to_bytes(8, "little")
    valid_zp.write_bytes(raw)
    assert "INVALID_DIRECTORY_OFFSET" in codes(valid_zp)


def test_declared_directory_length_beyond_eof_is_reported(valid_zp: Path) -> None:
    raw = bytearray(valid_zp.read_bytes())
    header, _directory, _payloads = load_raw_zp(valid_zp)
    directory_offset = header[-1]
    current_length = DIRECTORY_LENGTH_STRUCT.unpack(raw[directory_offset:directory_offset + 8])[0]
    raw[directory_offset:directory_offset + 8] = DIRECTORY_LENGTH_STRUCT.pack(current_length + 10)
    valid_zp.write_bytes(raw)
    assert "INVALID_DIRECTORY_LENGTH" in codes(valid_zp)
