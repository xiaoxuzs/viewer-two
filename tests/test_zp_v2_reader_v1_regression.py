from __future__ import annotations

from pathlib import Path

from binary_layer import ZpReader, ZpWriter
from zp_v2_reader_support import build_complete_v2


def test_v1_reader_results_remain_identical_across_all_array_apis(tmp_path: Path) -> None:
    v2_path = tmp_path / "source-v2.zp"
    v1_path = tmp_path / "regression-v1.zp"
    blocks = build_complete_v2(v2_path)
    ZpWriter().write(v1_path, blocks)
    reader = ZpReader(v1_path)

    assert reader.read_header().version == 1
    assert [entry.encoding for entry in reader.read_directory()] == ["json"] * 9
    assert reader.read_arrays() == blocks.arrays
    assert reader.read_array(blocks.arrays[0].array_id) == blocks.arrays[0]
    assert reader.read_spectrum(blocks.spectra[0].spectrum_id) == blocks.spectra[0]
    assert reader.read_spectrum_arrays(blocks.spectra[1].spectrum_id) == (
        blocks.spectra[1],
        next(item for item in blocks.arrays if item.array_id == blocks.spectra[1].mz_array_id),
        next(item for item in blocks.arrays if item.array_id == blocks.spectra[1].intensity_array_id),
    )
    assert reader.read_chromatograms() == blocks.chromatograms
    assert reader.read_chromatogram_arrays(blocks.chromatograms[0].chromatogram_id)[0] == blocks.chromatograms[0]

