from __future__ import annotations

import hashlib
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from binary_layer.mzml_admission import MzmlFeatureProfile, evaluate_mzml_admission  # noqa: E402
from mzml_test_support import build_feature_profile  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _spectrum_domain_flags(path: Path) -> tuple[set[str], set[str]]:
    dia_ids: set[str] = set()
    ion_mobility_ids: set[str] = set()
    for _event, element in ET.iterparse(path, events=("end",)):
        if _local_name(element.tag) != "spectrum":
            continue
        names = {
            item.attrib.get("name", "").lower()
            for item in element.iter()
            if _local_name(item.tag) == "cvParam"
        }
        native_id = element.attrib.get("id", "")
        if any("data independent acquisition" in name for name in names):
            dia_ids.add(native_id)
        if any("ion mobility" in name or "drift time" in name for name in names):
            ion_mobility_ids.add(native_id)
        element.clear()
    return dia_ids, ion_mobility_ids


def _apply_domain_flags(path: Path, profile: MzmlFeatureProfile) -> MzmlFeatureProfile:
    dia_ids, ion_mobility_ids = _spectrum_domain_flags(path)
    return replace(
        profile,
        spectra=tuple(
            replace(
                item,
                has_dia_semantics=item.native_id in dia_ids,
                has_ion_mobility=item.native_id in ion_mobility_ids,
            )
            for item in profile.spectra
        ),
    )


def _reason_summary(issues: tuple[object, ...]) -> list[dict[str, object]]:
    counts = Counter(getattr(item, "code") for item in issues)
    first_locations: dict[str, str] = {}
    for item in issues:
        first_locations.setdefault(getattr(item, "code"), getattr(item, "location"))
    return [
        {"code": code, "count": count, "first_location": first_locations[code]}
        for code, count in counts.items()
    ]


def _coverage_tags(summary: dict[str, object]) -> list[str]:
    tags: list[str] = []
    tags.append("indexed_mzml" if summary["indexed"] else "nonindexed_mzml")
    dtypes = set(summary["array_dtype"])
    compressions = set(summary["compression"])
    if "float32" in dtypes:
        tags.append("float32_arrays")
    if "float64" in dtypes:
        tags.append("float64_arrays")
    if "zlib" in compressions:
        tags.append("zlib_compressed")
    if "none" in compressions:
        tags.append("uncompressed")
    if summary["ms1_count"] and not summary["ms2_count"] and not summary["ms3_plus_count"]:
        tags.append("ms1_only")
    if summary["ms1_count"] and summary["ms2_count"]:
        tags.append("ms1_ms2")
    if summary["precursor_count"]:
        tags.append("precursor")
    if summary["TIC_count"] or summary["BPC_count"]:
        tags.append("tic_or_bpc_chromatogram")
    if summary["file_size"] > 30_000_000:
        tags.append("over_30mb")
    return tags


def inspect_sample(sample_id: str, path: Path) -> dict[str, object]:
    started = time.perf_counter()
    resolved = path.resolve(strict=True)
    profile = _apply_domain_flags(resolved, build_feature_profile(resolved))
    admission_started = time.perf_counter()
    first = evaluate_mzml_admission(profile)
    second = evaluate_mzml_admission(profile)
    admission_seconds = time.perf_counter() - admission_started
    admission_stable = first == second

    dtypes = {
        value
        for item in profile.spectra
        for value in (item.mz_dtype, item.intensity_dtype)
        if value
    }
    dtypes.update(
        value
        for item in profile.chromatograms
        for value in (item.time_dtype, item.intensity_dtype)
        if value
    )
    compressions = {
        value
        for item in profile.spectra
        for value in (item.mz_compression, item.intensity_compression)
        if value
    }
    compressions.update(
        value
        for item in profile.chromatograms
        for value in (item.time_compression, item.intensity_compression)
        if value
    )
    representations = Counter(item.representation for item in profile.spectra)
    rt_units = {
        item.rt_unit_name for item in profile.spectra if item.rt_unit_name
    }
    rt_units.update(
        item.time_unit_name for item in profile.chromatograms if item.time_unit_name
    )
    summary: dict[str, object] = {
        "sample_id": sample_id,
        "file_name": resolved.name,
        "file_size": resolved.stat().st_size,
        "source_sha256": sha256_file(resolved),
        "indexed": profile.indexed,
        "spectrum_count": len(profile.spectra),
        "ms1_count": sum(item.ms_level == 1 for item in profile.spectra),
        "ms2_count": sum(item.ms_level == 2 for item in profile.spectra),
        "ms3_plus_count": sum(item.ms_level >= 3 for item in profile.spectra),
        "chromatogram_count": len(profile.chromatograms),
        "TIC_count": sum(item.chromatogram_type == "tic" for item in profile.chromatograms),
        "BPC_count": sum(item.chromatogram_type == "bpc" for item in profile.chromatograms),
        "array_dtype": sorted(dtypes),
        "compression": sorted(compressions),
        "representation_counts": dict(sorted(representations.items())),
        "precursor_count": sum(item.precursor_count for item in profile.spectra),
        "multiple_precursor_detected": any(item.precursor_count > 1 for item in profile.spectra),
        "selected_ion_count": sum(item.selected_ion_count for item in profile.spectra),
        "ion_mobility_detected": any(item.has_ion_mobility for item in profile.spectra),
        "DIA_detected": any(item.has_dia_semantics for item in profile.spectra),
        "SRM_detected": any(item.chromatogram_type == "srm" for item in profile.chromatograms),
        "RT_unit": sorted(rt_units),
        "admission": "accepted" if first.accepted else "rejected",
        "admission_result": first.accepted,
        "admission_issue_count": len(first.issues),
        "admission_reasons": _reason_summary(first.issues),
        "admission_stable": admission_stable,
        "inspection_seconds": round(time.perf_counter() - started, 6),
        "admission_seconds": round(admission_seconds, 6),
    }
    summary["coverage_tags"] = _coverage_tags(summary)
    return summary
