from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from ..blocks import BlockCollection
from ..bottom_up_exceptions import DiaResultConversionError
from ..dia_result_adapter import DiaResultAdapter, DiaResultAdapterReport
from ..dia_result_bundle import SOURCE_TYPE
from ..dia_spectrum_association import DiaSpectrumAssociator
from ..exceptions import MzmlAdmissionError, MzmlParseError
from ..models import PipelineContext
from ..mzml_adapter import parse_mzml
from ..mzml_admission import evaluate_mzml_admission
from .base import BaseBlockTool
from .real_mzml import build_mzml_candidate, validate_mzml_candidate

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class RealDiaResultExecutionReport:
    mzml_parse_seconds: float
    mzml_admission_seconds: float
    mzml_block_build_seconds: float
    parquet_parse_seconds: float
    association_seconds: float
    extension_build_seconds: float
    identification_count: int
    peptide_count: int
    protein_group_count: int
    mzml_parse_cpu_seconds: float
    mzml_admission_cpu_seconds: float
    mzml_block_build_cpu_seconds: float
    parquet_parse_cpu_seconds: float
    association_cpu_seconds: float
    extension_build_cpu_seconds: float
    parquet_batch_count: int
    parquet_row_count: int
    spectrum_count: int
    array_count: int
    array_value_count: int


class RealDiaResultTool(BaseBlockTool):
    name = "real_dia_result"
    input_kinds = ("validated_source", "input_sha256")
    output_kinds = ("core_blocks", "arrays", "extensions")

    def __init__(self, adapter: DiaResultAdapter | None = None) -> None:
        self.adapter = adapter or DiaResultAdapter()
        self.last_report: RealDiaResultExecutionReport | None = None

    def build_blocks(self, context: PipelineContext) -> None:
        if context.blocks != BlockCollection():
            raise DiaResultConversionError(
                "BLOCK_COLLECTION_NOT_EMPTY",
                "DIA result conversion requires an empty BlockCollection",
            )
        profile = context.source_profile
        bundle = profile.dia_result_bundle
        if profile.source_type != SOURCE_TYPE or bundle is None:
            raise DiaResultConversionError(
                "INVALID_DIA_RESULT_SOURCE",
                "real_dia_result requires an inspected DIA-NN result bundle",
            )
        digest = context.metadata.get("input_sha256")
        hashes = context.metadata.get("source_file_hashes")
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            raise DiaResultConversionError(
                "MISSING_INPUT_SHA256",
                "hash_input must provide the bundle SHA-256",
            )
        if not isinstance(hashes, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in hashes.items()
        ):
            raise DiaResultConversionError(
                "MISSING_INPUT_SHA256",
                "hash_input must provide per-file bundle SHA-256 values",
            )

        parse_started = time.perf_counter()
        parse_cpu_started = time.process_time()
        document = parse_mzml(bundle.spectrum_source)
        dia_features = []
        for feature, spectrum in zip(document.feature_profile.spectra, document.spectra):
            precursor = spectrum.precursors[0] if len(spectrum.precursors) == 1 else None
            dia_features.append(
                replace(
                    feature,
                    isolation_target_mz=precursor.isolation_target_mz if precursor else None,
                    isolation_lower_offset=precursor.isolation_lower_offset if precursor else None,
                    isolation_upper_offset=precursor.isolation_upper_offset if precursor else None,
                    charge_present=precursor.charge_present if precursor else False,
                )
            )
        admitted_chromatograms = tuple(
            item
            for item in document.chromatograms
            if item.chromatogram_type in {"tic", "bpc"}
        )
        feature_profile = replace(
            document.feature_profile,
            spectra=tuple(dia_features),
        )
        document = replace(
            document,
            feature_profile=feature_profile,
            chromatograms=admitted_chromatograms,
        )
        parse_seconds = time.perf_counter() - parse_started
        parse_cpu_seconds = time.process_time() - parse_cpu_started
        admission_started = time.perf_counter()
        admission_cpu_started = time.process_time()
        admission = evaluate_mzml_admission(
            document.feature_profile,
            acquisition_mode="dia",
        )
        admission_seconds = time.perf_counter() - admission_started
        admission_cpu_seconds = time.process_time() - admission_cpu_started
        if not admission.accepted:
            details = "; ".join(
                f"{item.code} at {item.location}: {item.message}"
                for item in admission.issues
            )
            raise MzmlAdmissionError(details)
        if not document.spectra:
            raise MzmlParseError(
                "MZML_METADATA_UNAVAILABLE",
                "Admitted DIA mzML contains no spectra",
                "mzml_metadata",
            )
        spectrum_label = bundle.relative_label(bundle.spectrum_source)
        spectrum_sha256 = hashes.get(spectrum_label)
        if not isinstance(spectrum_sha256, str) or _SHA256_PATTERN.fullmatch(spectrum_sha256) is None:
            raise DiaResultConversionError(
                "MISSING_INPUT_SHA256",
                "Spectrum source SHA-256 is unavailable",
            )
        created_at = datetime.fromtimestamp(
            bundle.output_created_at_millis / 1000.0,
            tz=timezone.utc,
        )
        build_started = time.perf_counter()
        build_cpu_started = time.process_time()
        candidate = build_mzml_candidate(
            document,
            digest,
            created_at=created_at,
            source_file_label=bundle.spectrum_source.name,
            acquisition_mode="dia",
            source_type=SOURCE_TYPE,
            source_file_name=bundle.primary_report.name,
            notes=[
                "P2-C2 real Thermo DIA mzML plus DIA-NN 2.0 Parquet result bundle.",
                "DIA MS2 core precursors are isolation windows; identification charge and m/z remain in extensions.",
            ],
        )
        validate_mzml_candidate(candidate)
        block_seconds = time.perf_counter() - build_started
        block_cpu_seconds = time.process_time() - build_cpu_started
        run_id = candidate.runs[0].run_id
        associator = DiaSpectrumAssociator(candidate)
        adapter_report: DiaResultAdapterReport = self.adapter.read(
            bundle,
            run_id=run_id,
            spectrum_file_sha256=spectrum_sha256,
            source_file_hashes=hashes,
            associator=associator,
        )
        candidate.extensions.extend(adapter_report.document.extension_blocks())
        context.blocks = candidate
        counts = adapter_report.document.metadata["entity_counts"]
        self.last_report = RealDiaResultExecutionReport(
            mzml_parse_seconds=parse_seconds,
            mzml_admission_seconds=admission_seconds,
            mzml_block_build_seconds=block_seconds,
            parquet_parse_seconds=adapter_report.parquet_parse_seconds,
            association_seconds=adapter_report.association_seconds,
            extension_build_seconds=adapter_report.extension_build_seconds,
            identification_count=int(counts["identification"]),
            peptide_count=int(counts["peptide"]),
            protein_group_count=int(counts["protein_group"]),
            mzml_parse_cpu_seconds=parse_cpu_seconds,
            mzml_admission_cpu_seconds=admission_cpu_seconds,
            mzml_block_build_cpu_seconds=block_cpu_seconds,
            parquet_parse_cpu_seconds=adapter_report.parquet_parse_cpu_seconds,
            association_cpu_seconds=adapter_report.association_cpu_seconds,
            extension_build_cpu_seconds=adapter_report.extension_build_cpu_seconds,
            parquet_batch_count=adapter_report.parquet_batch_count,
            parquet_row_count=adapter_report.parquet_row_count,
            spectrum_count=len(candidate.spectra),
            array_count=len(candidate.arrays),
            array_value_count=sum(len(item.values) for item in candidate.arrays),
        )
