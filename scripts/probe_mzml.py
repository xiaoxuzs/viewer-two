from __future__ import annotations

import argparse
import json
import re
import sys
import time
import tracemalloc
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCAN_RE = re.compile(r"(?:^|\s)scan=(\d+)(?:\s|$)")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xml_structure(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        root_name = local_name(next(ET.iterparse(stream, events=("start",)))[1].tag)

    counts: Counter[str] = Counter()
    run_attributes: list[dict[str, str]] = []
    binary_terms: Counter[str] = Counter()
    binary_term_units: dict[str, set[str]] = defaultdict(set)
    active_binary_terms: list[tuple[str, str | None]] | None = None
    for event, element in ET.iterparse(path, events=("start", "end")):
        name = local_name(element.tag)
        if event == "start":
            if name in {"run", "spectrum", "chromatogram", "instrumentConfiguration", "software", "dataProcessing"}:
                counts[name] += 1
            if name == "run":
                run_attributes.append(dict(element.attrib))
            if name == "binaryDataArray":
                active_binary_terms = []
            elif name == "cvParam" and active_binary_terms is not None:
                term_name = element.attrib.get("name")
                if term_name:
                    active_binary_terms.append((term_name, element.attrib.get("unitName")))
        elif name == "binaryDataArray":
            for term_name, unit_name in active_binary_terms or []:
                lowered = term_name.lower()
                if (
                    "compression" in lowered
                    or "-bit float" in lowered
                    or "-bit integer" in lowered
                    or lowered.endswith(" array")
                ):
                    binary_terms[term_name] += 1
                    if unit_name:
                        binary_term_units[term_name].add(unit_name)
            active_binary_terms = None
            element.clear()
        else:
            element.clear()

    return {
        "root_element": root_name,
        "indexed": root_name == "indexedmzML",
        "counts": dict(counts),
        "run_attributes": run_attributes,
        "binary_terms": dict(binary_terms),
        "binary_term_units": {name: sorted(units) for name, units in sorted(binary_term_units.items())},
    }


def scan_number(native_id: object) -> int | None:
    match = SCAN_RE.search(str(native_id))
    return int(match.group(1)) if match else None


def scans_for(spectrum: dict[str, Any]) -> list[dict[str, Any]]:
    scan_list = spectrum.get("scanList")
    if not isinstance(scan_list, dict):
        return []
    scans = scan_list.get("scan")
    return scans if isinstance(scans, list) else []


def precursors_for(spectrum: dict[str, Any]) -> list[dict[str, Any]]:
    precursor_list = spectrum.get("precursorList")
    if not isinstance(precursor_list, dict):
        return []
    precursors = precursor_list.get("precursor")
    return precursors if isinstance(precursors, list) else []


def selected_ions_for(precursor: dict[str, Any]) -> list[dict[str, Any]]:
    selected_list = precursor.get("selectedIonList")
    if not isinstance(selected_list, dict):
        return []
    selected_ions = selected_list.get("selectedIon")
    return selected_ions if isinstance(selected_ions, list) else []


def array_items(record: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        (name, value)
        for name, value in record.items()
        if hasattr(value, "dtype") and hasattr(value, "__len__")
    ]


def spectrum_summary(spectrum: dict[str, Any]) -> dict[str, Any]:
    scans = scans_for(spectrum)
    rt = scans[0].get("scan start time") if scans else None
    precursors = precursors_for(spectrum)
    selected_ion_count = sum(len(selected_ions_for(precursor)) for precursor in precursors)
    return {
        "id": spectrum.get("id"),
        "index": spectrum.get("index"),
        "scan_number": scan_number(spectrum.get("id")),
        "ms_level": spectrum.get("ms level"),
        "rt_value": float(rt) if isinstance(rt, (int, float)) else None,
        "rt_unit": getattr(rt, "unit_info", None),
        "default_array_length": spectrum.get("defaultArrayLength"),
        "polarity": "positive" if "positive scan" in spectrum else "negative" if "negative scan" in spectrum else None,
        "representation": "centroid" if "centroid spectrum" in spectrum else "profile" if "profile spectrum" in spectrum else None,
        "arrays": [
            {"name": name, "dtype": str(value.dtype), "length": len(value)}
            for name, value in array_items(spectrum)
        ],
        "precursor_count": len(precursors),
        "selected_ion_count": selected_ion_count,
        "precursor_spectrum_refs": [precursor.get("spectrumRef") for precursor in precursors],
    }


def probe_spectra(path: Path) -> dict[str, Any]:
    try:
        import numpy as np
        from pyteomics import mzml
    except ImportError as exc:
        raise RuntimeError("probe_mzml.py requires the optional investigation dependency pyteomics") from exc

    spectrum_count = 0
    ms_levels: Counter[int] = Counter()
    missing_scan_number_count = 0
    missing_rt_count = 0
    missing_charge_count = 0
    missing_selected_ion_mz_count = 0
    missing_selected_ion_intensity_count = 0
    zero_charge_count = 0
    missing_precursor_spectrum_ref_count = 0
    missing_isolation_window_count = 0
    missing_activation_count = 0
    missing_collision_energy_count = 0
    multiple_precursor_count = 0
    multiple_selected_ion_count = 0
    activation_terms: Counter[str] = Counter()
    polarity_counts: Counter[str] = Counter()
    representation_counts: Counter[str] = Counter()
    array_dtypes: dict[str, set[str]] = defaultdict(set)
    array_lengths: dict[str, list[int]] = defaultdict(list)
    empty_array_count = 0
    nonfinite_array_count = 0
    peak_count_total = 0
    rt_units_seen: set[str] = set()
    samples: dict[str, dict[str, Any] | None] = {"ms1": None, "ms2": None}

    with mzml.MzML(str(path), iterative=True, use_index=True, decode_binary=True) as reader:
        for spectrum in reader:
            spectrum_count += 1
            ms_level = spectrum.get("ms level")
            if isinstance(ms_level, int):
                ms_levels[ms_level] += 1
            if scan_number(spectrum.get("id")) is None:
                missing_scan_number_count += 1

            scans = scans_for(spectrum)
            rt = scans[0].get("scan start time") if scans else None
            if not isinstance(rt, (int, float)):
                missing_rt_count += 1
            else:
                rt_units_seen.add(str(getattr(rt, "unit_info", None) or "unlabeled"))

            precursors = precursors_for(spectrum)
            if len(precursors) > 1:
                multiple_precursor_count += 1
            selected_ions = [ion for precursor in precursors for ion in selected_ions_for(precursor)]
            if len(selected_ions) > 1:
                multiple_selected_ion_count += 1
            if isinstance(ms_level, int) and ms_level >= 2:
                if not selected_ions or any("charge state" not in ion for ion in selected_ions):
                    missing_charge_count += 1
                if not selected_ions or any("selected ion m/z" not in ion for ion in selected_ions):
                    missing_selected_ion_mz_count += 1
                if not selected_ions or any("peak intensity" not in ion for ion in selected_ions):
                    missing_selected_ion_intensity_count += 1
                if any(ion.get("charge state") == 0 for ion in selected_ions):
                    zero_charge_count += 1
            for precursor in precursors:
                if not precursor.get("spectrumRef"):
                    missing_precursor_spectrum_ref_count += 1
                if not isinstance(precursor.get("isolationWindow"), dict):
                    missing_isolation_window_count += 1
                activation = precursor.get("activation")
                if not isinstance(activation, dict):
                    missing_activation_count += 1
                else:
                    if "collision energy" not in activation:
                        missing_collision_energy_count += 1
                    for term in activation:
                        if term != "collision energy":
                            activation_terms[term] += 1

            polarity = "positive" if "positive scan" in spectrum else "negative" if "negative scan" in spectrum else "missing"
            representation = "centroid" if "centroid spectrum" in spectrum else "profile" if "profile spectrum" in spectrum else "missing"
            polarity_counts[polarity] += 1
            representation_counts[representation] += 1

            for name, values in array_items(spectrum):
                length = len(values)
                array_dtypes[name].add(str(values.dtype))
                array_lengths[name].append(length)
                if length == 0:
                    empty_array_count += 1
                elif not bool(np.isfinite(values).all()):
                    nonfinite_array_count += 1
                if name == "m/z array":
                    peak_count_total += length

            if ms_level == 1 and samples["ms1"] is None:
                samples["ms1"] = spectrum_summary(spectrum)
            if ms_level == 2 and samples["ms2"] is None:
                samples["ms2"] = spectrum_summary(spectrum)

    return {
        "spectrum_count": spectrum_count,
        "ms_levels": dict(sorted(ms_levels.items())),
        "ms1_count": ms_levels[1],
        "ms2_count": ms_levels[2],
        "missing_scan_number_count": missing_scan_number_count,
        "missing_rt_count": missing_rt_count,
        "missing_charge_count": missing_charge_count,
        "missing_selected_ion_mz_count": missing_selected_ion_mz_count,
        "missing_selected_ion_intensity_count": missing_selected_ion_intensity_count,
        "zero_charge_count": zero_charge_count,
        "missing_precursor_spectrum_ref_count": missing_precursor_spectrum_ref_count,
        "missing_isolation_window_count": missing_isolation_window_count,
        "missing_activation_count": missing_activation_count,
        "missing_collision_energy_count": missing_collision_energy_count,
        "multiple_precursor_count": multiple_precursor_count,
        "multiple_selected_ion_count": multiple_selected_ion_count,
        "activation_terms": dict(sorted(activation_terms.items())),
        "polarity_counts": dict(sorted(polarity_counts.items())),
        "representation_counts": dict(sorted(representation_counts.items())),
        "array_dtypes": {name: sorted(values) for name, values in sorted(array_dtypes.items())},
        "array_length_ranges": {
            name: {"min": min(values), "max": max(values)}
            for name, values in sorted(array_lengths.items())
            if values
        },
        "empty_array_count": empty_array_count,
        "nonfinite_array_count": nonfinite_array_count,
        "peak_count_total": peak_count_total,
        "rt_units_seen": sorted(rt_units_seen),
        "sample_ms1": samples["ms1"],
        "sample_ms2": samples["ms2"],
    }


def probe_first_chromatogram(path: Path) -> dict[str, Any] | None:
    try:
        from pyteomics import mzml
    except ImportError as exc:
        raise RuntimeError("probe_mzml.py requires the optional investigation dependency pyteomics") from exc
    with mzml.MzML(str(path), iterative=True, use_index=True, decode_binary=True) as reader:
        chromatogram = next(reader.iterfind("chromatogram"), None)
    if chromatogram is None:
        return None
    return {
        "id": chromatogram.get("id"),
        "index": chromatogram.get("index"),
        "arrays": [
            {"name": name, "dtype": str(value.dtype), "length": len(value)}
            for name, value in array_items(chromatogram)
        ],
        "keys": sorted(name for name in chromatogram if not name.endswith(" array")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only structural probe for a real mzML file")
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    path = args.file.resolve()
    if not path.is_file():
        parser.error(f"file does not exist: {path}")

    started = time.perf_counter()
    tracemalloc.start()
    structure = xml_structure(path)
    spectra = probe_spectra(path)
    first_chromatogram = probe_first_chromatogram(path) if structure["counts"].get("chromatogram", 0) else None
    _current, traced_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"file={path}")
    print(f"file_size={path.stat().st_size}")
    print(f"indexed={structure['indexed']}")
    print(f"run_count={structure['counts'].get('run', 0)}")
    print(f"spectrum_count={spectra['spectrum_count']}")
    print(f"chromatogram_count={structure['counts'].get('chromatogram', 0)}")
    for key in (
        "ms1_count", "ms2_count", "missing_scan_number_count", "missing_rt_count",
        "missing_charge_count", "missing_selected_ion_mz_count", "missing_selected_ion_intensity_count",
        "zero_charge_count", "missing_precursor_spectrum_ref_count",
        "missing_isolation_window_count", "missing_activation_count", "missing_collision_energy_count", "multiple_precursor_count",
        "multiple_selected_ion_count", "peak_count_total", "empty_array_count", "nonfinite_array_count",
    ):
        print(f"{key}={spectra[key]}")
    print("ms_levels=" + json.dumps(spectra["ms_levels"], sort_keys=True))
    print("array_dtypes=" + json.dumps(spectra["array_dtypes"], sort_keys=True))
    print("array_length_ranges=" + json.dumps(spectra["array_length_ranges"], sort_keys=True))
    print("rt_units_seen=" + json.dumps(spectra["rt_units_seen"]))
    print("polarity_counts=" + json.dumps(spectra["polarity_counts"], sort_keys=True))
    print("representation_counts=" + json.dumps(spectra["representation_counts"], sort_keys=True))
    print("activation_terms=" + json.dumps(spectra["activation_terms"], sort_keys=True))
    print("structure_counts=" + json.dumps(structure["counts"], sort_keys=True))
    print("binary_terms=" + json.dumps(structure["binary_terms"], sort_keys=True))
    print("binary_term_units=" + json.dumps(structure["binary_term_units"], sort_keys=True))
    print("run_attributes=" + json.dumps(structure["run_attributes"], sort_keys=True))
    print("sample_ms1=" + json.dumps(spectra["sample_ms1"], sort_keys=True))
    print("sample_ms2=" + json.dumps(spectra["sample_ms2"], sort_keys=True))
    print("sample_chromatogram=" + json.dumps(first_chromatogram, sort_keys=True))
    print(f"probe_elapsed_seconds={time.perf_counter() - started:.3f}")
    print(f"python_tracemalloc_peak_bytes={traced_peak}")
    print("memory_note=tracemalloc excludes most native NumPy array allocations")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
