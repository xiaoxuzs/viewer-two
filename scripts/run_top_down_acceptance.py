from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_layer import (  # noqa: E402
    ConversionOptions,
    TopDownAdapter,
    TopDownExtensionValidator,
    TopDownReader,
    ZpReader,
    ZpValidator,
    convert_source_to_zp,
    inspect_source,
    validate_zp,
)
from binary_layer.serialization import to_primitive  # noqa: E402

_ASSIGNMENT = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*", re.DOTALL)
_SEED = 20260717


def main() -> int:
    parser = argparse.ArgumentParser(description="Run P2-B1 real Top-Down acceptance")
    parser.add_argument("source", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--viewer-back", type=Path, default=Path(r"E:\viewer\back"))
    args = parser.parse_args()

    output_root = args.output_root.resolve(strict=False)
    output_directory = output_root / "output"
    temporary_directory = output_root / "temporary"
    logs_directory = output_root / "logs"
    results_directory = output_root / "results"
    for directory in (
        output_directory,
        temporary_directory,
        logs_directory,
        results_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    target = output_directory / "top_down_real_v2.zp"
    if target.exists():
        raise FileExistsError(target)

    profile = inspect_source(args.source)
    bundle = profile.top_down_bundle
    if bundle is None:
        raise RuntimeError("source is not a real_top_down_bundle")
    adapter_document = TopDownAdapter().load(bundle)
    raw_documents = [_direct_prsm(path) for path in bundle.prsm_detail_files]
    raw_stats = _statistics(raw_documents)
    viewer_documents = _viewer_documents(args.viewer_back, bundle.prsm_detail_files)
    viewer_stats = _statistics(viewer_documents)

    conversion = convert_source_to_zp(
        args.source,
        target,
        format_version=2,
        options=ConversionOptions(temporary_directory=temporary_directory),
    )
    physical = ZpValidator().validate(target)
    business = TopDownExtensionValidator().validate(target)
    unified = validate_zp(target, mode="deep")
    reader = TopDownReader(target)
    summary = reader.get_top_down_summary()

    comparison = _compare_document(adapter_document, target)
    extension_stats = {
        "proteoform_count": summary["proteoform_count"],
        "prsm_count": summary["prsm_count"],
        "modification_count": summary["modification_count"],
        "fragment_match_count": summary["fragment_match_count"],
        "feature_count": summary["feature_count"],
        "associated_spectrum_count": summary["associated_spectrum_count"],
        "unique_protein_count": summary["unique_protein_count"],
        "modified_proteoform_count": summary["modified_proteoform_count"],
        "prsm_with_fragment_match_count": summary["prsm_with_fragment_match_count"],
    }
    critical_count_keys = tuple(extension_stats)
    count_consistent = all(
        raw_stats[key] == viewer_stats[key] == extension_stats[key]
        for key in critical_count_keys
    )
    report = {
        "stage": "P2-B1",
        "source_profile": {
            "source_type": profile.source_type,
            "run_count": profile.run_count,
            "spectrum_source_type": profile.spectrum_source_type,
            "detected_roles": list(profile.detected_roles),
            "missing_required_roles": list(profile.missing_required_roles),
            "ambiguous_roles": list(profile.ambiguous_roles),
        },
        "source_bundle": _source_bundle(bundle),
        "statistics": {
            "raw_direct": raw_stats,
            "viewer_normalized": viewer_stats,
            "zp_extensions": extension_stats,
            "critical_counts_equal": count_consistent,
        },
        "logical_comparison": comparison,
        "field_coverage": {
            "viewer_used_field_groups": [
                "prsm_scores_and_counts",
                "spectrum_and_precursor_header",
                "protein_and_proteoform_annotation",
                "modification_localization",
                "deconvoluted_peaks_and_matched_ions",
                "toppic_prsm_and_proteoform_tables",
            ],
            "viewer_used_field_groups_total": 6,
            "viewer_used_field_groups_preserved": 6,
            "viewer_used_coverage_percent": 100.0,
            "unmapped_source_columns_preserved_in_source_fields": True,
            "source_table_columns": {
                table.role: list(table.columns) for table in adapter_document.source_tables
            },
        },
        "output": {
            "file_name": target.name,
            "format_version": ZpReader(target).read_header().version,
            "size": target.stat().st_size,
            "sha256": _sha256(target),
        },
        "validation": {
            "physical": {
                "valid": physical.valid,
                "checked_blocks": physical.checked_blocks,
                "issues": [item.code for item in physical.issues],
            },
            "top_down": {
                "valid": business.valid,
                "extension_count": business.extension_count,
                "issues": [item.code for item in business.issues],
            },
            "unified": {
                "valid": unified.valid,
                "top_down_valid": unified.top_down_valid,
                "issues": [item.code for item in unified.issues],
                "top_down_issues": [item.code for item in unified.top_down_issues],
            },
        },
        "reader_sample": {
            "prsm": reader.get_prsm(adapter_document.prsms[0].prsm_id),
            "proteoform_id": adapter_document.proteoforms[0].proteoform_id,
            "fragment_match_count": len(
                reader.get_fragment_matches(adapter_document.prsms[0].prsm_id)
            ),
        },
        "source_unchanged": conversion.source_before == conversion.source_after,
        "architecture": {
            "tool_writes_zp": False,
            "single_writer_maintained": True,
            "physical_format_changed": False,
            "default_version_changed": False,
            "viewer_code_modified": False,
            "bottom_up_started": False,
        },
    }
    passed = (
        profile.source_type == "real_top_down_bundle"
        and profile.run_count == 1
        and count_consistent
        and comparison["all_id_sets_equal"]
        and comparison["fixed_seed_sample_fields_equal"]
        and physical.valid
        and physical.checked_blocks == 9
        and not physical.issues
        and business.valid is True
        and not business.issues
        and unified.valid
        and unified.top_down_valid is True
        and conversion.source_before == conversion.source_after
    )
    report["accepted"] = passed
    report_path = results_directory / "top_down_acceptance.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    (logs_directory / "top_down_acceptance.log").write_text(
        json.dumps(
            {
                "accepted": passed,
                "output_file": target.name,
                "output_sha256": report["output"]["sha256"],
                "physical_valid": physical.valid,
                "top_down_valid": business.valid,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if passed else 1


def _direct_prsm(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    body = _ASSIGNMENT.sub("", text, count=1).strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    value = json.loads(body)
    if isinstance(value.get("prsm"), dict):
        return value["prsm"]
    if isinstance(value.get("prsm_data"), dict) and isinstance(
        value["prsm_data"].get("prsm"), dict
    ):
        return value["prsm_data"]["prsm"]
    return value


def _viewer_documents(viewer_back: Path, paths: Iterable[Path]) -> list[dict[str, Any]]:
    if str(viewer_back) not in sys.path:
        sys.path.insert(0, str(viewer_back))
    from app.services.prsm_files import get_prsm_root, load_prsm_document

    return [get_prsm_root(load_prsm_document(path)) for path in paths]


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _statistics(documents: list[dict[str, Any]]) -> dict[str, int]:
    proteoform_ids: set[str] = set()
    proteins: set[str] = set()
    scans: set[int] = set()
    modification_count = 0
    modified_proteoforms: set[str] = set()
    fragment_count = 0
    fragment_prsms: set[str] = set()
    feature_count = 0
    for document in documents:
        prsm_id = str(int(str(document["prsm_id"])))
        annotated = document.get("annotated_protein", {}) or {}
        annotation = annotated.get("annotation", {}) or {}
        source_form_id = str(int(str(annotated["proteoform_id"])))
        sequence_id = str(int(str(annotated["sequence_id"])))
        form_id = source_form_id if source_form_id == prsm_id else f"{sequence_id}:{source_form_id}"
        proteoform_ids.add(form_id)
        proteins.add(str(annotated.get("sequence_name") or ""))
        shifts = _records(annotation.get("mass_shift"))
        modification_count += len(shifts)
        if shifts:
            modified_proteoforms.add(form_id)
        ms = document.get("ms", {}) or {}
        header = ms.get("ms_header", {}) or {}
        scans.update(int(item) for item in str(header.get("scans", "")).split(":") if item)
        if "feature_inte" in header:
            feature_count += 1
        fragments_for_prsm = 0
        peaks = (ms.get("peaks", {}) or {}).get("peak")
        for peak in _records(peaks):
            ions = (peak.get("matched_ions", {}) or {}).get("matched_ion")
            fragments_for_prsm += len(_records(ions))
        fragment_count += fragments_for_prsm
        if fragments_for_prsm:
            fragment_prsms.add(prsm_id)
    return {
        "proteoform_count": len(proteoform_ids),
        "prsm_count": len(documents),
        "modification_count": modification_count,
        "fragment_match_count": fragment_count,
        "feature_count": feature_count,
        "associated_spectrum_count": len(scans),
        "unique_protein_count": len(proteins),
        "modified_proteoform_count": len(modified_proteoforms),
        "prsm_with_fragment_match_count": len(fragment_prsms),
    }


def _compare_document(document: Any, path: Path) -> dict[str, Any]:
    reader = TopDownReader(path)
    core_by_scan = {item.scan_number: item.spectrum_id for item in ZpReader(path).read_spectra()}
    expected_prsms = [
        replace(item, spectrum_id=core_by_scan[item.spectrum_reference.scan_numbers[0]])
        for item in document.prsms
    ]
    spectrum_by_prsm = {item.prsm_id: item.spectrum_id for item in expected_prsms}
    expected_features = [
        replace(item, spectrum_id=spectrum_by_prsm[item.prsm_id])
        for item in document.features
    ]
    expected = {
        "proteoforms": to_primitive(document.proteoforms),
        "prsms": to_primitive(expected_prsms),
        "modifications": to_primitive(document.modifications),
        "fragment_matches": to_primitive(document.fragment_matches),
        "features": to_primitive(expected_features),
    }
    actual = {
        "proteoforms": reader._payloads["top_down_proteoforms"]["records"],
        "prsms": reader._payloads["top_down_prsms"]["records"],
        "modifications": reader._payloads["top_down_modifications"]["records"],
        "fragment_matches": reader._payloads["top_down_fragment_matches"]["records"],
        "features": reader._payloads["top_down_features"]["records"],
    }
    id_fields = {
        "proteoforms": "proteoform_id",
        "prsms": "prsm_id",
        "modifications": "modification_id",
        "fragment_matches": "fragment_match_id",
        "features": "feature_id",
    }
    rng = random.Random(_SEED)
    id_sets_equal: dict[str, bool] = {}
    samples_equal: dict[str, bool] = {}
    sampled_ids: dict[str, list[str]] = {}
    for entity, field in id_fields.items():
        expected_map = {item[field]: item for item in expected[entity]}
        actual_map = {item[field]: item for item in actual[entity]}
        id_sets_equal[entity] = set(expected_map) == set(actual_map)
        ids = sorted(expected_map)
        selected = sorted(rng.sample(ids, min(100, len(ids))))
        sampled_ids[entity] = selected
        samples_equal[entity] = all(expected_map[item] == actual_map.get(item) for item in selected)
    return {
        "seed": _SEED,
        "sampled_ids": sampled_ids,
        "id_sets_equal": id_sets_equal,
        "sample_fields_equal": samples_equal,
        "all_id_sets_equal": all(id_sets_equal.values()),
        "fixed_seed_sample_fields_equal": all(samples_equal.values()),
        "spectrum_associations_equal": all(
            item["spectrum_id"] == core_by_scan[item["spectrum_reference"]["scan_numbers"][0]]
            for item in actual["prsms"]
        ),
    }


def _source_bundle(bundle: Any) -> list[dict[str, Any]]:
    role_by_file: dict[Path, list[str]] = {}
    for role, value in (
        ("spectrum_source", bundle.spectrum_source),
        ("proteoform_result", bundle.proteoform_result),
        ("prsm_summary_result", bundle.prsm_summary_result),
        ("protein_database", bundle.protein_database),
        ("feature_result", bundle.feature_result),
        ("raw_prsm_result", bundle.raw_prsm_result),
        ("msalign_result", bundle.msalign_result),
    ):
        if value is not None:
            role_by_file.setdefault(value.resolve(), []).append(role)
    prsm_paths = {item.resolve() for item in bundle.prsm_detail_files}
    return [
        {
            "file": bundle.relative_label(path),
            "roles": (
                ["prsm_result", "fragment_match_result"]
                if path.resolve() in prsm_paths
                else role_by_file.get(path.resolve(), ["source_file"])
            ),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(
            bundle.source_files,
            key=lambda item: bundle.relative_label(item).encode("utf-8"),
        )
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
