from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from binary_layer.constants import DEFAULT_ZP_WRITE_VERSION
from binary_layer.conversion_exceptions import SourceConversionError
from binary_layer.models import ConversionOptions
from binary_layer.service import convert_source_to_zp, inspect_source, open_zp, validate_zp
from binary_layer.thermo_raw_schema import THERMO_RAW_CONVERSION_EXTENSION_TYPE

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


def _install_fake_converter_run(monkeypatch: pytest.MonkeyPatch, fixture: Path) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append({"command": command, **kwargs})
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="1.4.5\n", stderr="")
        output = Path(next(item[3:] for item in command if item.startswith("-b=")))
        shutil.copyfile(fixture, output)
        return SimpleNamespace(returncode=0, stdout="converted", stderr="")

    monkeypatch.setattr("binary_layer.thermo_raw_adapter.subprocess.run", run)
    return calls


def test_service_converts_fake_raw_through_real_mzml_writer_and_validator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = tmp_path / "中文 数据" / "sample_123.raw"
    raw.parent.mkdir()
    raw.write_bytes(b"fake thermo raw")
    converter = tmp_path / "工具" / "ThermoRawFileParser.exe"
    converter.parent.mkdir()
    converter.write_bytes(b"fake")
    target = tmp_path / "output" / "sample_123.v2.zp"
    intermediate = tmp_path / "intermediate"
    calls = _install_fake_converter_run(monkeypatch, FIXTURE_DIR / "accept_indexed_float64_zlib.mzML")

    result = convert_source_to_zp(
        raw,
        target,
        format_version=2,
        options=ConversionOptions(
            converter_path=converter,
            temporary_directory=intermediate,
            keep_intermediate=False,
            timeout_seconds=10,
        ),
    )

    assert result.source_profile.source_type == "real_thermo_raw"
    assert "real_thermo_raw_parse" in result.plan.required_steps
    assert result.format_version == 2
    assert result.validation.valid is True
    assert result.validation.checked_blocks == 9
    assert result.validation.issues == []
    assert result.source_before == result.source_after
    assert result.cleanup_result == "removed"
    assert list(intermediate.iterdir()) == []
    assert calls[1]["shell"] is False
    assert isinstance(calls[1]["command"], list)
    assert not list(target.parent.glob(".*.partial.zp"))
    reader = open_zp(target)
    assert reader.read_header().version == 2
    assert len(reader.read_spectra()) == 2
    assert any(item.extension_type == THERMO_RAW_CONVERSION_EXTENSION_TYPE for item in reader.read_extensions())
    assert validate_zp(target).valid is True


def test_service_keep_intermediate_returns_existing_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"fake thermo raw")
    converter = tmp_path / "ThermoRawFileParser.exe"
    converter.write_bytes(b"fake")
    intermediate = tmp_path / "intermediate"
    _install_fake_converter_run(monkeypatch, FIXTURE_DIR / "accept_ms1_only_indexed_float64_zlib.mzML")

    result = convert_source_to_zp(
        raw,
        tmp_path / "output.zp",
        format_version=2,
        options=ConversionOptions(
            converter_path=converter,
            temporary_directory=intermediate,
            keep_intermediate=True,
            timeout_seconds=10,
        ),
    )

    assert result.cleanup_result == "retained"
    assert result.intermediate_path is not None
    assert result.intermediate_path.is_file()


def test_service_rejects_existing_target_before_converter_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"raw")
    target = tmp_path / "existing.zp"
    target.write_bytes(b"existing")
    calls: list[object] = []
    monkeypatch.setattr(
        "binary_layer.thermo_raw_adapter.subprocess.run",
        lambda *_args, **_kwargs: calls.append(object()),
    )

    with pytest.raises(SourceConversionError) as captured:
        convert_source_to_zp(raw, target)

    assert captured.value.code == "TARGET_ALREADY_EXISTS"
    assert target.read_bytes() == b"existing"
    assert calls == []


def test_service_default_format_remains_v1(tmp_path: Path) -> None:
    source = FIXTURE_DIR / "accept_ms1_only_indexed_float64_zlib.mzML"
    target = tmp_path / "default.zp"

    result = convert_source_to_zp(source, target)

    assert DEFAULT_ZP_WRITE_VERSION == 1
    assert result.format_version == 1
    assert open_zp(target).read_header().version == 1


def test_public_inspection_records_real_raw_file_facts(tmp_path: Path) -> None:
    source = tmp_path / "sample.RAW"
    source.write_bytes(b"1234")

    profile = inspect_source(source)

    assert profile.source_type == "real_thermo_raw"
    assert profile.path == source
    assert profile.suffix == ".RAW"
    assert profile.file_size == 4
