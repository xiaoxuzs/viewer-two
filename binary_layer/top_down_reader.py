from __future__ import annotations

from pathlib import Path
from typing import Any

from .conversion_exceptions import TopDownSchemaError
from .reader import ZpReader
from .top_down_schema import TOP_DOWN_EXTENSION_TYPES
from .top_down_interpretation_schema import TOP_DOWN_INTERPRETATION_EXTENSION_TYPE
from .top_down_validator import TopDownExtensionValidator


class TopDownReader:
    """High-level reader for versioned Top-Down extensions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        validation = TopDownExtensionValidator().validate(self.path)
        if validation.valid is None:
            raise TopDownSchemaError("File does not contain Top-Down extensions")
        if validation.valid is False:
            codes = ", ".join(issue.code for issue in validation.issues)
            raise TopDownSchemaError(f"Top-Down extension validation failed: {codes}")
        extensions = ZpReader(self.path).read_extensions()
        self._payloads = {
            item.extension_type: item.payload
            for item in extensions
            if item.extension_type in (
                *TOP_DOWN_EXTENSION_TYPES,
                TOP_DOWN_INTERPRETATION_EXTENSION_TYPE,
            )
        }

    def get_top_down_summary(self) -> dict[str, Any]:
        proteoforms = self._records("top_down_proteoforms")
        prsms = self._records("top_down_prsms")
        modifications = self._records("top_down_modifications")
        fragments = self._records("top_down_fragment_matches")
        features = self._records("top_down_features")
        metadata = dict(self._payloads["top_down_metadata"]["metadata"])
        return {
            "schema_name": "top_down_summary",
            "schema_version": 1,
            "run_name": metadata["run_name"],
            "spectrum_source_type": metadata["spectrum_source_type"],
            "proteoform_count": len(proteoforms),
            "prsm_count": len(prsms),
            "modification_count": len(modifications),
            "fragment_match_count": len(fragments),
            "feature_count": len(features),
            "peak_count": self._payloads["top_down_fragment_matches"]["peak_count"],
            "associated_spectrum_count": len(
                {item["spectrum_id"] for item in prsms}
            ),
            "unique_protein_count": len(
                {item["protein_accession"] for item in proteoforms}
            ),
            "modified_proteoform_count": sum(
                bool(item["modification_ids"]) for item in proteoforms
            ),
            "prsm_with_fragment_match_count": len(
                {item["prsm_id"] for item in fragments}
            ),
        }

    def get_proteoform(self, proteoform_id: str) -> dict[str, Any]:
        return self._one("top_down_proteoforms", "proteoform_id", proteoform_id)

    def get_prsm(self, prsm_id: str) -> dict[str, Any]:
        return self._one("top_down_prsms", "prsm_id", prsm_id)

    def get_prsms_for_spectrum(self, spectrum_id: str) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self._records("top_down_prsms")
            if item["spectrum_id"] == spectrum_id
        ]

    def get_fragment_matches(self, prsm_id: str) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self._records("top_down_fragment_matches")
            if item["prsm_id"] == prsm_id
        ]

    def get_metadata(self) -> dict[str, Any]:
        return dict(self._payloads["top_down_metadata"]["metadata"])

    def get_top_down_interpretation_provenance(self) -> dict[str, Any] | None:
        payload = self._payloads.get(TOP_DOWN_INTERPRETATION_EXTENSION_TYPE)
        if payload is None:
            return None
        return dict(payload["provenance"])

    def _records(self, extension_type: str) -> list[dict[str, Any]]:
        return self._payloads[extension_type]["records"]

    def _one(self, extension_type: str, field: str, value: str) -> dict[str, Any]:
        match = next(
            (item for item in self._records(extension_type) if item[field] == value),
            None,
        )
        if match is None:
            raise KeyError(value)
        return dict(match)


def get_top_down_summary(path: str | Path) -> dict[str, Any]:
    return TopDownReader(path).get_top_down_summary()


def get_proteoform(path: str | Path, proteoform_id: str) -> dict[str, Any]:
    return TopDownReader(path).get_proteoform(proteoform_id)


def get_prsm(path: str | Path, prsm_id: str) -> dict[str, Any]:
    return TopDownReader(path).get_prsm(prsm_id)


def get_prsms_for_spectrum(path: str | Path, spectrum_id: str) -> list[dict[str, Any]]:
    return TopDownReader(path).get_prsms_for_spectrum(spectrum_id)


def get_fragment_matches(path: str | Path, prsm_id: str) -> list[dict[str, Any]]:
    return TopDownReader(path).get_fragment_matches(prsm_id)


def get_top_down_interpretation_provenance(
    path: str | Path,
) -> dict[str, Any] | None:
    return TopDownReader(path).get_top_down_interpretation_provenance()
