from __future__ import annotations

import math
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Callable, Mapping, TypeVar

from .exceptions import MzmlSchemaError

MZML_METADATA_EXTENSION_TYPE = "mzml_metadata"
MZML_AUXILIARY_ARRAYS_EXTENSION_TYPE = "mzml_auxiliary_arrays"
MZML_EXTENSION_SCHEMA_VERSION = 1


class Polarity(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class SpectrumRepresentation(str, Enum):
    CENTROID = "centroid"
    PROFILE = "profile"


class ChromatogramType(str, Enum):
    TIC = "tic"
    BPC = "bpc"


class OwnerKind(str, Enum):
    SPECTRUM = "spectrum"
    CHROMATOGRAM = "chromatogram"


class NumericDtype(str, Enum):
    INT32 = "int32"
    INT64 = "int64"
    FLOAT32 = "float32"
    FLOAT64 = "float64"


class ArrayCompression(str, Enum):
    NONE = "none"
    ZLIB = "zlib"


def _error(location: str, message: str) -> MzmlSchemaError:
    return MzmlSchemaError(f"{location}: {message}")


def _require_nonempty(value: object, location: str) -> None:
    if type(value) is not str or not value:
        raise _error(location, "must be a non-empty string")


def _require_optional_string(value: object, location: str) -> None:
    if value is not None and type(value) is not str:
        raise _error(location, "must be a string or null")


def _require_optional_finite(value: object, location: str, *, nonnegative: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _error(location, "must be a finite number or null")
    if nonnegative and value < 0:
        raise _error(location, "must not be negative")


def _require_instance(value: object, expected: type, location: str) -> None:
    if not isinstance(value, expected):
        raise _error(location, f"must be {expected.__name__}")


def _to_json_value(value: object, location: str = "payload") -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _to_json_value(getattr(value, item.name), f"{location}.{item.name}") for item in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_to_json_value(item, f"{location}[]") for item in value]
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _error(location, "must be finite")
        return value
    raise _error(location, f"unsupported non-JSON value type {type(value).__name__}")


def _mapping(payload: object, keys: set[str], location: str) -> Mapping[str, object]:
    if type(payload) is not dict:
        raise _error(location, "must be a plain JSON object")
    actual = set(payload)
    if actual != keys:
        unknown = sorted(actual - keys)
        missing = sorted(keys - actual)
        details = []
        if unknown:
            details.append(f"unknown fields {unknown}")
        if missing:
            details.append(f"missing fields {missing}")
        raise _error(location, "; ".join(details))
    if any(type(key) is not str for key in payload):
        raise _error(location, "field names must be strings")
    return payload


def _string(value: object, location: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str:
        raise _error(location, "must be a string" + (" or null" if optional else ""))
    return value


def _boolean(value: object, location: str) -> bool:
    if type(value) is not bool:
        raise _error(location, "must be a boolean")
    return value


def _integer(value: object, location: str) -> int:
    if type(value) is not int:
        raise _error(location, "must be an integer")
    return value


def _number(value: object, location: str, *, optional: bool = False) -> float | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _error(location, "must be a finite number" + (" or null" if optional else ""))
    return float(value)


E = TypeVar("E", bound=Enum)


def _enum(enum_type: type[E], value: object, location: str, *, optional: bool = False) -> E | None:
    if optional and value is None:
        return None
    if type(value) is not str:
        raise _error(location, "must be an enum string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _error(location, f"unsupported value {value!r}") from exc


def _sequence(value: object, parser: Callable[[object, str], Any], location: str) -> tuple[Any, ...]:
    if type(value) is not list:
        raise _error(location, "must be a JSON array")
    return tuple(parser(item, f"{location}[{index}]") for index, item in enumerate(value))


class PayloadSchema:
    def to_payload(self) -> dict[str, Any]:
        self.validate()
        payload = _to_json_value(self)
        if type(payload) is not dict:
            raise _error("payload", "top-level schema must serialize to an object")
        return payload

    def validate(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class CvParamV1(PayloadSchema):
    accession: str
    name: str
    value: str | None = None
    unit_accession: str | None = None
    unit_name: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _require_nonempty(self.accession, "cv_param.accession")
        _require_nonempty(self.name, "cv_param.name")
        _require_optional_string(self.value, "cv_param.value")
        _require_optional_string(self.unit_accession, "cv_param.unit_accession")
        _require_optional_string(self.unit_name, "cv_param.unit_name")
        if (self.unit_accession is None) != (self.unit_name is None):
            raise _error("cv_param", "unit accession and name must both be present or both be null")

    @classmethod
    def from_payload(cls, payload: object, location: str = "cv_param") -> "CvParamV1":
        data = _mapping(payload, {item.name for item in fields(cls)}, location)
        return cls(
            accession=_string(data["accession"], f"{location}.accession") or "",
            name=_string(data["name"], f"{location}.name") or "",
            value=_string(data["value"], f"{location}.value", optional=True),
            unit_accession=_string(data["unit_accession"], f"{location}.unit_accession", optional=True),
            unit_name=_string(data["unit_name"], f"{location}.unit_name", optional=True),
        )


@dataclass(frozen=True, slots=True)
class TraceableEntityV1(PayloadSchema):
    id: str
    accession: str | None
    name: str | None
    version: str | None
    cv_params: tuple[CvParamV1, ...] = ()

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _require_nonempty(self.id, "entity.id")
        for field_name in ("accession", "name", "version"):
            _require_optional_string(getattr(self, field_name), f"entity.{field_name}")
        if (self.accession is None) != (self.name is None):
            raise _error("entity", "accession and name must both be present or both be null")
        if type(self.cv_params) is not tuple:
            raise _error("entity.cv_params", "must be a tuple")
        for item in self.cv_params:
            _require_instance(item, CvParamV1, "entity.cv_params[]")

    @classmethod
    def from_payload(cls, payload: object, location: str = "entity") -> "TraceableEntityV1":
        data = _mapping(payload, {item.name for item in fields(cls)}, location)
        return cls(
            id=_string(data["id"], f"{location}.id") or "",
            accession=_string(data["accession"], f"{location}.accession", optional=True),
            name=_string(data["name"], f"{location}.name", optional=True),
            version=_string(data["version"], f"{location}.version", optional=True),
            cv_params=_sequence(data["cv_params"], CvParamV1.from_payload, f"{location}.cv_params"),
        )


@dataclass(frozen=True, slots=True)
class MzmlSourceMetadataV1(PayloadSchema):
    indexed: bool
    mzml_version: str
    native_id_format_accession: str
    native_id_format_name: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if type(self.indexed) is not bool:
            raise _error("source.indexed", "must be a boolean")
        for field_name in ("mzml_version", "native_id_format_accession", "native_id_format_name"):
            _require_nonempty(getattr(self, field_name), f"source.{field_name}")

    @classmethod
    def from_payload(cls, payload: object, location: str = "source") -> "MzmlSourceMetadataV1":
        data = _mapping(payload, {item.name for item in fields(cls)}, location)
        return cls(
            indexed=_boolean(data["indexed"], f"{location}.indexed"),
            mzml_version=_string(data["mzml_version"], f"{location}.mzml_version") or "",
            native_id_format_accession=_string(data["native_id_format_accession"], f"{location}.native_id_format_accession") or "",
            native_id_format_name=_string(data["native_id_format_name"], f"{location}.native_id_format_name") or "",
        )


@dataclass(frozen=True, slots=True)
class MzmlRunMetadataV1(PayloadSchema):
    run_id: str
    default_instrument_configuration_ref: str | None
    default_source_file_ref: str | None
    sample_ref: str | None
    start_time_stamp: str | None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _require_nonempty(self.run_id, "run.run_id")
        for field_name in fields(self):
            if field_name.name != "run_id":
                _require_optional_string(getattr(self, field_name.name), f"run.{field_name.name}")

    @classmethod
    def from_payload(cls, payload: object, location: str = "run") -> "MzmlRunMetadataV1":
        data = _mapping(payload, {item.name for item in fields(cls)}, location)
        return cls(
            run_id=_string(data["run_id"], f"{location}.run_id") or "",
            default_instrument_configuration_ref=_string(data["default_instrument_configuration_ref"], f"{location}.default_instrument_configuration_ref", optional=True),
            default_source_file_ref=_string(data["default_source_file_ref"], f"{location}.default_source_file_ref", optional=True),
            sample_ref=_string(data["sample_ref"], f"{location}.sample_ref", optional=True),
            start_time_stamp=_string(data["start_time_stamp"], f"{location}.start_time_stamp", optional=True),
        )


@dataclass(frozen=True, slots=True)
class SpectrumMetadataV1(PayloadSchema):
    spectrum_id: str
    polarity: Polarity | None
    representation: SpectrumRepresentation
    default_array_length: int
    total_ion_current: float | None
    base_peak_mz: float | None
    base_peak_intensity: float | None
    lowest_observed_mz: float | None
    highest_observed_mz: float | None
    scan_window_lower: float | None
    scan_window_upper: float | None
    filter_string: str | None
    instrument_configuration_ref: str | None
    data_processing_ref: str | None
    precursor_source_spectrum_ref: str | None
    isolation_window_target_mz: float | None
    isolation_window_lower_offset: float | None
    isolation_window_upper_offset: float | None
    activation_methods: tuple[CvParamV1, ...]
    collision_energy: float | None
    collision_energy_unit_accession: str | None
    collision_energy_unit_name: str | None
    source_mz_dtype: NumericDtype
    source_intensity_dtype: NumericDtype
    source_mz_compression: ArrayCompression
    source_intensity_compression: ArrayCompression
    source_rt_value: float
    source_rt_unit_accession: str
    source_rt_unit_name: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _require_nonempty(self.spectrum_id, "spectrum.spectrum_id")
        if self.polarity is not None:
            _require_instance(self.polarity, Polarity, "spectrum.polarity")
        _require_instance(self.representation, SpectrumRepresentation, "spectrum.representation")
        if type(self.default_array_length) is not int or self.default_array_length < 0:
            raise _error("spectrum.default_array_length", "must be a non-negative integer")
        for field_name in ("total_ion_current", "base_peak_intensity", "collision_energy"):
            _require_optional_finite(getattr(self, field_name), f"spectrum.{field_name}")
        for field_name in ("base_peak_mz", "lowest_observed_mz", "highest_observed_mz", "scan_window_lower", "scan_window_upper", "isolation_window_target_mz", "isolation_window_lower_offset", "isolation_window_upper_offset"):
            _require_optional_finite(getattr(self, field_name), f"spectrum.{field_name}", nonnegative=True)
        for field_name in ("filter_string", "instrument_configuration_ref", "data_processing_ref", "precursor_source_spectrum_ref", "collision_energy_unit_accession", "collision_energy_unit_name"):
            _require_optional_string(getattr(self, field_name), f"spectrum.{field_name}")
        if (self.collision_energy_unit_accession is None) != (self.collision_energy_unit_name is None):
            raise _error("spectrum", "collision-energy unit accession and name must be paired")
        if type(self.activation_methods) is not tuple or any(not isinstance(item, CvParamV1) for item in self.activation_methods):
            raise _error("spectrum.activation_methods", "must contain CvParamV1 values")
        for field_name, expected in (("source_mz_dtype", NumericDtype), ("source_intensity_dtype", NumericDtype), ("source_mz_compression", ArrayCompression), ("source_intensity_compression", ArrayCompression)):
            _require_instance(getattr(self, field_name), expected, f"spectrum.{field_name}")
        _require_optional_finite(self.source_rt_value, "spectrum.source_rt_value", nonnegative=True)
        _require_nonempty(self.source_rt_unit_accession, "spectrum.source_rt_unit_accession")
        _require_nonempty(self.source_rt_unit_name, "spectrum.source_rt_unit_name")

    @classmethod
    def from_payload(cls, payload: object, location: str = "spectrum") -> "SpectrumMetadataV1":
        data = _mapping(payload, {item.name for item in fields(cls)}, location)
        optional_numbers = {name: _number(data[name], f"{location}.{name}", optional=True) for name in (
            "total_ion_current", "base_peak_mz", "base_peak_intensity", "lowest_observed_mz", "highest_observed_mz", "scan_window_lower", "scan_window_upper", "isolation_window_target_mz", "isolation_window_lower_offset", "isolation_window_upper_offset", "collision_energy",
        )}
        return cls(
            spectrum_id=_string(data["spectrum_id"], f"{location}.spectrum_id") or "",
            polarity=_enum(Polarity, data["polarity"], f"{location}.polarity", optional=True),
            representation=_enum(SpectrumRepresentation, data["representation"], f"{location}.representation"),
            default_array_length=_integer(data["default_array_length"], f"{location}.default_array_length"),
            filter_string=_string(data["filter_string"], f"{location}.filter_string", optional=True),
            instrument_configuration_ref=_string(data["instrument_configuration_ref"], f"{location}.instrument_configuration_ref", optional=True),
            data_processing_ref=_string(data["data_processing_ref"], f"{location}.data_processing_ref", optional=True),
            precursor_source_spectrum_ref=_string(data["precursor_source_spectrum_ref"], f"{location}.precursor_source_spectrum_ref", optional=True),
            activation_methods=_sequence(data["activation_methods"], CvParamV1.from_payload, f"{location}.activation_methods"),
            collision_energy_unit_accession=_string(data["collision_energy_unit_accession"], f"{location}.collision_energy_unit_accession", optional=True),
            collision_energy_unit_name=_string(data["collision_energy_unit_name"], f"{location}.collision_energy_unit_name", optional=True),
            source_mz_dtype=_enum(NumericDtype, data["source_mz_dtype"], f"{location}.source_mz_dtype"),
            source_intensity_dtype=_enum(NumericDtype, data["source_intensity_dtype"], f"{location}.source_intensity_dtype"),
            source_mz_compression=_enum(ArrayCompression, data["source_mz_compression"], f"{location}.source_mz_compression"),
            source_intensity_compression=_enum(ArrayCompression, data["source_intensity_compression"], f"{location}.source_intensity_compression"),
            source_rt_value=_number(data["source_rt_value"], f"{location}.source_rt_value") or 0.0,
            source_rt_unit_accession=_string(data["source_rt_unit_accession"], f"{location}.source_rt_unit_accession") or "",
            source_rt_unit_name=_string(data["source_rt_unit_name"], f"{location}.source_rt_unit_name") or "",
            **optional_numbers,
        )


@dataclass(frozen=True, slots=True)
class ChromatogramMetadataV1(PayloadSchema):
    chromatogram_id: str
    chromatogram_type: ChromatogramType
    default_array_length: int
    data_processing_ref: str | None
    source_time_dtype: NumericDtype
    source_intensity_dtype: NumericDtype
    source_time_compression: ArrayCompression
    source_intensity_compression: ArrayCompression
    source_time_unit_accession: str
    source_time_unit_name: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _require_nonempty(self.chromatogram_id, "chromatogram.chromatogram_id")
        _require_instance(self.chromatogram_type, ChromatogramType, "chromatogram.chromatogram_type")
        if type(self.default_array_length) is not int or self.default_array_length < 0:
            raise _error("chromatogram.default_array_length", "must be a non-negative integer")
        _require_optional_string(self.data_processing_ref, "chromatogram.data_processing_ref")
        for field_name, expected in (("source_time_dtype", NumericDtype), ("source_intensity_dtype", NumericDtype), ("source_time_compression", ArrayCompression), ("source_intensity_compression", ArrayCompression)):
            _require_instance(getattr(self, field_name), expected, f"chromatogram.{field_name}")
        _require_nonempty(self.source_time_unit_accession, "chromatogram.source_time_unit_accession")
        _require_nonempty(self.source_time_unit_name, "chromatogram.source_time_unit_name")

    @classmethod
    def from_payload(cls, payload: object, location: str = "chromatogram") -> "ChromatogramMetadataV1":
        data = _mapping(payload, {item.name for item in fields(cls)}, location)
        return cls(
            chromatogram_id=_string(data["chromatogram_id"], f"{location}.chromatogram_id") or "",
            chromatogram_type=_enum(ChromatogramType, data["chromatogram_type"], f"{location}.chromatogram_type"),
            default_array_length=_integer(data["default_array_length"], f"{location}.default_array_length"),
            data_processing_ref=_string(data["data_processing_ref"], f"{location}.data_processing_ref", optional=True),
            source_time_dtype=_enum(NumericDtype, data["source_time_dtype"], f"{location}.source_time_dtype"),
            source_intensity_dtype=_enum(NumericDtype, data["source_intensity_dtype"], f"{location}.source_intensity_dtype"),
            source_time_compression=_enum(ArrayCompression, data["source_time_compression"], f"{location}.source_time_compression"),
            source_intensity_compression=_enum(ArrayCompression, data["source_intensity_compression"], f"{location}.source_intensity_compression"),
            source_time_unit_accession=_string(data["source_time_unit_accession"], f"{location}.source_time_unit_accession") or "",
            source_time_unit_name=_string(data["source_time_unit_name"], f"{location}.source_time_unit_name") or "",
        )


@dataclass(frozen=True, slots=True)
class MzmlMetadataV1(PayloadSchema):
    source: MzmlSourceMetadataV1
    run: MzmlRunMetadataV1
    spectra: tuple[SpectrumMetadataV1, ...]
    chromatograms: tuple[ChromatogramMetadataV1, ...]
    instruments: tuple[TraceableEntityV1, ...]
    software: tuple[TraceableEntityV1, ...]
    data_processing: tuple[TraceableEntityV1, ...]
    schema_version: int = MZML_EXTENSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.schema_version != MZML_EXTENSION_SCHEMA_VERSION or type(self.schema_version) is not int:
            raise _error("mzml_metadata.schema_version", f"must be {MZML_EXTENSION_SCHEMA_VERSION}")
        _require_instance(self.source, MzmlSourceMetadataV1, "mzml_metadata.source")
        _require_instance(self.run, MzmlRunMetadataV1, "mzml_metadata.run")
        for field_name, expected in (("spectra", SpectrumMetadataV1), ("chromatograms", ChromatogramMetadataV1), ("instruments", TraceableEntityV1), ("software", TraceableEntityV1), ("data_processing", TraceableEntityV1)):
            value = getattr(self, field_name)
            if type(value) is not tuple or any(not isinstance(item, expected) for item in value):
                raise _error(f"mzml_metadata.{field_name}", f"must contain {expected.__name__} values")

    @classmethod
    def from_payload(cls, payload: object) -> "MzmlMetadataV1":
        location = "mzml_metadata"
        data = _mapping(payload, {item.name for item in fields(cls)}, location)
        version = _integer(data["schema_version"], f"{location}.schema_version")
        if version != MZML_EXTENSION_SCHEMA_VERSION:
            raise _error(f"{location}.schema_version", f"unsupported version {version}")
        return cls(
            source=MzmlSourceMetadataV1.from_payload(data["source"], f"{location}.source"),
            run=MzmlRunMetadataV1.from_payload(data["run"], f"{location}.run"),
            spectra=_sequence(data["spectra"], SpectrumMetadataV1.from_payload, f"{location}.spectra"),
            chromatograms=_sequence(data["chromatograms"], ChromatogramMetadataV1.from_payload, f"{location}.chromatograms"),
            instruments=_sequence(data["instruments"], TraceableEntityV1.from_payload, f"{location}.instruments"),
            software=_sequence(data["software"], TraceableEntityV1.from_payload, f"{location}.software"),
            data_processing=_sequence(data["data_processing"], TraceableEntityV1.from_payload, f"{location}.data_processing"),
            schema_version=version,
        )


@dataclass(frozen=True, slots=True)
class SupportedAuxiliaryArray:
    accession: str
    name: str
    allowed_owner_kinds: frozenset[OwnerKind]
    allowed_dtypes: frozenset[NumericDtype]
    unit_required: bool


SUPPORTED_AUXILIARY_ARRAYS = (
    SupportedAuxiliaryArray(
        accession="MS:1000786",
        name="ms level",
        allowed_owner_kinds=frozenset({OwnerKind.CHROMATOGRAM}),
        allowed_dtypes=frozenset({NumericDtype.INT64}),
        unit_required=True,
    ),
)


def auxiliary_array_is_supported(
    accession: str,
    name: str,
    owner_kind: OwnerKind,
    dtype: NumericDtype,
    unit_accession: str | None,
    unit_name: str | None,
) -> bool:
    return any(
        item.accession == accession
        and item.name == name
        and owner_kind in item.allowed_owner_kinds
        and dtype in item.allowed_dtypes
        and (not item.unit_required or (bool(unit_accession) and bool(unit_name)))
        for item in SUPPORTED_AUXILIARY_ARRAYS
    )


@dataclass(frozen=True, slots=True)
class AuxiliaryArrayV1(PayloadSchema):
    owner_kind: OwnerKind
    owner_id: str
    array_accession: str
    array_name: str
    dtype: NumericDtype
    values: tuple[int | float, ...]
    unit_accession: str | None
    unit_name: str | None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _require_instance(self.owner_kind, OwnerKind, "auxiliary_array.owner_kind")
        _require_nonempty(self.owner_id, "auxiliary_array.owner_id")
        _require_nonempty(self.array_accession, "auxiliary_array.array_accession")
        _require_nonempty(self.array_name, "auxiliary_array.array_name")
        _require_instance(self.dtype, NumericDtype, "auxiliary_array.dtype")
        _require_optional_string(self.unit_accession, "auxiliary_array.unit_accession")
        _require_optional_string(self.unit_name, "auxiliary_array.unit_name")
        if (self.unit_accession is None) != (self.unit_name is None):
            raise _error("auxiliary_array", "unit accession and name must be paired")
        if type(self.values) is not tuple:
            raise _error("auxiliary_array.values", "must be a tuple")
        if self.dtype in {NumericDtype.INT32, NumericDtype.INT64}:
            if any(type(value) is not int for value in self.values):
                raise _error("auxiliary_array.values", "integer dtype requires only plain Python integers")
            limits = {NumericDtype.INT32: (-(2**31), 2**31 - 1), NumericDtype.INT64: (-(2**63), 2**63 - 1)}
            lower, upper = limits[self.dtype]
            if any(value < lower or value > upper for value in self.values):
                raise _error("auxiliary_array.values", f"value exceeds {self.dtype.value} range")
        else:
            if any(type(value) is not float or not math.isfinite(value) for value in self.values):
                raise _error("auxiliary_array.values", "float dtype requires only finite plain Python floats")
        if not auxiliary_array_is_supported(self.array_accession, self.array_name, self.owner_kind, self.dtype, self.unit_accession, self.unit_name):
            raise _error("auxiliary_array", f"unsupported auxiliary array {self.array_accession} {self.array_name!r}")

    @classmethod
    def from_payload(cls, payload: object, location: str = "auxiliary_array") -> "AuxiliaryArrayV1":
        data = _mapping(payload, {item.name for item in fields(cls)}, location)
        values = data["values"]
        if type(values) is not list:
            raise _error(f"{location}.values", "must be a JSON array")
        return cls(
            owner_kind=_enum(OwnerKind, data["owner_kind"], f"{location}.owner_kind"),
            owner_id=_string(data["owner_id"], f"{location}.owner_id") or "",
            array_accession=_string(data["array_accession"], f"{location}.array_accession") or "",
            array_name=_string(data["array_name"], f"{location}.array_name") or "",
            dtype=_enum(NumericDtype, data["dtype"], f"{location}.dtype"),
            values=tuple(values),
            unit_accession=_string(data["unit_accession"], f"{location}.unit_accession", optional=True),
            unit_name=_string(data["unit_name"], f"{location}.unit_name", optional=True),
        )


@dataclass(frozen=True, slots=True)
class MzmlAuxiliaryArraysV1(PayloadSchema):
    arrays: tuple[AuxiliaryArrayV1, ...]
    schema_version: int = MZML_EXTENSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.schema_version != MZML_EXTENSION_SCHEMA_VERSION or type(self.schema_version) is not int:
            raise _error("mzml_auxiliary_arrays.schema_version", f"must be {MZML_EXTENSION_SCHEMA_VERSION}")
        if type(self.arrays) is not tuple or any(not isinstance(item, AuxiliaryArrayV1) for item in self.arrays):
            raise _error("mzml_auxiliary_arrays.arrays", "must contain AuxiliaryArrayV1 values")
        keys = [(item.owner_kind, item.owner_id, item.array_accession, item.array_name) for item in self.arrays]
        if len(keys) != len(set(keys)):
            raise _error("mzml_auxiliary_arrays.arrays", "duplicate owner/array records are not allowed")

    @classmethod
    def from_payload(cls, payload: object) -> "MzmlAuxiliaryArraysV1":
        location = "mzml_auxiliary_arrays"
        data = _mapping(payload, {item.name for item in fields(cls)}, location)
        version = _integer(data["schema_version"], f"{location}.schema_version")
        if version != MZML_EXTENSION_SCHEMA_VERSION:
            raise _error(f"{location}.schema_version", f"unsupported version {version}")
        return cls(
            arrays=_sequence(data["arrays"], AuxiliaryArrayV1.from_payload, f"{location}.arrays"),
            schema_version=version,
        )
