from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


def to_primitive(value: Any) -> Any:
    if is_dataclass(value):
        result = {}
        for item in fields(value):
            item_value = getattr(value, item.name)
            if item_value is None and item.metadata.get("omit_if_none"):
                continue
            result[item.name] = to_primitive(item_value)
        return result
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        try:
            return value.decode("ascii")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        to_primitive(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def iter_canonical_json_bytes(
    value: Any,
    *,
    list_batch_size: int = 256,
):
    """Yield byte-identical canonical JSON while bounding large-list copies."""
    if type(list_batch_size) is not int or list_batch_size <= 0:
        raise ValueError("list_batch_size must be a positive integer")
    if is_dataclass(value):
        shallow: dict[str, Any] = {}
        for item in fields(value):
            item_value = getattr(value, item.name)
            if item_value is None and item.metadata.get("omit_if_none"):
                continue
            shallow[item.name] = item_value
        yield from iter_canonical_json_bytes(
            shallow,
            list_batch_size=list_batch_size,
        )
        return
    if isinstance(value, dict):
        yield b"{"
        normalized = {str(key): item for key, item in value.items()}
        for position, key in enumerate(sorted(normalized)):
            if position:
                yield b","
            yield canonical_json_bytes(key)
            yield b":"
            yield from iter_canonical_json_bytes(
                normalized[key],
                list_batch_size=list_batch_size,
            )
        yield b"}"
        return
    if isinstance(value, (list, tuple)):
        yield b"["
        if len(value) > list_batch_size:
            for start in range(0, len(value), list_batch_size):
                if start:
                    yield b","
                encoded = canonical_json_bytes(value[start : start + list_batch_size])
                yield encoded[1:-1]
        else:
            for position, item in enumerate(value):
                if position:
                    yield b","
                yield from iter_canonical_json_bytes(
                    item,
                    list_batch_size=list_batch_size,
                )
        yield b"]"
        return
    yield canonical_json_bytes(value)


def parse_json_bytes(value: bytes) -> Any:
    return json.loads(value.decode("utf-8"))


def parse_utc_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
