from __future__ import annotations

import base64
import hashlib
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .bottom_up_schema import (
    BOTTOM_UP_IDENTIFICATION_KIND,
    DIANN_COLUMN_NAMES,
    DIANN_COLUMN_SPECS,
    BottomUpDocument,
    BottomUpIdentification,
    BottomUpModification,
    BottomUpPeptide,
    BottomUpProtein,
    BottomUpProteinGroup,
    BottomUpQuantification,
    DiannColumnSpec,
)
from .bottom_up_exceptions import DiaResultConversionError
from .dia_result_bundle import ADAPTER_FLAVOR, SOURCE_TYPE, DiaResultBundle, normalize_run_name
from .dia_spectrum_association import ASSOCIATION_KIND, DiaSpectrumAssociator
from .serialization import canonical_json_bytes

PROTON_MASS = 1.007276466812
Q_VALUE_CUTOFF = 0.01
DEFAULT_BATCH_SIZE = 8192
_UNIMOD_TOKEN = re.compile(r"\(UniMod:(\d+)\)", re.IGNORECASE)
_DIANN_VERSION = re.compile(r"DIA-NN(?:\s+version)?\s+([0-9][0-9A-Za-z_.-]*)", re.IGNORECASE)

_IDENTIFICATION_QUANT_COLUMNS = frozenset(
    {
        "Channel",
        "Precursor.Quantity",
        "Precursor.Normalised",
        "Ms1.Area",
        "Ms1.Normalised",
        "Ms1.Apex.Area",
        "Ms1.Apex.Mz.Delta",
        "Normalisation.Factor",
        "Quantity.Quality",
        "Empirical.Quality",
        "Normalisation.Noise",
        "Ms1.Profile.Corr",
        "Ms1.Total.Signal.Before",
        "Ms1.Total.Signal.After",
    }
)
_GROUP_QUANT_COLUMNS = frozenset(
    {
        "PG.TopN",
        "PG.MaxLFQ",
        "Genes.TopN",
        "Genes.MaxLFQ",
        "Genes.MaxLFQ.Unique",
        "PG.MaxLFQ.Quality",
        "Genes.MaxLFQ.Quality",
        "Genes.MaxLFQ.Unique.Quality",
        "GG.Q.Value",
    }
)
_NONNEGATIVE_QUANT_COLUMNS = frozenset(
    {
        "Precursor.Quantity",
        "Precursor.Normalised",
        "Ms1.Area",
        "Ms1.Normalised",
        "Ms1.Apex.Area",
        "Normalisation.Factor",
        "Ms1.Total.Signal.Before",
        "Ms1.Total.Signal.After",
        "PG.TopN",
        "PG.MaxLFQ",
        "Genes.TopN",
        "Genes.MaxLFQ",
        "Genes.MaxLFQ.Unique",
    }
)
_PROBABILITY_COLUMNS = frozenset(
    {
        "Q.Value",
        "PEP",
        "Global.Q.Value",
        "Lib.Q.Value",
        "Peptidoform.Q.Value",
        "Global.Peptidoform.Q.Value",
        "Lib.Peptidoform.Q.Value",
        "Lib.PTM.Site.Confidence",
        "Translated.Q.Value",
        "Channel.Q.Value",
        "PG.Q.Value",
        "PG.PEP",
        "GG.Q.Value",
        "Protein.Q.Value",
        "Global.PG.Q.Value",
        "Lib.PG.Q.Value",
    }
)
_RT_COLUMNS = frozenset({"RT", "RT.Start", "RT.Stop", "Predicted.RT", "FWHM"})


@dataclass(frozen=True, slots=True)
class DiaResultAdapterReport:
    document: BottomUpDocument
    parquet_parse_seconds: float
    association_seconds: float
    extension_build_seconds: float
    parquet_parse_cpu_seconds: float
    association_cpu_seconds: float
    extension_build_cpu_seconds: float
    parquet_batch_count: int
    parquet_row_count: int


@dataclass(slots=True)
class _PeptideState:
    sequence: str
    identification_ids: set[str] = field(default_factory=set)
    modified_sequences: set[str] = field(default_factory=set)
    charges: set[int] = field(default_factory=set)
    protein_ids: set[str] = field(default_factory=set)
    protein_group_ids: set[str] = field(default_factory=set)
    modification_ids: set[str] = field(default_factory=set)


@dataclass(slots=True)
class _ProteinState:
    accession: str
    name: str | None = None
    gene: str | None = None
    q_value: float | None = None
    peptide_ids: set[str] = field(default_factory=set)
    identification_ids: set[str] = field(default_factory=set)
    protein_group_ids: set[str] = field(default_factory=set)
    source_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _GroupState:
    source_group: str
    member_protein_ids: tuple[str, ...]
    identification_ids: set[str] = field(default_factory=set)
    peptide_ids: set[str] = field(default_factory=set)
    quantification_ids: set[str] = field(default_factory=set)
    q_value: float | None = None
    pep: float | None = None
    global_q_value: float | None = None
    lib_q_value: float | None = None
    source_fields: dict[str, Any] = field(default_factory=dict)


class DiaResultAdapter:
    def __init__(self, *, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        self.batch_size = batch_size

    def read(
        self,
        bundle: DiaResultBundle,
        *,
        run_id: str,
        spectrum_file_sha256: str,
        source_file_hashes: dict[str, str],
        associator: DiaSpectrumAssociator,
    ) -> DiaResultAdapterReport:
        parquet_started = time.perf_counter()
        parquet_cpu_started = time.process_time()
        parquet = pq.ParquetFile(bundle.primary_report)
        present, missing, unknown = _validate_column_contract(parquet.schema_arrow)
        chunks: list[dict[str, Any]] = []
        identifications: list[BottomUpIdentification] = []
        modifications: list[BottomUpModification] = []
        quantification: dict[str, BottomUpQuantification] = {}
        peptides: dict[str, _PeptideState] = {}
        proteins: dict[str, _ProteinState] = {}
        groups: dict[str, _GroupState] = {}
        seen_identification_ids: set[str] = set()
        associated_spectra: set[str] = set()
        association_seconds = 0.0
        association_cpu_seconds = 0.0
        batch_count = 0
        fixed_unimod4 = _fixed_unimod4_evidence(bundle)
        row_offset = 0

        for batch in parquet.iter_batches(batch_size=self.batch_size):
            batch_count += 1
            names = tuple(batch.schema.names)
            column_values = [
                [_canonical_source_value(value) for value in batch.column(position).to_pylist()]
                for position in range(len(names))
            ]
            chunks.append(
                {
                    "role": "primary_report",
                    "source_file": bundle.relative_label(bundle.primary_report),
                    "storage": "columnar_record_batch",
                    "row_start": row_offset,
                    "row_count": batch.num_rows,
                    "columns": {
                        name: column_values[position]
                        for position, name in enumerate(names)
                    },
                }
            )
            for position in range(batch.num_rows):
                row = {
                    name: column_values[column_position][position]
                    for column_position, name in enumerate(names)
                }
                if not _viewer_selected(row):
                    continue
                typed = _typed_row(row, present)
                if normalize_run_name(str(typed["source_run_name"])) != bundle.normalized_run_name:
                    raise DiaResultConversionError(
                        "DIANN_RUN_NOT_MATCHED",
                        "An admitted DIA-NN row does not match the selected mzML run",
                    )
                source_precursor_id = _required_string(row, "Precursor.Id")
                identification_id = _stable_id(
                    "identification",
                    spectrum_file_sha256,
                    bundle.normalized_run_name,
                    source_precursor_id,
                )
                if identification_id in seen_identification_ids:
                    raise DiaResultConversionError(
                        "IDENTIFICATION_ID_CONFLICT",
                        f"Duplicate admitted Precursor.Id: {source_precursor_id}",
                    )
                seen_identification_ids.add(identification_id)
                sequence = _required_string(row, "Stripped.Sequence")
                modified_sequence = _required_string(row, "Modified.Sequence")
                charge = _positive_integer(row.get("Precursor.Charge"), "Precursor.Charge")
                precursor_mz = _finite_number(row.get("Precursor.Mz"), "Precursor.Mz", nonnegative=True)
                rt_minutes = _finite_number(row.get("RT"), "RT", nonnegative=True)
                rt_start_minutes = _finite_number(row.get("RT.Start"), "RT.Start", nonnegative=True)
                rt_stop_minutes = _finite_number(row.get("RT.Stop"), "RT.Stop", nonnegative=True)
                if not rt_start_minutes <= rt_minutes <= rt_stop_minutes:
                    raise DiaResultConversionError(
                        "DIANN_ROW_MALFORMED",
                        "RT.Start <= RT <= RT.Stop is required for admitted identifications",
                    )
                association_started = time.perf_counter()
                association_cpu_started = time.process_time()
                association = associator.associate(rt_minutes, precursor_mz)
                association_seconds += time.perf_counter() - association_started
                association_cpu_seconds += (
                    time.process_time() - association_cpu_started
                )
                associated_spectra.add(association.spectrum_id)

                peptide_id = _stable_id("peptide", sequence)
                group_text = _optional_string(row.get("Protein.Group"))
                accessions = tuple(_split_semicolon(group_text))
                protein_ids = tuple(_stable_id("protein", accession, "target") for accession in accessions)
                group_id = _stable_id("protein_group", group_text) if group_text else None
                row_modifications = _parse_modifications(
                    identification_id,
                    peptide_id,
                    sequence,
                    modified_sequence,
                    row,
                    is_fixed=fixed_unimod4,
                )
                modifications.extend(row_modifications)
                modification_ids = tuple(item.modification_id for item in row_modifications)

                quantification_ids: list[str] = []
                group_quantification_id: str | None = None
                ident_measurements = _measurement_values(row, _IDENTIFICATION_QUANT_COLUMNS)
                if ident_measurements:
                    quantification_id = _quantification_id(
                        "identification",
                        identification_id,
                        ident_measurements,
                    )
                    quantification.setdefault(
                        quantification_id,
                        BottomUpQuantification(
                            quantification_id=quantification_id,
                            entity_kind="identification",
                            entity_id=identification_id,
                            run_id=run_id,
                            sample_id=bundle.normalized_run_name,
                            measurements=ident_measurements,
                            unit="source_intensity",
                            normalization_kind="diann_reported",
                            quality=_quality_values(ident_measurements),
                        ),
                    )
                    quantification_ids.append(quantification_id)
                if group_id is not None:
                    group_measurements = _measurement_values(row, _GROUP_QUANT_COLUMNS)
                    if group_measurements:
                        group_quant_id = _quantification_id(
                            "protein_group",
                            group_id,
                            group_measurements,
                        )
                        quantification.setdefault(
                            group_quant_id,
                            BottomUpQuantification(
                                quantification_id=group_quant_id,
                                entity_kind="protein_group",
                                entity_id=group_id,
                                run_id=run_id,
                                sample_id=bundle.normalized_run_name,
                                measurements=group_measurements,
                                unit="source_intensity",
                                normalization_kind="diann_reported",
                                quality=_quality_values(group_measurements),
                            ),
                        )
                        quantification_ids.append(group_quant_id)
                        group_quantification_id = group_quant_id

                identification = BottomUpIdentification(
                    identification_id=identification_id,
                    identification_kind=BOTTOM_UP_IDENTIFICATION_KIND,
                    run_id=run_id,
                    source_run_name=bundle.report_run_name,
                    source_precursor_id=source_precursor_id,
                    spectrum_id=association.spectrum_id,
                    association_kind=ASSOCIATION_KIND,
                    association_rt_delta_seconds=association.rt_delta_seconds,
                    association_precursor_mz=precursor_mz,
                    peptide_id=peptide_id,
                    protein_group_id=group_id,
                    protein_ids=protein_ids,
                    modified_sequence=modified_sequence,
                    stripped_sequence=sequence,
                    charge=charge,
                    precursor_mz=precursor_mz,
                    neutral_mass=precursor_mz * charge - PROTON_MASS * charge,
                    rt_seconds=rt_minutes * 60.0,
                    rt_start_seconds=rt_start_minutes * 60.0,
                    rt_stop_seconds=rt_stop_minutes * 60.0,
                    typed_fields=typed,
                    modification_ids=modification_ids,
                    quantification_ids=tuple(sorted(quantification_ids)),
                    source_fields=row,
                )
                identifications.append(identification)
                _add_relations(
                    identification,
                    row,
                    peptides,
                    proteins,
                    groups,
                    group_quantification_id=group_quantification_id,
                )
            row_offset += batch.num_rows
        parquet_seconds = time.perf_counter() - parquet_started - association_seconds
        parquet_cpu_seconds = (
            time.process_time()
            - parquet_cpu_started
            - association_cpu_seconds
        )

        extension_started = time.perf_counter()
        extension_cpu_started = time.process_time()
        peptide_records = tuple(
            BottomUpPeptide(
                peptide_id=peptide_id,
                sequence=state.sequence,
                length=len(state.sequence),
                identification_ids=tuple(sorted(state.identification_ids)),
                modified_sequences=tuple(sorted(state.modified_sequences)),
                precursor_charges=tuple(sorted(state.charges)),
                protein_ids=tuple(sorted(state.protein_ids)),
                protein_group_ids=tuple(sorted(state.protein_group_ids)),
                modification_ids=tuple(sorted(state.modification_ids)),
            )
            for peptide_id, state in sorted(peptides.items())
        )
        protein_records = tuple(
            BottomUpProtein(
                protein_id=protein_id,
                accession=state.accession,
                is_decoy=False,
                name=state.name,
                gene=state.gene,
                description=None,
                sequence=None,
                q_value=state.q_value,
                peptide_ids=tuple(sorted(state.peptide_ids)),
                identification_ids=tuple(sorted(state.identification_ids)),
                protein_group_ids=tuple(sorted(state.protein_group_ids)),
                source_fields=state.source_fields,
            )
            for protein_id, state in sorted(proteins.items())
        )
        group_records = tuple(
            BottomUpProteinGroup(
                protein_group_id=group_id,
                source_group=state.source_group,
                member_protein_ids=state.member_protein_ids,
                leading_protein_id=None,
                identification_ids=tuple(sorted(state.identification_ids)),
                peptide_ids=tuple(sorted(state.peptide_ids)),
                q_value=state.q_value,
                pep=state.pep,
                global_q_value=state.global_q_value,
                lib_q_value=state.lib_q_value,
                quantification_ids=tuple(sorted(state.quantification_ids)),
                source_fields=state.source_fields,
            )
            for group_id, state in sorted(groups.items())
        )
        identifications.sort(key=lambda item: item.identification_id)
        modifications.sort(key=lambda item: item.modification_id)
        quantification_records = tuple(
            quantification[key] for key in sorted(quantification)
        )
        source_files = _source_file_manifest(bundle, source_file_hashes)
        metadata = {
            "source_type": SOURCE_TYPE,
            "adapter_flavor": ADAPTER_FLAVOR,
            "identification_kind": BOTTOM_UP_IDENTIFICATION_KIND,
            "analysis_mode": "bottom_up_dia",
            "report_role": bundle.report_role,
            "report_file_name": bundle.primary_report.name,
            "report_file_size": bundle.primary_report.stat().st_size,
            "report_file_sha256": source_file_hashes[bundle.relative_label(bundle.primary_report)],
            "spectrum_file_name": bundle.spectrum_source.name,
            "spectrum_file_size": bundle.spectrum_source.stat().st_size,
            "spectrum_file_sha256": spectrum_file_sha256,
            "report_run_name": bundle.report_run_name,
            "normalized_run_name": bundle.normalized_run_name,
            "core_run_id": run_id,
            "source_software": "DIA-NN",
            "source_software_version_evidence": _diann_version_evidence(bundle),
            "selection_policy": {
                "decoy": "Decoy == 0",
                "q_value": "Q.Value < 0.01",
                "q_value_cutoff": Q_VALUE_CUTOFF,
            },
            "field_mapping_version": 1,
            "field_mapping": [_field_mapping(item) for item in DIANN_COLUMN_SPECS],
            "field_coverage": {
                "known_column_count": len(DIANN_COLUMN_NAMES),
                "present_known_columns": list(present),
                "known_optional_columns_missing": list(missing),
                "unknown_columns": list(unknown),
                "typed_known_column_count": len(present),
                "all_69_columns_accounted_for": len(present) == 69,
                "unexplained_column_count": 0,
            },
            "entity_counts": {
                "identification": len(identifications),
                "peptide": len(peptide_records),
                "protein": len(protein_records),
                "protein_group": len(group_records),
                "modification": len(modifications),
                "fragment_match": 0,
                "quantification": len(quantification_records),
            },
            "association": {
                **associator.provenance,
                "identification_count": len(identifications),
                "associated_identification_count": len(identifications),
                "distinct_ms2_count": len(associated_spectra),
                "dangling_spectrum_reference_count": 0,
            },
            "source_files": source_files,
            "optional_roles": _optional_role_status(bundle),
            "source_artifacts": [
                item
                for item in source_files
                if item["role"] in {"spectral_library", "pfmb_pickle", "infoneg_pickle"}
            ],
            "fragment_support": {
                "status": "not_available",
                "reason": "fragment_source_not_loaded",
                "unsafe_pickle_deserialization_used": False,
            },
            "primary_report_total_row_count": parquet.metadata.num_rows,
            "primary_report_admitted_row_count": len(identifications),
            "source_table_storage": "columnar_record_batch",
        }
        status = {
            "bottom_up_metadata": "available",
            "bottom_up_identifications": "available",
            "bottom_up_peptides": "available",
            "bottom_up_proteins": "available" if protein_records else "not_present",
            "bottom_up_protein_groups": "available" if group_records else "not_present",
            "bottom_up_modifications": "available" if modifications else "not_present",
            "bottom_up_fragment_matches": "not_available",
            "bottom_up_quantification": "available" if quantification_records else "not_present",
        }
        warnings = tuple(
            [f"known_optional_columns_missing={','.join(missing)}"] if missing else []
        ) + tuple(
            [f"unknown_columns_preserved={','.join(unknown)}"] if unknown else []
        )
        document = BottomUpDocument(
            metadata=metadata,
            identifications=tuple(identifications),
            peptides=peptide_records,
            proteins=protein_records,
            protein_groups=group_records,
            modifications=tuple(modifications),
            quantification=quantification_records,
            source_table_chunks=tuple(chunks),
            warnings=warnings,
            extension_status=status,
        )
        extension_seconds = time.perf_counter() - extension_started
        extension_cpu_seconds = time.process_time() - extension_cpu_started
        return DiaResultAdapterReport(
            document=document,
            parquet_parse_seconds=max(0.0, parquet_seconds),
            association_seconds=association_seconds,
            extension_build_seconds=extension_seconds,
            parquet_parse_cpu_seconds=max(0.0, parquet_cpu_seconds),
            association_cpu_seconds=association_cpu_seconds,
            extension_build_cpu_seconds=extension_cpu_seconds,
            parquet_batch_count=batch_count,
            parquet_row_count=row_offset,
        )


def _validate_column_contract(
    schema: pa.Schema,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    names = tuple(schema.names)
    available = set(names)
    required_missing = [
        item.source_name
        for item in DIANN_COLUMN_SPECS
        if item.required and item.source_name not in available
    ]
    if required_missing:
        raise DiaResultConversionError(
            "DIANN_REQUIRED_COLUMN_MISSING",
            "Required DIA-NN columns are missing: " + ", ".join(required_missing),
        )
    specs = {item.source_name: item for item in DIANN_COLUMN_SPECS}
    for name in names:
        spec = specs.get(name)
        if spec is None:
            continue
        dtype = schema.field(name).type
        valid = (
            (spec.value_kind == "integer" and pa.types.is_integer(dtype))
            or (spec.value_kind == "float" and pa.types.is_floating(dtype))
            or (spec.value_kind == "string" and (pa.types.is_string(dtype) or pa.types.is_large_string(dtype)))
        )
        if not valid:
            raise DiaResultConversionError(
                "DIANN_COLUMN_TYPE_INVALID",
                f"Column {name} has Arrow type {dtype}; expected {spec.value_kind}",
            )
    present = tuple(name for name in DIANN_COLUMN_NAMES if name in available)
    missing = tuple(
        item.source_name
        for item in DIANN_COLUMN_SPECS
        if not item.required and item.source_name not in available
    )
    unknown = tuple(sorted(available - set(DIANN_COLUMN_NAMES)))
    return present, missing, unknown


def _viewer_selected(row: dict[str, Any]) -> bool:
    q_value = row.get("Q.Value")
    decoy = row.get("Decoy")
    return (
        isinstance(q_value, (int, float))
        and not isinstance(q_value, bool)
        and math.isfinite(q_value)
        and q_value < Q_VALUE_CUTOFF
        and decoy == 0
    )


def _typed_row(row: dict[str, Any], present: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    specs = {item.source_name: item for item in DIANN_COLUMN_SPECS}
    for name in present:
        spec = specs[name]
        value = row.get(name)
        typed = _typed_value(value, spec)
        if name in _PROBABILITY_COLUMNS and typed is not None and not 0 <= typed <= 1:
            raise DiaResultConversionError(
                "DIANN_ROW_MALFORMED",
                f"{name} must be between zero and one",
            )
        result[spec.logical_field] = typed
    return result


def _typed_value(value: Any, spec: DiannColumnSpec) -> Any:
    if value is None or (spec.value_kind == "string" and value == ""):
        if spec.required and not spec.nullable:
            raise DiaResultConversionError(
                "DIANN_ROW_MALFORMED",
                f"Required field {spec.source_name} is empty",
            )
        return None
    if spec.value_kind == "string":
        if not isinstance(value, str):
            raise DiaResultConversionError("DIANN_ROW_MALFORMED", f"{spec.source_name} must be a string")
        return value
    if spec.value_kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise DiaResultConversionError("DIANN_ROW_MALFORMED", f"{spec.source_name} must be an integer")
        return value
    numeric = _finite_number(value, spec.source_name)
    return numeric * 60.0 if spec.source_name in _RT_COLUMNS else numeric


def _measurement_values(row: dict[str, Any], columns: frozenset[str]) -> dict[str, float | str | None]:
    specs = {item.source_name: item for item in DIANN_COLUMN_SPECS}
    result: dict[str, float | str | None] = {}
    for name in DIANN_COLUMN_NAMES:
        if name not in columns or name not in row:
            continue
        value = _typed_value(row[name], specs[name])
        if value is None:
            result[specs[name].logical_field] = None
            continue
        if name in _NONNEGATIVE_QUANT_COLUMNS and isinstance(value, (int, float)) and value < 0:
            raise DiaResultConversionError(
                "DIANN_ROW_MALFORMED",
                f"{name} must be non-negative",
            )
        result[specs[name].logical_field] = value
    return result


def _quality_values(values: dict[str, float | str | None]) -> dict[str, float | None]:
    return {
        key: value
        for key, value in values.items()
        if (key.endswith("quality") or key.endswith("corr"))
        and (value is None or isinstance(value, float))
    }


def _parse_modifications(
    identification_id: str,
    peptide_id: str,
    sequence: str,
    modified_sequence: str,
    row: dict[str, Any],
    *,
    is_fixed: bool | None,
) -> tuple[BottomUpModification, ...]:
    output: list[BottomUpModification] = []
    stripped: list[str] = []
    position = 0
    cursor = 0
    ordinal = 0
    while cursor < len(modified_sequence):
        if modified_sequence[cursor] == "(":
            match = _UNIMOD_TOKEN.match(modified_sequence, cursor)
            if match is None or position == 0:
                raise DiaResultConversionError(
                    "MODIFICATION_POSITION_INVALID",
                    "Modified.Sequence contains an unsupported or unlocalized token",
                )
            accession_number = match.group(1)
            if accession_number != "4":
                raise DiaResultConversionError(
                    "DIANN_ROW_MALFORMED",
                    f"Unsupported typed modification UniMod:{accession_number}",
                )
            residue = stripped[-1]
            if residue != "C":
                raise DiaResultConversionError(
                    "MODIFICATION_POSITION_INVALID",
                    "UNIMOD:4 must be localized to C",
                )
            ordinal += 1
            modification_id = _stable_id(
                "modification",
                identification_id,
                str(ordinal),
                "UNIMOD:4",
                str(position),
            )
            output.append(
                BottomUpModification(
                    modification_id=modification_id,
                    identification_id=identification_id,
                    peptide_id=peptide_id,
                    token_ordinal=ordinal,
                    accession="UNIMOD:4",
                    name="Carbamidomethyl",
                    mass_shift=57.021464,
                    coordinate_system="peptide_residue_1_based",
                    position=position,
                    residue=residue,
                    terminal="none",
                    is_fixed=is_fixed,
                    localization_probability=None,
                    site_confidence=_optional_finite(row.get("PTM.Site.Confidence")),
                    site_occupancy=_optional_string(row.get("Site.Occupancy.Probabilities")),
                    source_protein_sites=_optional_string(row.get("Protein.Sites")),
                    lib_site_confidence=_optional_finite(row.get("Lib.PTM.Site.Confidence")),
                    source_fields={
                        "modified_sequence": modified_sequence,
                        "token": match.group(0),
                    },
                )
            )
            cursor = match.end()
            continue
        character = modified_sequence[cursor]
        if not character.isalpha() or not character.isupper():
            raise DiaResultConversionError(
                "MODIFICATION_POSITION_INVALID",
                "Modified.Sequence contains unsupported residue syntax",
            )
        stripped.append(character)
        position += 1
        cursor += 1
    if "".join(stripped) != sequence:
        raise DiaResultConversionError(
            "MODIFICATION_POSITION_INVALID",
            "Modified.Sequence does not reduce to Stripped.Sequence",
        )
    return tuple(output)


def _add_relations(
    identification: BottomUpIdentification,
    row: dict[str, Any],
    peptides: dict[str, _PeptideState],
    proteins: dict[str, _ProteinState],
    groups: dict[str, _GroupState],
    *,
    group_quantification_id: str | None,
) -> None:
    peptide = peptides.setdefault(
        identification.peptide_id,
        _PeptideState(identification.stripped_sequence),
    )
    peptide.identification_ids.add(identification.identification_id)
    peptide.modified_sequences.add(identification.modified_sequence)
    peptide.charges.add(identification.charge)
    peptide.protein_ids.update(identification.protein_ids)
    peptide.modification_ids.update(identification.modification_ids)
    if identification.protein_group_id is not None:
        peptide.protein_group_ids.add(identification.protein_group_id)

    accessions = _split_semicolon(_optional_string(row.get("Protein.Group")))
    names = _split_semicolon(_optional_string(row.get("Protein.Names")))
    genes = _split_semicolon(_optional_string(row.get("Genes")))
    for position, (accession, protein_id) in enumerate(zip(accessions, identification.protein_ids)):
        protein = proteins.setdefault(protein_id, _ProteinState(accession))
        if protein.name is None and len(names) == len(accessions):
            protein.name = names[position]
        if protein.gene is None and len(genes) == len(accessions):
            protein.gene = genes[position]
        protein.q_value = _minimum_optional(protein.q_value, _optional_finite(row.get("Protein.Q.Value")))
        protein.peptide_ids.add(identification.peptide_id)
        protein.identification_ids.add(identification.identification_id)
        if identification.protein_group_id is not None:
            protein.protein_group_ids.add(identification.protein_group_id)
        if not protein.source_fields:
            protein.source_fields = {
                "Protein.Ids": row.get("Protein.Ids"),
                "Protein.Names": row.get("Protein.Names"),
                "Genes": row.get("Genes"),
            }

    group_id = identification.protein_group_id
    if group_id is None:
        return
    group = groups.setdefault(
        group_id,
        _GroupState(
            source_group=str(row["Protein.Group"]),
            member_protein_ids=identification.protein_ids,
        ),
    )
    if group.member_protein_ids != identification.protein_ids:
        raise DiaResultConversionError(
            "PROTEIN_GROUP_ID_CONFLICT",
            "A protein-group ID resolved to conflicting ordered members",
        )
    group.identification_ids.add(identification.identification_id)
    group.peptide_ids.add(identification.peptide_id)
    if group_quantification_id is not None:
        group.quantification_ids.add(group_quantification_id)
    group.q_value = _minimum_optional(group.q_value, _optional_finite(row.get("PG.Q.Value")))
    group.pep = _minimum_optional(group.pep, _optional_finite(row.get("PG.PEP")))
    group.global_q_value = _minimum_optional(group.global_q_value, _optional_finite(row.get("Global.PG.Q.Value")))
    group.lib_q_value = _minimum_optional(group.lib_q_value, _optional_finite(row.get("Lib.PG.Q.Value")))
    if not group.source_fields:
        group.source_fields = {
            "Protein.Group": row.get("Protein.Group"),
            "PG.Q.Value": row.get("PG.Q.Value"),
            "PG.PEP": row.get("PG.PEP"),
        }


def _source_file_manifest(
    bundle: DiaResultBundle,
    source_file_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in bundle.source_files:
        label = bundle.relative_label(item.path)
        digest = source_file_hashes.get(label)
        if digest is None:
            raise DiaResultConversionError(
                "MISSING_INPUT_SHA256",
                f"HashInput did not fingerprint bundle role {label}",
            )
        result.append(
            {
                "role": item.role,
                "source_file": label,
                "file_name": item.path.name,
                "size": item.path.stat().st_size,
                "sha256": digest,
                "processing_status": item.processing_status,
            }
        )
    return result


def _optional_role_status(bundle: DiaResultBundle) -> dict[str, str]:
    roles = {item.role for item in bundle.source_files}
    return {
        role: ("present" if role in roles else "not_present")
        for role in (
            "refined_report",
            "fasta",
            "stats",
            "protein_description",
            "quant_matrix",
            "spectral_library",
            "log",
            "manifest",
            "pfmb_pickle",
            "infoneg_pickle",
        )
    }


def _diann_version_evidence(bundle: DiaResultBundle) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for item in bundle.source_files:
        if item.role not in {"log", "manifest"}:
            continue
        try:
            text = item.path.read_text(encoding="utf-8-sig", errors="replace")[:4_000_000]
        except OSError:
            continue
        matches = sorted(set(_DIANN_VERSION.findall(text)))
        if matches:
            evidence.append(
                {
                    "source_file": bundle.relative_label(item.path),
                    "software": "DIA-NN",
                    "version": matches[0],
                }
            )
    if not evidence:
        evidence.append(
            {
                "source_file": bundle.primary_report.name,
                "software": "DIA-NN",
                "version": "2.0_contract",
            }
        )
    return evidence


def _fixed_unimod4_evidence(bundle: DiaResultBundle) -> bool | None:
    for item in bundle.source_files:
        if item.role not in {"log", "manifest"}:
            continue
        try:
            text = item.path.read_text(encoding="utf-8-sig", errors="replace")[:4_000_000].casefold()
        except OSError:
            continue
        if "unimod4" in text or "unimod:4" in text:
            return True
    return None


def _field_mapping(item: DiannColumnSpec) -> dict[str, Any]:
    return {
        "source_column": item.source_name,
        "logical_entity": item.entity,
        "logical_field": item.logical_field,
        "type": item.value_kind,
        "nullable": item.nullable,
        "required": item.required,
        "unit": item.unit,
        "conversion": "multiply_by_60" if item.source_name in _RT_COLUMNS else "identity",
        "source_fields_policy": "preserve_original",
    }


def _canonical_source_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"$nonfinite": "NaN"}
        if math.isinf(value):
            return {"$nonfinite": "+Infinity" if value > 0 else "-Infinity"}
        return value
    if isinstance(value, bytes):
        return {"$bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Decimal):
        return {"$decimal": format(value, "f")}
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return {"$datetime": normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, dict):
        return {str(key): _canonical_source_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_source_value(item) for item in value]
    raise DiaResultConversionError(
        "DIANN_ROW_MALFORMED",
        f"Arrow value type cannot be canonicalized: {type(value).__name__}",
    )


def _stable_id(kind: str, *parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "little"))
        digest.update(encoded)
    return f"{kind}:{digest.hexdigest()}"


def _quantification_id(
    entity_kind: str,
    entity_id: str,
    measurements: dict[str, float | str | None],
) -> str:
    return _stable_id(
        "quantification",
        entity_kind,
        entity_id,
        canonical_json_bytes(measurements).decode("utf-8"),
    )


def _required_string(row: dict[str, Any], field_name: str) -> str:
    value = row.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise DiaResultConversionError(
            "DIANN_ROW_MALFORMED",
            f"{field_name} must be a non-empty string",
        )
    return value.strip()


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _split_semicolon(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def _positive_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DiaResultConversionError(
            "DIANN_ROW_MALFORMED",
            f"{field_name} must be a positive integer",
        )
    return value


def _finite_number(value: Any, field_name: str, *, nonnegative: bool = False) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or (nonnegative and value < 0)
    ):
        raise DiaResultConversionError(
            "DIANN_ROW_MALFORMED",
            f"{field_name} must be finite" + (" and non-negative" if nonnegative else ""),
        )
    return float(value)


def _optional_finite(value: Any) -> float | None:
    return (
        float(value)
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        else None
    )


def _minimum_optional(first: float | None, second: float | None) -> float | None:
    if first is None:
        return second
    if second is None:
        return first
    return min(first, second)
