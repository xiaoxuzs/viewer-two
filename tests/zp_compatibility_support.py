from __future__ import annotations

import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from unittest.mock import patch

from binary_layer import (
    ArrayBlock,
    BlockCollection,
    ChromatogramBlock,
    ExtensionBlock,
    GlobalMetaBlock,
    IndexBlock,
    PrecursorBlock,
    RunBlock,
    SpectrumBlock,
    StringPoolBlock,
    ZpValidator,
    ZpWriter,
)
from binary_layer.constants import BLOCK_NAMES


TOP_HEADER = struct.Struct("<4sHBBQQ")
DIRECTORY_LENGTH = struct.Struct("<Q")
ARRAYS_HEADER = struct.Struct("<8sHBBIQQQQ16s")
FIXED_CREATED_AT = datetime(2026, 7, 16, 0, 0, 0, tzinfo=timezone.utc)
FIXED_EPOCH_SECONDS = FIXED_CREATED_AT.timestamp()


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _spectrum_metadata(
    spectrum_id: str,
    *,
    default_array_length: int,
    source_rt_value: float,
    precursor_source_spectrum_ref: str | None,
) -> dict[str, object]:
    return {
        "activation_methods": [],
        "base_peak_intensity": None,
        "base_peak_mz": None,
        "collision_energy": None,
        "collision_energy_unit_accession": None,
        "collision_energy_unit_name": None,
        "data_processing_ref": None,
        "default_array_length": default_array_length,
        "filter_string": None,
        "highest_observed_mz": None,
        "instrument_configuration_ref": None,
        "isolation_window_lower_offset": None,
        "isolation_window_target_mz": None,
        "isolation_window_upper_offset": None,
        "lowest_observed_mz": None,
        "polarity": "positive",
        "precursor_source_spectrum_ref": precursor_source_spectrum_ref,
        "representation": "centroid",
        "scan_window_lower": None,
        "scan_window_upper": None,
        "source_intensity_compression": "none",
        "source_intensity_dtype": "float64",
        "source_mz_compression": "none",
        "source_mz_dtype": "float64",
        "source_rt_unit_accession": "UO:0000010",
        "source_rt_unit_name": "second",
        "source_rt_value": source_rt_value,
        "spectrum_id": spectrum_id,
        "total_ion_current": None,
    }


def build_full_blocks() -> BlockCollection:
    source = "p1-b8.5-full.mzML"
    run_id = "run_000001"
    ms1_native = "controllerType=0 controllerNumber=1 scan=1"
    ms2_native = "controllerType=0 controllerNumber=1 scan=2"
    chromatogram_native = "TIC"
    arrays = [
        ArrayBlock("chromatogram_000001:intensity", "intensity", "float64", [0.0, -0.5, 500.0, 1000.125]),
        ArrayBlock("chromatogram_000001:time", "time", "float64", [0.0, 0.125, 12.75, 60.5]),
        ArrayBlock("spectrum_000001:intensity", "intensity", "float64", [0.0, 12.5, -2.5, 1500.25]),
        ArrayBlock("spectrum_000001:mz", "mz", "float64", [0.0, 100.125, 2500.75, 3000.5]),
        ArrayBlock("spectrum_000002:intensity", "intensity", "float64", [0.0, 100.0, -1.25]),
        ArrayBlock("spectrum_000002:mz", "mz", "float64", [50.25, 100.5, 200.75]),
    ]
    metadata_payload = {
        "chromatograms": [
            {
                "chromatogram_id": "chromatogram_000001",
                "chromatogram_type": "tic",
                "data_processing_ref": None,
                "default_array_length": 4,
                "source_intensity_compression": "none",
                "source_intensity_dtype": "float64",
                "source_time_compression": "none",
                "source_time_dtype": "float64",
                "source_time_unit_accession": "UO:0000010",
                "source_time_unit_name": "second",
            }
        ],
        "data_processing": [],
        "instruments": [],
        "run": {
            "default_instrument_configuration_ref": None,
            "default_source_file_ref": None,
            "run_id": run_id,
            "sample_ref": None,
            "start_time_stamp": None,
        },
        "schema_version": 1,
        "software": [],
        "source": {
            "indexed": True,
            "mzml_version": "1.1.0",
            "native_id_format_accession": "MS:1000768",
            "native_id_format_name": "Thermo nativeID format",
        },
        "spectra": [
            _spectrum_metadata(
                "spectrum_000001",
                default_array_length=4,
                source_rt_value=0.125,
                precursor_source_spectrum_ref=None,
            ),
            _spectrum_metadata(
                "spectrum_000002",
                default_array_length=3,
                source_rt_value=12.75,
                precursor_source_spectrum_ref=ms1_native,
            ),
        ],
    }
    auxiliary_payload = {
        "arrays": [
            {
                "array_accession": "MS:1000786",
                "array_name": "ms level",
                "dtype": "int64",
                "owner_id": "chromatogram_000001",
                "owner_kind": "chromatogram",
                "unit_accession": "UO:0000186",
                "unit_name": "dimensionless unit",
                "values": [1, 1, 1, 1],
            }
        ],
        "schema_version": 1,
    }
    return BlockCollection(
        global_meta=GlobalMetaBlock(
            format_version=1,
            source_type="fixture",
            source_file_name=source,
            source_file_hash="0123456789abcdef" * 4,
            run_count=1,
            spectrum_count=2,
            chromatogram_count=1,
            array_count=6,
            created_at=FIXED_CREATED_AT,
            generator_name="zp-full-compatibility-gate",
            generator_version="1",
            notes=["P1-B8.5 deterministic full logical document."],
        ),
        runs=[RunBlock(run_id, source, "full-run", 2, 1, 0.125, 12.75)],
        spectra=[
            SpectrumBlock(
                "spectrum_000001",
                run_id,
                1,
                1,
                ms1_native,
                0.125,
                None,
                "spectrum_000001:mz",
                "spectrum_000001:intensity",
            ),
            SpectrumBlock(
                "spectrum_000002",
                run_id,
                2,
                2,
                ms2_native,
                12.75,
                "precursor_000001",
                "spectrum_000002:mz",
                "spectrum_000002:intensity",
            ),
        ],
        precursors=[PrecursorBlock("precursor_000001", "spectrum_000002", 445.25, 2, 1234.5)],
        chromatograms=[
            ChromatogramBlock(
                "chromatogram_000001",
                run_id,
                "tic",
                "chromatogram_000001:time",
                "chromatogram_000001:intensity",
                chromatogram_native,
            )
        ],
        arrays=arrays,
        string_pool=StringPoolBlock(
            [source, "full-run", ms1_native, ms2_native, "tic", chromatogram_native]
        ),
        indexes=IndexBlock(
            scan_index=[
                {"scan_number": 1, "spectrum_id": "spectrum_000001"},
                {"scan_number": 2, "spectrum_id": "spectrum_000002"},
            ],
            rt_index=[
                {"rt": 0.125, "spectrum_id": "spectrum_000001"},
                {"rt": 12.75, "spectrum_id": "spectrum_000002"},
            ],
            spectrum_id_index=[
                {"position": 0, "spectrum_id": "spectrum_000001"},
                {"position": 1, "spectrum_id": "spectrum_000002"},
            ],
        ),
        extensions=[
            ExtensionBlock("mzml_metadata", "1", metadata_payload),
            ExtensionBlock("mzml_auxiliary_arrays", "1", auxiliary_payload),
        ],
    )


def build_minimal_blocks() -> BlockCollection:
    source = "p1-b8.5-minimal.mzML"
    native_id = "controllerType=0 controllerNumber=1 scan=1"
    return BlockCollection(
        global_meta=GlobalMetaBlock(
            format_version=1,
            source_type="fixture",
            source_file_name=source,
            source_file_hash="fedcba9876543210" * 4,
            run_count=1,
            spectrum_count=1,
            chromatogram_count=0,
            array_count=2,
            created_at=FIXED_CREATED_AT,
            generator_name="zp-full-compatibility-gate",
            generator_version="1",
            notes=["P1-B8.5 deterministic minimal logical document."],
        ),
        runs=[RunBlock("run_000001", source, "minimal-run", 1, 0, 0.0, 0.0)],
        spectra=[
            SpectrumBlock(
                "spectrum_000001",
                "run_000001",
                1,
                1,
                native_id,
                0.0,
                None,
                "spectrum_000001:mz",
                "spectrum_000001:intensity",
            )
        ],
        arrays=[
            ArrayBlock("spectrum_000001:intensity", "intensity", "float64", [0.0]),
            ArrayBlock("spectrum_000001:mz", "mz", "float64", [0.0]),
        ],
        string_pool=StringPoolBlock([source, "minimal-run", native_id]),
        indexes=IndexBlock(
            scan_index=[{"scan_number": 1, "spectrum_id": "spectrum_000001"}],
            rt_index=[{"rt": 0.0, "spectrum_id": "spectrum_000001"}],
            spectrum_id_index=[{"position": 0, "spectrum_id": "spectrum_000001"}],
        ),
        extensions=[],
    )


def write_zp(path: Path, blocks: BlockCollection, version: int) -> Path:
    with patch("binary_layer.writer.time.time", return_value=FIXED_EPOCH_SECONDS):
        return ZpWriter().write(path, blocks, format_version=version)


def write_pair(directory: Path, blocks: BlockCollection | None = None) -> dict[int, Path]:
    logical = build_full_blocks() if blocks is None else blocks
    paths: dict[int, Path] = {}
    for version in (1, 2):
        path = directory / f"case-v{version}.zp"
        write_zp(path, logical, version)
        paths[version] = path
    return paths


def top_layout(path: Path) -> tuple[list[object], list[dict[str, object]], dict[str, bytes]]:
    raw = path.read_bytes()
    header = list(TOP_HEADER.unpack(raw[: TOP_HEADER.size]))
    directory_offset = int(header[-1])
    directory_length = DIRECTORY_LENGTH.unpack_from(raw, directory_offset)[0]
    directory_start = directory_offset + DIRECTORY_LENGTH.size
    directory = json.loads(raw[directory_start : directory_start + directory_length].decode("utf-8"))
    payloads = {
        entry["block_name"]: raw[entry["offset"] : entry["offset"] + entry["length"]]
        for entry in directory
    }
    return header, directory, payloads


def rebuild_top(
    path: Path,
    header: list[object],
    payloads: dict[str, bytes],
    encodings: dict[str, str],
) -> None:
    output = bytearray(TOP_HEADER.size)
    directory: list[dict[str, object]] = []
    for name in BLOCK_NAMES:
        payload = payloads[name]
        offset = len(output)
        output.extend(payload)
        directory.append(
            {
                "block_name": name,
                "offset": offset,
                "length": len(payload),
                "encoding": encodings[name],
                "checksum": hashlib.sha256(payload).hexdigest(),
            }
        )
    directory_offset = len(output)
    raw_directory = canonical(directory)
    output.extend(DIRECTORY_LENGTH.pack(len(raw_directory)))
    output.extend(raw_directory)
    header[-1] = directory_offset
    output[: TOP_HEADER.size] = TOP_HEADER.pack(*header)
    path.write_bytes(output)


def mutate_top_directory(
    path: Path,
    mutation: Callable[[list[dict[str, object]]], None],
) -> None:
    raw = path.read_bytes()
    header, directory, _payloads = top_layout(path)
    mutation(directory)
    directory_raw = canonical(directory)
    directory_offset = int(header[-1])
    path.write_bytes(
        raw[:directory_offset]
        + DIRECTORY_LENGTH.pack(len(directory_raw))
        + directory_raw
    )


def mutate_header(path: Path, mutation: Callable[[list[object]], None]) -> None:
    raw = bytearray(path.read_bytes())
    header = list(TOP_HEADER.unpack(raw[: TOP_HEADER.size]))
    mutation(header)
    raw[: TOP_HEADER.size] = TOP_HEADER.pack(*header)
    path.write_bytes(raw)


def replace_arrays_raw(path: Path, arrays_raw: bytes) -> None:
    header, directory, payloads = top_layout(path)
    encodings = {entry["block_name"]: entry["encoding"] for entry in directory}
    payloads["arrays"] = arrays_raw
    rebuild_top(path, header, payloads, encodings)


def mutate_v2_arrays_header(path: Path, mutation: Callable[[list[object]], None]) -> None:
    header, _directory, payloads = top_layout(path)
    if int(header[1]) != 2:
        raise ValueError("v2 arrays Header mutation requires a v2 file")
    raw = bytearray(payloads["arrays"])
    arrays_header = list(ARRAYS_HEADER.unpack(raw[: ARRAYS_HEADER.size]))
    mutation(arrays_header)
    raw[: ARRAYS_HEADER.size] = ARRAYS_HEADER.pack(*arrays_header)
    replace_arrays_raw(path, bytes(raw))


def mutate_v2_array_directory(
    path: Path,
    mutation: Callable[[dict[str, object], bytearray], None],
) -> None:
    header, _directory, payloads = top_layout(path)
    if int(header[1]) != 2:
        raise ValueError("v2 arrays directory mutation requires a v2 file")
    raw = payloads["arrays"]
    arrays_header = list(ARRAYS_HEADER.unpack(raw[: ARRAYS_HEADER.size]))
    internal_offset = int(arrays_header[5])
    internal_length = int(arrays_header[6])
    payload_offset = int(arrays_header[7])
    internal = json.loads(raw[internal_offset : internal_offset + internal_length].decode("utf-8"))
    payload = bytearray(raw[payload_offset : payload_offset + int(arrays_header[8])])
    mutation(internal, payload)
    raw_internal = canonical(internal)
    new_payload_offset = (ARRAYS_HEADER.size + len(raw_internal) + 7) & ~7
    arrays_header[4] = len(internal["entries"])
    arrays_header[5] = ARRAYS_HEADER.size
    arrays_header[6] = len(raw_internal)
    arrays_header[7] = new_payload_offset
    arrays_header[8] = len(payload)
    rebuilt = (
        ARRAYS_HEADER.pack(*arrays_header)
        + raw_internal
        + b"\0" * (new_payload_offset - ARRAYS_HEADER.size - len(raw_internal))
        + payload
    )
    replace_arrays_raw(path, rebuilt)


def mutate_json_blocks(path: Path, mutation: Callable[[dict[str, object]], None]) -> None:
    header, directory, payloads = top_layout(path)
    encodings = {entry["block_name"]: entry["encoding"] for entry in directory}
    values: dict[str, object] = {}
    for name, payload in payloads.items():
        if name != "arrays" or int(header[1]) == 1:
            values[name] = json.loads(payload.decode("utf-8"))
    mutation(values)
    for name, value in values.items():
        payloads[name] = canonical(value)
    rebuild_top(path, header, payloads, encodings)


def mutate_pair(paths: dict[int, Path], mutation: Callable[[dict[str, object]], None]) -> None:
    for path in paths.values():
        mutate_json_blocks(path, mutation)


def resize_array(path: Path, array_id: str, value_count: int) -> None:
    header, directory, payloads = top_layout(path)
    if int(header[1]) == 1:
        arrays = json.loads(payloads["arrays"].decode("utf-8"))
        record = next(item for item in arrays if item["array_id"] == array_id)
        record["values"] = record["values"][:value_count]
        payloads["arrays"] = canonical(arrays)
    else:
        raw = payloads["arrays"]
        arrays_header = list(ARRAYS_HEADER.unpack(raw[: ARRAYS_HEADER.size]))
        internal_offset = int(arrays_header[5])
        internal_length = int(arrays_header[6])
        payload_offset = int(arrays_header[7])
        internal = json.loads(raw[internal_offset : internal_offset + internal_length].decode("utf-8"))
        old_payload = raw[payload_offset : payload_offset + int(arrays_header[8])]
        new_payload = bytearray()
        for entry in internal["entries"]:
            start = entry["data_offset"]
            item = old_payload[start : start + entry["byte_length"]]
            if entry["array_id"] == array_id:
                item = item[: value_count * 8]
                entry["value_count"] = value_count
            entry["data_offset"] = len(new_payload)
            entry["byte_length"] = len(item)
            entry["checksum"] = hashlib.sha256(item).hexdigest()
            new_payload.extend(item)
        raw_internal = canonical(internal)
        new_payload_offset = (ARRAYS_HEADER.size + len(raw_internal) + 7) & ~7
        arrays_header[4] = len(internal["entries"])
        arrays_header[5] = ARRAYS_HEADER.size
        arrays_header[6] = len(raw_internal)
        arrays_header[7] = new_payload_offset
        arrays_header[8] = len(new_payload)
        payloads["arrays"] = (
            ARRAYS_HEADER.pack(*arrays_header)
            + raw_internal
            + b"\0" * (new_payload_offset - ARRAYS_HEADER.size - len(raw_internal))
            + new_payload
        )
    encodings = {entry["block_name"]: entry["encoding"] for entry in directory}
    rebuild_top(path, header, payloads, encodings)


def validation_summary(path: Path) -> dict[str, object]:
    result = ZpValidator().validate(path)
    return {
        "valid": result.valid,
        "checked_blocks": result.checked_blocks,
        "codes": [issue.code for issue in result.issues],
        "locations": [issue.block_name for issue in result.issues],
    }
