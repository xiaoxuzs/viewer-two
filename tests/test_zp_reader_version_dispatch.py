from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer.constants import HEADER_SIZE, HEADER_STRUCT, ZP_ENDIANNESS_LITTLE, ZP_MAGIC
from binary_layer.exceptions import UnsupportedVersionError, ZpReadError
from binary_layer.reader import ZpReader
from zp_v2_reader_support import TrackingStream, build_complete_v2


def _header(version: int, *, magic: bytes = ZP_MAGIC, endianness: int = ZP_ENDIANNESS_LITTLE) -> bytes:
    return HEADER_STRUCT.pack(magic, version, endianness, 0, 0, HEADER_SIZE)


def test_v2_reader_dispatches_without_v1_directory_or_json_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "v2.zp"
    blocks = build_complete_v2(path)
    monkeypatch.setattr("binary_layer.reader.parse_json_bytes", lambda _raw: pytest.fail("v1 JSON parser called"))

    assert ZpReader(path).read_array(blocks.arrays[0].array_id) == blocks.arrays[0]


def test_unknown_reader_version_is_distinct(tmp_path: Path) -> None:
    path = tmp_path / "unknown.zp"
    path.write_bytes(_header(999))

    with pytest.raises(UnsupportedVersionError) as captured:
        ZpReader(path).read_header()

    assert (captured.value.code, captured.value.version, captured.value.operation) == (
        "UNSUPPORTED_ZP_VERSION", 999, "read"
    )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (_header(2, magic=b"BAD!"), "Invalid magic"),
        (_header(2, endianness=2), "Unsupported endianness"),
        (b"short", "shorter than the fixed 24-byte header"),
    ],
)
def test_header_errors_take_priority_over_v2_dispatch(tmp_path: Path, raw: bytes, message: str) -> None:
    path = tmp_path / "invalid.zp"
    path.write_bytes(raw)

    with pytest.raises(ZpReadError, match=message) as captured:
        ZpReader(path).read_header()

    assert not isinstance(captured.value, UnsupportedVersionError)


def test_v2_reader_reads_exactly_one_header_and_closes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "v2.zp"
    build_complete_v2(path)
    events: list[tuple[str, int, int]] = []
    original_open = Path.open
    opened: list[TrackingStream] = []

    def tracked_open(self: Path, *args, **kwargs):
        raw = original_open(self, *args, **kwargs)
        if self != path:
            return raw
        tracked = TrackingStream(raw, events)
        opened.append(tracked)
        return tracked

    monkeypatch.setattr(Path, "open", tracked_open)
    assert ZpReader(path).read_header().version == 2

    assert [(offset, length) for kind, offset, length in events if kind == "read"] == [(0, HEADER_SIZE)]
    assert opened[0]._stream.closed is True
