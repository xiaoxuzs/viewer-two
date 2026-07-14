from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from binary_layer import ZpReader, ZpValidator, ZpWriter
from binary_layer.exceptions import ZpWriteError
from specs.zp_v2.arrays_reference_codec import decode_arrays_block, validate_arrays_block
from zp_v2_writer_support import parse_arrays_block, parse_v2_file


EXPECTED_BLOCKS = [
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


def test_complete_v2_layout_checksums_global_meta_and_input_immutability(
    pipeline_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = pipeline_factory(".mzML").blocks
    before = copy.deepcopy(blocks)
    monkeypatch.setattr("binary_layer.writer.time.time", lambda: 1_700_000_000.125)
    target = tmp_path / "complete-v2.zp"

    ZpWriter().write(target, blocks, format_version=2)

    parsed = parse_v2_file(target)
    assert parsed["header"] == (b"ZPMS", 2, 1, 0, 1_700_000_000_125, parsed["header"][-1])
    assert [entry["block_name"] for entry in parsed["directory"]] == EXPECTED_BLOCKS
    assert [entry["encoding"] for entry in parsed["directory"]] == [
        "utf-8-json",
        "utf-8-json",
        "utf-8-json",
        "utf-8-json",
        "utf-8-json",
        "utf-8-json",
        "zp-arrays-v2",
        "utf-8-json",
        "utf-8-json",
    ]
    global_meta = json.loads(parsed["payloads"]["global_meta"].decode("utf-8"))
    assert global_meta["format_version"] == 2
    arrays = parse_arrays_block(parsed["payloads"]["arrays"])
    assert arrays["header"][0:6] == (b"ZPARRV2\0", 2, 1, 0, len(blocks.arrays), 64)
    assert validate_arrays_block(parsed["payloads"]["arrays"]).valid is True
    assert len(decode_arrays_block(parsed["payloads"]["arrays"]).arrays) == len(blocks.arrays)
    assert blocks == before


def test_production_reader_and_validator_succeed_for_written_v2(
    pipeline_factory, tmp_path: Path
) -> None:
    target = tmp_path / "boundary.zp"
    ZpWriter().write(target, pipeline_factory(".mzML").blocks, format_version=2)
    assert ZpReader(target).read_header().version == 2
    assert ZpReader(target).read_arrays()
    result = ZpValidator().validate(target)
    assert result.valid is True
    assert result.issues == []
    assert result.checked_blocks == 9


@pytest.mark.parametrize("mutation", ["missing_array", "bad_precursor", "missing_pool", "bad_index"])
def test_v2_rejects_incomplete_or_broken_logical_blocks_before_io(
    pipeline_factory, tmp_path: Path, mutation: str
) -> None:
    blocks = pipeline_factory(".mzML").blocks
    if mutation == "missing_array":
        blocks.arrays.pop()
        blocks.global_meta.array_count -= 1
    elif mutation == "bad_precursor":
        blocks.precursors[0].spectrum_id = "missing"
    elif mutation == "missing_pool":
        blocks.string_pool = None
    else:
        blocks.indexes.scan_index[0]["spectrum_id"] = "missing"
    target = tmp_path / mutation / "bad.zp"
    with pytest.raises(ZpWriteError):
        ZpWriter().write(target, blocks, format_version=2)
    assert not target.parent.exists()
