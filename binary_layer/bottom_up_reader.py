from __future__ import annotations

from pathlib import Path
from typing import Any

from .bottom_up_exceptions import BottomUpSchemaError
from .bottom_up_schema import BOTTOM_UP_EXTENSION_TYPES
from .bottom_up_validator import BottomUpExtensionValidator
from .reader import ZpReader


class BottomUpReader:
    """Logical Bottom-Up reader independent of the v1/v2 physical arrays layout."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        validation = BottomUpExtensionValidator().validate(self.path)
        if validation.valid is None:
            raise BottomUpSchemaError("File does not contain Bottom-Up extensions")
        if validation.valid is False:
            codes = ", ".join(item.code for item in validation.issues)
            raise BottomUpSchemaError(f"Bottom-Up extension validation failed: {codes}")
        extensions = ZpReader(self.path).read_extensions()
        self._payloads = {
            item.extension_type: item.payload
            for item in extensions
            if item.extension_type in BOTTOM_UP_EXTENSION_TYPES
        }
        self._indexes: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}

    def get_bottom_up_summary(self) -> dict[str, Any]:
        metadata = self.get_metadata()
        counts = metadata["entity_counts"]
        association = metadata["association"]
        return {
            "schema_name": "bottom_up_summary",
            "schema_version": 1,
            "source_type": metadata["source_type"],
            "adapter_flavor": metadata["adapter_flavor"],
            "identification_kind": metadata["identification_kind"],
            "run_id": metadata["core_run_id"],
            "report_run_name": metadata["report_run_name"],
            **counts,
            "associated_identification_count": association["associated_identification_count"],
            "distinct_ms2_count": association["distinct_ms2_count"],
            "dangling_spectrum_reference_count": association["dangling_spectrum_reference_count"],
            "fragment_support": metadata["fragment_support"],
        }

    def get_metadata(self) -> dict[str, Any]:
        return dict(self._payloads["bottom_up_metadata"]["metadata"])

    def get_bottom_up_identification(self, identification_id: str) -> dict[str, Any]:
        return self._one("bottom_up_identifications", "identification_id", identification_id)

    def get_bottom_up_identifications_for_spectrum(self, spectrum_id: str) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self._records("bottom_up_identifications")
            if item.get("spectrum_id") == spectrum_id
        ]

    def get_bottom_up_peptide(self, peptide_id: str) -> dict[str, Any]:
        return self._one("bottom_up_peptides", "peptide_id", peptide_id)

    def get_bottom_up_protein(self, protein_id: str) -> dict[str, Any]:
        return self._one("bottom_up_proteins", "protein_id", protein_id)

    def get_bottom_up_protein_group(self, protein_group_id: str) -> dict[str, Any]:
        return self._one("bottom_up_protein_groups", "protein_group_id", protein_group_id)

    def get_bottom_up_modifications_for_identification(
        self,
        identification_id: str,
    ) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self._records("bottom_up_modifications")
            if item.get("identification_id") == identification_id
        ]

    def get_bottom_up_fragment_matches(
        self,
        identification_id: str,
    ) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self._records("bottom_up_fragment_matches")
            if item.get("identification_id") == identification_id
        ]

    def get_bottom_up_quantification_summary(self) -> dict[str, Any]:
        records = self._records("bottom_up_quantification")
        by_kind: dict[str, int] = {}
        for record in records:
            kind = record.get("entity_kind")
            if isinstance(kind, str):
                by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "schema_name": "bottom_up_quantification_summary",
            "schema_version": 1,
            "record_count": len(records),
            "records_by_entity_kind": dict(sorted(by_kind.items())),
            "measurement_names": sorted(
                {
                    key
                    for record in records
                    for key in record.get("measurements", {})
                    if isinstance(key, str)
                }
            ),
        }

    def _records(self, extension_type: str) -> list[dict[str, Any]]:
        payload = self._payloads.get(extension_type)
        if payload is None:
            return []
        records = payload.get("records", [])
        return records if isinstance(records, list) else []

    def _one(self, extension_type: str, field: str, value: str) -> dict[str, Any]:
        key = (extension_type, field)
        index = self._indexes.get(key)
        if index is None:
            index = {
                str(item[field]): item
                for item in self._records(extension_type)
                if isinstance(item.get(field), str)
            }
            self._indexes[key] = index
        try:
            return dict(index[value])
        except KeyError:
            raise KeyError(value) from None


def get_bottom_up_summary(path: str | Path) -> dict[str, Any]:
    return BottomUpReader(path).get_bottom_up_summary()


def get_bottom_up_identification(path: str | Path, identification_id: str) -> dict[str, Any]:
    return BottomUpReader(path).get_bottom_up_identification(identification_id)


def get_bottom_up_identifications_for_spectrum(path: str | Path, spectrum_id: str) -> list[dict[str, Any]]:
    return BottomUpReader(path).get_bottom_up_identifications_for_spectrum(spectrum_id)


def get_bottom_up_peptide(path: str | Path, peptide_id: str) -> dict[str, Any]:
    return BottomUpReader(path).get_bottom_up_peptide(peptide_id)


def get_bottom_up_protein(path: str | Path, protein_id: str) -> dict[str, Any]:
    return BottomUpReader(path).get_bottom_up_protein(protein_id)


def get_bottom_up_protein_group(path: str | Path, protein_group_id: str) -> dict[str, Any]:
    return BottomUpReader(path).get_bottom_up_protein_group(protein_group_id)


def get_bottom_up_modifications_for_identification(path: str | Path, identification_id: str) -> list[dict[str, Any]]:
    return BottomUpReader(path).get_bottom_up_modifications_for_identification(identification_id)


def get_bottom_up_fragment_matches(path: str | Path, identification_id: str) -> list[dict[str, Any]]:
    return BottomUpReader(path).get_bottom_up_fragment_matches(identification_id)


def get_bottom_up_quantification_summary(path: str | Path) -> dict[str, Any]:
    return BottomUpReader(path).get_bottom_up_quantification_summary()
