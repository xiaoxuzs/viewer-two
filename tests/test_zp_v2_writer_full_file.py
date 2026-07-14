from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ArrayBlock, ChromatogramBlock, ZpWriter
from specs.zp_v2.arrays_reference_codec import decode_arrays_block, validate_arrays_block
from zp_v2_writer_support import build_real_blocks, parse_v2_file


@pytest.mark.parametrize(
    "fixture_name",
    [
        "accept_ms1_only_nonindexed_float32_uncompressed.mzML",
        "accept_ms2_precursor_metadata.mzML",
        "accept_tic_bpc_chromatograms.mzML",
    ],
)
def test_real_block_collections_write_complete_reference_decodable_v2(
    fixture_name: str, tmp_path: Path
) -> None:
    blocks = build_real_blocks(fixture_name)
    target = tmp_path / f"{Path(fixture_name).stem}.zp"
    ZpWriter().write(target, blocks, format_version=2)
    parsed = parse_v2_file(target)
    arrays_raw = parsed["payloads"]["arrays"]
    result = validate_arrays_block(arrays_raw)
    decoded = decode_arrays_block(arrays_raw)
    assert (result.valid, result.entry_count) == (True, len(blocks.arrays))
    assert {item.array_id: item.values for item in decoded.arrays} == {
        item.array_id: tuple(float(value) for value in item.values) for item in blocks.arrays
    }


def test_small_full_file_contains_spectra_precursor_chromatogram_six_arrays_and_extension(tmp_path: Path) -> None:
    blocks = build_real_blocks("accept_ms2_precursor_metadata.mzML")
    blocks.arrays.extend(
        [
            ArrayBlock("chromatogram_manual:time", "time", "float64", [0.0, 0.125]),
            ArrayBlock("chromatogram_manual:intensity", "intensity", "float64", [10.0, -2.5]),
        ]
    )
    blocks.chromatograms.append(
        ChromatogramBlock(
            "chromatogram_manual",
            blocks.runs[0].run_id,
            "tic",
            "chromatogram_manual:time",
            "chromatogram_manual:intensity",
            "manual chromatogram",
        )
    )
    blocks.global_meta.chromatogram_count = 1
    blocks.global_meta.array_count = len(blocks.arrays)
    blocks.runs[0].chromatogram_count = 1
    target = tmp_path / "all-logical-block-types.zp"

    ZpWriter().write(target, blocks, format_version=2)

    parsed = parse_v2_file(target)
    decoded = decode_arrays_block(parsed["payloads"]["arrays"])
    assert (len(blocks.spectra), len(blocks.precursors), len(blocks.chromatograms), len(blocks.arrays)) == (2, 1, 1, 6)
    assert len(blocks.extensions) >= 1
    assert len(decoded.arrays) == 6
