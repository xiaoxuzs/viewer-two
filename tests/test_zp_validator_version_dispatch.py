from __future__ import annotations

import io
from pathlib import Path

import pytest

from binary_layer.constants import HEADER_SIZE, HEADER_STRUCT, ZP_ENDIANNESS_LITTLE, ZP_MAGIC
from binary_layer.validator import ZpValidator
from zp_v2_reader_support import build_complete_v2


def _header(version: int, *, magic: bytes = ZP_MAGIC, endianness: int = ZP_ENDIANNESS_LITTLE) -> bytes:
    return HEADER_STRUCT.pack(magic, version, endianness, 0, 0, HEADER_SIZE)


def test_v2_validator_dispatches_without_calling_v1_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "v2.zp"
    build_complete_v2(path)
    monkeypatch.setattr(ZpValidator, "_validate_schema", lambda *_args: pytest.fail("v1 schema validator called"))
    monkeypatch.setattr(ZpValidator, "_validate_references", lambda *_args: pytest.fail("v1 references validator called"))

    result = ZpValidator().validate(path)

    assert result.valid is True
    assert result.version == 2
    assert result.checked_blocks == 9
    assert result.issues == []


def test_unknown_validator_version_retains_unknown_version_issue(tmp_path: Path) -> None:
    path = tmp_path / "unknown.zp"
    path.write_bytes(_header(999))
    result = ZpValidator().validate(path)

    assert result.valid is False
    assert result.version == 999
    assert [issue.code for issue in result.issues] == ["UNSUPPORTED_VERSION"]
    assert result.checked_blocks == 0


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (_header(2, magic=b"BAD!"), "INVALID_MAGIC"),
        (_header(2, endianness=2), "UNSUPPORTED_ENDIANNESS"),
        (b"short", "FILE_TOO_SMALL"),
    ],
)
def test_validator_header_errors_take_priority_over_v2_dispatch(tmp_path: Path, raw: bytes, code: str) -> None:
    path = tmp_path / "invalid.zp"
    path.write_bytes(raw)
    result = ZpValidator().validate(path)

    assert result.valid is False
    assert [issue.code for issue in result.issues] == [code]
    assert "ZP_V2_VALIDATION_NOT_IMPLEMENTED" not in {issue.code for issue in result.issues}


def test_v2_validator_uses_one_open_stream_and_closes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "large-v2.zp"
    build_complete_v2(path)
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
    result = ZpValidator().validate(path)

    assert result.valid is True
    assert calls[0] == HEADER_SIZE
    assert len(calls) > 1
    assert stream.closed is True
