from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class GlobalMetaBlock:
    format_version: int
    source_type: str
    source_file_name: str
    source_file_hash: str
    run_count: int
    spectrum_count: int
    chromatogram_count: int
    array_count: int
    created_at: datetime
    generator_name: str
    generator_version: str
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RunBlock:
    run_id: str
    source_file: str
    run_name: str
    spectrum_count: int
    chromatogram_count: int
    start_rt: float
    end_rt: float


@dataclass(slots=True)
class SpectrumBlock:
    spectrum_id: str
    run_id: str
    ms_level: int
    scan_number: int
    native_id: str
    rt: float
    precursor_id: str | None
    mz_array_id: str
    intensity_array_id: str


@dataclass(slots=True)
class PrecursorBlock:
    precursor_id: str
    spectrum_id: str
    precursor_mz: float
    charge: int
    intensity: float


@dataclass(slots=True)
class ChromatogramBlock:
    chromatogram_id: str
    run_id: str
    chromatogram_type: str
    time_array_id: str
    intensity_array_id: str
    native_id: str


@dataclass(slots=True)
class ArrayBlock:
    array_id: str
    array_type: str
    dtype: str
    values: list[float]


@dataclass(slots=True)
class StringPoolBlock:
    strings: list[str]


@dataclass(slots=True)
class IndexBlock:
    scan_index: list[dict[str, Any]]
    rt_index: list[dict[str, Any]]
    spectrum_id_index: list[dict[str, Any]]


@dataclass(slots=True)
class ExtensionBlock:
    extension_type: str
    extension_version: str
    payload: dict[str, Any]


@dataclass(slots=True)
class BlockCollection:
    global_meta: GlobalMetaBlock | None = None
    runs: list[RunBlock] = field(default_factory=list)
    spectra: list[SpectrumBlock] = field(default_factory=list)
    precursors: list[PrecursorBlock] = field(default_factory=list)
    chromatograms: list[ChromatogramBlock] = field(default_factory=list)
    arrays: list[ArrayBlock] = field(default_factory=list)
    string_pool: StringPoolBlock | None = None
    indexes: IndexBlock | None = None
    extensions: list[ExtensionBlock] = field(default_factory=list)

    def get_spectrum(self, spectrum_id: str) -> SpectrumBlock | None:
        return next((item for item in self.spectra if item.spectrum_id == spectrum_id), None)

    def get_array(self, array_id: str) -> ArrayBlock | None:
        return next((item for item in self.arrays if item.array_id == array_id), None)

    def get_run(self, run_id: str) -> RunBlock | None:
        return next((item for item in self.runs if item.run_id == run_id), None)

    def get_precursor(self, precursor_id: str) -> PrecursorBlock | None:
        return next((item for item in self.precursors if item.precursor_id == precursor_id), None)

