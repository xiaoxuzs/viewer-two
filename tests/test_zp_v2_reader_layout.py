from __future__ import annotations

from pathlib import Path

from binary_layer import ZpReader
from zp_v2_reader_support import build_complete_v2, raw_layout


def test_v2_reader_directory_metadata_matches_independent_literal_layout(tmp_path: Path) -> None:
    path = tmp_path / "layout.zp"
    build_complete_v2(path)
    literal = raw_layout(path)
    reader = ZpReader(path)

    assert reader.read_header().directory_offset == literal["header"][-1]
    assert [entry.__dict__ if hasattr(entry, "__dict__") else (
        entry.block_name, entry.offset, entry.length, entry.encoding, entry.checksum
    ) for entry in reader.read_directory()] == [
        (item["block_name"], item["offset"], item["length"], item["encoding"], item["checksum"])
        for item in literal["directory"]
    ]

