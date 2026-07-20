from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .blocks import ExtensionBlock
from .models import ValidationIssue, ValidationResult
from .reader import ZpReader
from .top_down_schema import (
    TOP_DOWN_EXTENSION_TYPES,
    TOP_DOWN_OWNER,
    TOP_DOWN_SCHEMA_VERSION,
)
from .top_down_interpretation_schema import (
    TOP_DOWN_INTERPRETATION_EXTENSION_TYPE,
    TOP_DOWN_INTERPRETATION_ORIGINS,
    TOP_DOWN_INTERPRETATION_SCHEMA_VERSION,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class TopDownValidationResult:
    valid: bool | None
    issues: tuple[ValidationIssue, ...]
    extension_count: int


class TopDownExtensionValidator:
    """Validate Top-Down business extensions without duplicating physical validation."""

    def validate(
        self,
        path: str | Path,
        *,
        extensions: list[ExtensionBlock] | list[dict[str, object]] | None = None,
    ) -> TopDownValidationResult:
        reader = ZpReader(Path(path))
        if extensions is None:
            normalized = reader.read_extensions()
        else:
            normalized = _normalize_extensions(extensions)
        td_extensions = [
            item for item in normalized if item.extension_type.startswith("top_down_")
        ]
        if not td_extensions:
            return TopDownValidationResult(None, (), 0)

        issues: list[ValidationIssue] = []

        def add(code: str, message: str) -> None:
            issues.append(ValidationIssue(code, message, "error", "extensions"))

        types = [item.extension_type for item in td_extensions]
        for extension_type in sorted(set(types)):
            if types.count(extension_type) > 1:
                add(
                    "TOP_DOWN_DUPLICATE_EXTENSION",
                    f"Extension appears more than once: {extension_type}",
                )
        known_types = set(TOP_DOWN_EXTENSION_TYPES) | {
            TOP_DOWN_INTERPRETATION_EXTENSION_TYPE
        }
        unknown = sorted(set(types) - known_types)
        for extension_type in unknown:
            add(
                "TOP_DOWN_UNKNOWN_EXTENSION",
                f"Unknown Top-Down extension: {extension_type}",
            )
        for extension_type in TOP_DOWN_EXTENSION_TYPES:
            if extension_type not in types:
                add(
                    "TOP_DOWN_REQUIRED_EXTENSION_MISSING",
                    f"Required Top-Down extension is missing: {extension_type}",
                )

        payloads: dict[str, dict[str, Any]] = {}
        for extension in td_extensions:
            if extension.extension_version != str(TOP_DOWN_SCHEMA_VERSION):
                add(
                    "TOP_DOWN_UNSUPPORTED_SCHEMA_VERSION",
                    f"{extension.extension_type} has unsupported Extension version",
                )
            payload = extension.payload
            if not isinstance(payload, dict):
                add(
                    "TOP_DOWN_INVALID_SCHEMA",
                    f"{extension.extension_type} payload must be an object",
                )
                continue
            payloads.setdefault(extension.extension_type, payload)
            if (
                payload.get("owner") != TOP_DOWN_OWNER
                or payload.get("schema_name") != extension.extension_type
                or payload.get("schema_version") != TOP_DOWN_SCHEMA_VERSION
            ):
                add(
                    "TOP_DOWN_INVALID_SCHEMA",
                    f"{extension.extension_type} has an invalid owner or schema identity",
                )
            _validate_finite(payload, extension.extension_type, add)

        metadata = payloads.get("top_down_metadata")
        if metadata is not None:
            if metadata.get("record_count") != 1 or not isinstance(
                metadata.get("metadata"), dict
            ):
                add(
                    "TOP_DOWN_RECORD_COUNT_MISMATCH",
                    "top_down_metadata must contain exactly one metadata record",
                )
            else:
                self._validate_metadata(metadata["metadata"], add)

        records: dict[str, list[dict[str, Any]]] = {}
        for extension_type in TOP_DOWN_EXTENSION_TYPES[1:]:
            payload = payloads.get(extension_type)
            if payload is None:
                records[extension_type] = []
                continue
            raw_records = payload.get("records")
            if not isinstance(raw_records, list) or not all(
                isinstance(item, dict) for item in raw_records
            ):
                add(
                    "TOP_DOWN_INVALID_SCHEMA",
                    f"{extension_type}.records must be a list of objects",
                )
                records[extension_type] = []
                continue
            records[extension_type] = raw_records
            if payload.get("record_count") != len(raw_records):
                add(
                    "TOP_DOWN_RECORD_COUNT_MISMATCH",
                    f"{extension_type} record_count does not match records",
                )

        proteoforms = records.get("top_down_proteoforms", [])
        prsms = records.get("top_down_prsms", [])
        modifications = records.get("top_down_modifications", [])
        fragments = records.get("top_down_fragment_matches", [])
        features = records.get("top_down_features", [])
        fragment_payload = payloads.get("top_down_fragment_matches", {})
        raw_peaks = fragment_payload.get("peaks", [])
        peaks = raw_peaks if isinstance(raw_peaks, list) else []
        if not isinstance(raw_peaks, list) or not all(isinstance(item, dict) for item in peaks):
            add("TOP_DOWN_INVALID_SCHEMA", "top_down_fragment_matches.peaks is invalid")
            peaks = []
        if fragment_payload and fragment_payload.get("peak_count") != len(peaks):
            add(
                "TOP_DOWN_RECORD_COUNT_MISMATCH",
                "top_down_fragment_matches peak_count does not match peaks",
            )

        self._validate_entities(
            reader,
            metadata.get("metadata", {}) if isinstance(metadata, dict) else {},
            proteoforms,
            prsms,
            modifications,
            peaks,
            fragments,
            features,
            add,
        )
        self._validate_interpretation_provenance(
            payloads.get(TOP_DOWN_INTERPRETATION_EXTENSION_TYPE),
            prsms,
            add,
        )
        return TopDownValidationResult(not issues, tuple(issues), len(td_extensions))

    @staticmethod
    def _validate_interpretation_provenance(
        payload: dict[str, Any] | None,
        prsms: list[dict[str, Any]],
        add: Any,
    ) -> None:
        # Optional for P2-B1 files written before this extension existed.
        if payload is None:
            return
        if (
            payload.get("owner") != TOP_DOWN_OWNER
            or payload.get("schema_name") != TOP_DOWN_INTERPRETATION_EXTENSION_TYPE
            or payload.get("schema_version") != TOP_DOWN_INTERPRETATION_SCHEMA_VERSION
            or payload.get("record_count") != 1
        ):
            add(
                "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
                "Interpretation provenance schema identity or record_count is invalid",
            )
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict):
            add(
                "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
                "Interpretation provenance must be an object",
            )
            return
        origin = provenance.get("interpretation_origin")
        if origin not in TOP_DOWN_INTERPRETATION_ORIGINS:
            add(
                "TOP_DOWN_INVALID_INTERPRETATION_ORIGIN",
                "interpretation_origin is unsupported",
            )

        artifacts = _provenance_file_list(
            provenance.get("generated_prsm_files"),
            "generated_prsm_files",
            add,
        )
        names = provenance.get("generated_prsm_file_names")
        hashes = provenance.get("generated_prsm_sha256")
        count = provenance.get("generated_prsm_count")
        if (
            not isinstance(names, list)
            or not all(isinstance(item, str) and item for item in names)
            or not isinstance(hashes, list)
            or not all(
                isinstance(item, str) and _SHA256.fullmatch(item) is not None
                for item in hashes
            )
            or count != len(artifacts)
            or names != [item.get("file_name") for item in artifacts]
            or hashes != [item.get("sha256") for item in artifacts]
        ):
            add(
                "TOP_DOWN_INTERPRETATION_COUNT_MISMATCH",
                "Generated PrSM provenance names, hashes, and count must agree",
            )
        artifact_ids: list[str] = []
        for item in artifacts:
            file_name = item.get("file_name")
            match = (
                re.fullmatch(r"prsm(\d+)\.js", Path(file_name).name, re.IGNORECASE)
                if isinstance(file_name, str)
                else None
            )
            if match is None:
                add(
                    "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
                    "Generated PrSM file name must be prsm<id>.js",
                )
                continue
            artifact_ids.append(str(int(match.group(1))))
            declared_id = item.get("prsm_id")
            if declared_id is not None and declared_id != artifact_ids[-1]:
                add(
                    "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
                    "Generated PrSM artifact ID does not match its file name",
                )
        if len(artifact_ids) != len(set(artifact_ids)):
            add("PRSMUP_OUTPUT_DUPLICATE_ID", "Generated PrSM provenance IDs are not unique")
        prsm_ids = {
            item.get("prsm_id") for item in prsms if isinstance(item.get("prsm_id"), str)
        }
        if set(artifact_ids) != prsm_ids:
            add(
                "TOP_DOWN_INTERPRETATION_ID_MISMATCH",
                "Generated PrSM provenance IDs do not match Top-Down PrSM records",
            )

        if origin == "generated_from_toppic_topfd":
            if provenance.get("generator_name") != "prsmup.py":
                add(
                    "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
                    "Generated interpretation requires generator_name=prsmup.py",
                )
            script_sha = provenance.get("generator_script_sha256")
            if not isinstance(script_sha, str) or _SHA256.fullmatch(script_sha) is None:
                add(
                    "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
                    "Generated interpretation requires a valid script SHA-256",
                )
            python_version = provenance.get("python_version")
            if not isinstance(python_version, str) or not python_version:
                add(
                    "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
                    "Generated interpretation requires python_version",
                )
            xml_files = _provenance_file_list(
                provenance.get("toppic_prsm_xml_files"),
                "toppic_prsm_xml_files",
                add,
            )
            msalign_files = _provenance_file_list(
                provenance.get("topfd_ms2_msalign_files"),
                "topfd_ms2_msalign_files",
                add,
            )
            if not xml_files or not msalign_files:
                add(
                    "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
                    "Generated interpretation requires XML and MSALIGN input summaries",
                )

    @staticmethod
    def _validate_metadata(metadata: dict[str, Any], add: Any) -> None:
        run_name = metadata.get("run_name")
        if not isinstance(run_name, str) or not run_name:
            add("TOP_DOWN_INVALID_SCHEMA", "Top-Down metadata requires run_name")
        source_files = metadata.get("source_files")
        if not isinstance(source_files, list) or not source_files:
            add("TOP_DOWN_INVALID_SCHEMA", "Top-Down metadata requires source_files")
            return
        labels: list[str] = []
        for item in source_files:
            if not isinstance(item, dict):
                add("TOP_DOWN_INVALID_SCHEMA", "Top-Down source file entry must be an object")
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
                add(
                    "TOP_DOWN_DYNAMIC_SOURCE_VALUE",
                    "Top-Down source_file must be a non-absolute stable label",
                )
            if not isinstance(item.get("size"), int) or item.get("size", -1) < 0:
                add("TOP_DOWN_INVALID_SCHEMA", "Top-Down source file size is invalid")
            if not isinstance(item.get("sha256"), str) or _SHA256.fullmatch(
                item.get("sha256", "")
            ) is None:
                add("TOP_DOWN_INVALID_SCHEMA", "Top-Down source file SHA-256 is invalid")
        if labels != sorted(labels, key=lambda item: item.encode("utf-8")):
            add("TOP_DOWN_NONDETERMINISTIC_ORDER", "Top-Down source files are not sorted")

    @staticmethod
    def _validate_entities(
        reader: ZpReader,
        metadata: dict[str, Any],
        proteoforms: list[dict[str, Any]],
        prsms: list[dict[str, Any]],
        modifications: list[dict[str, Any]],
        peaks: list[dict[str, Any]],
        fragments: list[dict[str, Any]],
        features: list[dict[str, Any]],
        add: Any,
    ) -> None:
        proteoform_ids = _unique_ids(
            proteoforms,
            "proteoform_id",
            "TOP_DOWN_DUPLICATE_PROTEOFORM_ID",
            add,
        )
        prsm_ids = _unique_ids(
            prsms,
            "prsm_id",
            "TOP_DOWN_DUPLICATE_PRSM_ID",
            add,
        )
        modification_ids = _unique_ids(
            modifications,
            "modification_id",
            "TOP_DOWN_DUPLICATE_MODIFICATION_ID",
            add,
        )
        peak_ids = _unique_ids(peaks, "peak_id", "TOP_DOWN_DUPLICATE_PEAK_ID", add)
        _unique_ids(
            fragments,
            "fragment_match_id",
            "TOP_DOWN_DUPLICATE_FRAGMENT_MATCH_ID",
            add,
        )
        _unique_ids(features, "feature_id", "TOP_DOWN_DUPLICATE_FEATURE_ID", add)

        _validate_order(
            proteoforms,
            lambda item: _id_key(item.get("proteoform_id")),
            "top_down_proteoforms",
            add,
        )
        _validate_order(
            prsms,
            lambda item: _id_key(item.get("prsm_id")),
            "top_down_prsms",
            add,
        )
        _validate_order(
            modifications,
            lambda item: (
                _id_key(item.get("proteoform_id")),
                _integer_key(item.get("left_position")),
                _integer_key(item.get("right_position")),
                _id_key(item.get("modification_id")),
            ),
            "top_down_modifications",
            add,
        )
        _validate_order(
            peaks,
            lambda item: (
                _id_key(item.get("prsm_id")),
                _id_key(item.get("source_peak_id")),
            ),
            "top_down_fragment_matches.peaks",
            add,
        )
        _validate_order(
            fragments,
            lambda item: (
                _id_key(item.get("prsm_id")),
                str(item.get("ion_type", "")),
                _integer_key(item.get("ordinal")),
                _integer_key(item.get("charge"), missing=-1),
                _id_key(item.get("fragment_match_id")),
            ),
            "top_down_fragment_matches",
            add,
        )
        _validate_order(
            features,
            lambda item: _id_key(item.get("feature_id")),
            "top_down_features",
            add,
        )

        core_spectrum_ids = {item.spectrum_id for item in reader.read_spectra()}
        run_name = metadata.get("run_name")
        for prsm in prsms:
            prsm_id = prsm.get("prsm_id")
            if prsm.get("proteoform_id") not in proteoform_ids:
                add(
                    "TOP_DOWN_PRSM_PROTEOFORM_NOT_FOUND",
                    f"PrSM {prsm_id} references an unknown Proteoform",
                )
            if prsm.get("spectrum_id") not in core_spectrum_ids:
                add(
                    "TOP_DOWN_SPECTRUM_REFERENCE_NOT_FOUND",
                    f"PrSM {prsm_id} references an unknown core Spectrum",
                )
            reference = prsm.get("spectrum_reference")
            if not isinstance(reference, dict) or reference.get("run_name") != run_name:
                add(
                    "TOP_DOWN_CROSS_RUN_REFERENCE",
                    f"PrSM {prsm_id} has a mismatched run reference",
                )
            _positive_or_none(prsm, "precursor_mz", add)
            _positive_integer_or_none(prsm, "charge", add)
            _nonnegative_or_none(prsm, "precursor_mass", add)
            _nonnegative_or_none(prsm, "adjusted_mass", add)
            _probability_or_none(prsm, "p_value", add)
            _probability_or_none(prsm, "q_value", add)

        sequences = {
            item.get("proteoform_id"): item.get("sequence")
            for item in proteoforms
            if isinstance(item.get("proteoform_id"), str)
            and isinstance(item.get("sequence"), str)
        }
        for modification in modifications:
            owner = modification.get("proteoform_id")
            if owner not in proteoform_ids or modification.get("prsm_id") not in prsm_ids:
                add(
                    "TOP_DOWN_MODIFICATION_OWNER_NOT_FOUND",
                    f"Modification {modification.get('modification_id')} has an unknown owner",
                )
                continue
            left = modification.get("left_position")
            right = modification.get("right_position")
            sequence = sequences.get(owner, "")
            if (
                not isinstance(left, int)
                or isinstance(left, bool)
                or not isinstance(right, int)
                or isinstance(right, bool)
                or left < 0
                or right <= left
                or right > len(sequence)
            ):
                add(
                    "TOP_DOWN_MODIFICATION_POSITION_INVALID",
                    f"Modification {modification.get('modification_id')} position is outside the sequence",
                )
            _finite_or_none(modification, "mass_shift", add)

        for peak in peaks:
            if peak.get("prsm_id") not in prsm_ids:
                add(
                    "TOP_DOWN_FRAGMENT_PRSM_NOT_FOUND",
                    f"Peak {peak.get('peak_id')} references an unknown PrSM",
                )
            _positive_or_none(peak, "observed_mz", add)
            _nonnegative_or_none(peak, "intensity", add)
            _positive_integer_or_none(peak, "charge", add)
        for fragment in fragments:
            if fragment.get("prsm_id") not in prsm_ids:
                add(
                    "TOP_DOWN_FRAGMENT_PRSM_NOT_FOUND",
                    f"Fragment {fragment.get('fragment_match_id')} references an unknown PrSM",
                )
            if fragment.get("peak_id") not in peak_ids:
                add(
                    "TOP_DOWN_FRAGMENT_PEAK_NOT_FOUND",
                    f"Fragment {fragment.get('fragment_match_id')} references an unknown peak",
                )
            _positive_or_none(fragment, "observed_mz", add)
            _positive_or_none(fragment, "theoretical_mz", add)
            _positive_integer_or_none(fragment, "charge", add)
            _nonnegative_or_none(fragment, "intensity", add)
        for feature in features:
            if feature.get("prsm_id") not in prsm_ids:
                add(
                    "TOP_DOWN_FEATURE_PRSM_NOT_FOUND",
                    f"Feature {feature.get('feature_id')} references an unknown PrSM",
                )
            if feature.get("spectrum_id") not in core_spectrum_ids:
                add(
                    "TOP_DOWN_SPECTRUM_REFERENCE_NOT_FOUND",
                    f"Feature {feature.get('feature_id')} references an unknown Spectrum",
                )
            _nonnegative_or_none(feature, "intensity", add)

        if not modification_ids and any(
            item.get("modification_ids") for item in proteoforms
        ):
            add(
                "TOP_DOWN_MODIFICATION_OWNER_NOT_FOUND",
                "Proteoform modification_ids exist without Modification records",
            )
        for proteoform in proteoforms:
            for modification_id in proteoform.get("modification_ids", []):
                if modification_id not in modification_ids:
                    add(
                        "TOP_DOWN_MODIFICATION_OWNER_NOT_FOUND",
                        f"Proteoform {proteoform.get('proteoform_id')} references an unknown Modification",
                    )


def combine_top_down_validation(
    path: str | Path,
    physical: ValidationResult,
    *,
    extensions: list[ExtensionBlock] | list[dict[str, object]] | None = None,
) -> ValidationResult:
    if not physical.valid:
        return physical
    result = TopDownExtensionValidator().validate(path, extensions=extensions)
    return ValidationResult(
        valid=physical.valid and result.valid is not False,
        issues=list(physical.issues),
        checked_blocks=physical.checked_blocks,
        file_path=physical.file_path,
        version=physical.version,
        top_down_valid=result.valid,
        top_down_issues=list(result.issues),
        bottom_up_valid=physical.bottom_up_valid,
        bottom_up_issues=list(physical.bottom_up_issues),
        mode=physical.mode,
        file_sha256=physical.file_sha256,
        certificate_valid=physical.certificate_valid,
        deep_validation_reused=physical.deep_validation_reused,
        certificate_summary=dict(physical.certificate_summary),
        metrics=dict(physical.metrics),
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
    values: list[str] = []
    for record in records:
        value = record.get(field)
        if not isinstance(value, str) or not value:
            add("TOP_DOWN_INVALID_SCHEMA", f"{field} must be a non-empty string")
        else:
            values.append(value)
    for value in sorted(set(values)):
        if values.count(value) > 1:
            add(duplicate_code, f"Duplicate {field}: {value}")
    return set(values)


def _validate_order(records: list[dict[str, Any]], key: Any, label: str, add: Any) -> None:
    try:
        if records != sorted(records, key=key):
            add("TOP_DOWN_NONDETERMINISTIC_ORDER", f"{label} records are not sorted")
    except (TypeError, ValueError):
        add("TOP_DOWN_INVALID_SCHEMA", f"{label} sort fields are invalid")


def _id_key(value: Any) -> tuple[int, int | str]:
    text = str(value or "")
    suffix = text.rsplit(":", 1)[-1]
    return (0, int(suffix)) if suffix.isdigit() else (1, text)


def _integer_key(value: Any, *, missing: int = -2**63) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else missing


def _validate_finite(value: Any, location: str, add: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        add("TOP_DOWN_NONFINITE_NUMBER", f"Non-finite number at {location}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_finite(item, f"{location}.{key}", add)
    elif isinstance(value, list):
        for position, item in enumerate(value):
            _validate_finite(item, f"{location}[{position}]", add)


def _provenance_file_list(value: Any, label: str, add: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        add(
            "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
            f"{label} must be a list of file summaries",
        )
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        file_name = item.get("file_name")
        if (
            not isinstance(file_name, str)
            or not file_name
            or Path(file_name).is_absolute()
            or re.match(r"^[A-Za-z]:", file_name)
            or file_name.startswith("\\\\")
        ):
            add(
                "TOP_DOWN_DYNAMIC_SOURCE_VALUE",
                f"{label} contains an absolute or invalid file name",
            )
        if not isinstance(item.get("size"), int) or item.get("size", -1) < 0:
            add(
                "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
                f"{label} contains an invalid file size",
            )
        sha256 = item.get("sha256")
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            add(
                "TOP_DOWN_INVALID_INTERPRETATION_PROVENANCE",
                f"{label} contains an invalid SHA-256",
            )
        result.append(item)
    labels = [str(item.get("file_name", "")) for item in result]
    if labels != sorted(labels, key=lambda item: item.encode("utf-8")):
        add(
            "TOP_DOWN_NONDETERMINISTIC_ORDER",
            f"{label} is not deterministically sorted",
        )
    return result


def _finite_or_none(record: dict[str, Any], field: str, add: Any) -> None:
    value = record.get(field)
    if value is not None and (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        add("TOP_DOWN_INVALID_NUMERIC", f"{field} must be finite or null")


def _nonnegative_or_none(record: dict[str, Any], field: str, add: Any) -> None:
    _finite_or_none(record, field, add)
    value = record.get(field)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
        add("TOP_DOWN_INVALID_NUMERIC", f"{field} must be non-negative")


def _positive_or_none(record: dict[str, Any], field: str, add: Any) -> None:
    _finite_or_none(record, field, add)
    value = record.get(field)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 0:
        add("TOP_DOWN_INVALID_NUMERIC", f"{field} must be positive")


def _positive_integer_or_none(record: dict[str, Any], field: str, add: Any) -> None:
    value = record.get(field)
    if value is not None and (
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
    ):
        add("TOP_DOWN_INVALID_NUMERIC", f"{field} must be a positive integer or null")


def _probability_or_none(record: dict[str, Any], field: str, add: Any) -> None:
    _finite_or_none(record, field, add)
    value = record.get(field)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and not 0 <= value <= 1:
        add("TOP_DOWN_INVALID_NUMERIC", f"{field} must be between zero and one")
