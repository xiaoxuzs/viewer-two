from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .top_down_schema import TOP_DOWN_OWNER

if TYPE_CHECKING:
    from .top_down_schema import TopDownBundle

TOP_DOWN_INTERPRETATION_SCHEMA_VERSION = 1
TOP_DOWN_INTERPRETATION_EXTENSION_TYPE = "top_down_interpretation_provenance"
TOP_DOWN_INTERPRETATION_ORIGINS = frozenset(
    {"precomputed_prsm_js", "generated_from_toppic_topfd"}
)


@dataclass(frozen=True, slots=True)
class TopDownInterpretationInputPair:
    toppic_prsm_xml: Path
    topfd_ms2_msalign: Path
    pairing_evidence: str
    prsm_count: int
    prsm_ids: tuple[str, ...]
    modification_count: int
    modification_counts_by_prsm: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class TopDownIntermediateBundle:
    schema_name: str
    schema_version: int
    input_path: Path
    root: Path
    run_name: str
    spectrum_source: Path
    spectrum_source_type: str
    input_pairs: tuple[TopDownInterpretationInputPair, ...]
    detected_roles: tuple[str, ...]
    source_files: tuple[Path, ...]

    @property
    def run_count(self) -> int:
        return 1

    @property
    def toppic_prsm_xml_files(self) -> tuple[Path, ...]:
        return tuple(pair.toppic_prsm_xml for pair in self.input_pairs)

    @property
    def topfd_ms2_msalign_files(self) -> tuple[Path, ...]:
        return tuple(pair.topfd_ms2_msalign for pair in self.input_pairs)

    def relative_label(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.name


@dataclass(frozen=True, slots=True)
class TopDownInterpretationOptions:
    script_path: Path
    python_executable: Path
    working_directory: Path
    timeout_seconds: float
    keep_generated_files: bool = False
    generated_directory: Path | None = None


@dataclass(frozen=True, slots=True)
class GeneratedPrsmArtifact:
    path: Path
    file_name: str
    prsm_id: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PrsmupExecution:
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class TopDownInterpretationResult:
    script_path: Path
    script_sha256: str
    python_executable: Path
    python_version: str
    working_directory: Path
    generated_prsm_artifacts: tuple[GeneratedPrsmArtifact, ...]
    executions: tuple[PrsmupExecution, ...]
    duration_seconds: float


def precomputed_provenance_payload(bundle: TopDownBundle) -> dict[str, Any]:
    artifacts = sorted(
        (
            _file_summary(path, bundle.relative_label(path))
            for path in bundle.prsm_detail_files
        ),
        key=lambda item: item["file_name"].encode("utf-8"),
    )
    provenance = {
        "interpretation_origin": "precomputed_prsm_js",
        "generator_name": "precomputed_prsm_js",
        "generator_script_sha256": None,
        "python_version": None,
        "toppic_prsm_xml_files": [],
        "topfd_ms2_msalign_files": [],
        "generated_prsm_files": artifacts,
        "generated_prsm_file_names": [item["file_name"] for item in artifacts],
        "generated_prsm_sha256": [item["sha256"] for item in artifacts],
        "generated_prsm_count": len(artifacts),
    }
    return _payload(provenance)


def generated_provenance_payload(
    bundle: TopDownIntermediateBundle,
    result: TopDownInterpretationResult,
) -> dict[str, Any]:
    xml_files = [
        _file_summary(path, bundle.relative_label(path))
        for path in bundle.toppic_prsm_xml_files
    ]
    msalign_files = [
        _file_summary(path, bundle.relative_label(path))
        for path in bundle.topfd_ms2_msalign_files
    ]
    artifacts = sorted(
        (
            {
                "file_name": item.file_name,
                "size": item.size,
                "sha256": item.sha256,
                "prsm_id": item.prsm_id,
            }
            for item in result.generated_prsm_artifacts
        ),
        key=lambda item: item["file_name"].encode("utf-8"),
    )
    provenance: dict[str, Any] = {
        "interpretation_origin": "generated_from_toppic_topfd",
        "generator_name": "prsmup.py",
        "generator_script_sha256": result.script_sha256,
        "python_version": result.python_version,
        "toppic_prsm_xml_files": xml_files,
        "topfd_ms2_msalign_files": msalign_files,
        "generated_prsm_files": artifacts,
        "generated_prsm_file_names": [item["file_name"] for item in artifacts],
        "generated_prsm_sha256": [item["sha256"] for item in artifacts],
        "generated_prsm_count": len(artifacts),
    }
    _add_single_input_fields(provenance, "toppic_prsm_xml", xml_files)
    _add_single_input_fields(provenance, "topfd_ms2_msalign", msalign_files)
    return _payload(provenance)


def _payload(provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "owner": TOP_DOWN_OWNER,
        "schema_name": TOP_DOWN_INTERPRETATION_EXTENSION_TYPE,
        "schema_version": TOP_DOWN_INTERPRETATION_SCHEMA_VERSION,
        "record_count": 1,
        "provenance": provenance,
    }


def _file_summary(path: Path, label: str) -> dict[str, Any]:
    return {
        "file_name": label,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _add_single_input_fields(
    provenance: dict[str, Any],
    prefix: str,
    summaries: list[dict[str, Any]],
) -> None:
    if len(summaries) != 1:
        return
    provenance[f"{prefix}_file_name"] = summaries[0]["file_name"]
    provenance[f"{prefix}_size"] = summaries[0]["size"]
    provenance[f"{prefix}_sha256"] = summaries[0]["sha256"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
