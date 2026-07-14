from __future__ import annotations

import argparse
import base64
import hashlib
import struct
import zlib
from pathlib import Path
from xml.sax.saxutils import quoteattr

MS_NS = "http://psi.hupo.org/ms/mzml"


def _cv(accession: str, name: str, value: object = "", unit: tuple[str, str, str] | None = None) -> str:
    attributes = [f'cvRef="MS"', f'accession={quoteattr(accession)}', f'name={quoteattr(name)}', f'value={quoteattr(str(value))}']
    if unit is not None:
        cv_ref, unit_accession, unit_name = unit
        attributes.extend((f'unitCvRef={quoteattr(cv_ref)}', f'unitAccession={quoteattr(unit_accession)}', f'unitName={quoteattr(unit_name)}'))
    return "<cvParam " + " ".join(attributes) + "/>"


def _binary_array(values: list[float], dtype: str, compression: str, kind: str) -> str:
    format_code = "d" if dtype == "float64" else "f"
    raw = struct.pack("<" + format_code * len(values), *values)
    if compression == "zlib":
        raw = zlib.compress(raw)
    encoded = base64.b64encode(raw).decode("ascii")
    dtype_term = ("MS:1000523", "64-bit float") if dtype == "float64" else ("MS:1000521", "32-bit float")
    compression_term = ("MS:1000574", "zlib compression") if compression == "zlib" else ("MS:1000576", "no compression")
    array_terms = {
        "mz": ("MS:1000514", "m/z array", ("MS", "MS:1000040", "m/z")),
        "intensity": ("MS:1000515", "intensity array", ("MS", "MS:1000131", "number of detector counts")),
        "time": ("MS:1000595", "time array", ("UO", "UO:0000010", "second")),
    }
    accession, name, unit = array_terms[kind]
    return (
        f'<binaryDataArray encodedLength="{len(encoded)}">'
        + _cv(*dtype_term)
        + _cv(*compression_term)
        + _cv(accession, name, unit=unit)
        + f"<binary>{encoded}</binary></binaryDataArray>"
    )


def _spectrum(index: int, ms_level: int, peaks: int, dtype: str, compression: str) -> tuple[str, str]:
    native_id = f"controllerType=0 controllerNumber=1 scan={index + 1}"
    mz_values = [100.0 + value * 0.01 + (index % 100) * 0.0001 for value in range(peaks)]
    intensity_values = [float((value + 1) * (1 + index % 17)) for value in range(peaks)]
    arrays = _binary_array(mz_values, dtype, compression, "mz") + _binary_array(intensity_values, dtype, compression, "intensity")
    precursor = ""
    if ms_level == 2:
        precursor = (
            '<precursorList count="1"><precursor spectrumRef="controllerType=0 controllerNumber=1 scan=1">'
            '<isolationWindow>'
            + _cv("MS:1000827", "isolation window target m/z", 445.2, ("MS", "MS:1000040", "m/z"))
            + _cv("MS:1000828", "isolation window lower offset", 1.0, ("MS", "MS:1000040", "m/z"))
            + _cv("MS:1000829", "isolation window upper offset", 1.0, ("MS", "MS:1000040", "m/z"))
            + '</isolationWindow><selectedIonList count="1"><selectedIon>'
            + _cv("MS:1000744", "selected ion m/z", 445.2, ("MS", "MS:1000040", "m/z"))
            + _cv("MS:1000041", "charge state", 2)
            + _cv("MS:1000042", "peak intensity", 50.0, ("MS", "MS:1000131", "number of detector counts"))
            + '</selectedIon></selectedIonList><activation>'
            + _cv("MS:1000133", "collision-induced dissociation")
            + _cv("MS:1000045", "collision energy", 25.0, ("UO", "UO:0000266", "electronvolt"))
            + "</activation></precursor></precursorList>"
        )
    body = (
        f'<spectrum index="{index}" id={quoteattr(native_id)} defaultArrayLength="{peaks}">'
        + _cv("MS:1000511", "ms level", ms_level)
        + _cv("MS:1000127", "centroid spectrum")
        + _cv("MS:1000130", "positive scan")
        + '<scanList count="1"><scan instrumentConfigurationRef="IC1">'
        + _cv("MS:1000016", "scan start time", index * 0.5, ("UO", "UO:0000010", "second"))
        + "</scan></scanList>"
        + precursor
        + f'<binaryDataArrayList count="2">{arrays}</binaryDataArrayList></spectrum>'
    )
    return native_id, body


def _tic(spectrum_count: int, dtype: str, compression: str) -> tuple[str, str]:
    times = [index * 0.5 for index in range(spectrum_count)]
    intensities = [float(1000 + index * 3) for index in range(spectrum_count)]
    arrays = _binary_array(times, dtype, compression, "time") + _binary_array(intensities, dtype, compression, "intensity")
    return "TIC", (
        f'<chromatogram index="0" id="TIC" defaultArrayLength="{spectrum_count}">'
        + _cv("MS:1000235", "total ion current chromatogram")
        + f'<binaryDataArrayList count="2">{arrays}</binaryDataArrayList></chromatogram>'
    )


def _inner_document(name: str, spectra: list[tuple[str, str]], chromatograms: list[tuple[str, str]]) -> str:
    chromatogram_list = ""
    if chromatograms:
        chromatogram_list = f'<chromatogramList count="{len(chromatograms)}" defaultDataProcessingRef="DP1">{"".join(value for _, value in chromatograms)}</chromatogramList>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<mzML xmlns="{MS_NS}" id={quoteattr(name)} version="1.1.0">'
        '<cvList count="2"><cv id="MS" fullName="Proteomics Standards Initiative Mass Spectrometry Ontology" version="4.1.186" URI="https://raw.githubusercontent.com/HUPO-PSI/psi-ms-CV/master/psi-ms.obo"/><cv id="UO" fullName="Unit Ontology" version="2023-05-25" URI="http://purl.obolibrary.org/obo/uo.obo"/></cvList>'
        '<fileDescription><fileContent>' + _cv("MS:1000579", "MS1 spectrum") + _cv("MS:1000580", "MSn spectrum")
        + '</fileContent><sourceFileList count="1"><sourceFile id="SF1" name="scale.raw" location="file:///benchmark">'
        + _cv("MS:1000768", "Thermo nativeID format") + _cv("MS:1000563", "Thermo RAW format")
        + '</sourceFile></sourceFileList></fileDescription><softwareList count="1"><software id="benchmark_writer" version="1.0">'
        + _cv("MS:1000799", "custom unreleased software tool", "deterministic P1-B6 generator")
        + '</software></softwareList><instrumentConfigurationList count="1"><instrumentConfiguration id="IC1"><componentList count="3"><source order="1">'
        + _cv("MS:1000073", "electrospray ionization") + '</source><analyzer order="2">' + _cv("MS:1000484", "orbitrap")
        + '</analyzer><detector order="3">' + _cv("MS:1000253", "electron multiplier")
        + '</detector></componentList></instrumentConfiguration></instrumentConfigurationList><dataProcessingList count="1"><dataProcessing id="DP1"><processingMethod order="1" softwareRef="benchmark_writer">'
        + _cv("MS:1000544", "Conversion to mzML")
        + f'</processingMethod></dataProcessing></dataProcessingList><run id="run1" defaultInstrumentConfigurationRef="IC1" defaultSourceFileRef="SF1"><spectrumList count="{len(spectra)}" defaultDataProcessingRef="DP1">'
        + "".join(value for _, value in spectra) + f"</spectrumList>{chromatogram_list}</run></mzML>"
    )


def _indexed_document(inner: str, spectra: list[tuple[str, str]], chromatograms: list[tuple[str, str]]) -> str:
    body = f'<?xml version="1.0" encoding="UTF-8"?>\n<indexedmzML xmlns="{MS_NS}">\n' + inner.split("\n", 1)[1] + "\n"
    indexes: list[str] = []
    for name, records in (("spectrum", spectra), ("chromatogram", chromatograms)):
        if not records:
            continue
        search_from = 0
        offsets: list[str] = []
        for identifier, xml in records:
            position = body.index(xml, search_from)
            offsets.append(f'<offset idRef={quoteattr(identifier)}>{len(body[:position].encode("utf-8"))}</offset>')
            search_from = position + len(xml)
        indexes.append(f'<index name="{name}">{"".join(offsets)}</index>')
    index_list_offset = len(body.encode("utf-8"))
    before_checksum = body + f'<indexList count="{len(indexes)}">{"".join(indexes)}</indexList><indexListOffset>{index_list_offset}</indexListOffset><fileChecksum>'
    return before_checksum + hashlib.sha1(before_checksum.encode("utf-8")).hexdigest() + "</fileChecksum></indexedmzML>\n"


def generate_scale_mzml(
    output: str | Path,
    *,
    spectrum_count: int,
    ms2_ratio: float,
    peaks_per_spectrum: int,
    include_tic: bool,
    dtype: str,
    compression: str,
    indexed: bool,
) -> Path:
    if spectrum_count < 2 or peaks_per_spectrum < 1:
        raise ValueError("spectrum_count must be >= 2 and peaks_per_spectrum must be >= 1")
    if not 0.0 <= ms2_ratio < 1.0:
        raise ValueError("ms2_ratio must be in [0, 1)")
    if dtype not in {"float32", "float64"} or compression not in {"none", "zlib"}:
        raise ValueError("unsupported benchmark dtype or compression")
    ms2_count = min(spectrum_count - 1, int(round(spectrum_count * ms2_ratio)))
    ms1_count = spectrum_count - ms2_count
    spectra = [
        _spectrum(index, 1 if index < ms1_count else 2, peaks_per_spectrum, dtype, compression)
        for index in range(spectrum_count)
    ]
    chromatograms = [_tic(spectrum_count, dtype, compression)] if include_tic else []
    target = Path(output)
    if target.suffix.lower() == ".zp":
        raise ValueError("scale generator never writes .zp files")
    target.parent.mkdir(parents=True, exist_ok=True)
    document_id = (
        f"p1_b6_scale_s{spectrum_count}_p{peaks_per_spectrum}_m{ms2_count}_"
        f"{dtype}_{compression}_{'tic' if include_tic else 'no_tic'}"
    )
    inner = _inner_document(document_id, spectra, chromatograms)
    content = _indexed_document(inner, spectra, chromatograms) if indexed else inner + "\n"
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic, supported mzML scale samples")
    parser.add_argument("--spectrum-count", type=int, required=True)
    parser.add_argument("--ms2-ratio", type=float, default=0.5)
    parser.add_argument("--peaks-per-spectrum", type=int, required=True)
    parser.add_argument("--include-tic", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dtype", choices=("float32", "float64"), default="float64")
    parser.add_argument("--compression", choices=("none", "zlib"), default="zlib")
    parser.add_argument("--indexed", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path("benchmarks/generated") / f"scale_{args.spectrum_count}_{args.peaks_per_spectrum}.mzML"
    path = generate_scale_mzml(
        output,
        spectrum_count=args.spectrum_count,
        ms2_ratio=args.ms2_ratio,
        peaks_per_spectrum=args.peaks_per_spectrum,
        include_tic=args.include_tic,
        dtype=args.dtype,
        compression=args.compression,
        indexed=args.indexed,
    )
    print(path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
