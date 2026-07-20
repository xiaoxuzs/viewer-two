from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .exceptions import MzmlAdmissionError
from .mzml_schema import NumericDtype, OwnerKind, auxiliary_array_is_supported

SUPPORTED_MZML_VERSIONS = frozenset({"1.1.0", "1.1.1"})
SUPPORTED_CORE_DTYPES = frozenset({"float32", "float64"})
SUPPORTED_COMPRESSIONS = frozenset({"none", "zlib"})


class AdmissionSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class AuxiliaryArrayFeature:
    accession: str
    name: str
    dtype: str
    unit_accession: str | None
    unit_name: str | None


@dataclass(frozen=True, slots=True)
class SpectrumFeature:
    native_id: str
    scan_number: int | None
    scan_number_proven: bool
    ms_level: int
    rt_value: float | None
    rt_unit_accession: str | None
    rt_unit_name: str | None
    representation: str
    polarity: str | None
    precursor_count: int
    selected_ion_count: int
    selected_ion_mz: float | None
    charge: int | None
    selected_ion_intensity: float | None
    has_mz_array: bool
    has_intensity_array: bool
    mz_array_length: int | None
    intensity_array_length: int | None
    mz_dtype: str | None
    intensity_dtype: str | None
    mz_compression: str | None
    intensity_compression: str | None
    arrays_are_finite: bool
    minimum_mz: float | None
    auxiliary_arrays: tuple[AuxiliaryArrayFeature, ...] = ()
    has_dia_semantics: bool = False
    has_ion_mobility: bool = False
    isolation_target_mz: float | None = None
    isolation_lower_offset: float | None = None
    isolation_upper_offset: float | None = None
    charge_present: bool = False


@dataclass(frozen=True, slots=True)
class ChromatogramFeature:
    chromatogram_id: str
    chromatogram_type: str
    time_array_length: int | None
    intensity_array_length: int | None
    time_dtype: str | None
    intensity_dtype: str | None
    time_compression: str | None
    intensity_compression: str | None
    time_unit_accession: str | None
    time_unit_name: str | None
    auxiliary_arrays: tuple[AuxiliaryArrayFeature, ...] = ()
    has_precursor_semantics: bool = False
    has_product_semantics: bool = False


@dataclass(frozen=True, slots=True)
class MzmlFeatureProfile:
    indexed: bool
    run_count: int
    mzml_version: str
    native_id_format_accession: str | None
    native_id_format_name: str | None
    spectra: tuple[SpectrumFeature, ...]
    chromatograms: tuple[ChromatogramFeature, ...]

    def __post_init__(self) -> None:
        if type(self.indexed) is not bool:
            raise MzmlAdmissionError("profile.indexed must be a boolean")
        if type(self.run_count) is not int or self.run_count < 0:
            raise MzmlAdmissionError("profile.run_count must be a non-negative integer")
        if type(self.mzml_version) is not str or not self.mzml_version:
            raise MzmlAdmissionError("profile.mzml_version must be a non-empty string")
        for value, name in ((self.native_id_format_accession, "accession"), (self.native_id_format_name, "name")):
            if value is not None and type(value) is not str:
                raise MzmlAdmissionError(f"profile.native_id_format_{name} must be a string or null")
        if type(self.spectra) is not tuple or any(not isinstance(item, SpectrumFeature) for item in self.spectra):
            raise MzmlAdmissionError("profile.spectra must contain SpectrumFeature values")
        if type(self.chromatograms) is not tuple or any(not isinstance(item, ChromatogramFeature) for item in self.chromatograms):
            raise MzmlAdmissionError("profile.chromatograms must contain ChromatogramFeature values")


@dataclass(frozen=True, slots=True)
class MzmlAdmissionIssue:
    code: str
    message: str
    location: str
    severity: AdmissionSeverity


@dataclass(frozen=True, slots=True)
class MzmlAdmissionResult:
    accepted: bool
    issues: tuple[MzmlAdmissionIssue, ...]
    warnings: tuple[MzmlAdmissionIssue, ...]


ADMISSION_ISSUE_CODES = frozenset({
    "UNSUPPORTED_MZML_VERSION",
    "MULTIPLE_RUNS_UNSUPPORTED",
    "MISSING_SCAN_NUMBER",
    "UNPROVEN_SCAN_NUMBER",
    "MISSING_RT",
    "UNSUPPORTED_RT_UNIT",
    "UNSUPPORTED_MS_LEVEL",
    "PROFILE_SPECTRUM_UNSUPPORTED",
    "DIA_SPECTRUM_UNSUPPORTED",
    "ION_MOBILITY_UNSUPPORTED",
    "MISSING_MZ_ARRAY",
    "MISSING_INTENSITY_ARRAY",
    "EMPTY_SPECTRUM_ARRAY",
    "ARRAY_LENGTH_MISMATCH",
    "NONFINITE_ARRAY_VALUE",
    "NEGATIVE_MZ_VALUE",
    "UNSUPPORTED_ARRAY_DTYPE",
    "UNSUPPORTED_ARRAY_COMPRESSION",
    "UNSUPPORTED_AUXILIARY_ARRAY",
    "MS1_PRECURSOR_UNSUPPORTED",
    "MISSING_PRECURSOR",
    "MULTIPLE_PRECURSORS_UNSUPPORTED",
    "MISSING_SELECTED_ION",
    "MULTIPLE_SELECTED_IONS_UNSUPPORTED",
    "MISSING_SELECTED_ION_MZ",
    "MISSING_PRECURSOR_CHARGE",
    "MISSING_SELECTED_ION_INTENSITY",
    "UNSUPPORTED_CHROMATOGRAM_TYPE",
    "MISSING_CHROMATOGRAM_ARRAY",
    "CHROMATOGRAM_ARRAY_LENGTH_MISMATCH",
    "UNSUPPORTED_CHROMATOGRAM_SEMANTICS",
    "DIA_WINDOW_MALFORMED",
    "DIA_SELECTED_PRECURSOR_CONFLICT",
    "DIA_CHROMATOGRAM_PRESERVED_ONLY",
})


def time_unit_scale(unit_accession: str | None, unit_name: str | None) -> float | None:
    if unit_accession == "UO:0000010" and unit_name in {"second", "seconds"}:
        return 1.0
    if unit_accession == "UO:0000031" and unit_name in {"minute", "minutes"}:
        return 60.0
    return None


def normalize_time_seconds(value: float, unit_accession: str | None, unit_name: str | None) -> float:
    scale = time_unit_scale(unit_accession, unit_name)
    if scale is None:
        raise MzmlAdmissionError(f"unsupported time unit: {unit_accession!r} {unit_name!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise MzmlAdmissionError("time value must be a finite non-negative number")
    return float(value) * scale


def evaluate_mzml_admission(
    profile: MzmlFeatureProfile,
    *,
    acquisition_mode: str = "dda",
) -> MzmlAdmissionResult:
    if not isinstance(profile, MzmlFeatureProfile):
        raise MzmlAdmissionError("admission input must be MzmlFeatureProfile")
    if acquisition_mode not in {"dda", "dia"}:
        raise MzmlAdmissionError("acquisition_mode must be 'dda' or 'dia'")
    issues: list[MzmlAdmissionIssue] = []
    warnings: list[MzmlAdmissionIssue] = []

    def reject(code: str, message: str, location: str) -> None:
        if code not in ADMISSION_ISSUE_CODES:
            raise MzmlAdmissionError(f"unregistered admission issue code: {code}")
        issues.append(MzmlAdmissionIssue(code, message, location, AdmissionSeverity.ERROR))

    if profile.mzml_version not in SUPPORTED_MZML_VERSIONS:
        reject("UNSUPPORTED_MZML_VERSION", f"mzML version {profile.mzml_version!r} is not supported", "file")
    if profile.run_count != 1:
        reject("MULTIPLE_RUNS_UNSUPPORTED", f"exactly one run is required; got {profile.run_count}", "file")

    for index, spectrum in enumerate(profile.spectra):
        location = f"spectrum[{index}]"
        _evaluate_spectrum(spectrum, location, reject, acquisition_mode=acquisition_mode)

    for index, chromatogram in enumerate(profile.chromatograms):
        location = f"chromatogram[{index}]"
        if acquisition_mode == "dia" and chromatogram.chromatogram_type not in {"tic", "bpc"}:
            warnings.append(
                MzmlAdmissionIssue(
                    "DIA_CHROMATOGRAM_PRESERVED_ONLY",
                    "Non-TIC/BPC chromatogram is recorded as preserved-only source metadata",
                    location,
                    AdmissionSeverity.WARNING,
                )
            )
            continue
        _evaluate_chromatogram(chromatogram, location, reject)

    return MzmlAdmissionResult(not issues, tuple(issues), tuple(warnings))


def _evaluate_spectrum(
    spectrum: SpectrumFeature,
    location: str,
    reject: object,
    *,
    acquisition_mode: str,
) -> None:
    if spectrum.ms_level not in {1, 2}:
        reject("UNSUPPORTED_MS_LEVEL", f"only MS1 and MS2 are supported; got MS{spectrum.ms_level}", location)
    if spectrum.representation != "centroid":
        reject("PROFILE_SPECTRUM_UNSUPPORTED", f"only centroid spectra are supported; got {spectrum.representation!r}", location)
    if spectrum.has_dia_semantics and acquisition_mode == "dda":
        reject("DIA_SPECTRUM_UNSUPPORTED", "DIA spectrum semantics are outside P1-B", location)
    if spectrum.has_ion_mobility:
        reject("ION_MOBILITY_UNSUPPORTED", "ion-mobility semantics are outside P1-B", location)
    if spectrum.scan_number is None:
        reject("MISSING_SCAN_NUMBER", "scan number is absent", location)
    elif not spectrum.scan_number_proven:
        reject("UNPROVEN_SCAN_NUMBER", "scan number is not proven by nativeID semantics", location)
    if spectrum.rt_value is None:
        reject("MISSING_RT", "scan start time is absent", location)
    elif time_unit_scale(spectrum.rt_unit_accession, spectrum.rt_unit_name) is None:
        reject("UNSUPPORTED_RT_UNIT", "RT unit must be an explicit, consistent second or minute", location)
    elif isinstance(spectrum.rt_value, bool) or not isinstance(spectrum.rt_value, (int, float)) or not math.isfinite(spectrum.rt_value) or spectrum.rt_value < 0:
        reject("MISSING_RT", "RT must be a finite non-negative number", location)

    if not spectrum.has_mz_array:
        reject("MISSING_MZ_ARRAY", "m/z array is absent", location)
    if not spectrum.has_intensity_array:
        reject("MISSING_INTENSITY_ARRAY", "intensity array is absent", location)
    if spectrum.has_mz_array and spectrum.has_intensity_array:
        if spectrum.mz_array_length is None or spectrum.intensity_array_length is None or spectrum.mz_array_length <= 0 or spectrum.intensity_array_length <= 0:
            reject("EMPTY_SPECTRUM_ARRAY", "supported spectrum arrays must be nonempty", location)
        elif spectrum.mz_array_length != spectrum.intensity_array_length:
            reject("ARRAY_LENGTH_MISMATCH", "m/z and intensity array lengths differ", location)
    if not spectrum.arrays_are_finite:
        reject("NONFINITE_ARRAY_VALUE", "spectrum arrays contain NaN or Infinity", location)
    if spectrum.minimum_mz is not None and spectrum.minimum_mz < 0:
        reject("NEGATIVE_MZ_VALUE", "m/z values must not be negative", location)
    for label, dtype in (("m/z", spectrum.mz_dtype), ("intensity", spectrum.intensity_dtype)):
        if dtype not in SUPPORTED_CORE_DTYPES:
            reject("UNSUPPORTED_ARRAY_DTYPE", f"{label} dtype {dtype!r} is unsupported", location)
    for label, compression in (("m/z", spectrum.mz_compression), ("intensity", spectrum.intensity_compression)):
        if compression not in SUPPORTED_COMPRESSIONS:
            reject("UNSUPPORTED_ARRAY_COMPRESSION", f"{label} compression {compression!r} is unsupported", location)
    for auxiliary in spectrum.auxiliary_arrays:
        if not _auxiliary_supported(auxiliary, OwnerKind.SPECTRUM):
            reject("UNSUPPORTED_AUXILIARY_ARRAY", f"unsupported spectrum auxiliary array {auxiliary.accession} {auxiliary.name!r}", location)

    if spectrum.ms_level == 1 and spectrum.precursor_count != 0:
        reject("MS1_PRECURSOR_UNSUPPORTED", "MS1 must not carry precursor semantics", location)
    if spectrum.ms_level == 2:
        if spectrum.precursor_count == 0:
            reject("MISSING_PRECURSOR", "MS2 requires one precursor", location)
        elif spectrum.precursor_count != 1:
            reject("MULTIPLE_PRECURSORS_UNSUPPORTED", f"MS2 has {spectrum.precursor_count} precursors", location)
        elif acquisition_mode == "dia":
            _evaluate_dia_precursor(spectrum, location, reject)
        elif spectrum.selected_ion_count == 0:
            reject("MISSING_SELECTED_ION", "MS2 requires one selected ion", location)
        elif spectrum.selected_ion_count != 1:
            reject("MULTIPLE_SELECTED_IONS_UNSUPPORTED", f"MS2 has {spectrum.selected_ion_count} selected ions", location)
        else:
            if spectrum.selected_ion_mz is None:
                reject("MISSING_SELECTED_ION_MZ", "selected-ion m/z is absent", location)
            if spectrum.charge is None or spectrum.charge <= 0:
                reject("MISSING_PRECURSOR_CHARGE", "an explicit positive precursor charge is required", location)
            if spectrum.selected_ion_intensity is None:
                reject("MISSING_SELECTED_ION_INTENSITY", "selected-ion intensity is absent", location)


def _evaluate_dia_precursor(spectrum: SpectrumFeature, location: str, reject: object) -> None:
    if spectrum.selected_ion_count not in {0, 1}:
        reject(
            "DIA_WINDOW_MALFORMED",
            "DIA MS2 may carry at most one source selected-ion descriptor",
            location,
        )
    target = spectrum.isolation_target_mz
    lower_offset = spectrum.isolation_lower_offset
    upper_offset = spectrum.isolation_upper_offset
    values = (target, lower_offset, upper_offset)
    if any(
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in values
    ):
        reject(
            "DIA_WINDOW_MALFORMED",
            "DIA MS2 requires finite isolation target, lower offset, and upper offset",
            location,
        )
        return
    assert target is not None and lower_offset is not None and upper_offset is not None
    if target < 0 or lower_offset < 0 or upper_offset < 0 or lower_offset + upper_offset <= 0:
        reject(
            "DIA_WINDOW_MALFORMED",
            "DIA isolation offsets must be non-negative with positive total width",
            location,
        )
    if target - lower_offset < 0:
        reject(
            "DIA_WINDOW_MALFORMED",
            "DIA isolation lower bound must be non-negative",
            location,
        )
    if spectrum.charge_present or spectrum.charge is not None:
        reject(
            "DIA_SELECTED_PRECURSOR_CONFLICT",
            "DIA isolation-window MS2 must not carry a selected precursor charge",
            location,
        )
    if (
        spectrum.selected_ion_mz is not None
        and not math.isclose(spectrum.selected_ion_mz, target, rel_tol=0.0, abs_tol=1e-9)
    ):
        reject(
            "DIA_SELECTED_PRECURSOR_CONFLICT",
            "DIA source selected-ion m/z conflicts with the isolation-window target",
            location,
        )


def _evaluate_chromatogram(chromatogram: ChromatogramFeature, location: str, reject: object) -> None:
    if chromatogram.chromatogram_type not in {"tic", "bpc"}:
        reject("UNSUPPORTED_CHROMATOGRAM_TYPE", f"only TIC/BPC are supported; got {chromatogram.chromatogram_type!r}", location)
    if chromatogram.has_precursor_semantics or chromatogram.has_product_semantics:
        reject("UNSUPPORTED_CHROMATOGRAM_SEMANTICS", "precursor/product chromatogram semantics are unsupported", location)
    if chromatogram.time_array_length is None or chromatogram.intensity_array_length is None:
        reject("MISSING_CHROMATOGRAM_ARRAY", "time and intensity arrays must both be present", location)
    elif chromatogram.time_array_length != chromatogram.intensity_array_length:
        reject("CHROMATOGRAM_ARRAY_LENGTH_MISMATCH", "time and intensity array lengths differ", location)
    if time_unit_scale(chromatogram.time_unit_accession, chromatogram.time_unit_name) is None:
        reject("UNSUPPORTED_RT_UNIT", "chromatogram time unit must be an explicit, consistent second or minute", location)
    for label, dtype in (("time", chromatogram.time_dtype), ("intensity", chromatogram.intensity_dtype)):
        if dtype not in SUPPORTED_CORE_DTYPES:
            reject("UNSUPPORTED_ARRAY_DTYPE", f"chromatogram {label} dtype {dtype!r} is unsupported", location)
    for label, compression in (("time", chromatogram.time_compression), ("intensity", chromatogram.intensity_compression)):
        if compression not in SUPPORTED_COMPRESSIONS:
            reject("UNSUPPORTED_ARRAY_COMPRESSION", f"chromatogram {label} compression {compression!r} is unsupported", location)
    for auxiliary in chromatogram.auxiliary_arrays:
        if not _auxiliary_supported(auxiliary, OwnerKind.CHROMATOGRAM):
            reject("UNSUPPORTED_AUXILIARY_ARRAY", f"unsupported chromatogram auxiliary array {auxiliary.accession} {auxiliary.name!r}", location)


def _auxiliary_supported(auxiliary: AuxiliaryArrayFeature, owner_kind: OwnerKind) -> bool:
    try:
        dtype = NumericDtype(auxiliary.dtype)
    except ValueError:
        return False
    return auxiliary_array_is_supported(
        auxiliary.accession,
        auxiliary.name,
        owner_kind,
        dtype,
        auxiliary.unit_accession,
        auxiliary.unit_name,
    )
