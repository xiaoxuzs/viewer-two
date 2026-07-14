from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

from binary_layer import PipelineContext, SourceInspector, build_default_registry


TOP_LEVEL_HEADER = struct.Struct("<4sHBBQQ")
ARRAYS_HEADER = struct.Struct("<8sHBBIQQQQ16s")
DIRECTORY_LENGTH = struct.Struct("<Q")


def parse_v2_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    header = TOP_LEVEL_HEADER.unpack(raw[: TOP_LEVEL_HEADER.size])
    directory_offset = header[-1]
    directory_length = DIRECTORY_LENGTH.unpack(raw[directory_offset : directory_offset + 8])[0]
    directory_raw = raw[directory_offset + 8 : directory_offset + 8 + directory_length]
    assert directory_offset + 8 + directory_length == len(raw)
    directory = json.loads(directory_raw.decode("utf-8"))
    assert directory_raw == json.dumps(
        directory,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    payloads: dict[str, bytes] = {}
    previous_end = TOP_LEVEL_HEADER.size
    for entry in directory:
        assert entry["offset"] >= previous_end
        payload = raw[entry["offset"] : entry["offset"] + entry["length"]]
        assert len(payload) == entry["length"]
        assert hashlib.sha256(payload).hexdigest() == entry["checksum"]
        payloads[entry["block_name"]] = payload
        previous_end = entry["offset"] + entry["length"]
    return {"raw": raw, "header": header, "directory": directory, "payloads": payloads}


def parse_arrays_block(raw: bytes) -> dict[str, Any]:
    header = ARRAYS_HEADER.unpack(raw[: ARRAYS_HEADER.size])
    directory_offset = header[5]
    directory_length = header[6]
    payload_offset = header[7]
    payload_length = header[8]
    directory_raw = raw[directory_offset : directory_offset + directory_length]
    directory = json.loads(directory_raw.decode("utf-8"))
    assert directory_raw == json.dumps(
        directory,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert raw[directory_offset + directory_length : payload_offset] == b"\0" * (
        payload_offset - directory_offset - directory_length
    )
    assert payload_offset + payload_length == len(raw)
    return {"header": header, "directory": directory, "payload_offset": payload_offset}


def build_real_blocks(fixture_name: str):
    source = Path(__file__).parent / "fixtures" / "mzml" / fixture_name
    profile = SourceInspector().inspect([source])
    context = PipelineContext(profile)
    registry = build_default_registry()
    for step_name in ("file_validate", "hash_input", "real_mzml_parse", "string_pool_build", "index_build"):
        registry.get(step_name).run(context)
    return context.blocks
