from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from binary_layer import ZpReader, ZpValidator
from zp_compatibility_support import mutate_header, mutate_top_directory


FIXTURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


CASES = (
    ("v1_json_json", "valid_full_v1.zp", None, None, True),
    ("v2_utf8_binary", "valid_full_v2.zp", None, None, True),
    ("v1_utf8_binary", "valid_full_v2.zp", 1, None, False),
    ("v1_json_binary", "valid_full_v1.zp", None, {"arrays": "zp-arrays-v2"}, False),
    ("v2_json_json", "valid_full_v1.zp", 2, None, False),
    ("v2_utf8_json", "valid_full_v2.zp", None, {"arrays": "json"}, False),
    ("v2_json_binary", "valid_full_v2.zp", None, {"non_arrays": "json"}, False),
    ("unknown", "valid_full_v1.zp", 999, None, False),
)


@pytest.mark.parametrize(("_name", "source", "header_version", "encodings", "accepted"), CASES, ids=[item[0] for item in CASES])
def test_reader_and_validator_version_encoding_matrix(
    _name: str,
    source: str,
    header_version: int | None,
    encodings: dict[str, str] | None,
    accepted: bool,
    tmp_path: Path,
) -> None:
    path = tmp_path / "case.zp"
    shutil.copyfile(FIXTURE_DIR / source, path)
    if header_version is not None:
        mutate_header(path, lambda header: header.__setitem__(1, header_version))
    if encodings:
        def change(directory) -> None:
            for entry in directory:
                if entry["block_name"] == "arrays" and "arrays" in encodings:
                    entry["encoding"] = encodings["arrays"]
                elif entry["block_name"] != "arrays" and "non_arrays" in encodings:
                    entry["encoding"] = encodings["non_arrays"]
        mutate_top_directory(path, change)

    validation = ZpValidator().validate(path)
    if accepted:
        assert ZpReader(path).read_arrays()
        assert validation.valid is True
        assert validation.issues == []
    else:
        with pytest.raises(Exception):
            ZpReader(path).read_arrays()
        assert validation.valid is False
        assert validation.issues
