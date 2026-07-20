from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..blocks import BlockCollection, ExtensionBlock
from ..conversion_exceptions import TopDownConversionError
from ..models import ConversionOptions, PipelineContext, SourceProfile
from ..serialization import to_primitive
from ..top_down_adapter import TopDownAdapter
from ..top_down_schema import (
    TOP_DOWN_EXTENSION_TYPES,
    TOP_DOWN_OWNER,
    TOP_DOWN_SCHEMA_VERSION,
    TopDownBundle,
    TopDownDocument,
    TopDownFeature,
    TopDownPrsm,
)
from ..top_down_interpretation_schema import (
    TOP_DOWN_INTERPRETATION_EXTENSION_TYPE,
    precomputed_provenance_payload,
)
from .base import BaseBlockTool
from .real_mzml import RealMzmlExecutionReport, RealMzmlParseTool
from .real_thermo_raw import RealThermoRawExecutionReport, RealThermoRawParseTool


@dataclass(frozen=True, slots=True)
class RealTopDownExecutionReport:
    document: TopDownDocument
    spectrum_source_sha256: str
    mzml_report: RealMzmlExecutionReport | None = None
    raw_report: RealThermoRawExecutionReport | None = None


class RealTopDownTool(BaseBlockTool):
    """Compose existing core spectrum blocks with Viewer-compatible TD extensions."""

    name = "real_top_down"
    input_kinds = ("validated_source", "input_sha256")
    output_kinds = ("core_blocks", "arrays", "extensions")

    def __init__(
        self,
        adapter: TopDownAdapter | None = None,
        mzml_tool: RealMzmlParseTool | None = None,
        raw_tool: RealThermoRawParseTool | None = None,
    ) -> None:
        self.adapter = adapter or TopDownAdapter()
        self.mzml_tool = mzml_tool or RealMzmlParseTool()
        self.raw_tool = raw_tool or RealThermoRawParseTool()
        self.last_report: RealTopDownExecutionReport | None = None

    def build_blocks(self, context: PipelineContext) -> None:
        if context.blocks != BlockCollection():
            raise TopDownConversionError(
                "TOP_DOWN_BLOCK_COLLECTION_NOT_EMPTY",
                "Top-Down composition requires an empty BlockCollection",
            )
        profile = context.source_profile
        bundle = profile.top_down_bundle
        if profile.source_type != "real_top_down_bundle" or not isinstance(bundle, TopDownBundle):
            raise TopDownConversionError(
                "TOP_DOWN_INVALID_SOURCE",
                "real_top_down requires one inspected TopDownBundle",
            )
        aggregate_sha256 = context.metadata.get("input_sha256")
        if not isinstance(aggregate_sha256, str) or len(aggregate_sha256) != 64:
            raise TopDownConversionError(
                "TOP_DOWN_INPUT_SHA256_MISSING",
                "hash_input must run before real_top_down",
            )

        document = self.adapter.load(bundle)
        spectrum_sha256 = _sha256(bundle.spectrum_source)
        nested_context = self._build_core_context(context, bundle, spectrum_sha256)
        if bundle.spectrum_source_type == "mzml":
            self.mzml_tool.run(nested_context)
            mzml_report = self.mzml_tool.last_report
            raw_report = None
        elif bundle.spectrum_source_type == "thermo_raw":
            self.raw_tool.run(nested_context)
            raw_report = self.raw_tool.last_report
            mzml_report = raw_report.mzml_report if raw_report is not None else None
        else:
            raise TopDownConversionError(
                "TOP_DOWN_UNSUPPORTED_SPECTRUM_SOURCE",
                f"Unsupported spectrum source type: {bundle.spectrum_source_type}",
            )

        associated = _associate_spectra(document, nested_context.blocks)
        nested_context.blocks.extensions.extend(_extension_blocks(associated))
        nested_context.blocks.extensions.append(
            ExtensionBlock(
                TOP_DOWN_INTERPRETATION_EXTENSION_TYPE,
                "1",
                precomputed_provenance_payload(bundle),
            )
        )
        meta = nested_context.blocks.global_meta
        if meta is None:
            raise TopDownConversionError(
                "TOP_DOWN_CORE_BLOCKS_MISSING",
                "Existing spectrum tool did not produce GlobalMeta",
            )
        meta.source_type = "real_top_down_bundle"
        meta.source_file_name = bundle.input_path.name
        meta.source_file_hash = aggregate_sha256
        meta.notes.append(
            "P2-B1 Viewer-compatible Top-Down data preserved in versioned extensions."
        )
        context.blocks = nested_context.blocks
        self.last_report = RealTopDownExecutionReport(
            document=associated,
            spectrum_source_sha256=spectrum_sha256,
            mzml_report=mzml_report,
            raw_report=raw_report,
        )

    def _build_core_context(
        self,
        context: PipelineContext,
        bundle: TopDownBundle,
        spectrum_sha256: str,
    ) -> PipelineContext:
        source = bundle.spectrum_source
        source_type = (
            "real_mzml" if bundle.spectrum_source_type == "mzml" else "real_thermo_raw"
        )
        try:
            source_stat = source.stat()
        except OSError as exc:
            raise TopDownConversionError(
                "TOP_DOWN_SPECTRUM_SOURCE_NOT_READABLE",
                f"Spectrum source cannot be read: {source.name}",
            ) from exc
        nested_profile = SourceProfile(
            source_type=source_type,
            input_files=(source,),
            file_count=1,
            has_spectra=True,
            has_chromatograms=False,
            has_identification=False,
            has_quantification=False,
            requires_pre_conversion=source_type == "real_thermo_raw",
            path=source,
            suffix=source.suffix,
            file_size=source_stat.st_size,
        )
        metadata: dict[str, object] = {
            "file_validated": True,
            "input_sha256": spectrum_sha256,
            "block_created_at": datetime.fromtimestamp(
                source_stat.st_mtime_ns / 1_000_000_000,
                timezone.utc,
            ),
            "source_file_label": bundle.relative_label(source),
            "conversion_options": context.metadata.get(
                "conversion_options",
                ConversionOptions(),
            ),
        }
        return PipelineContext(nested_profile, metadata=metadata)


def _associate_spectra(
    document: TopDownDocument,
    blocks: BlockCollection,
) -> TopDownDocument:
    if len(blocks.runs) != 1:
        raise TopDownConversionError(
            "TOP_DOWN_MULTIPLE_RUNS_NOT_SUPPORTED",
            "Top-Down bundle spectrum source must produce exactly one core run",
        )
    by_scan: dict[int, list[str]] = {}
    by_native: dict[str, list[str]] = {}
    for spectrum in blocks.spectra:
        by_scan.setdefault(spectrum.scan_number, []).append(spectrum.spectrum_id)
        by_native.setdefault(spectrum.native_id, []).append(spectrum.spectrum_id)

    associated_prsms: list[TopDownPrsm] = []
    spectrum_by_prsm: dict[str, str] = {}
    for prsm in document.prsms:
        candidates: set[str] = set()
        for scan_number in prsm.spectrum_reference.scan_numbers:
            candidates.update(by_scan.get(scan_number, ()))
        for native_id in prsm.spectrum_reference.native_ids:
            candidates.update(by_native.get(native_id, ()))
        if not candidates:
            raise TopDownConversionError(
                "TOP_DOWN_SPECTRUM_REFERENCE_NOT_FOUND",
                f"PrSM {prsm.prsm_id} does not resolve to a core Spectrum",
                details={"prsm_id": prsm.prsm_id},
            )
        if len(candidates) != 1:
            raise TopDownConversionError(
                "TOP_DOWN_AMBIGUOUS_SPECTRUM_REFERENCE",
                f"PrSM {prsm.prsm_id} resolves to multiple core Spectra",
                details={"prsm_id": prsm.prsm_id, "candidate_count": len(candidates)},
            )
        spectrum_id = next(iter(candidates))
        spectrum_by_prsm[prsm.prsm_id] = spectrum_id
        associated_prsms.append(replace(prsm, spectrum_id=spectrum_id))

    prsm_ids = set(spectrum_by_prsm)
    proteoform_ids = {item.proteoform_id for item in document.proteoforms}
    for fragment in document.fragment_matches:
        if fragment.prsm_id not in prsm_ids:
            raise TopDownConversionError(
                "TOP_DOWN_FRAGMENT_PRSM_NOT_FOUND",
                f"Fragment {fragment.fragment_match_id} references an unknown PrSM",
            )
    for modification in document.modifications:
        if (
            modification.prsm_id not in prsm_ids
            or modification.proteoform_id not in proteoform_ids
        ):
            raise TopDownConversionError(
                "TOP_DOWN_MODIFICATION_OWNER_NOT_FOUND",
                f"Modification {modification.modification_id} has an unknown owner",
            )
    associated_features: list[TopDownFeature] = []
    for feature in document.features:
        spectrum_id = spectrum_by_prsm.get(feature.prsm_id)
        if spectrum_id is None:
            raise TopDownConversionError(
                "TOP_DOWN_FEATURE_PRSM_NOT_FOUND",
                f"Feature {feature.feature_id} references an unknown PrSM",
            )
        associated_features.append(replace(feature, spectrum_id=spectrum_id))
    return replace(
        document,
        prsms=tuple(associated_prsms),
        features=tuple(associated_features),
    )


def _extension_blocks(document: TopDownDocument) -> list[ExtensionBlock]:
    bundle = document.bundle
    role_by_path: dict[Path, list[str]] = {}
    for role, value in (
        ("spectrum_source", bundle.spectrum_source),
        ("prsm_result", bundle.prsm_detail_files[0].parent),
        ("fragment_match_result", bundle.prsm_detail_files[0].parent),
        ("proteoform_result", bundle.proteoform_result),
        ("prsm_summary_result", bundle.prsm_summary_result),
        ("protein_database", bundle.protein_database),
        ("feature_result", bundle.feature_result),
        ("raw_prsm_result", bundle.raw_prsm_result),
        ("msalign_result", bundle.msalign_result),
        ("manifest", bundle.manifest_path),
    ):
        if value is not None:
            role_by_path.setdefault(value, []).append(role)
    source_files = [
        {
            "roles": sorted(role_by_path.get(path, ["prsm_detail"])),
            "source_file": bundle.relative_label(path),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(
            bundle.source_files,
            key=lambda item: bundle.relative_label(item).encode("utf-8"),
        )
    ]
    metadata = {
        "run_name": bundle.run_name,
        "spectrum_source_type": bundle.spectrum_source_type,
        "detected_roles": sorted(bundle.detected_roles),
        "source_files": source_files,
        "source_tables": to_primitive(document.source_tables),
        "source_field_coverage": to_primitive(document.source_field_coverage),
        "warnings": list(document.warnings),
    }
    payloads: dict[str, dict[str, Any]] = {
        "top_down_metadata": _payload("top_down_metadata", 1, metadata=metadata),
        "top_down_proteoforms": _payload(
            "top_down_proteoforms",
            len(document.proteoforms),
            records=to_primitive(document.proteoforms),
        ),
        "top_down_prsms": _payload(
            "top_down_prsms",
            len(document.prsms),
            records=to_primitive(document.prsms),
        ),
        "top_down_modifications": _payload(
            "top_down_modifications",
            len(document.modifications),
            records=to_primitive(document.modifications),
        ),
        "top_down_fragment_matches": _payload(
            "top_down_fragment_matches",
            len(document.fragment_matches),
            records=to_primitive(document.fragment_matches),
            peaks=to_primitive(document.peaks),
            peak_count=len(document.peaks),
        ),
        "top_down_features": _payload(
            "top_down_features",
            len(document.features),
            records=to_primitive(document.features),
        ),
    }
    return [
        ExtensionBlock(extension_type, str(TOP_DOWN_SCHEMA_VERSION), payloads[extension_type])
        for extension_type in TOP_DOWN_EXTENSION_TYPES
    ]


def _payload(
    schema_name: str,
    record_count: int,
    **content: Any,
) -> dict[str, Any]:
    return {
        "owner": TOP_DOWN_OWNER,
        "schema_name": schema_name,
        "schema_version": TOP_DOWN_SCHEMA_VERSION,
        "record_count": record_count,
        **content,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
