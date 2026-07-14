from __future__ import annotations

from dataclasses import replace

import pytest

from binary_layer.mzml_admission import (
    AuxiliaryArrayFeature,
    ChromatogramFeature,
    MzmlFeatureProfile,
    SpectrumFeature,
    evaluate_mzml_admission,
    normalize_time_seconds,
)


def spectrum(ms_level: int = 1) -> SpectrumFeature:
    return SpectrumFeature(
        native_id=f"controllerType=0 controllerNumber=1 scan={ms_level}",
        scan_number=ms_level,
        scan_number_proven=True,
        ms_level=ms_level,
        rt_value=0.5,
        rt_unit_accession="UO:0000031",
        rt_unit_name="minute",
        representation="centroid",
        polarity="positive",
        precursor_count=1 if ms_level == 2 else 0,
        selected_ion_count=1 if ms_level == 2 else 0,
        selected_ion_mz=445.2 if ms_level == 2 else None,
        charge=2 if ms_level == 2 else None,
        selected_ion_intensity=50.0 if ms_level == 2 else None,
        has_mz_array=True,
        has_intensity_array=True,
        mz_array_length=2,
        intensity_array_length=2,
        mz_dtype="float64",
        intensity_dtype="float64",
        mz_compression="zlib",
        intensity_compression="zlib",
        arrays_are_finite=True,
        minimum_mz=100.0,
    )


def chromatogram(kind: str = "tic") -> ChromatogramFeature:
    return ChromatogramFeature(
        chromatogram_id=kind.upper(),
        chromatogram_type=kind,
        time_array_length=2,
        intensity_array_length=2,
        time_dtype="float64",
        intensity_dtype="float64",
        time_compression="zlib",
        intensity_compression="zlib",
        time_unit_accession="UO:0000010",
        time_unit_name="second",
    )


def profile(*, indexed: bool = True, spectra: tuple[SpectrumFeature, ...] | None = None, chromatograms: tuple[ChromatogramFeature, ...] = (), run_count: int = 1) -> MzmlFeatureProfile:
    return MzmlFeatureProfile(
        indexed=indexed,
        run_count=run_count,
        mzml_version="1.1.0",
        native_id_format_accession="MS:1000768",
        native_id_format_name="Thermo nativeID format",
        spectra=spectra or (spectrum(1), spectrum(2)),
        chromatograms=chromatograms,
    )


def codes(value: MzmlFeatureProfile) -> set[str]:
    return {item.code for item in evaluate_mzml_admission(value).issues}


def test_minimal_indexed_profile_is_accepted() -> None:
    assert evaluate_mzml_admission(profile(indexed=True)).accepted is True


def test_minimal_nonindexed_profile_is_accepted() -> None:
    assert evaluate_mzml_admission(profile(indexed=False)).accepted is True


def test_minute_rt_normalizes_to_seconds() -> None:
    assert normalize_time_seconds(1.5, "UO:0000031", "minute") == 90.0


def test_second_rt_is_accepted_without_scaling() -> None:
    assert normalize_time_seconds(1.5, "UO:0000010", "second") == 1.5


@pytest.mark.parametrize(("change", "code"), [
    ({"scan_number": None}, "MISSING_SCAN_NUMBER"),
    ({"scan_number": 0, "scan_number_proven": False}, "UNPROVEN_SCAN_NUMBER"),
    ({"rt_value": None}, "MISSING_RT"),
    ({"rt_unit_accession": None, "rt_unit_name": None}, "UNSUPPORTED_RT_UNIT"),
    ({"ms_level": 3}, "UNSUPPORTED_MS_LEVEL"),
    ({"representation": "profile"}, "PROFILE_SPECTRUM_UNSUPPORTED"),
    ({"has_dia_semantics": True}, "DIA_SPECTRUM_UNSUPPORTED"),
    ({"has_ion_mobility": True}, "ION_MOBILITY_UNSUPPORTED"),
    ({"has_mz_array": False, "mz_array_length": None, "mz_dtype": None, "mz_compression": None}, "MISSING_MZ_ARRAY"),
    ({"has_intensity_array": False, "intensity_array_length": None, "intensity_dtype": None, "intensity_compression": None}, "MISSING_INTENSITY_ARRAY"),
    ({"mz_array_length": 0, "intensity_array_length": 0}, "EMPTY_SPECTRUM_ARRAY"),
    ({"intensity_array_length": 3}, "ARRAY_LENGTH_MISMATCH"),
    ({"arrays_are_finite": False}, "NONFINITE_ARRAY_VALUE"),
    ({"minimum_mz": -0.1}, "NEGATIVE_MZ_VALUE"),
    ({"mz_dtype": "float16"}, "UNSUPPORTED_ARRAY_DTYPE"),
    ({"mz_compression": "numpress"}, "UNSUPPORTED_ARRAY_COMPRESSION"),
])
def test_spectrum_rejection_rules_are_stable(change: dict[str, object], code: str) -> None:
    changed = replace(spectrum(1), **change)
    assert code in codes(profile(spectra=(changed,)))


def test_source_index_cannot_substitute_for_scan_number() -> None:
    item = replace(spectrum(1), scan_number=0, scan_number_proven=False)
    result_codes = codes(profile(spectra=(item,)))
    assert "UNPROVEN_SCAN_NUMBER" in result_codes
    assert evaluate_mzml_admission(profile(spectra=(item,))).accepted is False


def test_ms1_precursor_is_rejected() -> None:
    assert "MS1_PRECURSOR_UNSUPPORTED" in codes(profile(spectra=(replace(spectrum(1), precursor_count=1),)))


@pytest.mark.parametrize(("change", "code"), [
    ({"precursor_count": 0}, "MISSING_PRECURSOR"),
    ({"precursor_count": 2}, "MULTIPLE_PRECURSORS_UNSUPPORTED"),
    ({"selected_ion_count": 0}, "MISSING_SELECTED_ION"),
    ({"selected_ion_count": 2}, "MULTIPLE_SELECTED_IONS_UNSUPPORTED"),
    ({"selected_ion_mz": None}, "MISSING_SELECTED_ION_MZ"),
    ({"charge": None}, "MISSING_PRECURSOR_CHARGE"),
    ({"charge": 0}, "MISSING_PRECURSOR_CHARGE"),
    ({"charge": -1}, "MISSING_PRECURSOR_CHARGE"),
    ({"selected_ion_intensity": None}, "MISSING_SELECTED_ION_INTENSITY"),
])
def test_ms2_rejection_rules_are_stable(change: dict[str, object], code: str) -> None:
    assert code in codes(profile(spectra=(replace(spectrum(2), **change),)))


def test_unknown_auxiliary_array_is_rejected() -> None:
    auxiliary = AuxiliaryArrayFeature("MS:1000786", "vendor mystery", "int64", "UO:0000186", "dimensionless unit")
    assert "UNSUPPORTED_AUXILIARY_ARRAY" in codes(profile(spectra=(replace(spectrum(1), auxiliary_arrays=(auxiliary,)),)))


def test_no_chromatogram_is_accepted() -> None:
    assert evaluate_mzml_admission(profile(chromatograms=())).accepted is True


@pytest.mark.parametrize("kind", ["tic", "bpc"])
def test_tic_and_bpc_are_accepted(kind: str) -> None:
    assert evaluate_mzml_admission(profile(chromatograms=(chromatogram(kind),))).accepted is True


@pytest.mark.parametrize("kind", ["srm", "mrm", "sic", "unknown"])
def test_unsupported_chromatogram_types_are_rejected(kind: str) -> None:
    assert "UNSUPPORTED_CHROMATOGRAM_TYPE" in codes(profile(chromatograms=(chromatogram(kind),)))


def test_chromatogram_precursor_or_product_semantics_are_rejected() -> None:
    item = replace(chromatogram(), has_precursor_semantics=True, has_product_semantics=True)
    assert "UNSUPPORTED_CHROMATOGRAM_SEMANTICS" in codes(profile(chromatograms=(item,)))


def test_chromatogram_length_mismatch_is_rejected() -> None:
    item = replace(chromatogram(), intensity_array_length=3)
    assert "CHROMATOGRAM_ARRAY_LENGTH_MISMATCH" in codes(profile(chromatograms=(item,)))


def test_chromatogram_unknown_time_unit_is_rejected() -> None:
    item = replace(chromatogram(), time_unit_accession=None, time_unit_name=None)
    assert "UNSUPPORTED_RT_UNIT" in codes(profile(chromatograms=(item,)))


def test_whitelisted_chromatogram_auxiliary_array_is_accepted() -> None:
    auxiliary = AuxiliaryArrayFeature("MS:1000786", "ms level", "int64", "UO:0000186", "dimensionless unit")
    item = replace(chromatogram(), auxiliary_arrays=(auxiliary,))
    assert evaluate_mzml_admission(profile(chromatograms=(item,))).accepted is True


def test_multiple_runs_are_rejected() -> None:
    assert "MULTIPLE_RUNS_UNSUPPORTED" in codes(profile(run_count=2))
