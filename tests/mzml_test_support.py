from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
from pyteomics import mzml

from binary_layer.mzml_admission import (
    AuxiliaryArrayFeature,
    ChromatogramFeature,
    MzmlFeatureProfile,
    SpectrumFeature,
)

SCAN_RE = re.compile(r"(?:^|\s)scan=(\d+)(?:\s|$)")
DTYPES = {"MS:1000521": "float32", "MS:1000523": "float64", "MS:1000522": "int64"}
COMPRESSIONS = {"MS:1000574": "zlib", "MS:1000576": "none"}
ARRAY_KINDS = {"MS:1000514": "mz", "MS:1000515": "intensity", "MS:1000595": "time"}
CHROMATOGRAM_TYPES = {"MS:1000235": "tic", "MS:1000628": "bpc", "MS:1001473": "srm"}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass(frozen=True)
class ArrayXmlFeature:
    kind: str
    accession: str
    name: str
    dtype: str | None
    compression: str | None
    unit_accession: str | None
    unit_name: str | None


@dataclass(frozen=True)
class OwnerXmlFeature:
    arrays: tuple[ArrayXmlFeature, ...]
    rt_value: float | None = None
    rt_unit_accession: str | None = None
    rt_unit_name: str | None = None
    chromatogram_type: str | None = None
    has_precursor_semantics: bool = False
    has_product_semantics: bool = False


@dataclass(frozen=True)
class XmlFeatureIndex:
    indexed: bool
    mzml_version: str
    run_count: int
    native_id_format_accession: str | None
    native_id_format_name: str | None
    spectra: dict[str, OwnerXmlFeature]
    chromatograms: dict[str, OwnerXmlFeature]


def _cv_params(element: ET.Element) -> list[ET.Element]:
    return [item for item in element.iter() if local_name(item.tag) == "cvParam"]


def _array_features(owner: ET.Element) -> tuple[ArrayXmlFeature, ...]:
    result = []
    for array in (item for item in owner.iter() if local_name(item.tag) == "binaryDataArray"):
        params = _cv_params(array)
        dtype = next((DTYPES[item.attrib.get("accession", "")] for item in params if item.attrib.get("accession") in DTYPES), None)
        compression = next((COMPRESSIONS[item.attrib.get("accession", "")] for item in params if item.attrib.get("accession") in COMPRESSIONS), None)
        kind_param = next((item for item in params if item.attrib.get("accession") in ARRAY_KINDS or item.attrib.get("accession") == "MS:1000786"), None)
        if kind_param is None:
            result.append(ArrayXmlFeature("unknown", "", "", dtype, compression, None, None))
            continue
        accession = kind_param.attrib.get("accession", "")
        kind = ARRAY_KINDS.get(accession, "auxiliary")
        name = kind_param.attrib.get("value") if kind == "auxiliary" else kind_param.attrib.get("name")
        result.append(ArrayXmlFeature(
            kind=kind,
            accession=accession,
            name=name or "",
            dtype=dtype,
            compression=compression,
            unit_accession=kind_param.attrib.get("unitAccession"),
            unit_name=kind_param.attrib.get("unitName"),
        ))
    return tuple(result)


def inspect_xml(path: Path) -> XmlFeatureIndex:
    indexed = False
    mzml_version = ""
    run_count = 0
    native_id_accession = None
    native_id_name = None
    spectra: dict[str, OwnerXmlFeature] = {}
    chromatograms: dict[str, OwnerXmlFeature] = {}
    first_element = True
    for event, element in ET.iterparse(path, events=("start", "end")):
        name = local_name(element.tag)
        if event == "start":
            if first_element:
                indexed = name == "indexedmzML"
                first_element = False
            if name == "mzML":
                mzml_version = element.attrib.get("version", "")
            continue
        if name == "sourceFile" and native_id_accession is None:
            param = next((item for item in _cv_params(element) if item.attrib.get("name", "").endswith("nativeID format")), None)
            if param is not None:
                native_id_accession = param.attrib.get("accession")
                native_id_name = param.attrib.get("name")
        elif name == "spectrum":
            rt_param = next((item for item in _cv_params(element) if item.attrib.get("accession") == "MS:1000016"), None)
            spectra[element.attrib["id"]] = OwnerXmlFeature(
                arrays=_array_features(element),
                rt_value=float(rt_param.attrib["value"]) if rt_param is not None else None,
                rt_unit_accession=rt_param.attrib.get("unitAccession") if rt_param is not None else None,
                rt_unit_name=rt_param.attrib.get("unitName") if rt_param is not None else None,
            )
            element.clear()
        elif name == "chromatogram":
            params = _cv_params(element)
            chrom_type = next((CHROMATOGRAM_TYPES[item.attrib.get("accession", "")] for item in params if item.attrib.get("accession") in CHROMATOGRAM_TYPES), "unknown")
            chromatograms[element.attrib["id"]] = OwnerXmlFeature(
                arrays=_array_features(element),
                chromatogram_type=chrom_type,
                has_precursor_semantics=any(local_name(item.tag) == "precursor" for item in element),
                has_product_semantics=any(local_name(item.tag) == "product" for item in element),
            )
            element.clear()
        elif name == "run":
            run_count += 1
    return XmlFeatureIndex(indexed, mzml_version, run_count, native_id_accession, native_id_name, spectra, chromatograms)


def _array_by_kind(metadata: OwnerXmlFeature, kind: str) -> ArrayXmlFeature | None:
    return next((item for item in metadata.arrays if item.kind == kind), None)


def _auxiliary_features(metadata: OwnerXmlFeature) -> tuple[AuxiliaryArrayFeature, ...]:
    return tuple(
        AuxiliaryArrayFeature(item.accession, item.name, item.dtype or "unknown", item.unit_accession, item.unit_name)
        for item in metadata.arrays
        if item.kind in {"auxiliary", "unknown"}
    )


def _scans(spectrum: dict[str, Any]) -> list[dict[str, Any]]:
    scan_list = spectrum.get("scanList")
    scans = scan_list.get("scan") if isinstance(scan_list, dict) else None
    return scans if isinstance(scans, list) else []


def _precursors(spectrum: dict[str, Any]) -> list[dict[str, Any]]:
    precursor_list = spectrum.get("precursorList")
    precursors = precursor_list.get("precursor") if isinstance(precursor_list, dict) else None
    return precursors if isinstance(precursors, list) else []


def _selected_ions(precursor: dict[str, Any]) -> list[dict[str, Any]]:
    selected_list = precursor.get("selectedIonList")
    ions = selected_list.get("selectedIon") if isinstance(selected_list, dict) else None
    return ions if isinstance(ions, list) else []


def build_feature_profile(path: Path) -> MzmlFeatureProfile:
    xml = inspect_xml(path)
    spectra: list[SpectrumFeature] = []
    with mzml.MzML(str(path), use_index=True, decode_binary=True) as reader:
        for record in reader:
            native_id = str(record.get("id", ""))
            metadata = xml.spectra[native_id]
            scan_match = SCAN_RE.search(native_id)
            mz_values = record.get("m/z array")
            intensity_values = record.get("intensity array")
            precursors = _precursors(record)
            ions = [ion for item in precursors for ion in _selected_ions(item)]
            single_ion = ions[0] if len(precursors) == 1 and len(ions) == 1 else {}
            mz_meta = _array_by_kind(metadata, "mz")
            intensity_meta = _array_by_kind(metadata, "intensity")
            spectra.append(SpectrumFeature(
                native_id=native_id,
                scan_number=int(scan_match.group(1)) if scan_match else None,
                scan_number_proven=bool(scan_match and xml.native_id_format_accession == "MS:1000768"),
                ms_level=int(record.get("ms level", 0)),
                rt_value=metadata.rt_value,
                rt_unit_accession=metadata.rt_unit_accession,
                rt_unit_name=metadata.rt_unit_name,
                representation="centroid" if "centroid spectrum" in record else "profile" if "profile spectrum" in record else "unknown",
                polarity="positive" if "positive scan" in record else "negative" if "negative scan" in record else None,
                precursor_count=len(precursors),
                selected_ion_count=len(ions),
                selected_ion_mz=single_ion.get("selected ion m/z"),
                charge=single_ion.get("charge state"),
                selected_ion_intensity=single_ion.get("peak intensity"),
                has_mz_array=mz_values is not None,
                has_intensity_array=intensity_values is not None,
                mz_array_length=len(mz_values) if mz_values is not None else None,
                intensity_array_length=len(intensity_values) if intensity_values is not None else None,
                mz_dtype=str(mz_values.dtype) if mz_values is not None else mz_meta.dtype if mz_meta else None,
                intensity_dtype=str(intensity_values.dtype) if intensity_values is not None else intensity_meta.dtype if intensity_meta else None,
                mz_compression=mz_meta.compression if mz_meta else None,
                intensity_compression=intensity_meta.compression if intensity_meta else None,
                arrays_are_finite=bool((mz_values is None or np.isfinite(mz_values).all()) and (intensity_values is None or np.isfinite(intensity_values).all())),
                minimum_mz=float(np.min(mz_values)) if mz_values is not None and len(mz_values) else None,
                auxiliary_arrays=_auxiliary_features(metadata),
            ))

    chromatograms: list[ChromatogramFeature] = []
    chromatogram_records: list[dict[str, Any]] = []
    if xml.chromatograms:
        with mzml.MzML(str(path), use_index=True, decode_binary=True) as reader:
            chromatogram_records = list(reader.iterfind("chromatogram"))
    for record in chromatogram_records:
        chromatogram_id = str(record.get("id", ""))
        metadata = xml.chromatograms[chromatogram_id]
        time_values = record.get("time array")
        intensity_values = record.get("intensity array")
        time_meta = _array_by_kind(metadata, "time")
        intensity_meta = _array_by_kind(metadata, "intensity")
        chromatograms.append(ChromatogramFeature(
            chromatogram_id=chromatogram_id,
            chromatogram_type=metadata.chromatogram_type or "unknown",
            time_array_length=len(time_values) if time_values is not None else None,
            intensity_array_length=len(intensity_values) if intensity_values is not None else None,
            time_dtype=str(time_values.dtype) if time_values is not None else time_meta.dtype if time_meta else None,
            intensity_dtype=str(intensity_values.dtype) if intensity_values is not None else intensity_meta.dtype if intensity_meta else None,
            time_compression=time_meta.compression if time_meta else None,
            intensity_compression=intensity_meta.compression if intensity_meta else None,
            time_unit_accession=time_meta.unit_accession if time_meta else None,
            time_unit_name=time_meta.unit_name if time_meta else None,
            auxiliary_arrays=_auxiliary_features(metadata),
            has_precursor_semantics=metadata.has_precursor_semantics,
            has_product_semantics=metadata.has_product_semantics,
        ))
    return MzmlFeatureProfile(
        indexed=xml.indexed,
        run_count=xml.run_count,
        mzml_version=xml.mzml_version,
        native_id_format_accession=xml.native_id_format_accession,
        native_id_format_name=xml.native_id_format_name,
        spectra=tuple(spectra),
        chromatograms=tuple(chromatograms),
    )


def summarize_profile(path: Path) -> dict[str, object]:
    started = time.perf_counter()
    profile = build_feature_profile(path)
    return {
        "file": str(path.resolve()),
        "pyteomics_version": version("pyteomics"),
        "run_count": profile.run_count,
        "spectrum_count": len(profile.spectra),
        "ms1_count": sum(item.ms_level == 1 for item in profile.spectra),
        "ms2_count": sum(item.ms_level == 2 for item in profile.spectra),
        "chromatogram_count": len(profile.chromatograms),
        "rt_units": sorted({item.rt_unit_name for item in profile.spectra if item.rt_unit_name}),
        "array_dtypes": sorted({value for item in profile.spectra for value in (item.mz_dtype, item.intensity_dtype) if value}),
        "array_compressions": sorted({value for item in profile.spectra for value in (item.mz_compression, item.intensity_compression) if value}),
        "auxiliary_array_terms": sorted({f"{item.accession}:{item.name}:{item.dtype}" for owner in (*profile.spectra, *profile.chromatograms) for item in owner.auxiliary_arrays}),
        "elapsed_seconds": time.perf_counter() - started,
        "profile": profile,
    }
