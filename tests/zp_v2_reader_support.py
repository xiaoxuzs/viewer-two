from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path
from typing import BinaryIO

from binary_layer import ArrayBlock, ChromatogramBlock, ZpWriter
from zp_v2_writer_support import build_real_blocks


TOP_HEADER = struct.Struct("<4sHBBQQ")
LENGTH = struct.Struct("<Q")
ARRAYS_HEADER = struct.Struct("<8sHBBIQQQQ16s")


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_complete_v2(path: Path, *, intensity_shift: float = 0.0):
    blocks = build_real_blocks("accept_ms2_precursor_metadata.mzML")
    blocks.arrays.extend(
        [
            ArrayBlock("chromatogram_manual:time", "time", "float64", [0.0, 0.125]),
            ArrayBlock("chromatogram_manual:intensity", "intensity", "float64", [10.0 + intensity_shift, -2.5]),
        ]
    )
    blocks.chromatograms.append(
        ChromatogramBlock(
            "chromatogram_manual",
            blocks.runs[0].run_id,
            "tic",
            "chromatogram_manual:time",
            "chromatogram_manual:intensity",
            "manual chromatogram",
        )
    )
    blocks.global_meta.chromatogram_count = 1
    blocks.global_meta.array_count = len(blocks.arrays)
    blocks.runs[0].chromatogram_count = 1
    blocks.string_pool.strings.extend(["tic", "manual chromatogram"])
    ZpWriter().write(path, blocks, format_version=2)
    return blocks


def raw_layout(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    header = TOP_HEADER.unpack(raw[:24])
    directory_offset = header[-1]
    directory_length = LENGTH.unpack(raw[directory_offset : directory_offset + 8])[0]
    directory = json.loads(raw[directory_offset + 8 : directory_offset + 8 + directory_length].decode("utf-8"))
    arrays_entry = next(item for item in directory if item["block_name"] == "arrays")
    arrays_start = arrays_entry["offset"]
    arrays_header = ARRAYS_HEADER.unpack(raw[arrays_start : arrays_start + 64])
    internal_start = arrays_start + arrays_header[5]
    internal_end = internal_start + arrays_header[6]
    internal = json.loads(raw[internal_start:internal_end].decode("utf-8"))
    return {
        "raw": raw,
        "header": header,
        "directory": directory,
        "arrays_entry": arrays_entry,
        "arrays_header": arrays_header,
        "internal": internal,
    }


def corrupt_array_payload(path: Path, array_id: str) -> None:
    layout = raw_layout(path)
    raw = bytearray(layout["raw"])
    top_entry = layout["arrays_entry"]
    arrays_header = layout["arrays_header"]
    entry = next(item for item in layout["internal"]["entries"] if item["array_id"] == array_id)
    position = top_entry["offset"] + arrays_header[7] + entry["data_offset"]
    raw[position] ^= 1
    path.write_bytes(raw)


class TrackingStream:
    def __init__(self, stream: BinaryIO, events: list[tuple[str, int, int]]) -> None:
        self._stream = stream
        self._events = events

    def __enter__(self):
        self._stream.__enter__()
        return self

    def __exit__(self, *args):
        return self._stream.__exit__(*args)

    def __getattr__(self, name: str):
        return getattr(self._stream, name)

    def seek(self, offset: int, whence: int = os.SEEK_SET):
        result = self._stream.seek(offset, whence)
        self._events.append(("seek", self._stream.tell(), 0))
        return result

    def read(self, size: int = -1) -> bytes:
        offset = self._stream.tell()
        value = self._stream.read(size)
        self._events.append(("read", offset, len(value)))
        return value
