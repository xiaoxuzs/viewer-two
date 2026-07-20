from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .blocks import ExtensionBlock
from .serialization import to_primitive

BOTTOM_UP_SCHEMA_VERSION = 1
BOTTOM_UP_OWNER = "bottom_up"
BOTTOM_UP_IDENTIFICATION_KIND = "dia_precursor_identification"
BOTTOM_UP_EXTENSION_TYPES = (
    "bottom_up_metadata",
    "bottom_up_identifications",
    "bottom_up_peptides",
    "bottom_up_proteins",
    "bottom_up_protein_groups",
    "bottom_up_modifications",
    "bottom_up_fragment_matches",
    "bottom_up_quantification",
)


@dataclass(frozen=True, slots=True)
class DiannColumnSpec:
    source_name: str
    entity: str
    logical_field: str
    value_kind: Literal["integer", "float", "string"]
    nullable: bool
    required: bool = False
    unit: str | None = None


def _column(
    source_name: str,
    entity: str,
    logical_field: str,
    value_kind: Literal["integer", "float", "string"],
    *,
    nullable: bool = False,
    required: bool = False,
    unit: str | None = None,
) -> DiannColumnSpec:
    return DiannColumnSpec(
        source_name,
        entity,
        logical_field,
        value_kind,
        nullable,
        required,
        unit,
    )


DIANN_COLUMN_SPECS = (
    _column("Run.Index", "metadata", "source_run_index", "integer", nullable=True),
    _column("Run", "identification", "source_run_name", "string", required=True),
    _column("Channel", "quantification", "channel", "string", nullable=True),
    _column("Precursor.Id", "identification", "source_precursor_id", "string", required=True),
    _column("Modified.Sequence", "identification", "modified_sequence", "string", required=True),
    _column("Stripped.Sequence", "peptide", "sequence", "string", required=True),
    _column("Precursor.Charge", "identification", "charge", "integer", required=True),
    _column("Precursor.Lib.Index", "identification", "library_index", "integer", nullable=True),
    _column("Decoy", "identification", "is_decoy", "integer", required=True),
    _column("Proteotypic", "identification", "is_proteotypic", "integer", nullable=True),
    _column("Precursor.Mz", "identification", "precursor_mz", "float", required=True, unit="Th"),
    _column("Protein.Ids", "protein", "source_protein_ids", "string", nullable=True),
    _column("Protein.Group", "protein_group", "source_member_accessions", "string", nullable=True),
    _column("Protein.Names", "protein", "source_names", "string", nullable=True),
    _column("Genes", "protein", "source_genes", "string", nullable=True),
    _column("RT", "identification", "rt_seconds", "float", required=True, unit="minute_to_second"),
    _column("iRT", "identification", "irt", "float", nullable=True),
    _column("Predicted.RT", "identification", "predicted_rt_seconds", "float", nullable=True, unit="minute_to_second"),
    _column("Predicted.iRT", "identification", "predicted_irt", "float", nullable=True),
    _column("IM", "identification", "ion_mobility", "float", nullable=True),
    _column("iIM", "identification", "iim", "float", nullable=True),
    _column("Predicted.IM", "identification", "predicted_ion_mobility", "float", nullable=True),
    _column("Predicted.iIM", "identification", "predicted_iim", "float", nullable=True),
    _column("Precursor.Quantity", "quantification", "precursor_quantity", "float", nullable=True, unit="source_intensity"),
    _column("Precursor.Normalised", "quantification", "precursor_normalised", "float", nullable=True, unit="source_intensity"),
    _column("Ms1.Area", "quantification", "ms1_area", "float", nullable=True, unit="source_intensity"),
    _column("Ms1.Normalised", "quantification", "ms1_normalised", "float", nullable=True, unit="source_intensity"),
    _column("Ms1.Apex.Area", "quantification", "ms1_apex_area", "float", nullable=True, unit="source_intensity"),
    _column("Ms1.Apex.Mz.Delta", "quantification", "ms1_apex_mz_delta", "float", nullable=True, unit="source"),
    _column("Normalisation.Factor", "quantification", "normalisation_factor", "float", nullable=True),
    _column("Quantity.Quality", "quantification", "quantity_quality", "float", nullable=True),
    _column("Empirical.Quality", "quantification", "empirical_quality", "float", nullable=True),
    _column("Normalisation.Noise", "quantification", "normalisation_noise", "float", nullable=True),
    _column("Ms1.Profile.Corr", "quantification", "ms1_profile_corr", "float", nullable=True),
    _column("Evidence", "identification", "evidence", "float", nullable=True),
    _column("Mass.Evidence", "identification", "mass_evidence", "float", nullable=True),
    _column("Channel.Evidence", "identification", "channel_evidence", "float", nullable=True),
    _column("Ms1.Total.Signal.Before", "quantification", "ms1_signal_before", "float", nullable=True, unit="source_intensity"),
    _column("Ms1.Total.Signal.After", "quantification", "ms1_signal_after", "float", nullable=True, unit="source_intensity"),
    _column("RT.Start", "identification", "rt_start_seconds", "float", required=True, unit="minute_to_second"),
    _column("RT.Stop", "identification", "rt_stop_seconds", "float", required=True, unit="minute_to_second"),
    _column("FWHM", "identification", "fwhm_seconds", "float", nullable=True, unit="minute_to_second"),
    _column("PG.TopN", "quantification", "pg_top_n", "float", nullable=True, unit="source_intensity"),
    _column("PG.MaxLFQ", "quantification", "pg_max_lfq", "float", nullable=True, unit="source_intensity"),
    _column("Genes.TopN", "quantification", "genes_top_n", "float", nullable=True, unit="source_intensity"),
    _column("Genes.MaxLFQ", "quantification", "genes_max_lfq", "float", nullable=True, unit="source_intensity"),
    _column("Genes.MaxLFQ.Unique", "quantification", "genes_max_lfq_unique", "float", nullable=True, unit="source_intensity"),
    _column("PG.MaxLFQ.Quality", "quantification", "pg_max_lfq_quality", "float", nullable=True),
    _column("Genes.MaxLFQ.Quality", "quantification", "genes_max_lfq_quality", "float", nullable=True),
    _column("Genes.MaxLFQ.Unique.Quality", "quantification", "genes_unique_quality", "float", nullable=True),
    _column("Q.Value", "identification", "q_value", "float", required=True),
    _column("PEP", "identification", "pep", "float", nullable=True),
    _column("Global.Q.Value", "identification", "global_q_value", "float", nullable=True),
    _column("Lib.Q.Value", "identification", "lib_q_value", "float", nullable=True),
    _column("Peptidoform.Q.Value", "identification", "peptidoform_q_value", "float", nullable=True),
    _column("Global.Peptidoform.Q.Value", "identification", "global_peptidoform_q_value", "float", nullable=True),
    _column("Lib.Peptidoform.Q.Value", "identification", "lib_peptidoform_q_value", "float", nullable=True),
    _column("PTM.Site.Confidence", "modification", "site_confidence", "float", nullable=True),
    _column("Site.Occupancy.Probabilities", "modification", "site_occupancy", "string", nullable=True),
    _column("Protein.Sites", "modification", "source_protein_sites", "string", nullable=True),
    _column("Lib.PTM.Site.Confidence", "modification", "lib_site_confidence", "float", nullable=True),
    _column("Translated.Q.Value", "identification", "translated_q_value", "float", nullable=True),
    _column("Channel.Q.Value", "identification", "channel_q_value", "float", nullable=True),
    _column("PG.Q.Value", "protein_group", "q_value", "float", nullable=True),
    _column("PG.PEP", "protein_group", "pep", "float", nullable=True),
    _column("GG.Q.Value", "quantification", "gene_group_q_value", "float", nullable=True),
    _column("Protein.Q.Value", "protein", "q_value", "float", nullable=True),
    _column("Global.PG.Q.Value", "protein_group", "global_q_value", "float", nullable=True),
    _column("Lib.PG.Q.Value", "protein_group", "lib_q_value", "float", nullable=True),
)

DIANN_COLUMN_NAMES = tuple(item.source_name for item in DIANN_COLUMN_SPECS)
if len(DIANN_COLUMN_NAMES) != 69 or len(set(DIANN_COLUMN_NAMES)) != 69:
    raise RuntimeError("The frozen DIA-NN column contract must contain 69 unique columns")


@dataclass(frozen=True, slots=True)
class BottomUpIdentification:
    identification_id: str
    identification_kind: str
    run_id: str
    source_run_name: str
    source_precursor_id: str
    spectrum_id: str
    association_kind: str
    association_rt_delta_seconds: float
    association_precursor_mz: float
    peptide_id: str
    protein_group_id: str | None
    protein_ids: tuple[str, ...]
    modified_sequence: str
    stripped_sequence: str
    charge: int
    precursor_mz: float
    neutral_mass: float
    rt_seconds: float
    rt_start_seconds: float
    rt_stop_seconds: float
    typed_fields: dict[str, Any]
    modification_ids: tuple[str, ...]
    quantification_ids: tuple[str, ...]
    source_fields: dict[str, Any]
    source_scan: None = None
    source_native_id: None = None
    rank: None = None


@dataclass(frozen=True, slots=True)
class BottomUpPeptide:
    peptide_id: str
    sequence: str
    length: int
    identification_ids: tuple[str, ...]
    modified_sequences: tuple[str, ...]
    precursor_charges: tuple[int, ...]
    protein_ids: tuple[str, ...]
    protein_group_ids: tuple[str, ...]
    modification_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BottomUpProtein:
    protein_id: str
    accession: str
    is_decoy: bool
    name: str | None
    gene: str | None
    description: str | None
    sequence: str | None
    q_value: float | None
    peptide_ids: tuple[str, ...]
    identification_ids: tuple[str, ...]
    protein_group_ids: tuple[str, ...]
    source_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BottomUpProteinGroup:
    protein_group_id: str
    source_group: str
    member_protein_ids: tuple[str, ...]
    leading_protein_id: None
    identification_ids: tuple[str, ...]
    peptide_ids: tuple[str, ...]
    q_value: float | None
    pep: float | None
    global_q_value: float | None
    lib_q_value: float | None
    quantification_ids: tuple[str, ...]
    source_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BottomUpModification:
    modification_id: str
    identification_id: str
    peptide_id: str
    token_ordinal: int
    accession: str
    name: str
    mass_shift: float
    coordinate_system: str
    position: int
    residue: str
    terminal: str
    is_fixed: bool | None
    localization_probability: float | None
    site_confidence: float | None
    site_occupancy: str | None
    source_protein_sites: str | None
    lib_site_confidence: float | None
    source_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BottomUpFragmentMatch:
    fragment_id: str
    identification_id: str
    peak_space: str
    source_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BottomUpQuantification:
    quantification_id: str
    entity_kind: str
    entity_id: str
    run_id: str
    sample_id: str
    measurements: dict[str, float | str | None]
    unit: str
    normalization_kind: str | None
    quality: dict[str, float | None]
    condition: None = None
    biological_replicate: None = None
    technical_replicate: None = None


@dataclass(frozen=True, slots=True)
class BottomUpDocument:
    metadata: dict[str, Any]
    identifications: tuple[BottomUpIdentification, ...]
    peptides: tuple[BottomUpPeptide, ...]
    proteins: tuple[BottomUpProtein, ...]
    protein_groups: tuple[BottomUpProteinGroup, ...]
    modifications: tuple[BottomUpModification, ...]
    fragment_matches: tuple[BottomUpFragmentMatch, ...] = ()
    quantification: tuple[BottomUpQuantification, ...] = ()
    source_table_chunks: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    extension_status: dict[str, str] = field(default_factory=dict)

    def extension_blocks(self) -> list[ExtensionBlock]:
        metadata = dict(self.metadata)
        metadata["source_tables"] = list(self.source_table_chunks)
        metadata["warnings"] = list(self.warnings)
        metadata["extension_status"] = dict(self.extension_status)
        values: dict[str, object] = {
            "bottom_up_metadata": metadata,
            "bottom_up_identifications": self.identifications,
            "bottom_up_peptides": self.peptides,
            "bottom_up_proteins": self.proteins,
            "bottom_up_protein_groups": self.protein_groups,
            "bottom_up_modifications": self.modifications,
            "bottom_up_fragment_matches": self.fragment_matches,
            "bottom_up_quantification": self.quantification,
        }
        result: list[ExtensionBlock] = []
        for extension_type in BOTTOM_UP_EXTENSION_TYPES:
            records = values[extension_type]
            if extension_type == "bottom_up_metadata":
                payload = _payload(extension_type, 1, metadata=records)
            else:
                primitive_records = to_primitive(records)
                if not primitive_records and self.extension_status.get(extension_type) != "available_empty":
                    continue
                payload = _payload(
                    extension_type,
                    len(primitive_records),
                    records=primitive_records,
                )
            result.append(ExtensionBlock(extension_type, "1", payload))
        return result


def _payload(extension_type: str, record_count: int, **values: object) -> dict[str, Any]:
    return {
        "owner": BOTTOM_UP_OWNER,
        "schema_name": extension_type,
        "schema_version": BOTTOM_UP_SCHEMA_VERSION,
        "record_count": record_count,
        **values,
    }
