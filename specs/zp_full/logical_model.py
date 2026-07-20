"""Version-neutral logical model derived from the independent inspector."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class LogicalZpDocument:
    global_meta: dict[str, object]
    runs: list[dict[str, object]]
    spectra: list[dict[str, object]]
    precursors: list[dict[str, object]]
    chromatograms: list[dict[str, object]]
    arrays: list[dict[str, object]]
    indexes: dict[str, object]
    extensions: list[dict[str, object]]
    string_pool: dict[str, object]

    @classmethod
    def from_inspection(cls, report: dict[str, Any]) -> "LogicalZpDocument":
        blocks = report["blocks"]
        global_meta = deepcopy(blocks["global_meta"])
        global_meta.pop("format_version", None)
        precursors = deepcopy(blocks["core_precursors"])
        for record in precursors:
            if isinstance(record, dict) and record.get("precursor_kind") in {
                None,
                "selected_precursor",
            }:
                record.pop("precursor_kind", None)
        arrays = sorted(
            deepcopy(report["arrays"]),
            key=lambda item: str(item["array_id"]).encode("utf-8"),
        )
        return cls(
            global_meta=global_meta,
            runs=deepcopy(blocks["core_runs"]),
            spectra=deepcopy(blocks["core_spectra"]),
            precursors=precursors,
            chromatograms=deepcopy(blocks["core_chromatograms"]),
            arrays=arrays,
            indexes=deepcopy(blocks["indexes"]),
            extensions=deepcopy(blocks["extensions"]),
            string_pool=deepcopy(blocks["string_pool"]),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "global_meta": self.global_meta,
            "runs": self.runs,
            "spectra": self.spectra,
            "precursors": self.precursors,
            "chromatograms": self.chromatograms,
            "arrays": self.arrays,
            "indexes": self.indexes,
            "extensions": self.extensions,
            "string_pool": self.string_pool,
        }


def logical_equivalence(left: LogicalZpDocument, right: LogicalZpDocument) -> dict[str, bool]:
    return {
        "logical_equal": left.as_dict() == right.as_dict(),
        "array_values_equal": left.arrays == right.arrays,
        "spectra_equal": left.spectra == right.spectra,
        "precursors_equal": left.precursors == right.precursors,
        "precursor_relationships_equal": [
            (item.get("spectrum_id"), item.get("precursor_id")) for item in left.spectra
        ]
        == [(item.get("spectrum_id"), item.get("precursor_id")) for item in right.spectra]
        and left.precursors == right.precursors,
        "chromatograms_equal": left.chromatograms == right.chromatograms,
        "references_equal": (
            left.spectra,
            left.precursors,
            left.chromatograms,
        )
        == (right.spectra, right.precursors, right.chromatograms),
        "indexes_equal": left.indexes == right.indexes,
        "extensions_equal": left.extensions == right.extensions,
        "extension_owners_equal": [
            item.get("payload") for item in left.extensions if item.get("extension_type") == "mzml_auxiliary_arrays"
        ]
        == [item.get("payload") for item in right.extensions if item.get("extension_type") == "mzml_auxiliary_arrays"],
        "string_pool_equal": left.string_pool == right.string_pool,
        "global_meta_counts_equal": all(
            left.global_meta.get(field) == right.global_meta.get(field)
            for field in ("run_count", "spectrum_count", "chromatogram_count", "array_count")
        ),
        "run_statistics_equal": [
            (item.get("run_id"), item.get("spectrum_count"), item.get("chromatogram_count"))
            for item in left.runs
        ]
        == [
            (item.get("run_id"), item.get("spectrum_count"), item.get("chromatogram_count"))
            for item in right.runs
        ],
    }
