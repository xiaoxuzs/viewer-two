from __future__ import annotations

import io
import json
from pathlib import Path

from binary_layer.v2_arrays_reader import ZpV2ArraysReader


FIXTURE_DIR = Path(__file__).parents[1] / "specs" / "zp_v2" / "fixtures"


def test_low_level_reader_decodes_every_frozen_golden_array() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))["fixtures"][0]
    raw = (FIXTURE_DIR / manifest["file"]).read_bytes()
    reader = ZpV2ArraysReader()
    stream = io.BytesIO(raw)
    directory = reader.read_directory(stream, block_offset=0, block_length=len(raw))
    assert (directory.directory_length, directory.payload_offset, directory.payload_length) == (688, 752, 72)
    assert [entry.array_id for entry in directory.entries] == [item["array_id"] for item in manifest["arrays"]]
    for expected in manifest["arrays"]:
        array = reader.read_array(stream, block_offset=0, directory=directory, array_id=expected["array_id"])
        assert (array.array_id, array.array_type, array.dtype, array.values) == (
            expected["array_id"], expected["array_type"], "float64", expected["values"]
        )


def test_low_level_reader_accepts_frozen_empty_arrays_block() -> None:
    raw = (FIXTURE_DIR / "valid_empty_arrays_v2.bin").read_bytes()
    directory = ZpV2ArraysReader().read_directory(io.BytesIO(raw), block_offset=0, block_length=len(raw))
    assert directory.entries == ()
    assert (directory.payload_offset, directory.payload_length) == (80, 0)
