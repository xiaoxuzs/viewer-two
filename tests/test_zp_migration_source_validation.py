from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from binary_layer import migrate_v1_to_v2
from binary_layer.constants import DIRECTORY_LENGTH_STRUCT
from binary_layer.migration import MigrationError
from zp_compatibility_support import (
    canonical,
    mutate_header,
    mutate_json_blocks,
    mutate_top_directory,
    top_layout,
)


FIXTURES = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_version",
        "arrays_encoding",
        "checksum",
        "domain_count",
        "global_meta_version",
        "noncanonical_directory",
        "trailing_data",
    ],
)
def test_corrupt_or_noncanonical_v1_source_is_rejected_before_target(
    mutation: str,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.zp"
    source.write_bytes((FIXTURES / "valid_full_v1.zp").read_bytes())
    if mutation == "unknown_version":
        mutate_header(source, lambda header: header.__setitem__(1, 999))
    elif mutation == "arrays_encoding":
        mutate_top_directory(
            source,
            lambda directory: next(item for item in directory if item["block_name"] == "arrays").__setitem__(
                "encoding", "zp-arrays-v2"
            ),
        )
    elif mutation == "checksum":
        mutate_top_directory(
            source,
            lambda directory: directory[0].__setitem__("checksum", "0" * 64),
        )
    elif mutation == "domain_count":
        mutate_json_blocks(
            source,
            lambda blocks: blocks["global_meta"].__setitem__("array_count", 999),
        )
    elif mutation == "global_meta_version":
        mutate_json_blocks(
            source,
            lambda blocks: blocks["global_meta"].__setitem__("format_version", 2),
        )
    elif mutation == "noncanonical_directory":
        raw = source.read_bytes()
        header, directory, _payloads = top_layout(source)
        directory_offset = int(header[-1])
        noncanonical = json.dumps(directory, ensure_ascii=False).encode("utf-8")
        source.write_bytes(
            raw[:directory_offset]
            + DIRECTORY_LENGTH_STRUCT.pack(len(noncanonical))
            + noncanonical
        )
    elif mutation == "trailing_data":
        source.write_bytes(source.read_bytes() + b"x")
    else:
        raise AssertionError(mutation)

    target = tmp_path / "target.zp"
    with pytest.raises(MigrationError):
        migrate_v1_to_v2(source, target)
    assert target.exists() is False
    assert not list(tmp_path.glob(".*.tmp"))

