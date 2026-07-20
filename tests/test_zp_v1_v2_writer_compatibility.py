from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from binary_layer import ZpWriter
from specs.zp_full.inspect_full_zp import inspect_full_zp
from zp_compatibility_support import FIXED_EPOCH_SECONDS, build_full_blocks


def test_default_and_explicit_v1_are_byte_identical_and_explicit_v2_is_versioned(tmp_path: Path) -> None:
    blocks = build_full_blocks()
    before = deepcopy(blocks)
    default_path = tmp_path / "default.zp"
    v1_path = tmp_path / "explicit-v1.zp"
    v2_path = tmp_path / "explicit-v2.zp"
    with patch("binary_layer.writer.time.time", return_value=FIXED_EPOCH_SECONDS):
        ZpWriter().write(default_path, blocks)
        ZpWriter().write(v1_path, blocks, format_version=1)
        ZpWriter().write(v2_path, blocks, format_version=2)

    v1 = inspect_full_zp(v1_path)
    v2 = inspect_full_zp(v2_path)
    assert default_path.read_bytes() == v1_path.read_bytes()
    assert v1["header"]["version"] == v1["blocks"]["global_meta"]["format_version"] == 1
    assert {item["encoding"] for item in v1["directory"]} == {"json"}
    assert v2["header"]["version"] == v2["blocks"]["global_meta"]["format_version"] == 2
    assert next(item for item in v2["directory"] if item["block_name"] == "arrays")["encoding"] == "zp-arrays-v2"
    assert all(
        item["encoding"] == "utf-8-json"
        for item in v2["directory"]
        if item["block_name"] != "arrays"
    )
    assert blocks == before
