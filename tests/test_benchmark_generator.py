from __future__ import annotations

from pathlib import Path

from pyteomics import mzml

from benchmarks.generate_scale_mzml import generate_scale_mzml
from binary_layer.mzml_adapter import parse_mzml


def test_scale_generator_is_deterministic_and_pyteomics_readable(tmp_path: Path) -> None:
    first = generate_scale_mzml(tmp_path / "first.mzML", spectrum_count=8, ms2_ratio=0.5, peaks_per_spectrum=16, include_tic=True, dtype="float64", compression="zlib", indexed=True)
    second = generate_scale_mzml(tmp_path / "second.mzML", spectrum_count=8, ms2_ratio=0.5, peaks_per_spectrum=16, include_tic=True, dtype="float64", compression="zlib", indexed=True)
    assert first.read_bytes() == second.read_bytes()
    with mzml.MzML(str(first), use_index=True) as reader:
        records = list(reader)
        chromatograms = list(reader.iterfind("chromatogram"))
    assert len(records) == 8
    assert [int(item["ms level"]) for item in records].count(2) == 4
    assert sum(len(item["m/z array"]) for item in records) == 128
    assert len(chromatograms) == 1
    document = parse_mzml(first)
    assert len(document.spectra) == 8
    assert sum(item.precursor_count for item in document.spectra) == 4
    assert document.feature_profile.indexed is True


def test_scale_generator_supports_float32_uncompressed_nonindexed_without_zp(tmp_path: Path) -> None:
    output = generate_scale_mzml(tmp_path / "small.mzML", spectrum_count=4, ms2_ratio=0.25, peaks_per_spectrum=3, include_tic=False, dtype="float32", compression="none", indexed=False)
    document = parse_mzml(output)
    assert len(document.spectra) == 4
    assert not document.chromatograms
    assert document.feature_profile.indexed is False
    assert {item.source_mz_dtype for item in document.spectra} == {"float32"}
    assert list(tmp_path.glob("*.zp")) == []

