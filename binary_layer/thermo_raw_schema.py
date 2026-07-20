from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .conversion_exceptions import ThermoRawConversionError

THERMO_RAW_CONVERSION_EXTENSION_TYPE = "thermo_raw_conversion_metadata"
THERMO_RAW_CONVERSION_SCHEMA_VERSION = 1

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ThermoRawConversionMetadataV1:
    source_kind: str
    source_file_name: str
    source_size: int
    source_sha256: str
    converter_name: str
    converter_version: str
    intermediate_format: str
    intermediate_indexed: bool
    intermediate_sha256: str
    schema_version: int = THERMO_RAW_CONVERSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != THERMO_RAW_CONVERSION_SCHEMA_VERSION:
            raise _schema_error("schema_version must be 1")
        if self.source_kind != "thermo_raw":
            raise _schema_error("source_kind must be 'thermo_raw'")
        if not self.source_file_name or "/" in self.source_file_name or "\\" in self.source_file_name:
            raise _schema_error("source_file_name must be a file name without directories")
        if type(self.source_size) is not int or self.source_size < 0:
            raise _schema_error("source_size must be a non-negative integer")
        for value, name in (
            (self.source_sha256, "source_sha256"),
            (self.intermediate_sha256, "intermediate_sha256"),
        ):
            if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
                raise _schema_error(f"{name} must be a lowercase SHA-256")
        if self.converter_name != "ThermoRawFileParser":
            raise _schema_error("converter_name must be 'ThermoRawFileParser'")
        if not isinstance(self.converter_version, str) or not self.converter_version:
            raise _schema_error("converter_version must be non-empty")
        if self.intermediate_format != "mzML":
            raise _schema_error("intermediate_format must be 'mzML'")
        if self.intermediate_indexed is not True:
            raise _schema_error("intermediate_indexed must be true")

    def to_payload(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: object) -> "ThermoRawConversionMetadataV1":
        if not isinstance(payload, dict):
            raise _schema_error("payload must be an object")
        expected = {
            "source_kind",
            "source_file_name",
            "source_size",
            "source_sha256",
            "converter_name",
            "converter_version",
            "intermediate_format",
            "intermediate_indexed",
            "intermediate_sha256",
            "schema_version",
        }
        if set(payload) != expected:
            raise _schema_error("payload fields do not match thermo_raw_conversion_metadata v1")
        try:
            return cls(**payload)  # type: ignore[arg-type]
        except TypeError as exc:
            raise _schema_error(str(exc)) from exc


def _schema_error(message: str) -> ThermoRawConversionError:
    return ThermoRawConversionError("THERMO_RAW_METADATA_INVALID", message)
