from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .blocks import ExtensionBlock, ISOLATION_WINDOW_KIND
from .bottom_up_schema import (
    BOTTOM_UP_EXTENSION_TYPES,
    BOTTOM_UP_IDENTIFICATION_KIND,
    BOTTOM_UP_OWNER,
    BOTTOM_UP_SCHEMA_VERSION,
)
from .models import ValidationIssue, ValidationResult
from .reader import ZpReader

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOW_EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class BottomUpValidationResult:
    valid: bool | None
    issues: tuple[ValidationIssue, ...]
    extension_count: int
    entity_counts: dict[str, int] | None = None
    metrics: dict[str, int | float] = field(default_factory=dict)


class BottomUpExtensionValidator:
    """Validate Bottom-Up business Extensions after physical validation."""

    def validate(
        self,
        path: str | Path,
        *,
        extensions: list[ExtensionBlock] | list[dict[str, object]] | None = None,
        physical_json_validated: bool = False,
    ) -> BottomUpValidationResult:
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        extension_read_started = time.perf_counter()
        reader = ZpReader(Path(path))
        normalized = (
            reader.read_extensions()
            if extensions is None
            else _normalize_extensions(extensions)
        )
        extension_read_seconds = time.perf_counter() - extension_read_started
        bottom_up = [
            item for item in normalized if item.extension_type.startswith("bottom_up_")
        ]
        if not bottom_up:
            return BottomUpValidationResult(
                None,
                (),
                0,
                None,
                {
                    "wall_seconds": time.perf_counter() - wall_started,
                    "cpu_seconds": time.process_time() - cpu_started,
                    "extension_read_seconds": extension_read_seconds,
                    "relationship_record_count": 0,
                    "source_fields_check_count": 0,
                },
            )
        issues: list[ValidationIssue] = []

        def add(code: str, message: str) -> None:
            issues.append(ValidationIssue(code, message, "error", "extensions"))

        types = [item.extension_type for item in bottom_up]
        for extension_type in sorted(set(types)):
            if types.count(extension_type) > 1:
                add("BOTTOM_UP_DUPLICATE_EXTENSION", f"Duplicate Extension: {extension_type}")
        for extension_type in sorted(set(types) - set(BOTTOM_UP_EXTENSION_TYPES)):
            add("BOTTOM_UP_UNKNOWN_EXTENSION", f"Unknown Bottom-Up Extension: {extension_type}")

        payloads: dict[str, dict[str, Any]] = {}
        for extension in bottom_up:
            if extension.extension_version != str(BOTTOM_UP_SCHEMA_VERSION):
                add(
                    "BOTTOM_UP_UNSUPPORTED_SCHEMA_VERSION",
                    f"{extension.extension_type} has unsupported Extension version",
                )
            payload = extension.payload
            if not isinstance(payload, dict):
                add("BOTTOM_UP_INVALID_SCHEMA", f"{extension.extension_type} payload is not an object")
                continue
            payloads.setdefault(extension.extension_type, payload)
            if (
                payload.get("owner") != BOTTOM_UP_OWNER
                or payload.get("schema_name") != extension.extension_type
                or payload.get("schema_version") != BOTTOM_UP_SCHEMA_VERSION
            ):
                add("BOTTOM_UP_INVALID_SCHEMA", f"{extension.extension_type} schema identity is invalid")
            if not physical_json_validated:
                _validate_finite(payload, extension.extension_type, add)

        metadata_payload = payloads.get("bottom_up_metadata")
        metadata: dict[str, Any] = {}
        if metadata_payload is None:
            add("BOTTOM_UP_REQUIRED_EXTENSION_MISSING", "bottom_up_metadata is required")
        elif (
            metadata_payload.get("record_count") != 1
            or not isinstance(metadata_payload.get("metadata"), dict)
        ):
            add("BOTTOM_UP_COUNT_MISMATCH", "bottom_up_metadata must contain one metadata record")
        else:
            metadata = metadata_payload["metadata"]
            self._validate_metadata(metadata, add)

        extension_status = metadata.get("extension_status", {})
        if not isinstance(extension_status, dict):
            add("BOTTOM_UP_INVALID_SCHEMA", "metadata.extension_status must be an object")
            extension_status = {}
        for extension_type in BOTTOM_UP_EXTENSION_TYPES[1:]:
            status = extension_status.get(extension_type)
            if status == "available" and extension_type not in payloads:
                add(
                    "BOTTOM_UP_REQUIRED_EXTENSION_MISSING",
                    f"{extension_type} is marked available but missing",
                )
        for required in ("bottom_up_identifications", "bottom_up_peptides"):
            if required not in payloads:
                add("BOTTOM_UP_REQUIRED_EXTENSION_MISSING", f"{required} is required")

        records: dict[str, list[dict[str, Any]]] = {}
        for extension_type in BOTTOM_UP_EXTENSION_TYPES[1:]:
            payload = payloads.get(extension_type)
            if payload is None:
                records[extension_type] = []
                continue
            raw = payload.get("records")
            if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
                add("BOTTOM_UP_INVALID_SCHEMA", f"{extension_type}.records must be a list of objects")
                records[extension_type] = []
                continue
            records[extension_type] = raw
            if payload.get("record_count") != len(raw):
                add("BOTTOM_UP_COUNT_MISMATCH", f"{extension_type} record_count mismatch")

        entity_counts = self._validate_entities(reader, metadata, records, add)
        return BottomUpValidationResult(
            not issues,
            tuple(issues),
            len(bottom_up),
            entity_counts,
            {
                "wall_seconds": time.perf_counter() - wall_started,
                "cpu_seconds": time.process_time() - cpu_started,
                "extension_read_seconds": extension_read_seconds,
                "relationship_record_count": sum(len(value) for value in records.values()),
                "source_fields_check_count": len(
                    records["bottom_up_identifications"]
                ),
            },
        )

    @staticmethod
    def _validate_metadata(metadata: dict[str, Any], add: Any) -> None:
        if metadata.get("source_type") != "real_dia_result_bundle":
            add("BOTTOM_UP_INVALID_SCHEMA", "metadata source_type is invalid")
        if metadata.get("adapter_flavor") != "diann_2_parquet":
            add("BOTTOM_UP_INVALID_SCHEMA", "metadata adapter_flavor is invalid")
        if metadata.get("identification_kind") != BOTTOM_UP_IDENTIFICATION_KIND:
            add("BOTTOM_UP_INVALID_SCHEMA", "metadata identification_kind is invalid")
        coverage = metadata.get("field_coverage")
        if not isinstance(coverage, dict) or coverage.get("unexplained_column_count") != 0:
            add("BOTTOM_UP_INVALID_SCHEMA", "field coverage must account for every source column")
        source_files = metadata.get("source_files")
        if not isinstance(source_files, list) or not source_files:
            add("BOTTOM_UP_INVALID_SCHEMA", "metadata source_files are required")
        else:
            labels: list[str] = []
            for item in source_files:
                if not isinstance(item, dict):
                    add("BOTTOM_UP_INVALID_SCHEMA", "source_files entry must be an object")
                    continue
                label = item.get("source_file")
                labels.append(label if isinstance(label, str) else "")
                if (
                    not isinstance(label, str)
                    or not label
                    or Path(label).is_absolute()
                    or re.match(r"^[A-Za-z]:", label)
                    or label.startswith("\\\\")
                ):
                    add("BOTTOM_UP_DYNAMIC_SOURCE_VALUE", "source_file must be a stable relative label")
                if not isinstance(item.get("size"), int) or item.get("size", -1) < 0:
                    add("BOTTOM_UP_INVALID_SCHEMA", "source file size is invalid")
                digest = item.get("sha256")
                if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                    add("BOTTOM_UP_INVALID_SCHEMA", "source file SHA-256 is invalid")
            if labels != sorted(labels, key=lambda item: item.encode("utf-8")):
                add("BOTTOM_UP_NONDETERMINISTIC_ORDER", "source_files are not deterministically sorted")

    @staticmethod
    def _validate_entities(
        reader: ZpReader,
        metadata: dict[str, Any],
        records: dict[str, list[dict[str, Any]]],
        add: Any,
    ) -> dict[str, int]:
        identifications = records["bottom_up_identifications"]
        peptides = records["bottom_up_peptides"]
        proteins = records["bottom_up_proteins"]
        groups = records["bottom_up_protein_groups"]
        modifications = records["bottom_up_modifications"]
        fragments = records["bottom_up_fragment_matches"]
        quantification = records["bottom_up_quantification"]
        identification_ids = _unique_ids(identifications, "identification_id", "IDENTIFICATION_ID_CONFLICT", add)
        peptide_ids = _unique_ids(peptides, "peptide_id", "PEPTIDE_ID_CONFLICT", add)
        protein_ids = _unique_ids(proteins, "protein_id", "BOTTOM_UP_REFERENCE_MISSING", add)
        group_ids = _unique_ids(groups, "protein_group_id", "PROTEIN_GROUP_ID_CONFLICT", add)
        modification_ids = _unique_ids(modifications, "modification_id", "BOTTOM_UP_REFERENCE_MISSING", add)
        _unique_ids(fragments, "fragment_id", "BOTTOM_UP_REFERENCE_MISSING", add)
        quantification_ids = _unique_ids(quantification, "quantification_id", "BOTTOM_UP_REFERENCE_MISSING", add)
        for extension_type, rows, field in (
            ("bottom_up_identifications", identifications, "identification_id"),
            ("bottom_up_peptides", peptides, "peptide_id"),
            ("bottom_up_proteins", proteins, "protein_id"),
            ("bottom_up_protein_groups", groups, "protein_group_id"),
            ("bottom_up_modifications", modifications, "modification_id"),
            ("bottom_up_fragment_matches", fragments, "fragment_id"),
            ("bottom_up_quantification", quantification, "quantification_id"),
        ):
            if not _records_are_sorted(rows, field):
                add("BOTTOM_UP_NONDETERMINISTIC_ORDER", f"{extension_type} records are not sorted")

        spectra = {item.spectrum_id: item for item in reader.read_spectra()}
        precursors = {item.precursor_id: item for item in reader.read_precursors()}
        run_ids = {item.run_id for item in reader.read_runs()}
        for identification in identifications:
            identifier = identification.get("identification_id")
            if identification.get("identification_kind") != BOTTOM_UP_IDENTIFICATION_KIND:
                add("BOTTOM_UP_INVALID_SCHEMA", f"Identification {identifier} kind is invalid")
            spectrum = spectra.get(str(identification.get("spectrum_id")))
            if spectrum is None:
                add("IDENTIFICATION_SPECTRUM_NOT_FOUND", f"Identification {identifier} references no core Spectrum")
            elif spectrum.ms_level != 2 or identification.get("run_id") != spectrum.run_id:
                add("BOTTOM_UP_REFERENCE_MISSING", f"Identification {identifier} has a cross-run or non-MS2 reference")
            if identification.get("run_id") not in run_ids:
                add("BOTTOM_UP_REFERENCE_MISSING", f"Identification {identifier} references no core Run")
            if identification.get("peptide_id") not in peptide_ids:
                add("BOTTOM_UP_REFERENCE_MISSING", f"Identification {identifier} references no Peptide")
            group_id = identification.get("protein_group_id")
            if group_id is not None and group_id not in group_ids:
                add("BOTTOM_UP_REFERENCE_MISSING", f"Identification {identifier} references no ProteinGroup")
            for protein_id in identification.get("protein_ids", []):
                if protein_id not in protein_ids:
                    add("BOTTOM_UP_REFERENCE_MISSING", f"Identification {identifier} references no Protein")
            for modification_id in identification.get("modification_ids", []):
                if modification_id not in modification_ids:
                    add("BOTTOM_UP_REFERENCE_MISSING", f"Identification {identifier} references no Modification")
            for quantification_id in identification.get("quantification_ids", []):
                if quantification_id not in quantification_ids:
                    add("BOTTOM_UP_REFERENCE_MISSING", f"Identification {identifier} references no Quantification")
            charge = identification.get("charge")
            if not isinstance(charge, int) or isinstance(charge, bool) or charge <= 0:
                add("BOTTOM_UP_INVALID_NUMERIC", f"Identification {identifier} charge is invalid")
            _nonnegative_finite(identification, "precursor_mz", add)
            _nonnegative_finite(identification, "rt_seconds", add)
            _probability_in_typed_fields(identification, add)
            if not _valid_source_fields(identification.get("source_fields")):
                add("BOTTOM_UP_INVALID_SCHEMA", f"Identification {identifier} source_fields are not canonical JSON")

        peptide_by_id = {item.get("peptide_id"): item for item in peptides}
        for peptide in peptides:
            identifier = peptide.get("peptide_id")
            sequence = peptide.get("sequence")
            if not isinstance(sequence, str) or not sequence or peptide.get("length") != len(sequence):
                add("BOTTOM_UP_INVALID_SCHEMA", f"Peptide {identifier} sequence or length is invalid")
            for identification_id in peptide.get("identification_ids", []):
                if identification_id not in identification_ids:
                    add("BOTTOM_UP_REFERENCE_MISSING", f"Peptide {identifier} references no Identification")

        for group in groups:
            identifier = group.get("protein_group_id")
            for protein_id in group.get("member_protein_ids", []):
                if protein_id not in protein_ids:
                    add("BOTTOM_UP_REFERENCE_MISSING", f"ProteinGroup {identifier} references no Protein")
            for identification_id in group.get("identification_ids", []):
                if identification_id not in identification_ids:
                    add("BOTTOM_UP_REFERENCE_MISSING", f"ProteinGroup {identifier} references no Identification")

        for modification in modifications:
            identifier = modification.get("modification_id")
            peptide = peptide_by_id.get(modification.get("peptide_id"))
            if modification.get("identification_id") not in identification_ids or peptide is None:
                add("BOTTOM_UP_REFERENCE_MISSING", f"Modification {identifier} has no owner")
                continue
            position = modification.get("position")
            sequence = peptide.get("sequence", "")
            residue = modification.get("residue")
            if (
                not isinstance(position, int)
                or isinstance(position, bool)
                or not 1 <= position <= len(sequence)
                or sequence[position - 1] != residue
            ):
                add("MODIFICATION_POSITION_INVALID", f"Modification {identifier} position or residue is invalid")

        entity_ids = {
            "identification": identification_ids,
            "protein_group": group_ids,
        }
        for record in quantification:
            identifier = record.get("quantification_id")
            kind = record.get("entity_kind")
            if kind not in entity_ids or record.get("entity_id") not in entity_ids.get(kind, set()):
                add("BOTTOM_UP_REFERENCE_MISSING", f"Quantification {identifier} has no entity")
            measurements = record.get("measurements")
            if not isinstance(measurements, dict):
                add("BOTTOM_UP_INVALID_SCHEMA", f"Quantification {identifier} measurements are invalid")
            else:
                for value in measurements.values():
                    if value is not None and not isinstance(value, str) and (
                        not isinstance(value, (int, float))
                        or isinstance(value, bool)
                        or not math.isfinite(value)
                    ):
                        add("BOTTOM_UP_INVALID_NUMERIC", f"Quantification {identifier} contains a non-finite value")

        for spectrum in spectra.values():
            if spectrum.ms_level != 2:
                continue
            precursor = precursors.get(spectrum.precursor_id or "")
            if (
                precursor is None
                or precursor.effective_precursor_kind != ISOLATION_WINDOW_KIND
                or precursor.charge is not None
                or precursor.precursor_mz is not None
                or precursor.intensity is not None
                or precursor.isolation_lower_mz is None
                or precursor.isolation_upper_mz is None
                or precursor.isolation_upper_mz - precursor.isolation_lower_mz <= _WINDOW_EPSILON
            ):
                add("DIA_WINDOW_MALFORMED", f"MS2 {spectrum.spectrum_id} has an invalid DIA core precursor")

        declared_counts = metadata.get("entity_counts", {})
        actual_counts = {
            "identification": len(identifications),
            "peptide": len(peptides),
            "protein": len(proteins),
            "protein_group": len(groups),
            "modification": len(modifications),
            "fragment_match": len(fragments),
            "quantification": len(quantification),
        }
        if not isinstance(declared_counts, dict):
            add("BOTTOM_UP_COUNT_MISMATCH", "metadata entity_counts is invalid")
        else:
            for field, actual in actual_counts.items():
                if declared_counts.get(field) != actual:
                    add("BOTTOM_UP_COUNT_MISMATCH", f"metadata count {field} does not match records")
        return actual_counts


def combine_bottom_up_validation(
    path: str | Path,
    physical: ValidationResult,
    *,
    extensions: list[ExtensionBlock] | list[dict[str, object]] | None = None,
) -> ValidationResult:
    if not physical.valid:
        return physical
    result = BottomUpExtensionValidator().validate(
        path,
        extensions=extensions,
        physical_json_validated=physical.version == 2,
    )
    metrics = dict(physical.metrics)
    if result.entity_counts is not None:
        metrics["entity_counts"] = dict(result.entity_counts)
    for key, value in result.metrics.items():
        metrics[f"bottom_up_{key}"] = value
    return ValidationResult(
        valid=physical.valid and result.valid is not False,
        issues=list(physical.issues),
        checked_blocks=physical.checked_blocks,
        file_path=physical.file_path,
        version=physical.version,
        top_down_valid=physical.top_down_valid,
        top_down_issues=list(physical.top_down_issues),
        bottom_up_valid=result.valid,
        bottom_up_issues=list(result.issues),
        mode=physical.mode,
        file_sha256=physical.file_sha256,
        certificate_valid=physical.certificate_valid,
        deep_validation_reused=physical.deep_validation_reused,
        certificate_summary=dict(physical.certificate_summary),
        metrics=metrics,
    )


def _normalize_extensions(
    extensions: list[ExtensionBlock] | list[dict[str, object]],
) -> list[ExtensionBlock]:
    normalized: list[ExtensionBlock] = []
    for item in extensions:
        if isinstance(item, ExtensionBlock):
            normalized.append(item)
        elif isinstance(item, dict):
            extension_type = item.get("extension_type")
            extension_version = item.get("extension_version")
            payload = item.get("payload")
            if (
                isinstance(extension_type, str)
                and isinstance(extension_version, str)
                and isinstance(payload, dict)
            ):
                normalized.append(
                    ExtensionBlock(extension_type, extension_version, payload)
                )
    return normalized


def _unique_ids(
    records: Iterable[dict[str, Any]],
    field: str,
    duplicate_code: str,
    add: Any,
) -> set[str]:
    counts: dict[str, int] = {}
    for record in records:
        value = record.get(field)
        if not isinstance(value, str) or not value:
            add("BOTTOM_UP_INVALID_SCHEMA", f"{field} must be a non-empty string")
        else:
            counts[value] = counts.get(value, 0) + 1
    for value in sorted(counts):
        if counts[value] > 1:
            add(duplicate_code, f"Duplicate {field}: {value}")
    return set(counts)


def _records_are_sorted(records: list[dict[str, Any]], field: str) -> bool:
    previous: str | None = None
    for record in records:
        current = str(record.get(field, ""))
        if previous is not None and current < previous:
            return False
        previous = current
    return True


def _valid_source_fields(value: object) -> bool:
    return isinstance(value, dict)


def _validate_finite(value: Any, location: str, add: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        add("BOTTOM_UP_INVALID_NUMERIC", f"Non-finite number at {location}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_finite(item, f"{location}.{key}", add)
    elif isinstance(value, list):
        for position, item in enumerate(value):
            _validate_finite(item, f"{location}[{position}]", add)


def _nonnegative_finite(record: dict[str, Any], field: str, add: Any) -> None:
    value = record.get(field)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        add("BOTTOM_UP_INVALID_NUMERIC", f"{field} must be finite and non-negative")


def _probability_in_typed_fields(record: dict[str, Any], add: Any) -> None:
    fields = record.get("typed_fields")
    if not isinstance(fields, dict):
        add("BOTTOM_UP_INVALID_SCHEMA", "identification typed_fields must be an object")
        return
    for key, value in fields.items():
        if (key == "pep" or key.endswith("q_value")) and value is not None and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            add("BOTTOM_UP_INVALID_NUMERIC", f"typed probability {key} is invalid")
