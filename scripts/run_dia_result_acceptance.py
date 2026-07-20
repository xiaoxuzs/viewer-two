from __future__ import annotations

import argparse
import bisect
import gc
import hashlib
import json
import math
import re
import struct
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
from pyteomics import mzml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_layer import (
    BottomUpReader,
    SourceInspector,
    ValidationResult,
    ZpReader,
    ZpValidator,
    validate_zp,
)
from binary_layer.bottom_up_validator import combine_bottom_up_validation
from binary_layer.dia_resource_limits import (
    DIA_V2_ARRAY_READ_LIMITS,
    DIA_V2_VALIDATION_LIMITS,
)
from binary_layer.quick_validator import (
    VALIDATOR_CONTRACT_VERSION,
    write_deep_validation_certificate,
)
from binary_layer.top_down_validator import combine_top_down_validation
from binary_layer.v2_arrays_reader import ZpV2ArraysReader


_SCAN = re.compile(r"(?<!\S)scan=(\d+)(?!\S)")
_FLOAT64 = struct.Struct("<d")
_CUTOFF = 0.01
_CHECKPOINT_STAGES = (
    "physical_validation_completed",
    "bottom_up_validation_completed",
    "source_array_comparison_completed",
    "association_comparison_completed",
    "reader_verification_completed",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_checkpoint(path: Path | None, file_sha256: str) -> dict[str, Any]:
    empty = {
        "checkpoint_schema_version": 1,
        "validator_version": VALIDATOR_CONTRACT_VERSION,
        "zp_file_sha256": file_sha256,
        "stages": {name: False for name in _CHECKPOINT_STAGES},
        "evidence": {},
    }
    if path is None or not path.is_file():
        return empty
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return empty
    if (
        not isinstance(loaded, dict)
        or loaded.get("checkpoint_schema_version") != 1
        or loaded.get("validator_version") != VALIDATOR_CONTRACT_VERSION
        or loaded.get("zp_file_sha256") != file_sha256
        or not isinstance(loaded.get("stages"), dict)
        or not isinstance(loaded.get("evidence"), dict)
    ):
        return empty
    loaded["stages"] = {
        name: loaded["stages"].get(name) is True for name in _CHECKPOINT_STAGES
    }
    return loaded


def _save_checkpoint(path: Path | None, checkpoint: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _array_sha256(value: object, *, scale: float = 1.0) -> tuple[str, int]:
    decoded = value.decode()  # type: ignore[union-attr]
    array = np.asarray(decoded, dtype="<f8")
    if scale != 1.0:
        array = np.multiply(array, scale, dtype="<f8")
    return hashlib.sha256(array.tobytes(order="C")).hexdigest(), int(array.size)


def _chromatogram_time_scale(path: Path) -> float:
    pattern = re.compile(
        rb'<cvParam\b[^>]*\baccession="MS:1000595"[^>]*/?>'
    )
    carry = b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            combined = carry + chunk
            match = pattern.search(combined)
            if match is not None:
                tag = match.group(0)
                if b'unitAccession="UO:0000031"' in tag:
                    return 60.0
                if b'unitAccession="UO:0000010"' in tag:
                    return 1.0
                raise AssertionError("unsupported chromatogram time-array unit")
            carry = combined[-4096:]
    raise AssertionError("chromatogram time-array unit is missing")


def _rt_seconds(record: dict[str, Any]) -> float:
    scans = record.get("scanList", {}).get("scan", [])
    if not isinstance(scans, list) or len(scans) != 1:
        raise AssertionError("spectrum does not contain exactly one scan")
    raw = scans[0].get("scan start time")
    value = float(raw)
    unit = str(getattr(raw, "unit_info", "")).casefold()
    if "minute" in unit:
        return value * 60.0
    if "second" in unit:
        return value
    raise AssertionError(f"unsupported RT unit: {unit!r}")


def _window(record: dict[str, Any]) -> tuple[float, float] | None:
    if int(record.get("ms level", 0)) != 2:
        return None
    precursors = record.get("precursorList", {}).get("precursor", [])
    if not isinstance(precursors, list) or len(precursors) != 1:
        raise AssertionError("DIA MS2 does not contain exactly one precursor container")
    isolation = precursors[0].get("isolationWindow", {})
    target = float(isolation["isolation window target m/z"])
    lower = float(isolation["isolation window lower offset"])
    upper = float(isolation["isolation window upper offset"])
    return target - lower, target + upper


def _canonical_source_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"$nonfinite": "NaN"}
        if math.isinf(value):
            return {"$nonfinite": "+Infinity" if value > 0 else "-Infinity"}
        return value
    raise AssertionError(f"unexpected DIA-NN source value: {type(value).__name__}")


def _array_directory(zp_path: Path, reader: ZpReader) -> dict[str, Any]:
    arrays_entry = next(
        item for item in reader.read_directory() if item.block_name == "arrays"
    )
    with zp_path.open("rb") as stream:
        directory = ZpV2ArraysReader(DIA_V2_ARRAY_READ_LIMITS).read_directory(
            stream,
            block_offset=arrays_entry.offset,
            block_length=arrays_entry.length,
        )
    return dict(directory.entries_by_id)


def compare_core_and_arrays(bundle: Any, zp_path: Path) -> dict[str, Any]:
    reader = ZpReader(zp_path, v2_limits=DIA_V2_ARRAY_READ_LIMITS)
    spectra = reader.read_spectra()
    chromatograms = reader.read_chromatograms()
    precursors = {item.precursor_id: item for item in reader.read_precursors()}
    directory = _array_directory(zp_path, reader)
    expected_array_ids = {
        *(item.mz_array_id for item in spectra),
        *(item.intensity_array_id for item in spectra),
        *(item.time_array_id for item in chromatograms),
        *(item.intensity_array_id for item in chromatograms),
    }
    if (
        len(directory) != 2 * (len(spectra) + len(chromatograms))
        or set(directory) != expected_array_ids
    ):
        raise AssertionError("unexpected real v2 array count")

    ms_levels: Counter[int] = Counter()
    window_counts: Counter[tuple[float, float]] = Counter()
    array_mismatches = 0
    metadata_mismatches = 0
    window_mismatches = 0
    source_array_value_count = 0
    with mzml.MzML(
        str(bundle.spectrum_source),
        use_index=True,
        decode_binary=False,
    ) as source:
        for position, record in enumerate(source):
            if position >= len(spectra):
                raise AssertionError("source contains more spectra than .zp")
            spectrum = spectra[position]
            native_id = str(record.get("id", ""))
            match = _SCAN.search(native_id)
            scan = int(match.group(1)) if match else None
            level = int(record.get("ms level", 0))
            ms_levels[level] += 1
            if (
                spectrum.native_id != native_id
                or spectrum.scan_number != scan
                or spectrum.ms_level != level
                or not math.isclose(spectrum.rt, _rt_seconds(record), abs_tol=1e-9)
            ):
                metadata_mismatches += 1

            for array_id, raw_value in (
                (spectrum.mz_array_id, record.get("m/z array")),
                (spectrum.intensity_array_id, record.get("intensity array")),
            ):
                digest, count = _array_sha256(raw_value)
                source_array_value_count += count
                entry = directory.get(array_id)
                if entry is None or entry.checksum != digest or entry.value_count != count:
                    array_mismatches += 1

            source_window = _window(record)
            if source_window is None:
                if spectrum.precursor_id is not None:
                    window_mismatches += 1
                continue
            window_counts[source_window] += 1
            precursor = precursors.get(str(spectrum.precursor_id))
            if (
                precursor is None
                or precursor.effective_precursor_kind != "isolation_window"
                or precursor.charge is not None
                or precursor.precursor_mz is not None
                or precursor.intensity is not None
                or precursor.isolation_lower_mz != source_window[0]
                or precursor.isolation_upper_mz != source_window[1]
            ):
                window_mismatches += 1

        time_scale = (
            _chromatogram_time_scale(bundle.spectrum_source)
            if chromatograms
            else 1.0
        )
        for chromatogram in chromatograms:
            record = source.get_by_id(chromatogram.native_id)
            if not isinstance(record, dict):
                raise AssertionError("source chromatogram is missing")
            expected_type_key = {
                "tic": "total ion current chromatogram",
                "bpc": "basepeak chromatogram",
            }[chromatogram.chromatogram_type]
            if expected_type_key not in record:
                metadata_mismatches += 1
            for array_id, raw_value, scale in (
                (chromatogram.time_array_id, record.get("time array"), time_scale),
                (
                    chromatogram.intensity_array_id,
                    record.get("intensity array"),
                    1.0,
                ),
            ):
                digest, count = _array_sha256(raw_value, scale=scale)
                source_array_value_count += count
                entry = directory.get(array_id)
                if (
                    entry is None
                    or entry.checksum != digest
                    or entry.value_count != count
                ):
                    array_mismatches += 1
    if len(spectra) != sum(ms_levels.values()):
        raise AssertionError("source contains fewer spectra than .zp")
    if array_mismatches or metadata_mismatches or window_mismatches:
        raise AssertionError(
            "core comparison failed: "
            f"arrays={array_mismatches}, metadata={metadata_mismatches}, "
            f"windows={window_mismatches}"
        )
    return {
        "spectrum_count": len(spectra),
        "ms1_count": ms_levels[1],
        "ms2_count": ms_levels[2],
        "precursor_count": len(precursors),
        "chromatogram_count": len(chromatograms),
        "distinct_isolation_window_count": len(window_counts),
        "array_count": len(directory),
        "source_array_value_count": source_array_value_count,
        "all_array_hashes_equal": True,
        "array_hash_mismatch_count": 0,
        "spectrum_metadata_mismatch_count": 0,
        "isolation_window_mismatch_count": 0,
        "core_dia_charge_fabricated": False,
        "identification_copied_to_core_precursor": False,
    }


class _ReferenceAssociator:
    def __init__(self, spectra: list[Any], precursors: list[Any]) -> None:
        precursor_by_id = {item.precursor_id: item for item in precursors}
        groups: dict[tuple[float, float], list[tuple[float, int, str]]] = defaultdict(list)
        for spectrum in spectra:
            if spectrum.ms_level != 2:
                continue
            precursor = precursor_by_id[str(spectrum.precursor_id)]
            groups[(precursor.isolation_lower_mz, precursor.isolation_upper_mz)].append(
                (spectrum.rt, spectrum.scan_number, spectrum.spectrum_id)
            )
        self.groups = [
            (
                lower,
                upper,
                tuple(item[0] for item in sorted(rows)),
                tuple(sorted(rows)),
            )
            for (lower, upper), rows in sorted(groups.items())
        ]

    def associate(self, rt_minutes: float, precursor_mz: float) -> str:
        target = rt_minutes * 60.0
        candidates: list[tuple[float, int, str]] = []
        for lower, upper, rts, rows in self.groups:
            if not lower <= precursor_mz <= upper:
                continue
            position = bisect.bisect_left(rts, target)
            for index in (position - 1, position):
                if 0 <= index < len(rows):
                    rt, scan, spectrum_id = rows[index]
                    candidates.append((abs(rt - target), scan, spectrum_id))
        if not candidates or min(candidates)[0] > 30.0:
            raise AssertionError("reference association failed")
        return min(candidates)[2]


def compare_bottom_up(bundle: Any, zp_path: Path) -> dict[str, Any]:
    reader = ZpReader(zp_path, v2_limits=DIA_V2_ARRAY_READ_LIMITS)
    spectra = reader.read_spectra()
    precursors = reader.read_precursors()
    reference = _ReferenceAssociator(spectra, precursors)
    del spectra, precursors

    bottom_up = BottomUpReader(zp_path)
    summary = bottom_up.get_bottom_up_summary()
    identifications = bottom_up._records("bottom_up_identifications")
    peptides = bottom_up._records("bottom_up_peptides")
    proteins = bottom_up._records("bottom_up_proteins")
    groups = bottom_up._records("bottom_up_protein_groups")
    ident_by_source = {item["source_precursor_id"]: item for item in identifications}
    source_ids: set[str] = set()
    source_peptides: set[str] = set()
    source_groups: set[str] = set()
    association_mismatches = 0
    field_mismatches = 0
    admitted = 0
    parquet = pq.ParquetFile(bundle.primary_report)
    for batch in parquet.iter_batches(batch_size=8192):
        names = tuple(batch.schema.names)
        columns = [batch.column(index).to_pylist() for index in range(len(names))]
        for row_index in range(batch.num_rows):
            row = {name: columns[index][row_index] for index, name in enumerate(names)}
            q_value = row.get("Q.Value")
            if row.get("Decoy") != 0 or not isinstance(q_value, (int, float)) or q_value >= _CUTOFF:
                continue
            admitted += 1
            source_id = str(row["Precursor.Id"])
            source_ids.add(source_id)
            source_peptides.add(str(row["Stripped.Sequence"]))
            group = str(row.get("Protein.Group") or "").strip()
            if group:
                source_groups.add(group)
            identification = ident_by_source.get(source_id)
            if identification is None:
                field_mismatches += 1
                continue
            if identification["spectrum_id"] != reference.associate(
                float(row["RT"]), float(row["Precursor.Mz"])
            ):
                association_mismatches += 1
            expected_source_fields = {
                name: _canonical_source_value(row[name]) for name in names
            }
            if identification["source_fields"] != expected_source_fields:
                field_mismatches += 1

    peptide_sequences = {item["sequence"] for item in peptides}
    group_strings = {item["source_group"] for item in groups}
    if (
        source_ids != set(ident_by_source)
        or source_peptides != peptide_sequences
        or source_groups != group_strings
        or association_mismatches
        or field_mismatches
    ):
        raise AssertionError("Bottom-Up reference comparison failed")

    by_spectrum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in identifications:
        by_spectrum[item["spectrum_id"]].append(item)
    single = next(items for items in by_spectrum.values() if len(items) == 1)[0]
    shared = next(items for items in by_spectrum.values() if len(items) > 1)[0]
    modified = next(item for item in identifications if item["modification_ids"])
    group_sample = next(item for item in groups if item["quantification_ids"])
    protein_sample = proteins[0]
    reader_checks = {
        "identification": bool(bottom_up.get_bottom_up_identification(single["identification_id"])),
        "single_spectrum_identification_count": len(
            bottom_up.get_bottom_up_identifications_for_spectrum(single["spectrum_id"])
        ),
        "shared_spectrum_identification_count": len(
            bottom_up.get_bottom_up_identifications_for_spectrum(shared["spectrum_id"])
        ),
        "peptide": bool(bottom_up.get_bottom_up_peptide(modified["peptide_id"])),
        "protein": bool(bottom_up.get_bottom_up_protein(protein_sample["protein_id"])),
        "protein_group": bool(
            bottom_up.get_bottom_up_protein_group(group_sample["protein_group_id"])
        ),
        "modification_count": len(
            bottom_up.get_bottom_up_modifications_for_identification(
                modified["identification_id"]
            )
        ),
        "fragment_match_count": len(
            bottom_up.get_bottom_up_fragment_matches(modified["identification_id"])
        ),
        "quantification_summary": bottom_up.get_bottom_up_quantification_summary(),
    }
    return {
        "summary": summary,
        "source_admitted_identification_count": admitted,
        "identification_set_equal": True,
        "peptide_set_equal": True,
        "protein_group_set_equal": True,
        "association_equal": True,
        "association_mismatch_count": 0,
        "all_69_source_fields_equal": True,
        "source_field_mismatch_count": 0,
        "reader_checks": reader_checks,
    }


def run(
    bundle_path: Path,
    zp_path: Path,
    *,
    checkpoint_path: Path | None = None,
    certificate_path: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile = SourceInspector().inspect((bundle_path,))
    bundle = profile.dia_result_bundle
    if bundle is None:
        raise AssertionError("input was not recognized as a DIA result bundle")
    source_before = {
        bundle.relative_label(item.path): {
            "size": item.path.stat().st_size,
            "sha256": _sha256(item.path),
            "role": item.role,
        }
        for item in bundle.source_files
    }
    quick = validate_zp(
        zp_path,
        mode="quick",
        certificate_path=(
            certificate_path
            if certificate_path is not None and certificate_path.is_file()
            else None
        ),
    )
    if not quick.valid or quick.file_sha256 is None:
        raise AssertionError(
            f"quick physical validation failed: {[item.code for item in quick.issues]}"
        )
    checkpoint = _load_checkpoint(checkpoint_path, quick.file_sha256)
    stages = checkpoint["stages"]
    evidence = checkpoint["evidence"]
    cached_extensions: list[dict[str, object]] | None = None
    if stages["physical_validation_completed"]:
        physical_metrics = evidence.get("physical_metrics", {})
        physical = ValidationResult(
            True,
            [],
            9,
            zp_path,
            int(evidence.get("format_version", quick.version or 2)),
            mode="deep",
            file_sha256=quick.file_sha256,
            metrics=(
                dict(physical_metrics) if isinstance(physical_metrics, dict) else {}
            ),
        )
    else:
        validator = ZpValidator()
        validator.v2_limits = DIA_V2_VALIDATION_LIMITS
        physical = validator.validate(zp_path)
        if not physical.valid:
            raise AssertionError(
                f"physical validation failed: {[item.code for item in physical.issues]}"
            )
        cached_extensions = getattr(validator, "_last_v2_extensions", None)
        stages["physical_validation_completed"] = True
        evidence["format_version"] = physical.version
        evidence["physical_metrics"] = dict(physical.metrics)
        _save_checkpoint(checkpoint_path, checkpoint)

    if stages["bottom_up_validation_completed"]:
        validation_metrics = evidence.get("validation_metrics", physical.metrics)
        validation = ValidationResult(
            True,
            [],
            physical.checked_blocks,
            zp_path,
            physical.version,
            bottom_up_valid=True,
            mode="deep",
            file_sha256=quick.file_sha256,
            metrics=(
                dict(validation_metrics)
                if isinstance(validation_metrics, dict)
                else dict(physical.metrics)
            ),
        )
    else:
        validation = combine_top_down_validation(
            zp_path,
            physical,
            extensions=cached_extensions,
        )
        validation = combine_bottom_up_validation(
            zp_path,
            validation,
            extensions=cached_extensions,
        )
        cached_extensions = None
        gc.collect()
        if not validation.valid:
            raise AssertionError(
                "unified business validation failed: "
                f"{[item.code for item in validation.bottom_up_issues]}"
            )
        stages["bottom_up_validation_completed"] = True
        evidence["validation_metrics"] = dict(validation.metrics)
        _save_checkpoint(checkpoint_path, checkpoint)

    if certificate_path is not None and not quick.deep_validation_reused:
        write_deep_validation_certificate(
            zp_path,
            validation,
            certificate_path=certificate_path,
        )

    if stages["source_array_comparison_completed"]:
        core = dict(evidence["core"])
    else:
        core = compare_core_and_arrays(bundle, zp_path)
        stages["source_array_comparison_completed"] = True
        evidence["core"] = core
        _save_checkpoint(checkpoint_path, checkpoint)
    gc.collect()
    if stages["association_comparison_completed"] and stages["reader_verification_completed"]:
        bottom_up = dict(evidence["bottom_up"])
    else:
        bottom_up = compare_bottom_up(bundle, zp_path)
        stages["association_comparison_completed"] = True
        stages["reader_verification_completed"] = True
        evidence["bottom_up"] = bottom_up
        _save_checkpoint(checkpoint_path, checkpoint)
    source_after = {
        bundle.relative_label(item.path): {
            "size": item.path.stat().st_size,
            "sha256": _sha256(item.path),
            "role": item.role,
        }
        for item in bundle.source_files
    }
    if source_after != source_before:
        raise AssertionError("real source identities changed during acceptance")
    return {
        "stage": "P2-C2",
        "source_type": profile.source_type,
        "adapter_flavor": "diann_2_parquet",
        "format_version": validation.version,
        "formal_real_dataset_count": 1,
        "cross_dataset_generalization_not_yet_proven": True,
        "bruker_d_implemented": False,
        "physical_valid": validation.valid,
        "physical_issues": [item.code for item in validation.issues],
        "checked_blocks": validation.checked_blocks,
        "bottom_up_valid": validation.bottom_up_valid,
        "bottom_up_issues": [item.code for item in validation.bottom_up_issues],
        "unified_valid": validation.valid,
        "zp": {
            "file_name": zp_path.name,
            "size": zp_path.stat().st_size,
            "sha256": quick.file_sha256,
        },
        "source_files": source_before,
        "source_files_unchanged": True,
        "core": core,
        "bottom_up": bottom_up,
        "checkpoint": {
            "validator_version": VALIDATOR_CONTRACT_VERSION,
            "zp_file_sha256": quick.file_sha256,
            "stages": dict(stages),
        },
        "validation_metrics": dict(validation.metrics),
        "acceptance_seconds": time.perf_counter() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--zp", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--sha256-manifest", required=True, type=Path)
    parser.add_argument("--checkpoint-json", type=Path)
    parser.add_argument("--certificate-json", type=Path)
    args = parser.parse_args()
    checkpoint_path = (
        args.checkpoint_json.resolve()
        if args.checkpoint_json is not None
        else args.report_json.resolve().with_name("P2_C2_1_CHECKPOINT.json")
    )
    certificate_path = (
        args.certificate_json.resolve()
        if args.certificate_json is not None
        else args.report_json.resolve().with_name("P2_C2_1_DEEP_CERTIFICATE.json")
    )
    report = run(
        args.bundle.resolve(),
        args.zp.resolve(),
        checkpoint_path=checkpoint_path,
        certificate_path=certificate_path,
    )
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.sha256_manifest.write_text(
        f"{report['zp']['sha256']}  {report['zp']['file_name']}\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
