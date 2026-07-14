from pathlib import Path

import pytest

from binary_layer.constants import HEADER_SIZE, HEADER_STRUCT, ZP_ENDIANNESS_LITTLE, ZP_MAGIC
from binary_layer.exceptions import ZpVersionNotImplementedError
from binary_layer.reader import ZpReader
from binary_layer.validator import ZpValidator
from binary_layer.writer import ZpWriter


def test_v2_writer_dispatch_precedes_validation_serialization_and_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ZpWriter, "_serialize_blocks", lambda *_args: pytest.fail("v1 serialization called"))
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("file opened"))
    monkeypatch.setattr("binary_layer.writer.os.replace", lambda *_args: pytest.fail("os.replace called"))

    with pytest.raises(ZpVersionNotImplementedError):
        ZpWriter().write(tmp_path / "v2.zp", object(), format_version=2)


def test_v2_reader_and_validator_do_not_enter_v1_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "v2.zp"
    path.write_bytes(HEADER_STRUCT.pack(ZP_MAGIC, 2, ZP_ENDIANNESS_LITTLE, 0, 0, HEADER_SIZE) + b"invalid body")
    monkeypatch.setattr("binary_layer.reader.parse_json_bytes", lambda *_args: pytest.fail("reader v1 JSON called"))
    monkeypatch.setattr("binary_layer.validator.parse_json_bytes", lambda *_args: pytest.fail("validator v1 JSON called"))

    with pytest.raises(ZpVersionNotImplementedError):
        ZpReader(path).read_directory()
    result = ZpValidator().validate(path)
    assert result.valid is False
    assert [issue.code for issue in result.issues] == ["ZP_V2_VALIDATION_NOT_IMPLEMENTED"]


def test_production_package_does_not_import_reference_codec() -> None:
    package = Path(__file__).parents[1] / "binary_layer"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.rglob("*.py"))
    assert "specs.zp_v2" not in source
    assert "arrays_reference_codec" not in source
