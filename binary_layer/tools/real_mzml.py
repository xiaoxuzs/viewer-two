from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ..blocks import ArrayBlock, BlockCollection, ChromatogramBlock, ExtensionBlock, GlobalMetaBlock, ISOLATION_WINDOW_KIND, NormalizedFloat64List, PrecursorBlock, RunBlock, SpectrumBlock
from ..constants import ZP_VERSION
from ..exceptions import BlockValidationError, MzmlAdmissionError, MzmlParseError
from ..models import PipelineContext
from ..mzml_adapter import ParsedMzmlDocument, parse_mzml
from ..mzml_admission import evaluate_mzml_admission
from ..mzml_schema import (
    MZML_AUXILIARY_ARRAYS_EXTENSION_TYPE,
    MZML_EXTENSION_SCHEMA_VERSION,
    MZML_METADATA_EXTENSION_TYPE,
    AuxiliaryArrayV1,
    MzmlAuxiliaryArraysV1,
    MzmlMetadataV1,
    NumericDtype,
    OwnerKind,
)
from .base import BaseBlockTool

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
DIA_MZML_METADATA_EXTENSION_TYPE = "dia_mzml_metadata"


@dataclass(frozen=True, slots=True)
class RealMzmlExecutionReport:
    parse_seconds: float
    admission_seconds: float
    block_build_seconds: float


class RealMzmlParseTool(BaseBlockTool):
    name = "real_mzml_parse"
    input_kinds = ("validated_source", "input_sha256")
    output_kinds = ("core_blocks", "arrays", "extensions")

    def __init__(self) -> None:
        self.last_report: RealMzmlExecutionReport | None = None

    def build_blocks(self, context: PipelineContext) -> None:
        if context.blocks != BlockCollection():
            raise MzmlParseError(
                "BLOCK_COLLECTION_NOT_EMPTY",
                "real mzML parsing requires an empty BlockCollection",
                "context.blocks",
            )
        if context.source_profile.source_type != "real_mzml" or len(context.source_profile.input_files) != 1:
            raise MzmlParseError(
                "INVALID_REAL_MZML_SOURCE",
                "real_mzml_parse requires one real_mzml input file",
                "context.source_profile",
            )
        digest = context.metadata.get("input_sha256")
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            raise MzmlParseError(
                "MISSING_INPUT_SHA256",
                "hash_input must provide a lowercase SHA-256 before real_mzml_parse",
                "context.metadata.input_sha256",
            )

        parse_started = time.perf_counter()
        document = parse_mzml(context.source_profile.input_files[0])
        parse_seconds = time.perf_counter() - parse_started
        admission_started = time.perf_counter()
        admission = evaluate_mzml_admission(document.feature_profile)
        admission_seconds = time.perf_counter() - admission_started
        if not admission.accepted:
            self.last_report = RealMzmlExecutionReport(parse_seconds, admission_seconds, 0.0)
            details = "; ".join(f"{item.code} at {item.location}: {item.message}" for item in admission.issues)
            raise MzmlAdmissionError(details)

        if not document.spectra:
            raise MzmlParseError("EMPTY_SPECTRUM_LIST", "at least one Spectrum is required", "run.spectra")
        if document.metadata_schema is None:
            raise MzmlParseError(
                "MZML_METADATA_UNAVAILABLE",
                "admitted Spectrum facts could not produce mzml_metadata v1",
                "mzml_metadata",
            )

        created_at = context.metadata.get("block_created_at")
        if created_at is None:
            created_at = datetime.now(timezone.utc)
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            raise MzmlParseError(
                "INVALID_BLOCK_CREATED_AT",
                "block_created_at must be a timezone-aware datetime",
                "context.metadata.block_created_at",
            )
        source_file_label = context.metadata.get("source_file_label")
        if source_file_label is None:
            source_file_label = str(document.source_path)
        if not isinstance(source_file_label, str) or not source_file_label:
            raise MzmlParseError(
                "INVALID_SOURCE_FILE_LABEL",
                "source_file_label must be a non-empty string",
                "context.metadata.source_file_label",
            )
        build_started = time.perf_counter()
        candidate = build_mzml_candidate(document, digest, created_at=created_at, source_file_label=source_file_label)
        validate_mzml_candidate(candidate)
        build_seconds = time.perf_counter() - build_started
        self.last_report = RealMzmlExecutionReport(parse_seconds, admission_seconds, build_seconds)
        context.blocks = candidate


def build_mzml_candidate(
    document: ParsedMzmlDocument,
    digest: str,
    *,
    created_at: datetime,
    source_file_label: str,
    acquisition_mode: str = "dda",
    source_type: str = "real_mzml",
    source_file_name: str | None = None,
    notes: list[str] | None = None,
) -> BlockCollection:
    if acquisition_mode not in {"dda", "dia"}:
        raise MzmlParseError(
            "INVALID_ACQUISITION_MODE",
            "acquisition_mode must be dda or dia",
            "build_mzml_candidate",
        )
    source = document.source_path
    spectra: list[SpectrumBlock] = []
    precursors: list[PrecursorBlock] = []
    chromatograms: list[ChromatogramBlock] = []
    arrays: list[ArrayBlock] = []
    for position, item in enumerate(document.spectra, 1):
        spectrum_id = f"spectrum_{position:06d}"
        mz_array_id = f"{spectrum_id}:mz"
        intensity_array_id = f"{spectrum_id}:intensity"
        if item.scan_number is None or item.rt_seconds is None:
            raise MzmlParseError(
                "INCOMPLETE_ADMITTED_SPECTRUM",
                "admitted Spectrum lacks a required scan number or normalized RT",
                f"spectrum[{position - 1}]",
            )
        precursor_id: str | None = None
        if item.ms_level == 2:
            if len(item.precursors) != 1:
                raise MzmlParseError(
                    "INCOMPLETE_ADMITTED_PRECURSOR",
                    "admitted MS2 Spectrum must contain exactly one precursor",
                    f"spectrum[{position - 1}].precursors",
                )
            precursor = item.precursors[0]
            precursor_id = f"{spectrum_id}:precursor"
            if acquisition_mode == "dia":
                target = precursor.isolation_target_mz
                lower_offset = precursor.isolation_lower_offset
                upper_offset = precursor.isolation_upper_offset
                if target is None or lower_offset is None or upper_offset is None:
                    raise MzmlParseError(
                        "DIA_WINDOW_MALFORMED",
                        "admitted DIA MS2 lacks a complete isolation window",
                        f"spectrum[{position - 1}].precursor",
                    )
                precursors.append(
                    PrecursorBlock(
                        precursor_id=precursor_id,
                        spectrum_id=spectrum_id,
                        precursor_mz=None,
                        charge=None,
                        intensity=None,
                        precursor_kind=ISOLATION_WINDOW_KIND,
                        isolation_lower_mz=target - lower_offset,
                        isolation_upper_mz=target + upper_offset,
                    )
                )
            else:
                if (
                    precursor.selected_ion_count != 1
                    or precursor.selected_ion_mz is None
                    or precursor.charge is None
                    or precursor.charge <= 0
                    or precursor.intensity is None
                ):
                    raise MzmlParseError(
                        "INCOMPLETE_ADMITTED_PRECURSOR",
                        "admitted MS2 precursor lacks one selected ion with m/z, positive charge, and intensity",
                        f"spectrum[{position - 1}].precursor",
                    )
                precursors.append(
                    PrecursorBlock(
                        precursor_id=precursor_id,
                        spectrum_id=spectrum_id,
                        precursor_mz=precursor.selected_ion_mz,
                        charge=precursor.charge,
                        intensity=precursor.intensity,
                    )
                )
        spectra.append(
            SpectrumBlock(
                spectrum_id=spectrum_id,
                run_id=document.run.run_id,
                ms_level=item.ms_level,
                scan_number=item.scan_number,
                native_id=item.native_id,
                rt=item.rt_seconds,
                precursor_id=precursor_id,
                mz_array_id=mz_array_id,
                intensity_array_id=intensity_array_id,
            )
        )
        arrays.extend(
            (
                ArrayBlock(mz_array_id, "mz", "float64", NormalizedFloat64List(item.mz_values)),
                ArrayBlock(intensity_array_id, "intensity", "float64", NormalizedFloat64List(item.intensity_values)),
            )
        )

    auxiliary_records: list[AuxiliaryArrayV1] = []
    admitted_chromatograms = tuple(
        item
        for item in document.chromatograms
        if acquisition_mode == "dda" or item.chromatogram_type in {"tic", "bpc"}
    )
    for position, item in enumerate(admitted_chromatograms, 1):
        chromatogram_id = f"chromatogram_{position:06d}"
        time_array_id = f"{chromatogram_id}:time"
        intensity_array_id = f"{chromatogram_id}:intensity"
        if (
            not item.native_id
            or item.chromatogram_type not in {"tic", "bpc"}
            or not item.time_values_seconds
            or not item.intensity_values
            or len(item.time_values_seconds) != len(item.intensity_values)
            or item.default_array_length != len(item.time_values_seconds)
        ):
            raise MzmlParseError(
                "INCOMPLETE_ADMITTED_CHROMATOGRAM",
                "admitted chromatogram lacks aligned nonempty arrays or source identity",
                f"chromatogram[{position - 1}]",
            )
        chromatograms.append(
            ChromatogramBlock(
                chromatogram_id=chromatogram_id,
                run_id=document.run.run_id,
                chromatogram_type=item.chromatogram_type,
                time_array_id=time_array_id,
                intensity_array_id=intensity_array_id,
                native_id=item.native_id,
            )
        )
        arrays.extend(
            (
                ArrayBlock(time_array_id, "time", "float64", NormalizedFloat64List(item.time_values_seconds)),
                ArrayBlock(intensity_array_id, "intensity", "float64", NormalizedFloat64List(item.intensity_values)),
            )
        )
        for auxiliary in item.auxiliary_arrays:
            if auxiliary.dtype is None:
                raise MzmlParseError(
                    "INCOMPLETE_ADMITTED_AUXILIARY_ARRAY",
                    "admitted auxiliary array lacks a dtype",
                    f"chromatogram[{position - 1}].auxiliary_arrays",
                )
            auxiliary_records.append(
                AuxiliaryArrayV1(
                    owner_kind=OwnerKind.CHROMATOGRAM,
                    owner_id=chromatogram_id,
                    array_accession=auxiliary.accession,
                    array_name=auxiliary.name,
                    dtype=NumericDtype(auxiliary.dtype),
                    values=auxiliary.values,
                    unit_accession=auxiliary.unit_accession,
                    unit_name=auxiliary.unit_name,
                )
            )

    rt_values = [item.rt for item in spectra]
    if acquisition_mode == "dia":
        extensions = [ExtensionBlock(
            extension_type=DIA_MZML_METADATA_EXTENSION_TYPE,
            extension_version="1",
            payload={
                "owner": "dia_acquisition",
                "schema_name": DIA_MZML_METADATA_EXTENSION_TYPE,
                "schema_version": 1,
                "record_count": len(spectra),
                "spectra": [
                    {
                        "spectrum_id": f"spectrum_{position:06d}",
                        "source_index": item.source_index,
                        "native_id": item.native_id,
                        "polarity": item.polarity,
                        "representation": item.representation,
                        "source_mz_dtype": item.source_mz_dtype,
                        "source_intensity_dtype": item.source_intensity_dtype,
                        "source_mz_compression": item.source_mz_compression,
                        "source_intensity_compression": item.source_intensity_compression,
                        "source_rt_value": item.source_rt_value,
                        "source_rt_unit_accession": item.source_rt_unit_accession,
                        "source_rt_unit_name": item.source_rt_unit_name,
                        "isolation_window_target_mz": (
                            item.precursors[0].isolation_target_mz
                            if len(item.precursors) == 1 else None
                        ),
                        "isolation_window_lower_offset": (
                            item.precursors[0].isolation_lower_offset
                            if len(item.precursors) == 1 else None
                        ),
                        "isolation_window_upper_offset": (
                            item.precursors[0].isolation_upper_offset
                            if len(item.precursors) == 1 else None
                        ),
                        "activation_methods": (
                            [
                                {
                                    "accession": method.accession,
                                    "name": method.name,
                                    "value": method.value,
                                }
                                for method in item.precursors[0].activation_methods
                            ]
                            if len(item.precursors) == 1 else []
                        ),
                        "collision_energy": (
                            item.precursors[0].collision_energy
                            if len(item.precursors) == 1 else None
                        ),
                        "source_selected_ion_mz": (
                            item.precursors[0].selected_ion_mz
                            if len(item.precursors) == 1 else None
                        ),
                        "source_selected_ion_intensity": (
                            item.precursors[0].intensity
                            if len(item.precursors) == 1 else None
                        ),
                        "source_selected_ion_charge": (
                            item.precursors[0].charge
                            if len(item.precursors) == 1 else None
                        ),
                    }
                    for position, item in enumerate(document.spectra, 1)
                ],
                "preserved_only_chromatogram_policy": (
                    "non_tic_bpc_manifest_only"
                ),
            },
        )]
    else:
        extensions = [ExtensionBlock(
            extension_type=MZML_METADATA_EXTENSION_TYPE,
            extension_version=str(MZML_EXTENSION_SCHEMA_VERSION),
            payload=document.metadata_schema.to_payload(),
        )]
    if auxiliary_records:
        auxiliary_schema = MzmlAuxiliaryArraysV1(tuple(auxiliary_records))
        extensions.append(
            ExtensionBlock(
                extension_type=MZML_AUXILIARY_ARRAYS_EXTENSION_TYPE,
                extension_version=str(MZML_EXTENSION_SCHEMA_VERSION),
                payload=auxiliary_schema.to_payload(),
            )
        )
    return BlockCollection(
        global_meta=GlobalMetaBlock(
            format_version=ZP_VERSION,
            source_type=source_type,
            source_file_name=source_file_name or source.name,
            source_file_hash=digest,
            run_count=1,
            spectrum_count=len(spectra),
            chromatogram_count=len(chromatograms),
            array_count=len(arrays),
            created_at=created_at,
            generator_name="zp-binary-layer",
            generator_version="0.1.0",
            notes=notes or ["P1-B5 real mzML conversion: strict single-run centroid MS1/MS2 plus TIC/BPC subset."],
        ),
        runs=[
            RunBlock(
                run_id=document.run.run_id,
                source_file=source_file_label,
                run_name=document.run.run_id,
                spectrum_count=len(spectra),
                chromatogram_count=len(chromatograms),
                start_rt=min(rt_values),
                end_rt=max(rt_values),
            )
        ],
        spectra=spectra,
        precursors=precursors,
        chromatograms=chromatograms,
        arrays=arrays,
        extensions=extensions,
    )


def validate_mzml_candidate(candidate: BlockCollection) -> None:
    meta = candidate.global_meta
    if meta is None or len(candidate.runs) != 1:
        raise BlockValidationError("real mzML candidate requires one GlobalMeta and one Run")
    if candidate.string_pool is not None or candidate.indexes is not None:
        raise BlockValidationError("P1-B5 candidate contains a forbidden derived Block")
    if (
        meta.run_count != 1
        or meta.spectrum_count != len(candidate.spectra)
        or meta.chromatogram_count != len(candidate.chromatograms)
        or meta.array_count != len(candidate.arrays)
    ):
        raise BlockValidationError("real mzML candidate counts are inconsistent")
    spectrum_ids = [item.spectrum_id for item in candidate.spectra]
    precursor_ids = [item.precursor_id for item in candidate.precursors]
    chromatogram_ids = [item.chromatogram_id for item in candidate.chromatograms]
    array_ids = [item.array_id for item in candidate.arrays]
    if (
        len(spectrum_ids) != len(set(spectrum_ids))
        or len(precursor_ids) != len(set(precursor_ids))
        or len(chromatogram_ids) != len(set(chromatogram_ids))
        or len(array_ids) != len(set(array_ids))
    ):
        raise BlockValidationError("real mzML candidate IDs must be unique")
    arrays = {item.array_id: item for item in candidate.arrays}
    precursors = {item.precursor_id: item for item in candidate.precursors}
    run_id = candidate.runs[0].run_id
    for spectrum in candidate.spectra:
        if spectrum.run_id != run_id or spectrum.ms_level not in (1, 2):
            raise BlockValidationError(f"invalid Spectrum ownership: {spectrum.spectrum_id}")
        if spectrum.ms_level == 1 and spectrum.precursor_id is not None:
            raise BlockValidationError(f"MS1 Spectrum has a precursor: {spectrum.spectrum_id}")
        if spectrum.ms_level == 2:
            precursor = precursors.get(spectrum.precursor_id or "")
            if precursor is None or precursor.spectrum_id != spectrum.spectrum_id:
                raise BlockValidationError(f"invalid MS2 precursor reference: {spectrum.spectrum_id}")
        if spectrum.mz_array_id not in arrays or arrays[spectrum.mz_array_id].array_type != "mz":
            raise BlockValidationError(f"missing m/z Array reference: {spectrum.spectrum_id}")
        if spectrum.intensity_array_id not in arrays or arrays[spectrum.intensity_array_id].array_type != "intensity":
            raise BlockValidationError(f"missing intensity Array reference: {spectrum.spectrum_id}")
    for chromatogram in candidate.chromatograms:
        if chromatogram.run_id != run_id or not chromatogram.native_id or chromatogram.chromatogram_type not in {"tic", "bpc"}:
            raise BlockValidationError(f"invalid Chromatogram ownership or identity: {chromatogram.chromatogram_id}")
        time_array = arrays.get(chromatogram.time_array_id)
        intensity_array = arrays.get(chromatogram.intensity_array_id)
        if time_array is None or time_array.array_type != "time":
            raise BlockValidationError(f"missing time Array reference: {chromatogram.chromatogram_id}")
        if intensity_array is None or intensity_array.array_type != "intensity":
            raise BlockValidationError(f"missing intensity Array reference: {chromatogram.chromatogram_id}")
        if not time_array.values or not intensity_array.values or len(time_array.values) != len(intensity_array.values):
            raise BlockValidationError(f"Chromatogram arrays must be nonempty and aligned: {chromatogram.chromatogram_id}")
        if any(not isinstance(value, (int, float)) or value < 0 for value in time_array.values):
            raise BlockValidationError(f"Chromatogram time values must be non-negative: {chromatogram.chromatogram_id}")
    if len(candidate.arrays) != 2 * (len(candidate.spectra) + len(candidate.chromatograms)):
        raise BlockValidationError("each Spectrum and Chromatogram must own exactly two core Arrays")
    linked_precursor_ids = {item.precursor_id for item in candidate.spectra if item.ms_level == 2}
    if linked_precursor_ids != set(precursors):
        raise BlockValidationError("real mzML candidate contains an orphan or multiply linked Precursor")
    extension_map = {item.extension_type: item for item in candidate.extensions}
    if len(extension_map) != len(candidate.extensions):
        raise BlockValidationError("mzML candidate Extension types must be unique")
    if meta.source_type == "real_dia_result_bundle":
        extension = extension_map.get(DIA_MZML_METADATA_EXTENSION_TYPE)
        if extension is None or extension.extension_version != "1":
            raise BlockValidationError("DIA candidate requires dia_mzml_metadata v1")
        payload = extension.payload
        records = payload.get("spectra") if isinstance(payload, dict) else None
        if (
            payload.get("owner") != "dia_acquisition"
            or payload.get("schema_name") != DIA_MZML_METADATA_EXTENSION_TYPE
            or payload.get("schema_version") != 1
            or payload.get("record_count") != len(candidate.spectra)
            or not isinstance(records, list)
            or [item.get("spectrum_id") for item in records if isinstance(item, dict)] != spectrum_ids
        ):
            raise BlockValidationError("DIA mzML metadata does not match core Spectra")
        allowed_extensions = {
            DIA_MZML_METADATA_EXTENSION_TYPE,
            MZML_AUXILIARY_ARRAYS_EXTENSION_TYPE,
        }
        if set(extension_map) - allowed_extensions:
            raise BlockValidationError("unexpected DIA mzML Extension type")
        return
    if MZML_METADATA_EXTENSION_TYPE not in extension_map:
        raise BlockValidationError("P1-B5 candidate requires one uniquely typed mzml_metadata extension")
    extension = extension_map[MZML_METADATA_EXTENSION_TYPE]
    if extension.extension_version != str(MZML_EXTENSION_SCHEMA_VERSION):
        raise BlockValidationError("unexpected real mzML extension identity")
    metadata = MzmlMetadataV1.from_payload(extension.payload)
    if tuple(item.spectrum_id for item in metadata.spectra) != tuple(spectrum_ids):
        raise BlockValidationError("mzml_metadata Spectrum IDs do not match core Spectra")
    if tuple(item.chromatogram_id for item in metadata.chromatograms) != tuple(chromatogram_ids):
        raise BlockValidationError("mzml_metadata Chromatogram IDs do not match core Chromatograms")
    auxiliary_extension = extension_map.get(MZML_AUXILIARY_ARRAYS_EXTENSION_TYPE)
    if auxiliary_extension is not None:
        if auxiliary_extension.extension_version != str(MZML_EXTENSION_SCHEMA_VERSION):
            raise BlockValidationError("unexpected mzml_auxiliary_arrays extension version")
        auxiliary_schema = MzmlAuxiliaryArraysV1.from_payload(auxiliary_extension.payload)
        if any(item.owner_kind is not OwnerKind.CHROMATOGRAM or item.owner_id not in set(chromatogram_ids) for item in auxiliary_schema.arrays):
            raise BlockValidationError("mzml auxiliary array owner does not match a core Chromatogram")
    elif len(candidate.extensions) != 1:
        raise BlockValidationError("unexpected real mzML extension type")
