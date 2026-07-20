from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from binary_layer.conversion_exceptions import ThermoRawConversionError
from binary_layer.inspector import SourceInspector
from binary_layer.models import ConversionOptions, PipelineContext, SourceProfile
from binary_layer.thermo_raw_adapter import (
    CONVERTER_NAME,
    THERMO_RAW_TEMP_CLEANUP_FAILED,
    ThermoRawAdapterResult,
)
from binary_layer.thermo_raw_schema import ThermoRawConversionMetadataV1
from binary_layer.tools.common import IndexBuildTool, StringPoolBuildTool
from binary_layer.tools.real_mzml import RealMzmlParseTool
from binary_layer.tools.real_thermo_raw import (
    THERMO_RAW_DOWNSTREAM_MZML_REJECTED,
    RealThermoRawParseTool,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


class StubAdapter:
    def __init__(self, result: ThermoRawAdapterResult, *, cleanup_fails: bool = False) -> None:
        self.result = result
        self.cleanup_fails = cleanup_fails
        self.cleanup_calls = 0

    def convert(self, *_args, **_kwargs) -> ThermoRawAdapterResult:
        return self.result

    def cleanup_intermediate(self, _result: ThermoRawAdapterResult) -> str:
        self.cleanup_calls += 1
        if self.cleanup_fails:
            raise ThermoRawConversionError(THERMO_RAW_TEMP_CLEANUP_FAILED, "locked")
        return "removed"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _adapter_result(tmp_path: Path, fixture_name: str) -> ThermoRawAdapterResult:
    mzml_path = FIXTURE_DIR / fixture_name
    return ThermoRawAdapterResult(
        converter_path=tmp_path / "ThermoRawFileParser.exe",
        converter_name=CONVERTER_NAME,
        converter_version="1.4.5",
        command=("ThermoRawFileParser.exe", "-f=2"),
        exit_code=0,
        stdout="ok",
        stderr="",
        mzml_path=mzml_path,
        work_directory=tmp_path / "work",
        raw_to_mzml_seconds=0.25,
        intermediate_file_size=mzml_path.stat().st_size,
        intermediate_sha256=_sha256(mzml_path),
    )


def _raw_context(raw: Path, *, keep_intermediate: bool = False) -> PipelineContext:
    return PipelineContext(
        SourceInspector().inspect((raw,)),
        metadata={
            "input_sha256": _sha256(raw),
            "file_validated": True,
            "conversion_options": ConversionOptions(keep_intermediate=keep_intermediate),
        },
    )


def test_real_thermo_tool_reuses_real_mzml_and_adds_versioned_provenance(tmp_path: Path) -> None:
    raw = tmp_path / "中文_sample_123.raw"
    raw.write_bytes(b"raw source")
    adapter = StubAdapter(_adapter_result(tmp_path, "accept_indexed_float64_zlib.mzML"))
    context = _raw_context(raw)
    tool = RealThermoRawParseTool(adapter)  # type: ignore[arg-type]

    tool.run(context)

    assert len(context.blocks.spectra) == 2
    assert len(context.blocks.precursors) == 1
    assert adapter.cleanup_calls == 1
    assert tool.last_report is not None
    assert tool.last_report.cleanup_result == "removed"
    provenance = context.blocks.extensions[-1]
    metadata = ThermoRawConversionMetadataV1.from_payload(provenance.payload)
    assert metadata.source_kind == "thermo_raw"
    assert metadata.source_file_name == raw.name
    assert metadata.source_sha256 == _sha256(raw)
    assert metadata.intermediate_sha256 == adapter.result.intermediate_sha256
    assert context.blocks.runs[0].source_file == adapter.result.mzml_path.name
    assert str(tmp_path) not in str(provenance.payload)


def test_keep_intermediate_skips_cleanup(tmp_path: Path) -> None:
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"raw source")
    adapter = StubAdapter(_adapter_result(tmp_path, "accept_ms1_only_indexed_float64_zlib.mzML"))
    tool = RealThermoRawParseTool(adapter)  # type: ignore[arg-type]

    tool.run(_raw_context(raw, keep_intermediate=True))

    assert adapter.cleanup_calls == 0
    assert tool.last_report is not None
    assert tool.last_report.intermediate_retained is True
    assert tool.last_report.cleanup_result == "retained"


def test_downstream_admission_rejection_preserves_issue_codes_and_cleans(tmp_path: Path) -> None:
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"raw source")
    adapter = StubAdapter(_adapter_result(tmp_path, "reject_missing_charge.mzML"))
    tool = RealThermoRawParseTool(adapter)  # type: ignore[arg-type]

    with pytest.raises(ThermoRawConversionError) as captured:
        tool.run(_raw_context(raw))

    assert captured.value.code == THERMO_RAW_DOWNSTREAM_MZML_REJECTED
    assert "MISSING_PRECURSOR_CHARGE" in captured.value.details["admission_issue_codes"]
    assert adapter.cleanup_calls == 1


def test_downstream_failure_cleanup_error_is_stable(tmp_path: Path) -> None:
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"raw source")
    adapter = StubAdapter(_adapter_result(tmp_path, "reject_missing_charge.mzML"), cleanup_fails=True)
    tool = RealThermoRawParseTool(adapter)  # type: ignore[arg-type]

    with pytest.raises(ThermoRawConversionError) as captured:
        tool.run(_raw_context(raw))

    assert captured.value.code == THERMO_RAW_TEMP_CLEANUP_FAILED
    assert captured.value.details["original_error_code"] == THERMO_RAW_DOWNSTREAM_MZML_REJECTED


def test_raw_and_direct_mzml_core_blocks_are_logically_equal(tmp_path: Path) -> None:
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"raw source")
    adapter = StubAdapter(_adapter_result(tmp_path, "accept_indexed_float64_zlib.mzML"))
    raw_context = _raw_context(raw, keep_intermediate=True)
    raw_tool = RealThermoRawParseTool(adapter)  # type: ignore[arg-type]
    raw_tool.run(raw_context)
    assert raw_tool.last_report is not None

    mzml_path = adapter.result.mzml_path
    direct_profile = SourceProfile(
        "real_mzml",
        (mzml_path,),
        1,
        True,
        False,
        False,
        False,
        False,
        path=mzml_path,
        suffix=mzml_path.suffix,
        file_size=mzml_path.stat().st_size,
    )
    direct_context = PipelineContext(
        direct_profile,
        metadata={
            "input_sha256": adapter.result.intermediate_sha256,
            "file_validated": True,
            "block_created_at": raw_tool.last_report.block_created_at,
            "source_file_label": mzml_path.name,
        },
    )
    RealMzmlParseTool().run(direct_context)
    for context in (raw_context, direct_context):
        StringPoolBuildTool().run(context)
        IndexBuildTool().run(context)

    assert raw_context.blocks.global_meta == direct_context.blocks.global_meta
    assert raw_context.blocks.string_pool == direct_context.blocks.string_pool
    assert raw_context.blocks.runs == direct_context.blocks.runs
    assert raw_context.blocks.spectra == direct_context.blocks.spectra
    assert raw_context.blocks.precursors == direct_context.blocks.precursors
    assert raw_context.blocks.chromatograms == direct_context.blocks.chromatograms
    assert raw_context.blocks.arrays == direct_context.blocks.arrays
    assert raw_context.blocks.indexes == direct_context.blocks.indexes
    assert raw_context.blocks.extensions[:-1] == direct_context.blocks.extensions
