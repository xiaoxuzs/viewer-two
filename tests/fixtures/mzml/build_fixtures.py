from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path
from xml.sax.saxutils import quoteattr

MS_NS = "http://psi.hupo.org/ms/mzml"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"


def cv(accession: str, name: str, value: object = "", unit: tuple[str, str, str] | None = None) -> str:
    attributes = [f'cvRef="MS"', f'accession={quoteattr(accession)}', f'name={quoteattr(name)}', f'value={quoteattr(str(value))}']
    if unit:
        cv_ref, unit_accession, unit_name = unit
        attributes.extend((f'unitCvRef={quoteattr(cv_ref)}', f'unitAccession={quoteattr(unit_accession)}', f'unitName={quoteattr(unit_name)}'))
    return "<cvParam " + " ".join(attributes) + "/>"


def encode(values: list[int | float], dtype: str, compression: str) -> str:
    formats = {"float32": "f", "float64": "d", "int64": "q"}
    raw = struct.pack("<" + formats[dtype] * len(values), *values)
    if compression == "zlib":
        raw = zlib.compress(raw)
    return base64.b64encode(raw).decode("ascii")


def binary_array(
    values: list[int | float],
    dtype: str,
    compression: str,
    kind: str,
    *,
    auxiliary_name: str | None = None,
    time_unit: str = "second",
) -> str:
    dtype_terms = {
        "float32": ("MS:1000521", "32-bit float"),
        "float64": ("MS:1000523", "64-bit float"),
        "int64": ("MS:1000522", "64-bit integer"),
    }
    compression_terms = {
        "none": ("MS:1000576", "no compression"),
        "zlib": ("MS:1000574", "zlib compression"),
    }
    time_units = {
        "second": ("UO", "UO:0000010", "second"),
        "minute": ("UO", "UO:0000031", "minute"),
        "unknown": None,
    }
    array_terms = {
        "mz": ("MS:1000514", "m/z array", "", ("MS", "MS:1000040", "m/z")),
        "intensity": ("MS:1000515", "intensity array", "", ("MS", "MS:1000131", "number of detector counts")),
        "time": ("MS:1000595", "time array", "", time_units[time_unit]),
        "auxiliary": ("MS:1000786", "non-standard data array", auxiliary_name or "", ("UO", "UO:0000186", "dimensionless unit")),
    }
    encoded = encode(values, dtype, compression)
    dtype_accession, dtype_name = dtype_terms[dtype]
    compression_accession, compression_name = compression_terms[compression]
    array_accession, array_name, array_value, unit = array_terms[kind]
    return (
        f'<binaryDataArray encodedLength="{len(encoded)}">'
        + cv(dtype_accession, dtype_name)
        + cv(compression_accession, compression_name)
        + cv(array_accession, array_name, array_value, unit)
        + f"<binary>{encoded}</binary></binaryDataArray>"
    )


def scan_list(rt: float, rt_unit: str) -> str:
    if rt_unit == "minute":
        term = cv("MS:1000016", "scan start time", rt, ("UO", "UO:0000031", "minute"))
    elif rt_unit == "second":
        term = cv("MS:1000016", "scan start time", rt, ("UO", "UO:0000010", "second"))
    else:
        term = cv("MS:1000016", "scan start time", rt)
    return f'<scanList count="1"><scan instrumentConfigurationRef="IC1">{term}</scan></scanList>'


def selected_ion(*, charge: int | None = 2, intensity: float | None = 50.0, mz: float | None = 445.2) -> str:
    terms = []
    if mz is not None:
        terms.append(cv("MS:1000744", "selected ion m/z", mz, ("MS", "MS:1000040", "m/z")))
    if intensity is not None:
        terms.append(cv("MS:1000042", "peak intensity", intensity, ("MS", "MS:1000131", "number of detector counts")))
    if charge is not None:
        terms.append(cv("MS:1000041", "charge state", charge))
    return "<selectedIon>" + "".join(terms) + "</selectedIon>"


def precursor(
    *,
    selected_ions: int = 1,
    selected_ion_mz: float | None = 445.2,
    charge: int | None = 2,
    intensity: float | None = 50.0,
    source_spectrum_ref: str = "controllerType=0 controllerNumber=1 scan=1",
    isolation_target_mz: float = 445.2,
    isolation_lower_offset: float = 1.0,
    isolation_upper_offset: float = 1.0,
    activation_accession: str = "MS:1000133",
    activation_name: str = "collision-induced dissociation",
    collision_energy: float = 25.0,
) -> str:
    ions = "".join(
        selected_ion(
            charge=charge,
            intensity=intensity,
            mz=None if selected_ion_mz is None else selected_ion_mz + index,
        )
        for index in range(selected_ions)
    )
    return (
        f'<precursor spectrumRef={quoteattr(source_spectrum_ref)}>'
        '<isolationWindow>'
        + cv("MS:1000827", "isolation window target m/z", isolation_target_mz, ("MS", "MS:1000040", "m/z"))
        + cv("MS:1000828", "isolation window lower offset", isolation_lower_offset, ("MS", "MS:1000040", "m/z"))
        + cv("MS:1000829", "isolation window upper offset", isolation_upper_offset, ("MS", "MS:1000040", "m/z"))
        + f'</isolationWindow><selectedIonList count="{selected_ions}">{ions}</selectedIonList>'
        '<activation>'
        + cv(activation_accession, activation_name)
        + cv("MS:1000045", "collision energy", collision_energy, ("UO", "UO:0000266", "electronvolt"))
        + "</activation></precursor>"
    )


def spectrum(
    index: int,
    *,
    ms_level: int,
    dtype: str = "float64",
    compression: str = "zlib",
    rt_unit: str = "minute",
    native_id: str | None = None,
    precursor_count: int = 1,
    selected_ions: int = 1,
    selected_ion_mz: float | None = 445.2,
    charge: int | None = 2,
    selected_ion_intensity: float | None = 50.0,
    source_spectrum_ref: str = "controllerType=0 controllerNumber=1 scan=1",
    isolation_target_mz: float = 445.2,
    isolation_lower_offset: float = 1.0,
    isolation_upper_offset: float = 1.0,
    activation_accession: str = "MS:1000133",
    activation_name: str = "collision-induced dissociation",
    collision_energy: float = 25.0,
    auxiliary_name: str | None = None,
) -> tuple[str, str]:
    native_id = native_id or f"controllerType=0 controllerNumber=1 scan={index + 1}"
    mz_values = [100.0 + index, 200.0 + index]
    intensity_values = [10.0 + index, 20.0 + index]
    arrays = [
        binary_array(mz_values, dtype, compression, "mz"),
        binary_array(intensity_values, dtype, compression, "intensity"),
    ]
    if auxiliary_name:
        arrays.append(binary_array([1, ms_level], "int64", "zlib", "auxiliary", auxiliary_name=auxiliary_name))
    precursor_xml = ""
    if ms_level == 2:
        items = "".join(
            precursor(
                selected_ions=selected_ions,
                selected_ion_mz=selected_ion_mz,
                charge=charge,
                intensity=selected_ion_intensity,
                source_spectrum_ref=source_spectrum_ref,
                isolation_target_mz=isolation_target_mz,
                isolation_lower_offset=isolation_lower_offset,
                isolation_upper_offset=isolation_upper_offset,
                activation_accession=activation_accession,
                activation_name=activation_name,
                collision_energy=collision_energy,
            )
            for _ in range(precursor_count)
        )
        precursor_xml = f'<precursorList count="{precursor_count}">{items}</precursorList>'
    body = (
        f'<spectrum index="{index}" id={quoteattr(native_id)} defaultArrayLength="2">'
        + cv("MS:1000511", "ms level", ms_level)
        + cv("MS:1000127", "centroid spectrum")
        + cv("MS:1000130", "positive scan")
        + scan_list(0.5 + index, rt_unit)
        + precursor_xml
        + f'<binaryDataArrayList count="{len(arrays)}">'
        + "".join(arrays)
        + "</binaryDataArrayList></spectrum>"
    )
    return native_id, body


def chromatogram(
    index: int,
    kind: str,
    *,
    include_ms_level: bool = False,
    auxiliary_name: str | None = None,
    precursor_semantics: bool = False,
    product_semantics: bool = False,
    include_time: bool = True,
    include_intensity: bool = True,
    time_values: list[float] | None = None,
    intensity_values: list[float] | None = None,
    time_unit: str = "second",
    dtype: str = "float64",
    compression: str = "zlib",
) -> tuple[str, str]:
    identifiers = {"tic": ("TIC", "MS:1000235", "total ion current chromatogram"), "bpc": ("BPC", "MS:1000628", "basepeak chromatogram"), "srm": ("SRM", "MS:1001473", "selected reaction monitoring chromatogram")}
    chromatogram_id, accession, name = identifiers[kind]
    time_values = [0.0, 1.0] if time_values is None else time_values
    intensity_values = [100.0, 120.0] if intensity_values is None else intensity_values
    arrays = []
    if include_time:
        arrays.append(binary_array(time_values, dtype, compression, "time", time_unit=time_unit))
    if include_intensity:
        arrays.append(binary_array(intensity_values, dtype, compression, "intensity"))
    if include_ms_level:
        arrays.append(binary_array([1, 2], "int64", "zlib", "auxiliary", auxiliary_name="ms level"))
    if auxiliary_name:
        arrays.append(binary_array([1, 2], "int64", "zlib", "auxiliary", auxiliary_name=auxiliary_name))
    semantic_xml = ""
    if precursor_semantics:
        semantic_xml = (
            "<precursor><isolationWindow>"
            + cv("MS:1000827", "isolation window target m/z", 445.2, ("MS", "MS:1000040", "m/z"))
            + "</isolationWindow></precursor>"
        )
    if product_semantics:
        semantic_xml += (
            "<product><isolationWindow>"
            + cv("MS:1000827", "isolation window target m/z", 175.1, ("MS", "MS:1000040", "m/z"))
            + "</isolationWindow></product>"
        )
    body = (
        f'<chromatogram index="{index}" id="{chromatogram_id}" defaultArrayLength="2">'
        + cv(accession, name)
        + semantic_xml
        + f'<binaryDataArrayList count="{len(arrays)}">'
        + "".join(arrays)
        + "</binaryDataArrayList></chromatogram>"
    )
    return chromatogram_id, body


def mzml_document(
    name: str,
    spectra: list[tuple[str, str]],
    chromatograms: list[tuple[str, str]],
    *,
    native_id_format: tuple[str, str] = ("MS:1000768", "Thermo nativeID format"),
) -> str:
    spectrum_xml = "".join(item[1] for item in spectra)
    chromatogram_xml = "".join(item[1] for item in chromatograms)
    chromatogram_list = ""
    if chromatograms:
        chromatogram_list = f'<chromatogramList count="{len(chromatograms)}" defaultDataProcessingRef="DP1">{chromatogram_xml}</chromatogramList>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<mzML xmlns="{MS_NS}" xmlns:xsi="{XSI_NS}" xsi:schemaLocation="{MS_NS} http://psidev.info/files/ms/mzML/xsd/mzML1.1.0.xsd" id={quoteattr(name)} version="1.1.0">'
        '<cvList count="2"><cv id="MS" fullName="Proteomics Standards Initiative Mass Spectrometry Ontology" version="4.1.186" URI="https://raw.githubusercontent.com/HUPO-PSI/psi-ms-CV/master/psi-ms.obo"/><cv id="UO" fullName="Unit Ontology" version="2023-05-25" URI="http://purl.obolibrary.org/obo/uo.obo"/></cvList>'
        '<fileDescription><fileContent>'
        + cv("MS:1000579", "MS1 spectrum")
        + cv("MS:1000580", "MSn spectrum")
        + '</fileContent><sourceFileList count="1"><sourceFile id="SF1" name="fixture.raw" location="file:///fixture">'
        + cv(native_id_format[0], native_id_format[1])
        + cv("MS:1000563", "Thermo RAW format")
        + "</sourceFile></sourceFileList></fileDescription>"
        '<softwareList count="1"><software id="fixture_writer" version="1.0">'
        + cv("MS:1000799", "custom unreleased software tool", "deterministic fixture builder")
        + "</software></softwareList>"
        '<instrumentConfigurationList count="1"><instrumentConfiguration id="IC1"><componentList count="3"><source order="1">'
        + cv("MS:1000073", "electrospray ionization")
        + '</source><analyzer order="2">'
        + cv("MS:1000484", "orbitrap")
        + '</analyzer><detector order="3">'
        + cv("MS:1000253", "electron multiplier")
        + "</detector></componentList></instrumentConfiguration></instrumentConfigurationList>"
        '<dataProcessingList count="1"><dataProcessing id="DP1"><processingMethod order="1" softwareRef="fixture_writer">'
        + cv("MS:1000544", "Conversion to mzML")
        + "</processingMethod></dataProcessing></dataProcessingList>"
        f'<run id="run1" defaultInstrumentConfigurationRef="IC1" defaultSourceFileRef="SF1"><spectrumList count="{len(spectra)}" defaultDataProcessingRef="DP1">{spectrum_xml}</spectrumList>{chromatogram_list}</run></mzML>'
    )


def indexed_document(inner: str, spectra: list[tuple[str, str]], chromatograms: list[tuple[str, str]]) -> str:
    prefix = f'<?xml version="1.0" encoding="UTF-8"?>\n<indexedmzML xmlns="{MS_NS}">\n'
    inner_without_declaration = inner.split("\n", 1)[1]
    body = prefix + inner_without_declaration + "\n"
    indexes: list[str] = []
    if spectra:
        spectrum_offsets = []
        search_from = 0
        for identifier, xml in spectra:
            position = body.index(xml, search_from)
            spectrum_offsets.append(f'<offset idRef={quoteattr(identifier)}>{len(body[:position].encode("utf-8"))}</offset>')
            search_from = position + len(xml)
        indexes.append('<index name="spectrum">' + "".join(spectrum_offsets) + "</index>")
    if chromatograms:
        chrom_offsets = []
        search_from = 0
        for identifier, xml in chromatograms:
            position = body.index(xml, search_from)
            chrom_offsets.append(f'<offset idRef={quoteattr(identifier)}>{len(body[:position].encode("utf-8"))}</offset>')
            search_from = position + len(xml)
        indexes.append('<index name="chromatogram">' + "".join(chrom_offsets) + "</index>")
    index_list_offset = len(body.encode("utf-8"))
    before_checksum = body + f'<indexList count="{len(indexes)}">{"".join(indexes)}</indexList><indexListOffset>{index_list_offset}</indexListOffset><fileChecksum>'
    checksum = hashlib.sha1(before_checksum.encode("utf-8")).hexdigest()
    return before_checksum + checksum + "</fileChecksum></indexedmzML>\n"


def build_specs() -> dict[str, tuple[str, dict[str, object]]]:
    specs: dict[str, tuple[str, dict[str, object]]] = {}

    def add(filename: str, content: str, admitted: bool, issues: list[str], indexed: bool, spectra_count: int, chromatogram_count: int, ms_levels: list[int], representation: str, rt_units: list[str], dtypes: list[str], compression: list[str], notes: str, adapter_error: str | None = None) -> None:
        specs[filename] = (content, {"fixture_name": filename, "expected_admission": admitted, "expected_issue_codes": issues, "expected_adapter_error": adapter_error, "indexed": indexed, "run_count": 1, "spectrum_count": spectra_count, "chromatogram_count": chromatogram_count, "ms_levels": ms_levels, "representation": representation, "rt_units": rt_units, "array_dtypes": dtypes, "array_compression": compression, "notes": notes})

    a01_spectra = [spectrum(0, ms_level=1), spectrum(1, ms_level=2)]
    a01_inner = mzml_document("accept_indexed_float64_zlib", a01_spectra, [])
    add("accept_indexed_float64_zlib.mzML", indexed_document(a01_inner, a01_spectra, []), True, [], True, 2, 0, [1, 2], "centroid", ["minute"], ["float64"], ["zlib"], "A01 indexed MS1/MS2 with one complete precursor")

    a02_spectra = [spectrum(0, ms_level=1, dtype="float32", compression="none", rt_unit="second"), spectrum(1, ms_level=2, dtype="float32", compression="none", rt_unit="second")]
    add("accept_nonindexed_float32_uncompressed.mzML", mzml_document("accept_nonindexed_float32_uncompressed", a02_spectra, []), True, [], False, 2, 0, [1, 2], "centroid", ["second"], ["float32"], ["none"], "A02 non-indexed float32 uncompressed")

    a03_spectra = [spectrum(0, ms_level=1, rt_unit="second")]
    a03_chrom = [chromatogram(0, "tic", include_ms_level=True), chromatogram(1, "bpc")]
    add("accept_tic_bpc_chromatograms.mzML", mzml_document("accept_tic_bpc_chromatograms", a03_spectra, a03_chrom), True, [], False, 1, 2, [1], "centroid", ["second"], ["float64", "int64"], ["zlib"], "A03 TIC and BPC; TIC carries whitelisted ms level auxiliary array")

    a07_spectra = [spectrum(0, ms_level=1), spectrum(1, ms_level=2)]
    a07_chrom = [chromatogram(0, "tic", time_unit="minute")]
    a07_inner = mzml_document("accept_indexed_tic_minutes_float64_zlib", a07_spectra, a07_chrom)
    add("accept_indexed_tic_minutes_float64_zlib.mzML", indexed_document(a07_inner, a07_spectra, a07_chrom), True, [], True, 2, 1, [1, 2], "centroid", ["minute"], ["float64"], ["zlib"], "A07 indexed MS1/MS2 with TIC minute time array")

    a08_spectra = [
        spectrum(0, ms_level=1, dtype="float32", compression="none", rt_unit="second"),
        spectrum(1, ms_level=2, dtype="float32", compression="none", rt_unit="second"),
    ]
    a08_chrom = [chromatogram(0, "bpc", dtype="float32", compression="none")]
    add("accept_nonindexed_bpc_seconds_float32_uncompressed.mzML", mzml_document("accept_nonindexed_bpc_seconds_float32_uncompressed", a08_spectra, a08_chrom), True, [], False, 2, 1, [1, 2], "centroid", ["second"], ["float32"], ["none"], "A08 non-indexed MS1/MS2 with BPC second time array")

    a04_spectra = [spectrum(0, ms_level=1), spectrum(1, ms_level=1)]
    a04_inner = mzml_document("accept_ms1_only_indexed_float64_zlib", a04_spectra, [])
    add("accept_ms1_only_indexed_float64_zlib.mzML", indexed_document(a04_inner, a04_spectra, []), True, [], True, 2, 0, [1], "centroid", ["minute"], ["float64"], ["zlib"], "A04 indexed MS1-only float64 zlib")

    a05_spectra = [
        spectrum(0, ms_level=1, dtype="float32", compression="none", rt_unit="second"),
        spectrum(1, ms_level=1, dtype="float32", compression="none", rt_unit="second"),
    ]
    add("accept_ms1_only_nonindexed_float32_uncompressed.mzML", mzml_document("accept_ms1_only_nonindexed_float32_uncompressed", a05_spectra, []), True, [], False, 2, 0, [1], "centroid", ["second"], ["float32"], ["none"], "A05 non-indexed MS1-only float32 uncompressed")

    a06_spectra = [
        spectrum(0, ms_level=1),
        spectrum(
            1,
            ms_level=2,
            selected_ion_mz=678.9,
            charge=3,
            selected_ion_intensity=1234.5,
            source_spectrum_ref="controllerType=0 controllerNumber=1 scan=1",
            isolation_target_mz=679.0,
            isolation_lower_offset=0.7,
            isolation_upper_offset=1.3,
            activation_accession="MS:1000422",
            activation_name="beam-type collision-induced dissociation",
            collision_energy=31.5,
        ),
    ]
    add("accept_ms2_precursor_metadata.mzML", mzml_document("accept_ms2_precursor_metadata", a06_spectra, []), True, [], False, 2, 0, [1, 2], "centroid", ["minute"], ["float64"], ["zlib"], "A06 complete deterministic MS2 precursor and activation metadata")

    reject_cases = [
        ("reject_missing_scan_number.mzML", [spectrum(0, ms_level=1, native_id="spectrum=1")], [], ("MS:1000774", "multiple peak list nativeID format"), "MISSING_SCAN_NUMBER", "R01 native ID has no proven scan number"),
        ("reject_missing_charge.mzML", [spectrum(0, ms_level=1), spectrum(1, ms_level=2, charge=None)], [], None, "MISSING_PRECURSOR_CHARGE", "R02 selected ion omits charge"),
        ("reject_missing_selected_ion_intensity.mzML", [spectrum(0, ms_level=1), spectrum(1, ms_level=2, selected_ion_intensity=None)], [], None, "MISSING_SELECTED_ION_INTENSITY", "R03 selected ion omits intensity"),
        ("reject_multiple_precursors.mzML", [spectrum(0, ms_level=1), spectrum(1, ms_level=2, precursor_count=2)], [], None, "MULTIPLE_PRECURSORS_UNSUPPORTED", "R04 two precursors"),
        ("reject_multiple_selected_ions.mzML", [spectrum(0, ms_level=1), spectrum(1, ms_level=2, selected_ions=2)], [], None, "MULTIPLE_SELECTED_IONS_UNSUPPORTED", "R05 two selected ions"),
        ("reject_unknown_rt_unit.mzML", [spectrum(0, ms_level=1, rt_unit="unknown")], [], None, "UNSUPPORTED_RT_UNIT", "R06 RT has no declared unit"),
        ("reject_unknown_auxiliary_array.mzML", [spectrum(0, ms_level=1, auxiliary_name="vendor mystery")], [], None, "UNSUPPORTED_AUXILIARY_ARRAY", "R07 non-whitelisted auxiliary array"),
        ("reject_srm_chromatogram.mzML", [spectrum(0, ms_level=1)], [chromatogram(0, "srm")], None, "UNSUPPORTED_CHROMATOGRAM_TYPE", "R08 selected-reaction-monitoring chromatogram"),
        ("reject_missing_precursor.mzML", [spectrum(0, ms_level=1), spectrum(1, ms_level=2, precursor_count=0)], [], None, "MISSING_PRECURSOR", "R09 MS2 omits precursor"),
        ("reject_missing_selected_ion.mzML", [spectrum(0, ms_level=1), spectrum(1, ms_level=2, selected_ions=0)], [], None, "MISSING_SELECTED_ION", "R10 precursor omits selected ion"),
        ("reject_missing_selected_ion_mz.mzML", [spectrum(0, ms_level=1), spectrum(1, ms_level=2, selected_ion_mz=None)], [], None, "MISSING_SELECTED_ION_MZ", "R11 selected ion omits m/z"),
        ("reject_zero_charge.mzML", [spectrum(0, ms_level=1), spectrum(1, ms_level=2, charge=0)], [], None, "MISSING_PRECURSOR_CHARGE", "R12 selected ion declares zero charge; Pyteomics preserves key presence separately from its None value"),
    ]
    for filename, spectra_items, chrom_items, native_format, issue, notes in reject_cases:
        content = mzml_document(filename[:-5], spectra_items, chrom_items, native_id_format=native_format or ("MS:1000768", "Thermo nativeID format"))
        add(filename, content, False, [issue], False, len(spectra_items), len(chrom_items), sorted({1 if "ms level\" value=\"1" in item[1] else 2 for item in spectra_items}), "centroid", ["unknown" if issue == "UNSUPPORTED_RT_UNIT" else "minute"], ["float64", "int64"] if issue == "UNSUPPORTED_AUXILIARY_ARRAY" else ["float64"], ["zlib"], notes)

    chromatogram_rejects = [
        ("reject_missing_chromatogram_time_array.mzML", chromatogram(0, "tic", include_time=False), ["MISSING_CHROMATOGRAM_ARRAY", "UNSUPPORTED_RT_UNIT", "UNSUPPORTED_ARRAY_DTYPE", "UNSUPPORTED_ARRAY_COMPRESSION"], "R15 TIC omits time array"),
        ("reject_missing_chromatogram_intensity_array.mzML", chromatogram(0, "tic", include_intensity=False), ["MISSING_CHROMATOGRAM_ARRAY", "UNSUPPORTED_ARRAY_DTYPE", "UNSUPPORTED_ARRAY_COMPRESSION"], "R16 TIC omits intensity array"),
        ("reject_chromatogram_array_length_mismatch.mzML", chromatogram(0, "tic", intensity_values=[100.0]), ["CHROMATOGRAM_ARRAY_LENGTH_MISMATCH"], "R17 TIC arrays have different decoded lengths"),
        ("reject_unknown_chromatogram_time_unit.mzML", chromatogram(0, "tic", time_unit="unknown"), ["UNSUPPORTED_RT_UNIT"], "R18 TIC time array omits its unit"),
        ("reject_chromatogram_precursor_semantics.mzML", chromatogram(0, "tic", precursor_semantics=True), ["UNSUPPORTED_CHROMATOGRAM_SEMANTICS"], "R19 TIC carries precursor semantics"),
        ("reject_chromatogram_product_semantics.mzML", chromatogram(0, "tic", product_semantics=True), ["UNSUPPORTED_CHROMATOGRAM_SEMANTICS"], "R20 TIC carries product semantics"),
        ("reject_unknown_chromatogram_auxiliary_array.mzML", chromatogram(0, "tic", auxiliary_name="vendor mystery"), ["UNSUPPORTED_AUXILIARY_ARRAY"], "R21 TIC carries a non-whitelisted auxiliary array"),
    ]
    for filename, chrom_item, issues, notes in chromatogram_rejects:
        spectra_items = [spectrum(0, ms_level=1)]
        content = mzml_document(filename[:-5], spectra_items, [chrom_item])
        add(filename, content, False, issues, False, 1, 1, [1], "centroid", ["unknown" if "UNSUPPORTED_RT_UNIT" in issues else "second"], ["float64", "int64"] if "UNSUPPORTED_AUXILIARY_ARRAY" in issues else ["float64"], ["zlib"], notes)

    adapter_rejects = [
        ("reject_negative_isolation_offset.mzML", spectrum(1, ms_level=2, isolation_lower_offset=-0.1), "NEGATIVE_ISOLATION_OFFSET", "R13 isolation lower offset is negative"),
        ("reject_nonfinite_precursor_value.mzML", spectrum(1, ms_level=2, selected_ion_mz=float("nan")), "NONFINITE_PRECURSOR_VALUE", "R14 selected ion m/z is NaN"),
    ]
    for filename, ms2_item, error_code, notes in adapter_rejects:
        spectra_items = [spectrum(0, ms_level=1), ms2_item]
        content = mzml_document(filename[:-5], spectra_items, [])
        add(filename, content, True, [], False, 2, 0, [1, 2], "centroid", ["minute"], ["float64"], ["zlib"], notes, adapter_error=error_code)
    return specs


def build_all(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    specs = build_specs()
    for filename, (content, _manifest) in specs.items():
        (output_dir / filename).write_text(content, encoding="utf-8", newline="\n")
    manifest = {"schema_version": 1, "fixtures": [item[1] for item in specs.values()]}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic P1-B1 mzML fixtures")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    build_all(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
