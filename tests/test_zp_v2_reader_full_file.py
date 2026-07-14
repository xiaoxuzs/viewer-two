from __future__ import annotations

from pathlib import Path

from binary_layer import ArrayBlock, ZpReader
from zp_v2_reader_support import build_complete_v2


def test_complete_v2_public_reader_api_returns_existing_block_types(tmp_path: Path) -> None:
    path = tmp_path / "complete.zp"
    blocks = build_complete_v2(path)
    reader = ZpReader(path)

    assert reader.read_header().version == 2
    assert [entry.block_name for entry in reader.read_directory()] == [
        "global_meta", "string_pool", "core_runs", "core_spectra", "core_precursors",
        "core_chromatograms", "arrays", "indexes", "extensions",
    ]
    assert reader.read_global_meta().format_version == 2
    assert len(reader.read_runs()) == 1
    assert len(reader.read_spectra()) == 2
    assert len(reader.read_precursors()) == 1
    assert len(reader.read_chromatograms()) == 1
    assert len(reader.read_extensions()) >= 1
    assert reader.read_indexes().spectrum_id_index == blocks.indexes.spectrum_id_index

    single = reader.read_array(blocks.arrays[0].array_id)
    assert isinstance(single, ArrayBlock)
    assert single == blocks.arrays[0]

    spectrum, mz_array, intensity_array = reader.read_spectrum_arrays(blocks.spectra[1].spectrum_id)
    assert spectrum == blocks.spectra[1]
    assert (mz_array.array_id, intensity_array.array_id) == (spectrum.mz_array_id, spectrum.intensity_array_id)

    chromatogram, time_array, chromatogram_intensity = reader.read_chromatogram_arrays("chromatogram_manual")
    assert chromatogram == blocks.chromatograms[0]
    assert (time_array.array_type, chromatogram_intensity.array_type) == ("time", "intensity")
    assert chromatogram_intensity.values == [10.0, -2.5]

    arrays = reader.read_arrays()
    assert arrays == sorted(blocks.arrays, key=lambda item: item.array_id.encode("utf-8"))
    raw_logical_arrays = reader.read_block("arrays")
    assert raw_logical_arrays[0] == {
        "array_id": arrays[0].array_id,
        "array_type": arrays[0].array_type,
        "dtype": "float64",
        "values": arrays[0].values,
    }
