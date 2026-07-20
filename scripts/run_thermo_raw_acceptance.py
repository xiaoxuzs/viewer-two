from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_layer.constants import BLOCK_NAMES
from binary_layer.conversion_exceptions import SourceConversionError, ThermoRawConversionError
from binary_layer.logical_comparison import build_extension_filtered_logical_fingerprint
from binary_layer.logical_fingerprint import LogicalArrayFingerprint
from binary_layer.models import ConversionOptions, PipelineContext, SourceProfile
from binary_layer.reader import ZpReader
from binary_layer.service import convert_source_to_zp
from binary_layer.thermo_raw_schema import THERMO_RAW_CONVERSION_EXTENSION_TYPE
from binary_layer.tools.common import IndexBuildTool, StringPoolBuildTool
from binary_layer.tools.real_mzml import RealMzmlParseTool
from binary_layer.v2_arrays_reader import ZpV2ArraysReader
from binary_layer.validator import ZpValidator
from binary_layer.writer import ZpWriter

NON_ARRAY_BLOCKS = tuple(name for name in BLOCK_NAMES if name != "arrays")


def main() -> int:
    args = _parse_args()
    source = args.source.resolve()
    converter = args.converter.resolve()
    work_root = args.work_root.resolve()
    intermediate_dir = work_root / "intermediate"
    output_dir = work_root / "output"
    logs_dir = work_root / "logs"
    results_dir = work_root / "results"
    for directory in (intermediate_dir, output_dir, logs_dir, results_dir):
        directory.mkdir(parents=True, exist_ok=True)
    target = (args.target or output_dir / f"{source.stem}.v2.zp").resolve()
    direct_target = output_dir / f"{source.stem}.direct-mzml.v2.zp"
    manifest_path = results_dir / "p2_a1_acceptance.json"
    if target.exists() or direct_target.exists():
        raise SystemExit(f"Acceptance output already exists: {target if target.exists() else direct_target}")

    source_before = _identity(source)
    try:
        result = convert_source_to_zp(
            source,
            target,
            format_version=2,
            options=ConversionOptions(
                converter_path=converter,
                temporary_directory=intermediate_dir,
                keep_intermediate=True,
                timeout_seconds=args.timeout_seconds,
            ),
        )
    except (ThermoRawConversionError, SourceConversionError) as exc:
        source_after = _identity(source)
        details = exc.details if isinstance(exc, ThermoRawConversionError) else {}
        _write_converter_logs(logs_dir, details)
        reason = {
            "THERMO_RAW_CONVERTER_NOT_FOUND": "thermo_raw_file_parser_not_found",
            "THERMO_RAW_DOWNSTREAM_MZML_REJECTED": "blocked_by_downstream_mzml_admission",
        }.get(exc.code, exc.code)
        manifest = {
            "conclusion": f"P2-A1 failed: {reason}",
            "reason": reason,
            "error_code": exc.code,
            "error_message": exc.message,
            "error_details": _jsonable(details),
            "source_before": source_before,
            "source_after": source_after,
            "source_unchanged": source_before == source_after,
            "target_exists": target.exists(),
        }
        _write_manifest(manifest_path, manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 2

    _write_converter_logs(
        logs_dir,
        {"converter_stdout": result.converter_stdout, "converter_stderr": result.converter_stderr},
    )
    if result.intermediate_path is None or not result.intermediate_path.is_file():
        raise RuntimeError("keep_intermediate=True did not retain the indexed mzML")

    direct_context = _build_direct_context(result.intermediate_path, result.intermediate_sha256 or "", result.source_before.mtime_ns)
    RealMzmlParseTool().run(direct_context)
    StringPoolBuildTool().run(direct_context)
    IndexBuildTool().run(direct_context)
    ZpWriter().write(direct_target, direct_context.blocks, format_version=2)
    direct_validation = ZpValidator().validate(direct_target)

    raw_reader = ZpReader(target)
    direct_reader = ZpReader(direct_target)
    raw_blocks = {name: raw_reader.read_block(name) for name in NON_ARRAY_BLOCKS}
    direct_blocks = {name: direct_reader.read_block(name) for name in NON_ARRAY_BLOCKS}
    raw_blocks_without_provenance = dict(raw_blocks)
    raw_blocks_without_provenance["extensions"] = _without_extension(
        raw_blocks["extensions"],
        THERMO_RAW_CONVERSION_EXTENSION_TYPE,
    )
    block_equal = {
        name: raw_blocks_without_provenance[name] == direct_blocks[name]
        for name in NON_ARRAY_BLOCKS
    }

    raw_arrays = _v2_array_fingerprints(target)
    direct_arrays = _v2_array_fingerprints(direct_target)
    all_array_hashes_equal = raw_arrays == direct_arrays
    raw_fingerprint = build_extension_filtered_logical_fingerprint(
        raw_blocks,
        raw_arrays,
        excluded_extension_types={THERMO_RAW_CONVERSION_EXTENSION_TYPE},
    )
    direct_fingerprint = build_extension_filtered_logical_fingerprint(
        direct_blocks,
        direct_arrays,
        excluded_extension_types={THERMO_RAW_CONVERSION_EXTENSION_TYPE},
    )

    spectra = raw_reader.read_spectra()
    precursors = raw_reader.read_precursors()
    chromatograms = raw_reader.read_chromatograms()
    spectrum_ids = _sample_ids((item.spectrum_id for item in spectra), 100)
    array_ids = _sample_ids((item.array_id for item in raw_arrays), 100)
    spectrum_samples_equal = all(
        raw_reader.read_spectrum_arrays(item) == direct_reader.read_spectrum_arrays(item)
        for item in spectrum_ids
    )
    array_samples_equal = all(raw_reader.read_array(item) == direct_reader.read_array(item) for item in array_ids)
    chromatogram_samples_equal = all(
        raw_reader.read_chromatogram_arrays(item.chromatogram_id)
        == direct_reader.read_chromatogram_arrays(item.chromatogram_id)
        for item in chromatograms
    )
    reader_sample_equal = spectrum_samples_equal and array_samples_equal and chromatogram_samples_equal
    source_unchanged = result.source_before == result.source_after
    intermediate_retained = result.intermediate_path.is_file()
    orphan_temp_files = [
        str(path)
        for path in intermediate_dir.rglob("*")
        if path.is_file() and path.resolve() != result.intermediate_path.resolve()
    ]
    raw_path_vs_direct_mzml_core_equal = all(block_equal.values()) and raw_fingerprint.sha256 == direct_fingerprint.sha256
    accepted = all(
        (
            result.validation.valid,
            result.validation.checked_blocks == 9,
            result.validation.issues == [],
            raw_reader.read_header().version == 2,
            direct_validation.valid,
            direct_validation.checked_blocks == 9,
            direct_validation.issues == [],
            source_unchanged,
            raw_path_vs_direct_mzml_core_equal,
            all_array_hashes_equal,
            reader_sample_equal,
            len(spectrum_ids) >= 100,
            len(array_ids) >= 100,
            intermediate_retained,
            not orphan_temp_files,
        )
    )
    manifest = {
        "conclusion": "P2-A1 Thermo RAW→.zp验收通过" if accepted else "P2-A1 failed: acceptance_condition_mismatch",
        "source": {
            "file_name": source.name,
            "file_size": result.source_before.file_size,
            "sha256": result.source_before.sha256,
            "mtime_ns": result.source_before.mtime_ns,
            "unchanged": source_unchanged,
        },
        "converter": {
            "path": str(result.converter_path),
            "name": result.converter_name,
            "version": result.converter_version,
            "command": list(result.converter_command),
            "exit_code": result.converter_exit_code,
        },
        "intermediate": {
            "path": str(result.intermediate_path),
            "file_size": result.intermediate_file_size,
            "sha256": result.intermediate_sha256,
            "indexed": True,
        },
        "data_statistics": {
            "spectrum_count": len(spectra),
            "ms1_count": sum(item.ms_level == 1 for item in spectra),
            "ms2_count": sum(item.ms_level == 2 for item in spectra),
            "ms3_plus_count": sum(item.ms_level >= 3 for item in spectra),
            "precursor_count": len(precursors),
            "chromatogram_count": len(chromatograms),
            "array_count": len(raw_arrays),
            "numeric_value_count": sum(item.value_count for item in raw_arrays),
        },
        "output": {
            "path": str(result.target_path),
            "format_version": result.format_version,
            "file_size": result.output_file_size,
            "sha256": result.output_sha256,
            "valid": result.validation.valid,
            "checked_blocks": result.validation.checked_blocks,
            "issues": [item.code for item in result.validation.issues],
        },
        "direct_mzml_output": {
            "path": str(direct_target),
            "valid": direct_validation.valid,
            "checked_blocks": direct_validation.checked_blocks,
            "issues": [item.code for item in direct_validation.issues],
        },
        "logical_consistency": {
            "raw_path_vs_direct_mzml_core_equal": raw_path_vs_direct_mzml_core_equal,
            "all_array_hashes_equal": all_array_hashes_equal,
            "spectrum_count_equal": len(spectra) == len(direct_reader.read_spectra()),
            "precursor_count_equal": len(precursors) == len(direct_reader.read_precursors()),
            "chromatogram_count_equal": len(chromatograms) == len(direct_reader.read_chromatograms()),
            "reader_sample_equal": reader_sample_equal,
            "block_equal": block_equal,
            "raw_filtered_fingerprint": raw_fingerprint.sha256,
            "direct_filtered_fingerprint": direct_fingerprint.sha256,
        },
        "reader_sampling": {
            "spectrum_sample_count": len(spectrum_ids),
            "array_sample_count": len(array_ids),
            "chromatogram_sample_count": len(chromatograms),
            "spectrum_samples_equal": spectrum_samples_equal,
            "array_samples_equal": array_samples_equal,
            "all_chromatograms_equal": chromatogram_samples_equal,
        },
        "temporary_files": {
            "keep_intermediate": True,
            "cleanup_result": result.cleanup_result,
            "intermediate_retained": intermediate_retained,
            "orphan_temp_files": orphan_temp_files,
        },
        "performance": result.performance,
    }
    _write_manifest(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if accepted else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one P2-A1 Thermo RAW acceptance conversion")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--converter", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=7200.0)
    return parser.parse_args()


def _build_direct_context(path: Path, sha256: str, raw_mtime_ns: int) -> PipelineContext:
    profile = SourceProfile(
        source_type="real_mzml",
        input_files=(path,),
        file_count=1,
        has_spectra=True,
        has_chromatograms=False,
        has_identification=False,
        has_quantification=False,
        requires_pre_conversion=False,
        notes=("P2-A1 direct intermediate mzML comparison.",),
        path=path,
        suffix=path.suffix,
        file_size=path.stat().st_size,
    )
    return PipelineContext(
        profile,
        metadata={
            "file_validated": True,
            "input_sha256": sha256,
            "block_created_at": datetime.fromtimestamp(raw_mtime_ns / 1_000_000_000, timezone.utc),
            "source_file_label": path.name,
        },
    )


def _v2_array_fingerprints(path: Path) -> tuple[LogicalArrayFingerprint, ...]:
    reader = ZpReader(path)
    arrays_entry = next(item for item in reader.read_directory() if item.block_name == "arrays")
    with path.open("rb") as stream:
        directory = ZpV2ArraysReader().read_directory(
            stream,
            block_offset=arrays_entry.offset,
            block_length=arrays_entry.length,
        )
    return tuple(
        LogicalArrayFingerprint(item.array_id, item.array_type, item.value_count, item.checksum)
        for item in directory.entries
    )


def _without_extension(value: object, extension_type: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("extensions block must be a list")
    return [
        item
        for item in value
        if not isinstance(item, dict) or item.get("extension_type") != extension_type
    ]


def _sample_ids(values: Iterable[str], required: int) -> tuple[str, ...]:
    items = tuple(values)
    if len(items) <= required:
        return items
    return tuple(items[(position * len(items)) // required] for position in range(required))


def _identity(path: Path) -> dict[str, int | str]:
    stat = path.stat()
    return {"file_size": stat.st_size, "sha256": _sha256(path), "mtime_ns": stat.st_mtime_ns}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_converter_logs(logs_dir: Path, details: dict[str, object]) -> None:
    (logs_dir / "thermo_raw_stdout.log").write_text(
        str(details.get("converter_stdout", "")), encoding="utf-8", errors="replace"
    )
    (logs_dir / "thermo_raw_stderr.log").write_text(
        str(details.get("converter_stderr", "")), encoding="utf-8", errors="replace"
    )


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _jsonable(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
