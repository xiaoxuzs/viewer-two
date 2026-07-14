from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Callable

from binary_layer.constants import BLOCK_NAMES


TOP_HEADER = struct.Struct("<4sHBBQQ")
LENGTH = struct.Struct("<Q")
ARRAYS_HEADER = struct.Struct("<8sHBBIQQQQ16s")


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def top_layout(path: Path) -> tuple[list[object], list[dict[str, object]], dict[str, bytes]]:
    raw = path.read_bytes()
    header = list(TOP_HEADER.unpack(raw[: TOP_HEADER.size]))
    directory_offset = header[-1]
    directory_length = LENGTH.unpack(raw[directory_offset : directory_offset + LENGTH.size])[0]
    directory = json.loads(
        raw[directory_offset + LENGTH.size : directory_offset + LENGTH.size + directory_length].decode("utf-8")
    )
    payloads = {
        entry["block_name"]: raw[entry["offset"] : entry["offset"] + entry["length"]]
        for entry in directory
    }
    return header, directory, payloads


def rebuild(path: Path, payloads: dict[str, bytes], *, header: list[object] | None = None) -> None:
    if header is None:
        header, _directory, _old_payloads = top_layout(path)
    raw = bytearray(TOP_HEADER.size)
    directory: list[dict[str, object]] = []
    for name in BLOCK_NAMES:
        payload = payloads[name]
        offset = len(raw)
        raw.extend(payload)
        directory.append(
            {
                "block_name": name,
                "offset": offset,
                "length": len(payload),
                "encoding": "zp-arrays-v2" if name == "arrays" else "utf-8-json",
                "checksum": hashlib.sha256(payload).hexdigest(),
            }
        )
    directory_offset = len(raw)
    directory_raw = canonical(directory)
    raw.extend(LENGTH.pack(len(directory_raw)))
    raw.extend(directory_raw)
    header[-1] = directory_offset
    raw[: TOP_HEADER.size] = TOP_HEADER.pack(*header)
    path.write_bytes(raw)


def replace_block_raw(path: Path, block_name: str, payload: bytes) -> None:
    header, _directory, payloads = top_layout(path)
    payloads[block_name] = payload
    rebuild(path, payloads, header=header)


def mutate_json_block(
    path: Path,
    block_name: str,
    mutate: Callable[[object], None],
) -> None:
    header, _directory, payloads = top_layout(path)
    value = json.loads(payloads[block_name].decode("utf-8"))
    mutate(value)
    payloads[block_name] = canonical(value)
    rebuild(path, payloads, header=header)


def corrupt_top_block_without_checksum_update(path: Path, block_name: str) -> None:
    raw = bytearray(path.read_bytes())
    _header, directory, _payloads = top_layout(path)
    entry = next(item for item in directory if item["block_name"] == block_name)
    raw[entry["offset"]] ^= 1
    path.write_bytes(raw)


def replace_arrays_block(path: Path, arrays_raw: bytes) -> None:
    replace_block_raw(path, "arrays", arrays_raw)


def arrays_layout(path: Path) -> tuple[list[object], dict[str, object], bytearray]:
    _header, _directory, payloads = top_layout(path)
    raw = payloads["arrays"]
    header = list(ARRAYS_HEADER.unpack(raw[: ARRAYS_HEADER.size]))
    directory_offset, directory_length, payload_offset, payload_length = header[5:9]
    directory = json.loads(raw[directory_offset : directory_offset + directory_length].decode("utf-8"))
    payload = bytearray(raw[payload_offset : payload_offset + payload_length])
    return header, directory, payload


def rebuild_arrays(
    header: list[object],
    directory: dict[str, object],
    payload: bytes,
) -> bytes:
    directory_raw = canonical(directory)
    payload_offset = (ARRAYS_HEADER.size + len(directory_raw) + 7) & ~7
    header[4] = len(directory["entries"])
    header[5] = ARRAYS_HEADER.size
    header[6] = len(directory_raw)
    header[7] = payload_offset
    header[8] = len(payload)
    return (
        ARRAYS_HEADER.pack(*header)
        + directory_raw
        + b"\0" * (payload_offset - ARRAYS_HEADER.size - len(directory_raw))
        + payload
    )


def mutate_array_value(
    path: Path,
    array_id: str,
    value_index: int,
    value: float,
    *,
    update_per_array_checksum: bool,
) -> None:
    header, directory, payload = arrays_layout(path)
    entry = next(item for item in directory["entries"] if item["array_id"] == array_id)
    struct.pack_into("<d", payload, entry["data_offset"] + value_index * 8, value)
    if update_per_array_checksum:
        start = entry["data_offset"]
        end = start + entry["byte_length"]
        entry["checksum"] = hashlib.sha256(payload[start:end]).hexdigest()
    replace_arrays_block(path, rebuild_arrays(header, directory, payload))


def mutate_array_directory(
    path: Path,
    mutate: Callable[[dict[str, object], bytearray], None],
) -> None:
    header, directory, payload = arrays_layout(path)
    mutate(directory, payload)
    replace_arrays_block(path, rebuild_arrays(header, directory, payload))


def resize_array(path: Path, array_id: str, value_count: int) -> None:
    header, directory, payload = arrays_layout(path)
    rebuilt_payload = bytearray()
    for entry in directory["entries"]:
        start = entry["data_offset"]
        current = payload[start : start + entry["byte_length"]]
        if entry["array_id"] == array_id:
            current = current[: value_count * 8]
            entry["value_count"] = value_count
        entry["data_offset"] = len(rebuilt_payload)
        entry["byte_length"] = len(current)
        entry["checksum"] = hashlib.sha256(current).hexdigest()
        rebuilt_payload.extend(current)
    replace_arrays_block(path, rebuild_arrays(header, directory, rebuilt_payload))
