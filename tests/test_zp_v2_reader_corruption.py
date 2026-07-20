from __future__ import annotations

import json
import io
import os
import struct
from pathlib import Path

import pytest

from binary_layer import ZpReader
from binary_layer.exceptions import UnsupportedVersionError, ZpReadError, ZpV2ArrayReadError
from binary_layer.v2_arrays_reader import ZpV2ArraysReader
from test_zp_v2_reference_corruption import CASES as ARRAY_CORRUPTION_CASES
from zp_v2_reader_support import build_complete_v2, canonical, raw_layout


TOP_HEADER = struct.Struct("<4sHBBQQ")
LENGTH = struct.Struct("<Q")
ARRAYS_HEADER = struct.Struct("<8sHBBIQQQQ16s")


def _replace_top_directory(raw: bytes, directory: object, *, canonical_form: bool = True) -> bytes:
    header = list(TOP_HEADER.unpack(raw[: TOP_HEADER.size]))
    payload = canonical(directory)
    if not canonical_form:
        payload += b" "
    prefix = raw[: header[5]]
    return prefix + LENGTH.pack(len(payload)) + payload


def _top_cases(path: Path) -> list[tuple[str, bytes, str, str | None]]:
    layout = raw_layout(path)
    raw = layout["raw"]
    directory = layout["directory"]
    header = list(layout["header"])
    cases: list[tuple[str, bytes, str, str | None]] = []

    def header_case(name: str, position: int, value: object, code: str) -> None:
        mutated_header = header.copy()
        mutated_header[position] = value
        cases.append((name, TOP_HEADER.pack(*mutated_header) + raw[TOP_HEADER.size :], code, None))

    header_case("top magic", 0, b"BAD!", "ZP_READ_ERROR")
    header_case("top version", 1, 999, "UNSUPPORTED_ZP_VERSION")
    header_case("top endianness", 2, 2, "ZP_READ_ERROR")
    header_case("top flags", 3, 1, "UNSUPPORTED_TOP_LEVEL_FLAGS")
    header_case("top directory offset", 5, len(raw) + 1, "INVALID_TOP_DIRECTORY_OFFSET")

    bad_length = bytearray(raw)
    bad_length[header[5] : header[5] + LENGTH.size] = LENGTH.pack(128 * 1024 * 1024)
    cases.append(("top directory length", bytes(bad_length), "TOP_DIRECTORY_TOO_LARGE", None))
    cases.append(("noncanonical top directory", _replace_top_directory(raw, directory, canonical_form=False), "NONCANONICAL_TOP_DIRECTORY", None))

    missing = [dict(item) for item in directory[:-1]]
    cases.append(("missing required top block", _replace_top_directory(raw, missing), "INVALID_TOP_DIRECTORY_SCHEMA", None))
    reordered = [dict(item) for item in directory]
    reordered[0], reordered[1] = reordered[1], reordered[0]
    cases.append(("top block order", _replace_top_directory(raw, reordered), "INVALID_TOP_DIRECTORY_ORDER", None))
    overlapping = [dict(item) for item in directory]
    overlapping[1]["offset"] = overlapping[0]["offset"]
    cases.append(("top block overlap", _replace_top_directory(raw, overlapping), "OVERLAPPING_TOP_LEVEL_BLOCKS", None))
    bad_arrays_encoding = [dict(item) for item in directory]
    bad_arrays_encoding[6]["encoding"] = "utf-8-json"
    cases.append(("arrays encoding", _replace_top_directory(raw, bad_arrays_encoding), "ARRAYS_ENCODING_VERSION_MISMATCH", None))
    bad_json_encoding = [dict(item) for item in directory]
    bad_json_encoding[0]["encoding"] = "json"
    cases.append(("non-arrays encoding", _replace_top_directory(raw, bad_json_encoding), "ARRAYS_ENCODING_VERSION_MISMATCH", None))
    corrupt_json_block = bytearray(raw)
    corrupt_json_block[directory[0]["offset"]] ^= 1
    cases.append(("non-arrays block checksum", bytes(corrupt_json_block), "BLOCK_CHECKSUM_MISMATCH", "global_meta"))
    return cases


def _error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else "ZP_READ_ERROR"


def test_top_corruption_matrix_contains_all_13_required_real_file_mutations(tmp_path: Path) -> None:
    path = tmp_path / "valid.zp"
    build_complete_v2(path)
    cases = _top_cases(path)
    assert len(cases) == 13
    assert len({item[0] for item in cases}) == 13
    assert all(mutated != path.read_bytes() for _, mutated, _, _ in cases)


@pytest.mark.parametrize("case_position", range(13))
def test_v2_reader_rejects_top_level_real_byte_corruption(case_position: int, tmp_path: Path) -> None:
    valid = tmp_path / "valid.zp"
    corrupted = tmp_path / "corrupted.zp"
    build_complete_v2(valid)
    name, mutated, expected_code, block_name = _top_cases(valid)[case_position]
    corrupted.write_bytes(mutated)
    reader = ZpReader(corrupted)
    with pytest.raises((ZpReadError, UnsupportedVersionError, ZpV2ArrayReadError)) as captured:
        if block_name is None:
            reader.read_directory()
        else:
            reader.read_block(block_name)
    assert _error_code(captured.value) == expected_code, name


def test_arrays_corruption_matrix_contains_all_32_required_real_byte_mutations() -> None:
    assert len(ARRAY_CORRUPTION_CASES) == 32
    assert len({item[0] for item in ARRAY_CORRUPTION_CASES}) == 32


@pytest.mark.parametrize(
    ("name", "mutated", "expected_code"),
    ARRAY_CORRUPTION_CASES,
    ids=[item[0] for item in ARRAY_CORRUPTION_CASES],
)
def test_production_arrays_reader_rejects_real_byte_corruption(
    name: str, mutated: bytes, expected_code: str
) -> None:
    reader = ZpV2ArraysReader()
    stream = io.BytesIO(mutated)
    with pytest.raises(ZpV2ArrayReadError) as captured:
        directory = reader.read_directory(stream, block_offset=0, block_length=len(mutated))
        for entry in directory.entries:
            reader.read_array(stream, block_offset=0, directory=directory, array_id=entry.array_id)
    assert captured.value.code == expected_code, name


def test_reader_rejects_file_replacement_during_payload_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "current.zp"
    blocks = build_complete_v2(path)
    original = ZpV2ArraysReader.read_array
    changed = False

    def replacing_read(self, stream, **kwargs):
        nonlocal changed
        result = original(self, stream, **kwargs)
        if not changed:
            changed = True
            current = path.stat()
            os.utime(
                path,
                ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000),
            )
            assert path.stat().st_mtime_ns > current.st_mtime_ns
        return result

    monkeypatch.setattr(ZpV2ArraysReader, "read_array", replacing_read)
    with pytest.raises(ZpV2ArrayReadError) as captured:
        ZpReader(path).read_array(blocks.arrays[0].array_id)
    assert captured.value.code == "FILE_CHANGED_DURING_READ"


def test_reader_source_is_independent_from_writer_and_reference_codec() -> None:
    package = Path(__file__).parents[1] / "binary_layer"
    source = (package / "reader.py").read_text(encoding="utf-8") + (package / "v2_arrays_reader.py").read_text(encoding="utf-8")
    assert "v2_arrays_writer" not in source
    assert "arrays_reference_codec" not in source
    assert "specs.zp_v2" not in source


def test_top_directory_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    valid = tmp_path / "valid.zp"
    corrupted = tmp_path / "duplicate-top-key.zp"
    build_complete_v2(valid)
    layout = raw_layout(valid)
    raw = layout["raw"]
    directory_offset = layout["header"][-1]
    original = raw[directory_offset + LENGTH.size :]
    duplicate = original.replace(
        b'"block_name":"global_meta"',
        b'"block_name":"duplicate","block_name":"global_meta"',
        1,
    )
    corrupted.write_bytes(raw[:directory_offset] + LENGTH.pack(len(duplicate)) + duplicate)
    with pytest.raises(ZpV2ArrayReadError) as captured:
        ZpReader(corrupted).read_directory()
    assert captured.value.code == "INVALID_TOP_DIRECTORY_SCHEMA"


def test_internal_directory_rejects_duplicate_json_keys() -> None:
    fixture = Path(__file__).parents[1] / "specs" / "zp_v2" / "fixtures" / "valid_arrays_v2.bin"
    raw = fixture.read_bytes()
    header = list(ARRAYS_HEADER.unpack(raw[: ARRAYS_HEADER.size]))
    directory = raw[header[5] : header[5] + header[6]]
    payload = raw[header[7] : header[7] + header[8]]
    duplicate = directory.replace(b'{"entries":', b'{"entries":[],"entries":', 1)
    payload_offset = (ARRAYS_HEADER.size + len(duplicate) + 7) & ~7
    header[6] = len(duplicate)
    header[7] = payload_offset
    mutated = ARRAYS_HEADER.pack(*header) + duplicate + b"\0" * (payload_offset - ARRAYS_HEADER.size - len(duplicate)) + payload
    with pytest.raises(ZpV2ArrayReadError) as captured:
        ZpV2ArraysReader().read_directory(io.BytesIO(mutated), block_offset=0, block_length=len(mutated))
    assert captured.value.code == "INVALID_ARRAY_DIRECTORY_SCHEMA"


def test_unknown_array_error_preserves_requested_array_id(tmp_path: Path) -> None:
    path = tmp_path / "valid.zp"
    build_complete_v2(path)
    with pytest.raises(ZpV2ArrayReadError) as captured:
        ZpReader(path).read_array("missing-array")
    assert (captured.value.code, captured.value.array_id) == ("ARRAY_NOT_FOUND", "missing-array")
