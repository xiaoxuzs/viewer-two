from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

import pytest

from binary_layer import PipelineContext, PipelineRunner, PlanBuilder, SourceInspector, SourceProfile, build_default_registry
from binary_layer.constants import BLOCK_NAMES, DIRECTORY_LENGTH_STRUCT, HEADER_SIZE, HEADER_STRUCT
from binary_layer.serialization import canonical_json_bytes, parse_json_bytes


def mock_mzml_profile(source: Path) -> SourceProfile:
    return SourceProfile(
        source_type="mock_mzml",
        input_files=(source,),
        file_count=1,
        has_spectra=True,
        has_chromatograms=False,
        has_identification=False,
        has_quantification=False,
        requires_pre_conversion=False,
        notes=("Explicit P0 mock mzML profile for tests.",),
    )


def mock_raw_profile(source: Path) -> SourceProfile:
    return SourceProfile(
        source_type="mock_raw",
        input_files=(source,),
        file_count=1,
        has_spectra=True,
        has_chromatograms=False,
        has_identification=False,
        has_quantification=False,
        requires_pre_conversion=True,
        notes=("Explicit P0 mock RAW profile for tests.",),
    )


@pytest.fixture
def pipeline_factory(tmp_path: Path) -> Callable[[str], PipelineContext]:
    def build(suffix: str = ".mzML") -> PipelineContext:
        source = tmp_path / f"sample{suffix}"
        source.write_bytes(b"mock source bytes")
        profile = mock_raw_profile(source) if suffix.lower() == ".raw" else mock_mzml_profile(source)
        plan = PlanBuilder().build(profile)
        output = tmp_path / f"result_{suffix[1:].lower()}.zp"
        context = PipelineContext(profile, metadata={"output_path": output})
        return PipelineRunner().run(plan, build_default_registry(), context)
    return build


@pytest.fixture
def valid_zp(pipeline_factory: Callable[[str], PipelineContext]) -> Path:
    return Path(pipeline_factory(".mzML").artifacts["output_zp_path"])


def load_raw_zp(path: Path) -> tuple[tuple[Any, ...], list[dict[str, Any]], dict[str, Any]]:
    raw = path.read_bytes()
    header = HEADER_STRUCT.unpack(raw[:HEADER_SIZE])
    offset = header[-1]
    directory_length = DIRECTORY_LENGTH_STRUCT.unpack(raw[offset:offset + 8])[0]
    directory = parse_json_bytes(raw[offset + 8:offset + 8 + directory_length])
    payloads = {
        entry["block_name"]: parse_json_bytes(raw[entry["offset"]:entry["offset"] + entry["length"]])
        for entry in directory
    }
    return header, directory, payloads


def rewrite_zp(
    path: Path,
    mutate_payloads: Callable[[dict[str, Any]], None] | None = None,
    omitted: set[str] | None = None,
) -> None:
    header, _directory, payloads = load_raw_zp(path)
    if mutate_payloads:
        mutate_payloads(payloads)
    omitted = omitted or set()
    output = bytearray(b"\x00" * HEADER_SIZE)
    entries: list[dict[str, Any]] = []
    for name in BLOCK_NAMES:
        if name in omitted:
            continue
        raw = canonical_json_bytes(payloads[name])
        entry = {
            "block_name": name,
            "offset": len(output),
            "length": len(raw),
            "encoding": "json",
            "checksum": hashlib.sha256(raw).hexdigest(),
        }
        output.extend(raw)
        entries.append(entry)
    directory_offset = len(output)
    directory_raw = canonical_json_bytes(entries)
    output.extend(DIRECTORY_LENGTH_STRUCT.pack(len(directory_raw)))
    output.extend(directory_raw)
    output[:HEADER_SIZE] = HEADER_STRUCT.pack(*header[:-1], directory_offset)
    path.write_bytes(output)


def rewrite_directory(
    path: Path,
    mutate: Callable[[list[dict[str, Any]]], None],
    trailing: bytes = b"",
) -> None:
    raw = path.read_bytes()
    header = HEADER_STRUCT.unpack(raw[:HEADER_SIZE])
    directory_offset = header[-1]
    directory_length = DIRECTORY_LENGTH_STRUCT.unpack(raw[directory_offset:directory_offset + 8])[0]
    directory = parse_json_bytes(raw[directory_offset + 8:directory_offset + 8 + directory_length])
    mutate(directory)
    directory_raw = canonical_json_bytes(directory)
    path.write_bytes(
        raw[:directory_offset]
        + DIRECTORY_LENGTH_STRUCT.pack(len(directory_raw))
        + directory_raw
        + trailing
    )
