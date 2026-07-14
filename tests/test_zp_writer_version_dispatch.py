from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from binary_layer.constants import BLOCK_NAMES, ZP_VERSION_V1, ZP_VERSION_V2
from binary_layer.exceptions import UnsupportedVersionError, ZpVersionNotImplementedError
from conftest import load_raw_zp


def test_default_and_explicit_v1_writes_are_byte_identical(
    pipeline_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = pipeline_factory(".mzML")
    monkeypatch.setattr("binary_layer.writer.time.time", lambda: 1_700_000_000.125)
    default_path = tmp_path / "default.zp"
    explicit_path = tmp_path / "explicit.zp"

    ZpWriter().write(default_path, context.blocks)
    ZpWriter().write(explicit_path, context.blocks, format_version=ZP_VERSION_V1)

    assert default_path.read_bytes() == explicit_path.read_bytes()
    header, directory, _payloads = load_raw_zp(default_path)
    assert header[1] == ZP_VERSION_V1
    assert [entry["block_name"] for entry in directory] == list(BLOCK_NAMES)
    assert next(entry for entry in directory if entry["block_name"] == "arrays")["encoding"] == "json"
    assert ZpValidator().validate(default_path).valid is True


@pytest.mark.parametrize(
    ("version", "exception_type", "code"),
    [
        (ZP_VERSION_V2, ZpVersionNotImplementedError, "ZP_V2_WRITE_NOT_IMPLEMENTED"),
        (999, UnsupportedVersionError, "UNSUPPORTED_ZP_VERSION"),
    ],
)
def test_unavailable_write_versions_fail_before_creating_files(
    pipeline_factory, tmp_path: Path, version: int, exception_type: type[Exception], code: str
) -> None:
    context = pipeline_factory(".mzML")
    target = tmp_path / f"version-{version}" / "output.zp"

    with pytest.raises(exception_type) as captured:
        ZpWriter().write(target, context.blocks, format_version=version)

    assert captured.value.code == code
    assert captured.value.version == version
    assert captured.value.operation == "write"
    assert not target.exists()
    assert not target.with_name(target.name + ".tmp").exists()
    assert not target.parent.exists()


def test_v2_write_does_not_overwrite_existing_target(pipeline_factory, tmp_path: Path) -> None:
    context = pipeline_factory(".mzML")
    target = tmp_path / "existing.zp"
    original = b"existing bytes"
    target.write_bytes(original)

    with pytest.raises(ZpVersionNotImplementedError):
        ZpWriter().write(target, context.blocks, format_version=ZP_VERSION_V2)

    assert target.read_bytes() == original
    assert not target.with_name(target.name + ".tmp").exists()
