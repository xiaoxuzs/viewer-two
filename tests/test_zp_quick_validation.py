from __future__ import annotations

import json
from pathlib import Path

import pytest

from binary_layer import ZpWriter
from binary_layer.service import validate_zp
from zp_v2_reader_support import build_complete_v2, raw_layout
from zp_v2_writer_support import build_real_blocks


def test_quick_validates_all_nine_blocks_without_deep_semantics(tmp_path: Path) -> None:
    path = tmp_path / "quick.zp"
    build_complete_v2(path)

    result = validate_zp(path, mode="quick")

    assert result.valid is True
    assert result.mode == "quick"
    assert result.checked_blocks == 9
    assert result.deep_validation_reused is False
    assert result.bottom_up_valid is None
    assert result.metrics["extension_json_parsed"] is False
    assert result.metrics["array_values_visited"] == 0


def test_quick_rejects_header_corruption(tmp_path: Path) -> None:
    path = tmp_path / "header.zp"
    build_complete_v2(path)
    raw = bytearray(path.read_bytes())
    raw[0] ^= 1
    path.write_bytes(raw)

    result = validate_zp(path, mode="quick")

    assert result.valid is False
    assert result.issues[0].code == "INVALID_MAGIC"


def test_quick_rejects_directory_corruption(tmp_path: Path) -> None:
    path = tmp_path / "directory.zp"
    build_complete_v2(path)
    raw = bytearray(path.read_bytes())
    raw[-1] = ord("{")
    path.write_bytes(raw)

    result = validate_zp(path, mode="quick")

    assert result.valid is False
    assert result.issues[0].code == "INVALID_DIRECTORY_JSON"


def test_quick_rejects_block_checksum_corruption(tmp_path: Path) -> None:
    path = tmp_path / "block.zp"
    build_complete_v2(path)
    layout = raw_layout(path)
    raw = bytearray(layout["raw"])
    entry = next(
        item for item in layout["directory"] if item["block_name"] == "global_meta"
    )
    raw[entry["offset"]] ^= 1
    path.write_bytes(raw)

    result = validate_zp(path, mode="quick")

    assert result.valid is False
    assert result.checked_blocks == 9
    assert result.issues[0].code == "BLOCK_CHECKSUM_MISMATCH"


def test_deep_certificate_is_reused_only_for_identical_file(tmp_path: Path) -> None:
    path = tmp_path / "certified.zp"
    certificate = tmp_path / "acceptance.json"
    build_complete_v2(path)
    deep = validate_zp(path, mode="deep", certificate_path=certificate)
    assert deep.valid is True
    assert certificate.is_file()
    payload = json.loads(certificate.read_text(encoding="utf-8"))
    assert payload["zp_file_sha256"] == deep.file_sha256
    assert tuple(payload["block_checksums"]) == (
        "arrays",
        "core_chromatograms",
        "core_precursors",
        "core_runs",
        "core_spectra",
        "extensions",
        "global_meta",
        "indexes",
        "string_pool",
    )
    assert not {
        "absolute_path",
        "created_at",
        "timestamp",
        "uuid",
        "username",
        "temporary_directory",
    } & set(payload)

    quick = validate_zp(path, mode="quick", certificate_path=certificate)
    assert quick.valid is True
    assert quick.certificate_valid is True
    assert quick.deep_validation_reused is True

    raw = bytearray(path.read_bytes())
    raw[8] ^= 1  # created_at: structurally legal and outside every block checksum
    path.write_bytes(raw)
    changed = validate_zp(path, mode="quick", certificate_path=certificate)
    assert changed.valid is False
    assert changed.certificate_valid is False
    assert changed.issues[-1].code == "DEEP_VALIDATION_CERTIFICATE_FILE_MISMATCH"


def test_quick_rejects_incompatible_certificate_version(tmp_path: Path) -> None:
    path = tmp_path / "version.zp"
    certificate = tmp_path / "acceptance.json"
    build_complete_v2(path)
    assert validate_zp(path, mode="deep", certificate_path=certificate).valid is True
    payload = json.loads(certificate.read_text(encoding="utf-8"))
    payload["validator_version"] = "future-contract"
    certificate.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_zp(path, mode="quick", certificate_path=certificate)

    assert result.valid is False
    assert result.issues[-1].code == "DEEP_VALIDATION_CERTIFICATE_VERSION_INCOMPATIBLE"


@pytest.mark.parametrize("format_version", [1, 2])
def test_quick_is_compatible_with_v1_and_v2(tmp_path: Path, format_version: int) -> None:
    path = tmp_path / f"v{format_version}.zp"
    ZpWriter().write(
        path,
        build_real_blocks("accept_ms1_only_indexed_float64_zlib.mzML"),
        format_version=format_version,
        created_at_millis=0,
    )

    result = validate_zp(path, mode="quick")

    assert result.valid is True
    assert result.version == format_version
    assert result.checked_blocks == 9


def test_invalid_validation_mode_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="quick.*deep"):
        validate_zp(tmp_path / "unused.zp", mode="sample")
