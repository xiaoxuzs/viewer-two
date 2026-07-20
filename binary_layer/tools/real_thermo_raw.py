from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from ..blocks import BlockCollection, ExtensionBlock
from ..conversion_exceptions import ThermoRawConversionError
from ..exceptions import MzmlAdmissionError
from ..models import ConversionOptions, PipelineContext, SourceProfile
from ..thermo_raw_adapter import ThermoRawAdapter, ThermoRawAdapterResult
from ..thermo_raw_schema import (
    THERMO_RAW_CONVERSION_EXTENSION_TYPE,
    THERMO_RAW_CONVERSION_SCHEMA_VERSION,
    ThermoRawConversionMetadataV1,
)
from .base import BaseBlockTool
from .real_mzml import RealMzmlExecutionReport, RealMzmlParseTool

THERMO_RAW_DOWNSTREAM_MZML_REJECTED = "THERMO_RAW_DOWNSTREAM_MZML_REJECTED"
THERMO_RAW_DOWNSTREAM_MZML_FAILED = "THERMO_RAW_DOWNSTREAM_MZML_FAILED"

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class RealThermoRawExecutionReport:
    adapter_result: ThermoRawAdapterResult
    mzml_report: RealMzmlExecutionReport
    block_created_at: datetime
    cleanup_result: str
    intermediate_retained: bool


class RealThermoRawParseTool(BaseBlockTool):
    name = "real_thermo_raw_parse"
    input_kinds = ("validated_source", "input_sha256")
    output_kinds = ("core_blocks", "arrays", "extensions")

    def __init__(self, adapter: ThermoRawAdapter | None = None) -> None:
        self.adapter = adapter or ThermoRawAdapter()
        self.last_report: RealThermoRawExecutionReport | None = None

    def build_blocks(self, context: PipelineContext) -> None:
        if context.blocks != BlockCollection():
            raise ThermoRawConversionError(
                "THERMO_RAW_BLOCK_COLLECTION_NOT_EMPTY",
                "real Thermo RAW parsing requires an empty BlockCollection",
            )
        profile = context.source_profile
        if profile.source_type != "real_thermo_raw" or len(profile.input_files) != 1:
            raise ThermoRawConversionError(
                "THERMO_RAW_INVALID_SOURCE",
                "real_thermo_raw_parse requires one real_thermo_raw input file",
            )
        source = profile.input_files[0]
        source_sha256 = context.metadata.get("input_sha256")
        if not isinstance(source_sha256, str) or _SHA256_PATTERN.fullmatch(source_sha256) is None:
            raise ThermoRawConversionError(
                "THERMO_RAW_INPUT_SHA256_MISSING",
                "hash_input must provide a lowercase SHA-256 before real_thermo_raw_parse",
            )
        options = context.metadata.get("conversion_options", ConversionOptions())
        if not isinstance(options, ConversionOptions):
            raise ThermoRawConversionError(
                "THERMO_RAW_OPTIONS_INVALID",
                "conversion_options must be a ConversionOptions instance",
            )

        adapter_result = self.adapter.convert(
            source,
            converter_path=options.converter_path,
            temporary_directory=options.temporary_directory,
            timeout_seconds=options.timeout_seconds,
        )
        try:
            source_stat = source.stat()
        except OSError as exc:
            error = ThermoRawConversionError(
                "THERMO_RAW_SOURCE_RECHECK_FAILED",
                f"Thermo RAW source could not be rechecked after conversion: {source}",
            )
            self._cleanup_after_failure(adapter_result, options, error)
            raise AssertionError("unreachable") from exc
        block_created_at = datetime.fromtimestamp(source_stat.st_mtime_ns / 1_000_000_000, timezone.utc)
        mzml_tool = RealMzmlParseTool()
        nested_profile = SourceProfile(
            source_type="real_mzml",
            input_files=(adapter_result.mzml_path,),
            file_count=1,
            has_spectra=True,
            has_chromatograms=False,
            has_identification=False,
            has_quantification=False,
            requires_pre_conversion=False,
            notes=("ThermoRawFileParser indexed mzML intermediate.",),
            path=adapter_result.mzml_path,
            suffix=adapter_result.mzml_path.suffix,
            file_size=adapter_result.intermediate_file_size,
        )
        nested_context = PipelineContext(
            nested_profile,
            metadata={
                "file_validated": True,
                "input_sha256": adapter_result.intermediate_sha256,
                "block_created_at": block_created_at,
                "source_file_label": adapter_result.mzml_path.name,
            },
        )
        try:
            mzml_tool.run(nested_context)
        except MzmlAdmissionError as exc:
            error = ThermoRawConversionError(
                THERMO_RAW_DOWNSTREAM_MZML_REJECTED,
                "ThermoRawFileParser output was rejected by the existing mzML Admission policy",
                details={
                    "admission_issue_codes": _admission_issue_codes(str(exc)),
                    "admission_summary": str(exc),
                    **_adapter_details(adapter_result),
                    "mzml_admission_seconds": mzml_tool.last_report.admission_seconds
                    if mzml_tool.last_report is not None
                    else "not_measured",
                    "mzml_parse_seconds": mzml_tool.last_report.parse_seconds
                    if mzml_tool.last_report is not None
                    else "not_measured",
                },
            )
            self._cleanup_after_failure(adapter_result, options, error)
            raise AssertionError("unreachable")
        except ThermoRawConversionError:
            raise
        except Exception as exc:
            error = ThermoRawConversionError(
                THERMO_RAW_DOWNSTREAM_MZML_FAILED,
                f"Existing RealMzmlParseTool failed for Thermo RAW intermediate: {exc}",
                details=_adapter_details(adapter_result),
            )
            self._cleanup_after_failure(adapter_result, options, error)
            raise AssertionError("unreachable")

        if mzml_tool.last_report is None:
            error = ThermoRawConversionError(
                THERMO_RAW_DOWNSTREAM_MZML_FAILED,
                "Existing RealMzmlParseTool produced no execution report",
            )
            self._cleanup_after_failure(adapter_result, options, error)
            raise AssertionError("unreachable")

        metadata = ThermoRawConversionMetadataV1(
            source_kind="thermo_raw",
            source_file_name=source.name,
            source_size=source_stat.st_size,
            source_sha256=source_sha256,
            converter_name=adapter_result.converter_name,
            converter_version=adapter_result.converter_version,
            intermediate_format="mzML",
            intermediate_indexed=adapter_result.intermediate_indexed,
            intermediate_sha256=adapter_result.intermediate_sha256,
        )
        nested_context.blocks.extensions.append(
            ExtensionBlock(
                extension_type=THERMO_RAW_CONVERSION_EXTENSION_TYPE,
                extension_version=str(THERMO_RAW_CONVERSION_SCHEMA_VERSION),
                payload=metadata.to_payload(),
            )
        )

        if options.keep_intermediate:
            cleanup_result = "retained"
        else:
            cleanup_result = self.adapter.cleanup_intermediate(adapter_result)
        self.last_report = RealThermoRawExecutionReport(
            adapter_result=adapter_result,
            mzml_report=mzml_tool.last_report,
            block_created_at=block_created_at,
            cleanup_result=cleanup_result,
            intermediate_retained=options.keep_intermediate,
        )
        context.blocks = nested_context.blocks

    def _cleanup_after_failure(
        self,
        adapter_result: ThermoRawAdapterResult,
        options: ConversionOptions,
        original_error: ThermoRawConversionError,
    ) -> None:
        if options.keep_intermediate:
            raise original_error
        try:
            self.adapter.cleanup_intermediate(adapter_result)
        except ThermoRawConversionError as cleanup_error:
            cleanup_error.details["original_error_code"] = original_error.code
            raise cleanup_error from original_error
        raise original_error


def _adapter_details(result: ThermoRawAdapterResult) -> dict[str, object]:
    return {
        "converter_path": str(result.converter_path),
        "converter_name": result.converter_name,
        "converter_version": result.converter_version,
        "converter_exit_code": result.exit_code,
        "converter_command": result.command,
        "converter_stdout": result.stdout,
        "converter_stderr": result.stderr,
        "intermediate_path": str(result.mzml_path),
        "intermediate_file_size": result.intermediate_file_size,
        "intermediate_sha256": result.intermediate_sha256,
        "raw_to_mzml_seconds": result.raw_to_mzml_seconds,
    }


def _admission_issue_codes(summary: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"(?:^|; )([A-Z][A-Z0-9_]+) at ", summary)))
