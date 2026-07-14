from __future__ import annotations

import io
from pathlib import Path

import pytest

from binary_layer.constants import HEADER_SIZE, HEADER_STRUCT, ZP_ENDIANNESS_LITTLE, ZP_MAGIC
from binary_layer.exceptions import UnsupportedVersionError, ZpReadError, ZpVersionNotImplementedError
from binary_layer.reader import ZpReader


def _header(version: int, *, magic: bytes = ZP_MAGIC, endianness: int = ZP_ENDIANNESS_LITTLE) -> bytes:
    return HEADER_STRUCT.pack(magic, version, endianness, 0, 0, HEADER_SIZE)


def test_v2_reader_fails_before_v1_directory_or_json_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "v2.zp"
    path.write_bytes(_header(2) + b"not a v1 directory" * 100)
    monkeypatch.setattr("binary_layer.reader.parse_json_bytes", lambda _raw: pytest.fail("v1 JSON parser called"))

    with pytest.raises(ZpVersionNotImplementedError) as captured:
        ZpReader(path).read_arrays()

    assert (captured.value.code, captured.value.version, captured.value.operation) == (
        "ZP_V2_READ_NOT_IMPLEMENTED", 2, "read"
    )


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

    assert not isinstance(captured.value, ZpVersionNotImplementedError)


def test_v2_reader_reads_exactly_one_header_and_closes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "large-v2.zp"
    path.write_bytes(_header(2) + b"x" * 1_000_000)
    calls: list[int] = []

    class TrackingStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            calls.append(size)
            return super().read(size)

    stream = TrackingStream(path.read_bytes())
    original_open = Path.open

    def tracked_open(self: Path, *args, **kwargs):
        return stream if self == path else original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)
    with pytest.raises(ZpVersionNotImplementedError):
        ZpReader(path).read_header()

    assert calls == [HEADER_SIZE]
    assert stream.closed is True
