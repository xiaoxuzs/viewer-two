from __future__ import annotations

import importlib.util
from importlib.metadata import version
from pathlib import Path

import numpy as np
from packaging.version import Version
from pyteomics import mzml

from binary_layer.mzml_admission import evaluate_mzml_admission
from mzml_test_support import build_feature_profile, inspect_xml

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


def test_pyteomics_version_and_public_reader_gate() -> None:
    installed = Version(version("pyteomics"))
    assert Version("4.7.5") <= installed < Version("5")
    assert callable(mzml.MzML)


def test_indexed_and_nonindexed_fixtures_open() -> None:
    indexed_path = FIXTURE_DIR / "accept_indexed_float64_zlib.mzML"
    with mzml.MzML(str(indexed_path), use_index=True, decode_binary=True) as reader:
        record = reader.get_by_id("controllerType=0 controllerNumber=1 scan=2")
        assert record["ms level"] == 2
    nonindexed_path = FIXTURE_DIR / "accept_nonindexed_float32_uncompressed.mzML"
    with mzml.MzML(str(nonindexed_path), use_index=True, decode_binary=True) as reader:
        assert [record["ms level"] for record in reader] == [1, 2]


def test_float64_zlib_arrays_decode_exactly() -> None:
    path = FIXTURE_DIR / "accept_indexed_float64_zlib.mzML"
    with mzml.MzML(str(path), use_index=True, decode_binary=True) as reader:
        first = next(reader)
    assert first["m/z array"].dtype == np.dtype("float64")
    assert first["intensity array"].dtype == np.dtype("float64")
    assert first["m/z array"].tolist() == [100.0, 200.0]
    xml = inspect_xml(path)
    assert {array.compression for owner in xml.spectra.values() for array in owner.arrays} == {"zlib"}


def test_float32_uncompressed_arrays_decode_exactly() -> None:
    path = FIXTURE_DIR / "accept_nonindexed_float32_uncompressed.mzML"
    with mzml.MzML(str(path), use_index=True, decode_binary=True) as reader:
        first = next(reader)
    assert first["m/z array"].dtype == np.dtype("float32")
    assert first["intensity array"].dtype == np.dtype("float32")
    assert first["m/z array"].tolist() == [100.0, 200.0]
    xml = inspect_xml(path)
    assert {array.compression for owner in xml.spectra.values() for array in owner.arrays} == {"none"}


def test_ms_counts_rt_precursor_and_selected_ion_shape() -> None:
    path = FIXTURE_DIR / "accept_indexed_float64_zlib.mzML"
    with mzml.MzML(str(path), use_index=True, decode_binary=True) as reader:
        records = list(reader)
    assert [item["ms level"] for item in records] == [1, 2]
    rt = records[0]["scanList"]["scan"][0]["scan start time"]
    assert float(rt) == 0.5
    assert rt.unit_info == "minute"
    precursors = records[1]["precursorList"]["precursor"]
    ions = precursors[0]["selectedIonList"]["selectedIon"]
    assert len(precursors) == len(ions) == 1
    assert ions[0]["charge state"] == 2
    assert ions[0]["peak intensity"] == 50.0


def test_missing_and_zero_selected_ion_values_remain_distinguishable(tmp_path: Path) -> None:
    missing_path = FIXTURE_DIR / "reject_missing_charge.mzML"
    with mzml.MzML(str(missing_path), decode_binary=True) as reader:
        missing_ion = list(reader)[1]["precursorList"]["precursor"][0]["selectedIonList"]["selectedIon"][0]
    assert "charge state" not in missing_ion

    script = FIXTURE_DIR / "build_fixtures.py"
    spec = importlib.util.spec_from_file_location("fixture_builder", script)
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    spectra = [builder.spectrum(0, ms_level=1), builder.spectrum(1, ms_level=2, charge=0, selected_ion_intensity=0.0)]
    zero_path = tmp_path / "zero_values.mzML"
    zero_path.write_text(builder.mzml_document("zero_values", spectra, []), encoding="utf-8")
    with mzml.MzML(str(zero_path), decode_binary=True) as reader:
        zero_ion = list(reader)[1]["precursorList"]["precursor"][0]["selectedIonList"]["selectedIon"][0]
    assert "charge state" in zero_ion
    assert zero_ion["charge state"] is None
    assert zero_ion["peak intensity"] == 0.0


def test_tic_bpc_and_auxiliary_array_are_publicly_observable() -> None:
    path = FIXTURE_DIR / "accept_tic_bpc_chromatograms.mzML"
    xml = inspect_xml(path)
    assert {item.chromatogram_type for item in xml.chromatograms.values()} == {"tic", "bpc"}
    tic_xml = xml.chromatograms["TIC"]
    auxiliary = next(item for item in tic_xml.arrays if item.kind == "auxiliary")
    assert (auxiliary.accession, auxiliary.name, auxiliary.dtype) == ("MS:1000786", "ms level", "int64")
    with mzml.MzML(str(path), decode_binary=True) as reader:
        chromatograms = {item["id"]: item for item in reader.iterfind("chromatogram")}
    assert "total ion current chromatogram" in chromatograms["TIC"]
    assert "basepeak chromatogram" in chromatograms["BPC"]
    assert chromatograms["TIC"]["ms level"].dtype == np.dtype("int64")
    assert chromatograms["TIC"]["ms level"].tolist() == [1, 2]


def test_test_side_profile_extraction_is_accepted_without_library_dict_leakage() -> None:
    profile = build_feature_profile(FIXTURE_DIR / "accept_tic_bpc_chromatograms.mzML")
    result = evaluate_mzml_admission(profile)
    assert result.accepted is True
    assert not hasattr(profile, "keys")
    assert all(not hasattr(item, "keys") for item in (*profile.spectra, *profile.chromatograms))
