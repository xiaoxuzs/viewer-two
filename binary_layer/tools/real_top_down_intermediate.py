from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..blocks import BlockCollection, ExtensionBlock
from ..conversion_exceptions import TopDownConversionError
from ..models import ConversionOptions, PipelineContext, SourceProfile
from ..top_down_interpretation_adapter import TopDownInterpretationAdapter
from ..top_down_interpretation_schema import (
    TOP_DOWN_INTERPRETATION_EXTENSION_TYPE,
    TopDownIntermediateBundle,
    TopDownInterpretationResult,
    generated_provenance_payload,
)
from ..top_down_schema import TOP_DOWN_SCHEMA_VERSION, TopDownBundle
from .base import BaseBlockTool
from .real_top_down import RealTopDownExecutionReport, RealTopDownTool


@dataclass(frozen=True, slots=True)
class RealTopDownIntermediateExecutionReport:
    interpretation_result: TopDownInterpretationResult
    top_down_report: RealTopDownExecutionReport
    retained_directory: Path | None
    cleanup_result: str


class RealTopDownIntermediateTool(BaseBlockTool):
    """Generate prsm*.js, then delegate all TD parsing/composition to P2-B1."""

    name = "real_top_down_intermediate_parse"
    input_kinds = ("validated_source", "input_sha256")
    output_kinds = ("core_blocks", "arrays", "extensions")

    def __init__(
        self,
        adapter: TopDownInterpretationAdapter | None = None,
        top_down_tool: RealTopDownTool | None = None,
    ) -> None:
        self.adapter = adapter or TopDownInterpretationAdapter()
        self.top_down_tool = top_down_tool or RealTopDownTool()
        self.last_report: RealTopDownIntermediateExecutionReport | None = None

    def build_blocks(self, context: PipelineContext) -> None:
        if context.blocks != BlockCollection():
            raise TopDownConversionError(
                "TOP_DOWN_BLOCK_COLLECTION_NOT_EMPTY",
                "Top-Down intermediate composition requires an empty BlockCollection",
            )
        profile = context.source_profile
        bundle = profile.top_down_intermediate_bundle
        if (
            profile.source_type != "real_top_down_intermediate_bundle"
            or not isinstance(bundle, TopDownIntermediateBundle)
        ):
            raise TopDownConversionError(
                "TOP_DOWN_INVALID_SOURCE",
                "real_top_down_intermediate_parse requires an inspected intermediate bundle",
            )
        aggregate_sha256 = context.metadata.get("input_sha256")
        if not isinstance(aggregate_sha256, str) or len(aggregate_sha256) != 64:
            raise TopDownConversionError(
                "TOP_DOWN_INPUT_SHA256_MISSING",
                "hash_input must run before real_top_down_intermediate_parse",
            )
        conversion_options = context.metadata.get("conversion_options", ConversionOptions())
        if not isinstance(conversion_options, ConversionOptions):
            raise TopDownConversionError(
                "TOP_DOWN_INVALID_OPTIONS",
                "conversion_options must be a ConversionOptions instance",
            )
        interpretation_options = self.adapter.options_from_conversion(conversion_options)
        interpretation_result = self.adapter.generate(bundle, interpretation_options)
        finalized = False
        try:
            generated_bundle = TopDownBundle(
                schema_name="top_down_bundle",
                schema_version=TOP_DOWN_SCHEMA_VERSION,
                input_path=bundle.input_path,
                root=bundle.root,
                run_name=bundle.run_name,
                spectrum_source=bundle.spectrum_source,
                spectrum_source_type="mzml",
                prsm_detail_files=tuple(
                    item.path for item in interpretation_result.generated_prsm_artifacts
                ),
                # P2-B1 already treats this source table as optional while loading.
                # Intermediate inputs have no separate TopPIC proteoform TSV.
                proteoform_result=None,  # type: ignore[arg-type]
                detected_roles=(
                    "spectrum_source",
                    "prsm_result",
                    "fragment_match_result",
                ),
                source_files=(
                    bundle.spectrum_source,
                    *(item.path for item in interpretation_result.generated_prsm_artifacts),
                ),
            )
            nested_profile = SourceProfile(
                source_type="real_top_down_bundle",
                input_files=(bundle.input_path,),
                file_count=len(generated_bundle.source_files),
                has_spectra=True,
                has_chromatograms=False,
                has_identification=True,
                has_quantification=False,
                requires_pre_conversion=False,
                path=bundle.input_path,
                spectrum_source_type="mzml",
                detected_roles=generated_bundle.detected_roles,
                identity_files=generated_bundle.source_files,
                top_down_bundle=generated_bundle,
            )
            nested_context = PipelineContext(
                nested_profile,
                metadata={
                    "file_validated": True,
                    "input_sha256": aggregate_sha256,
                    "conversion_options": conversion_options,
                },
            )
            try:
                self.top_down_tool.run(nested_context)
            except TopDownConversionError as exc:
                raise _generated_output_error(exc) from exc
            top_down_report = self.top_down_tool.last_report
            if top_down_report is None:
                raise TopDownConversionError(
                    "PRSMUP_OUTPUT_MALFORMED",
                    "P2-B1 TopDownAdapter did not return an execution report",
                )
            expected_modification_count = sum(
                pair.modification_count for pair in bundle.input_pairs
            )
            generated_modification_count = len(top_down_report.document.modifications)
            expected_modification_counts_by_prsm = {
                prsm_id: count
                for pair in bundle.input_pairs
                for prsm_id, count in pair.modification_counts_by_prsm
            }
            generated_count_index = Counter(
                modification.prsm_id
                for modification in top_down_report.document.modifications
            )
            generated_modification_counts_by_prsm = {
                prsm_id: generated_count_index[prsm_id]
                for prsm_id in expected_modification_counts_by_prsm
            }
            mismatched_prsm_ids = [
                prsm_id
                for prsm_id, expected_count in expected_modification_counts_by_prsm.items()
                if generated_modification_counts_by_prsm[prsm_id] != expected_count
            ]
            if (
                generated_modification_count != expected_modification_count
                or mismatched_prsm_ids
            ):
                details = {
                    "xml_modification_count": expected_modification_count,
                    "generated_modification_count": generated_modification_count,
                    "mismatched_prsm_ids": mismatched_prsm_ids,
                    "per_prsm_counts": {
                        prsm_id: {
                            "xml": expected_modification_counts_by_prsm[prsm_id],
                            "generated": generated_modification_counts_by_prsm[prsm_id],
                        }
                        for prsm_id in mismatched_prsm_ids
                    },
                }
                raise TopDownConversionError(
                    "PRSMUP_OUTPUT_MALFORMED",
                    "Generated PrSM modification count does not match TopPIC XML",
                    details=details,
                )
            nested_context.blocks.extensions = [
                extension
                for extension in nested_context.blocks.extensions
                if extension.extension_type != TOP_DOWN_INTERPRETATION_EXTENSION_TYPE
            ]
            nested_context.blocks.extensions.append(
                ExtensionBlock(
                    TOP_DOWN_INTERPRETATION_EXTENSION_TYPE,
                    "1",
                    generated_provenance_payload(bundle, interpretation_result),
                )
            )
            meta = nested_context.blocks.global_meta
            if meta is None:
                raise TopDownConversionError(
                    "TOP_DOWN_CORE_BLOCKS_MISSING",
                    "P2-B1 composition did not return GlobalMeta",
                )
            meta.source_type = "real_top_down_intermediate_bundle"
            meta.notes.append(
                "P2-B2 TopPIC/TopFD interpretation generated by prsmup.py and parsed by P2-B1."
            )
            retained, cleanup_result = self.adapter.finalize(
                interpretation_result,
                interpretation_options,
            )
            finalized = True
            context.blocks = nested_context.blocks
            self.last_report = RealTopDownIntermediateExecutionReport(
                interpretation_result=interpretation_result,
                top_down_report=top_down_report,
                retained_directory=retained,
                cleanup_result=cleanup_result,
            )
        finally:
            if not finalized and interpretation_result.working_directory.exists():
                self.adapter.cleanup(
                    interpretation_result.working_directory,
                    interpretation_options.working_directory,
                )


def _generated_output_error(exc: TopDownConversionError) -> TopDownConversionError:
    if exc.code in {"TOP_DOWN_DUPLICATE_PRSM_ID", "TOP_DOWN_DUPLICATE_PROTEOFORM_ID"}:
        return TopDownConversionError("PRSMUP_OUTPUT_DUPLICATE_ID", exc.message)
    if exc.code in {
        "TOP_DOWN_SPECTRUM_REFERENCE_NOT_FOUND",
        "TOP_DOWN_AMBIGUOUS_SPECTRUM_REFERENCE",
        "TOP_DOWN_RUN_NAME_MISMATCH",
    }:
        return TopDownConversionError(
            "PRSMUP_OUTPUT_SPECTRUM_REFERENCE_INVALID",
            exc.message,
        )
    return TopDownConversionError("PRSMUP_OUTPUT_MALFORMED", exc.message)
