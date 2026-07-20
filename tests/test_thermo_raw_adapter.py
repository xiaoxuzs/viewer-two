from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from binary_layer.conversion_exceptions import ThermoRawConversionError
from binary_layer.thermo_raw_adapter import (
    THERMO_RAW_CONVERSION_TIMEOUT,
    THERMO_RAW_CONVERTER_FAILED,
    THERMO_RAW_CONVERTER_NOT_FOUND,
    THERMO_RAW_OUTPUT_EMPTY,
    THERMO_RAW_OUTPUT_MISSING,
    THERMO_RAW_OUTPUT_NOT_INDEXED,
    THERMO_RAW_OUTPUT_NOT_MZML,
    THERMO_RAW_TEMP_CLEANUP_FAILED,
    ThermoRawAdapter,
    build_thermo_raw_command,
    discover_thermo_raw_file_parser,
)


def _indexed_mzml_bytes() -> bytes:
    prefix = b'<?xml version="1.0"?><indexedmzML><mzML></mzML>'
    offset = len(prefix)
    return (
        prefix
        + b'<indexList count="0"></indexList><indexListOffset>'
        + str(offset).encode("ascii")
        + b"</indexListOffset></indexedmzML>"
    )


def _converter(tmp_path: Path) -> Path:
    path = tmp_path / "ThermoRawFileParser.exe"
    path.write_bytes(b"fake executable")
    return path


def _run_stub(
    calls: list[tuple[list[str], dict[str, Any]]],
    output_factory: Callable[[Path], None] | None = None,
    *,
    returncode: int = 0,
) -> Callable[..., SimpleNamespace]:
    def run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((command, kwargs))
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="1.4.5\n", stderr="")
        if output_factory is not None:
            output_arg = next(item for item in command if item.startswith("-b="))
            output_factory(Path(output_arg[3:]))
        return SimpleNamespace(returncode=returncode, stdout="converted", stderr="failed" if returncode else "")

    return run


def test_command_is_argument_list_and_preserves_chinese_path(tmp_path: Path) -> None:
    executable = tmp_path / "工具" / "ThermoRawFileParser.exe"
    source = tmp_path / "中文 目录" / "sample_123.raw"
    output = tmp_path / "输出 目录" / "sample_123.mzML"

    command = build_thermo_raw_command(executable, source, output)

    assert command == [str(executable), f"-i={source}", f"-b={output}", "-f=2", "-m=2"]
    assert isinstance(command, list)


def test_discovery_uses_configured_path_and_reports_missing(tmp_path: Path) -> None:
    converter = _converter(tmp_path)
    assert discover_thermo_raw_file_parser(converter) == converter.resolve()

    with pytest.raises(ThermoRawConversionError) as captured:
        discover_thermo_raw_file_parser(tmp_path / "missing.exe")
    assert captured.value.code == THERMO_RAW_CONVERTER_NOT_FOUND


def test_adapter_uses_subprocess_list_shell_false_and_keeps_valid_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "中文 目录"
    root.mkdir()
    raw = root / "sample_123.raw"
    raw.write_bytes(b"raw")
    converter = _converter(root)
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(
        "binary_layer.thermo_raw_adapter.subprocess.run",
        _run_stub(calls, lambda path: path.write_bytes(_indexed_mzml_bytes())),
    )

    result = ThermoRawAdapter().convert(
        raw,
        converter_path=converter,
        temporary_directory=root / "临时 输出",
        timeout_seconds=10,
    )

    conversion_command, conversion_kwargs = calls[1]
    assert conversion_command[1] == f"-i={raw}"
    assert isinstance(conversion_command, list)
    assert conversion_kwargs["shell"] is False
    assert result.converter_version == "1.4.5"
    assert result.intermediate_indexed is True
    assert result.mzml_path.is_file()


@pytest.mark.parametrize(
    ("output_factory", "expected_code"),
    [
        (None, THERMO_RAW_OUTPUT_MISSING),
        (lambda path: path.write_bytes(b""), THERMO_RAW_OUTPUT_EMPTY),
        (lambda path: path.write_text("<html/>", encoding="utf-8"), THERMO_RAW_OUTPUT_NOT_MZML),
        (lambda path: path.write_text("<mzML/>", encoding="utf-8"), THERMO_RAW_OUTPUT_NOT_INDEXED),
    ],
)
def test_adapter_rejects_invalid_output_and_cleans_task_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    output_factory: Callable[[Path], None] | None,
    expected_code: str,
) -> None:
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"raw")
    converter = _converter(tmp_path)
    temporary = tmp_path / "temporary"
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(
        "binary_layer.thermo_raw_adapter.subprocess.run",
        _run_stub(calls, output_factory),
    )

    with pytest.raises(ThermoRawConversionError) as captured:
        ThermoRawAdapter().convert(
            raw,
            converter_path=converter,
            temporary_directory=temporary,
            timeout_seconds=10,
        )

    assert captured.value.code == expected_code
    assert list(temporary.iterdir()) == []


def test_adapter_reports_nonzero_exit_and_cleans_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"raw")
    converter = _converter(tmp_path)
    temporary = tmp_path / "temporary"
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(
        "binary_layer.thermo_raw_adapter.subprocess.run",
        _run_stub(calls, lambda path: path.write_bytes(b"incomplete"), returncode=7),
    )

    with pytest.raises(ThermoRawConversionError) as captured:
        ThermoRawAdapter().convert(raw, converter_path=converter, temporary_directory=temporary, timeout_seconds=10)

    assert captured.value.code == THERMO_RAW_CONVERTER_FAILED
    assert captured.value.details["exit_code"] == 7
    assert list(temporary.iterdir()) == []


def test_adapter_reports_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"raw")
    converter = _converter(tmp_path)

    def run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="1.4.5", stderr="")
        raise subprocess.TimeoutExpired(command, 1, output="partial", stderr="timeout")

    monkeypatch.setattr("binary_layer.thermo_raw_adapter.subprocess.run", run)

    with pytest.raises(ThermoRawConversionError) as captured:
        ThermoRawAdapter().convert(
            raw,
            converter_path=converter,
            temporary_directory=tmp_path / "temporary",
            timeout_seconds=1,
        )
    assert captured.value.code == THERMO_RAW_CONVERSION_TIMEOUT


def test_failed_output_cleanup_has_stable_error_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"raw")
    converter = _converter(tmp_path)
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(
        "binary_layer.thermo_raw_adapter.subprocess.run",
        _run_stub(calls, lambda path: path.write_text("<mzML/>", encoding="utf-8")),
    )
    monkeypatch.setattr(
        "binary_layer.thermo_raw_adapter.shutil.rmtree",
        lambda _path: (_ for _ in ()).throw(OSError("locked")),
    )

    with pytest.raises(ThermoRawConversionError) as captured:
        ThermoRawAdapter().convert(
            raw,
            converter_path=converter,
            temporary_directory=tmp_path / "temporary",
            timeout_seconds=10,
        )

    assert captured.value.code == THERMO_RAW_TEMP_CLEANUP_FAILED
    assert captured.value.details["original_error_code"] == THERMO_RAW_OUTPUT_NOT_INDEXED
