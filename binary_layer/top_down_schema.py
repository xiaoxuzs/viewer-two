from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOP_DOWN_SCHEMA_VERSION = 1
TOP_DOWN_OWNER = "top_down"
TOP_DOWN_EXTENSION_TYPES = (
    "top_down_metadata",
    "top_down_proteoforms",
    "top_down_prsms",
    "top_down_modifications",
    "top_down_fragment_matches",
    "top_down_features",
)


@dataclass(frozen=True, slots=True)
class TopDownBundleManifest:
    schema_name: str
    schema_version: int
    run_name: str
    roles: dict[str, str]


@dataclass(frozen=True, slots=True)
class TopDownBundle:
    schema_name: str
    schema_version: int
    input_path: Path
    root: Path
    run_name: str
    spectrum_source: Path
    spectrum_source_type: str
    prsm_detail_files: tuple[Path, ...]
    proteoform_result: Path
    prsm_summary_result: Path | None = None
    protein_database: Path | None = None
    feature_result: Path | None = None
    raw_prsm_result: Path | None = None
    msalign_result: Path | None = None
    manifest_path: Path | None = None
    detected_roles: tuple[str, ...] = ()
    source_files: tuple[Path, ...] = ()

    @property
    def run_count(self) -> int:
        return 1

    def relative_label(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.name


@dataclass(frozen=True, slots=True)
class TopDownResidue:
    position: int
    acid: str


@dataclass(frozen=True, slots=True)
class TopDownCleavageMatch:
    ion_type: str
    ion_position: int
    ion_display_position: int
    source_spectrum_id: str
    source_peak_id: str
    peak_charge: int | None


@dataclass(frozen=True, slots=True)
class TopDownCleavage:
    position: int
    has_n_terminal_ion: bool
    has_c_terminal_ion: bool
    matched_peaks: tuple[TopDownCleavageMatch, ...]


@dataclass(frozen=True, slots=True)
class TopDownProteoform:
    proteoform_id: str
    sequence_id: str
    protein_accession: str
    protein_description: str | None
    sequence: str
    start_position: int
    end_position: int
    protein_length: int
    experimental_mass: float | None
    theoretical_mass: float | None
    mass_error: float | None
    terminal_state: str | None
    best_prsm_id: str
    score_summary: dict[str, float | None]
    annotated_sequence: str
    residues: tuple[TopDownResidue, ...]
    cleavages: tuple[TopDownCleavage, ...]
    modification_ids: tuple[str, ...]
    source_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TopDownSpectrumReference:
    run_name: str
    spectrum_file_name: str
    scan_numbers: tuple[int, ...]
    native_ids: tuple[str, ...]
    ms1_scan_numbers: tuple[int, ...]
    ms1_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TopDownPrsm:
    prsm_id: str
    spectrum_id: str | None
    spectrum_reference: TopDownSpectrumReference
    proteoform_id: str
    precursor_mz: float | None
    charge: int | None
    precursor_mass: float | None
    adjusted_mass: float | None
    matched_fragment_count: int | None
    matched_peak_count: int | None
    total_fragment_count: int | None
    p_value: float | None
    e_value: float | None
    q_value: float | None
    score: float | None
    rank: int | None
    feature_intensity: float | None
    source_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TopDownModification:
    modification_id: str
    proteoform_id: str
    prsm_id: str
    name: str
    mass_shift: float | None
    position: int | None
    left_position: int
    right_position: int
    residue: str | None
    modification_type: str
    localization: dict[str, Any]
    source_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TopDownPeak:
    peak_id: str
    prsm_id: str
    source_spectrum_id: str
    source_peak_id: str
    monoisotopic_mass: float | None
    observed_mz: float | None
    intensity: float | None
    charge: int | None
    matched_ion_count: int
    source_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TopDownFragmentMatch:
    fragment_match_id: str
    prsm_id: str
    peak_id: str
    ion_type: str
    ordinal: int
    ion_display_position: int
    ion_left_position: int
    ion_sort_name: str
    charge: int | None
    theoretical_mass: float | None
    theoretical_mz: float | None
    observed_mz: float | None
    mass_error: float | None
    ppm: float | None
    intensity: float | None
    matched_peak_index: int | None
    match_shift: float | None
    neutral_loss: str | None
    source_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TopDownFeature:
    feature_id: str
    source_feature_id: str | None
    prsm_id: str
    spectrum_id: str | None
    intensity: float | None
    score: float | None
    min_rt_seconds: float | None
    max_rt_seconds: float | None
    apex_rt_seconds: float | None
    source_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TopDownSourceTable:
    role: str
    source_file: str
    columns: tuple[str, ...]
    parameters: dict[str, str]
    rows: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class TopDownDocument:
    schema_name: str
    schema_version: int
    bundle: TopDownBundle
    proteoforms: tuple[TopDownProteoform, ...]
    prsms: tuple[TopDownPrsm, ...]
    modifications: tuple[TopDownModification, ...]
    peaks: tuple[TopDownPeak, ...]
    fragment_matches: tuple[TopDownFragmentMatch, ...]
    features: tuple[TopDownFeature, ...]
    source_tables: tuple[TopDownSourceTable, ...] = ()
    warnings: tuple[str, ...] = ()
    source_field_coverage: dict[str, tuple[str, ...]] = field(default_factory=dict)
