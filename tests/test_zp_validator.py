from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import PipelineContext, PipelineRunner, PlanBuilder, SourceInspector, build_default_registry
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


def _valid_chromatogram_zp(tmp_path: Path) -> Path:
    source = Path(__file__).parent / "fixtures" / "mzml" / "accept_indexed_tic_minutes_float64_zlib.mzML"
    profile = SourceInspector().inspect([source])
    output = tmp_path / "valid-chromatogram.zp"
    PipelineRunner().run(
        PlanBuilder().build(profile),
        build_default_registry(),
        PipelineContext(profile, metadata={"output_path": output}),
    )
    assert ZpValidator().validate(output).valid is True
    return output


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing_time", "INVALID_REFERENCE"),
        ("missing_intensity", "INVALID_REFERENCE"),
        ("wrong_time_type", "ARRAY_TYPE_MISMATCH"),
        ("wrong_intensity_type", "ARRAY_TYPE_MISMATCH"),
        ("length_mismatch", "ARRAY_LENGTH_MISMATCH"),
        ("missing_run", "INVALID_REFERENCE"),
        ("negative_time", "INVALID_TIME_ARRAY_VALUE"),
    ],
)
def test_chromatogram_relationship_corruption_is_semantic_not_checksum_failure(
    mutation: str,
    expected_code: str,
    tmp_path: Path,
) -> None:
    output = _valid_chromatogram_zp(tmp_path)

    def mutate(payloads):
        chromatogram = payloads["core_chromatograms"][0]
        arrays = {item["array_id"]: item for item in payloads["arrays"]}
        if mutation == "missing_time":
            chromatogram["time_array_id"] = "missing_time"
        elif mutation == "missing_intensity":
            chromatogram["intensity_array_id"] = "missing_intensity"
        elif mutation == "wrong_time_type":
            chromatogram["time_array_id"] = chromatogram["intensity_array_id"]
        elif mutation == "wrong_intensity_type":
            chromatogram["intensity_array_id"] = chromatogram["time_array_id"]
        elif mutation == "length_mismatch":
            arrays[chromatogram["intensity_array_id"]]["values"].pop()
        elif mutation == "missing_run":
            chromatogram["run_id"] = "missing_run"
        elif mutation == "negative_time":
            arrays[chromatogram["time_array_id"]]["values"][0] = -1.0

    rewrite_zp(output, mutate)
    result_codes = codes(output)
    assert expected_code in result_codes
    assert "CHECKSUM_MISMATCH" not in result_codes
