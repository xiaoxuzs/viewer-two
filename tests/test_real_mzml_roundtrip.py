from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import PipelineContext, PipelineRunner, PlanBuilder, SourceInspector, ZpReader, build_default_registry
from binary_layer.constants import BLOCK_NAMES
from binary_layer.mzml_schema import MzmlMetadataV1
from binary_layer.validator import ZpValidator
from conftest import rewrite_zp

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


@pytest.mark.parametrize(
    ("fixture_name", "expected_rt", "expected_dtype", "expected_compression"),
    [
        ("accept_ms1_only_indexed_float64_zlib.mzML", 30.0, "float64", "zlib"),
        ("accept_ms1_only_nonindexed_float32_uncompressed.mzML", 0.5, "float32", "none"),
    ],
)
def test_real_ms1_pipeline_writer_reader_and_validator_roundtrip(
    fixture_name: str,
    expected_rt: float,
    expected_dtype: str,
    expected_compression: str,
    tmp_path: Path,
) -> None:
    source = FIXTURE_DIR / fixture_name
    profile = SourceInspector().inspect([source])
    plan = PlanBuilder().build(profile)
    output = tmp_path / f"{source.stem}.zp"
    context = PipelineContext(profile, metadata={"output_path": output})

    PipelineRunner().run(plan, build_default_registry(), context)

    assert profile.source_type == "real_mzml"
    assert context.artifacts["output_zp_path"] == output
    assert output.exists()
    assert context.artifacts["validation_result"].valid is True
    reader = ZpReader(output)
    assert reader.read_header().version == 1
    assert tuple(item.block_name for item in reader.read_directory()) == BLOCK_NAMES
    assert len(reader.read_runs()) == 1
    assert reader.read_precursors() == []
    assert reader.read_chromatograms() == []
    assert len(reader.read_spectra()) == 2
    assert len(reader.read_arrays()) == 4
    extensions = reader.read_extensions()
    assert len(extensions) == 1
    metadata = MzmlMetadataV1.from_payload(extensions[0].payload)
    assert metadata.spectra[0].source_mz_dtype.value == expected_dtype
    assert metadata.spectra[0].source_mz_compression.value == expected_compression
    spectrum, mz_array, intensity_array = reader.read_spectrum_arrays("spectrum_000001")
    assert spectrum.scan_number == 1
    assert spectrum.rt == expected_rt
    assert not hasattr(spectrum, "mz_values")
    assert mz_array.dtype == intensity_array.dtype == "float64"
    assert mz_array.values == [100.0, 200.0]
    assert intensity_array.values == [10.0, 20.0]


@pytest.mark.parametrize(
    "fixture_name",
    [
        "accept_indexed_float64_zlib.mzML",
        "accept_nonindexed_float32_uncompressed.mzML",
    ],
)
def test_real_ms2_pipeline_roundtrip_preserves_precursor_links(fixture_name: str, tmp_path: Path) -> None:
    source = FIXTURE_DIR / fixture_name
    profile = SourceInspector().inspect([source])
    output = tmp_path / f"{source.stem}.zp"
    context = PipelineContext(profile, metadata={"output_path": output})

    PipelineRunner().run(PlanBuilder().build(profile), build_default_registry(), context)

    reader = ZpReader(output)
    spectra = reader.read_spectra()
    precursors = reader.read_precursors()
    assert [item.ms_level for item in spectra] == [1, 2]
    assert spectra[0].precursor_id is None
    assert spectra[1].precursor_id == "spectrum_000002:precursor"
    assert len(precursors) == 1
    assert precursors[0].precursor_id == spectra[1].precursor_id
    assert precursors[0].spectrum_id == spectra[1].spectrum_id
    assert (precursors[0].precursor_mz, precursors[0].charge, precursors[0].intensity) == (445.2, 2, 50.0)
    assert ZpValidator().validate(output).valid is True


def _build_real_ms2_zp(tmp_path: Path) -> Path:
    source = FIXTURE_DIR / "accept_indexed_float64_zlib.mzML"
    profile = SourceInspector().inspect([source])
    output = tmp_path / "real-ms2.zp"
    PipelineRunner().run(
        PlanBuilder().build(profile),
        build_default_registry(),
        PipelineContext(profile, metadata={"output_path": output}),
    )
    return output


def test_validator_rejects_missing_spectrum_to_precursor_reference(tmp_path: Path) -> None:
    output = _build_real_ms2_zp(tmp_path)

    def mutate(payloads):
        payloads["core_spectra"][1]["precursor_id"] = "missing_precursor"

    rewrite_zp(output, mutate)
    codes = {item.code for item in ZpValidator().validate(output).issues}
    assert "INVALID_REFERENCE" in codes
    assert "CHECKSUM_MISMATCH" not in codes


def test_validator_rejects_precursor_pointing_to_missing_child_spectrum(tmp_path: Path) -> None:
    output = _build_real_ms2_zp(tmp_path)

    def mutate(payloads):
        payloads["core_precursors"][0]["spectrum_id"] = "missing_spectrum"

    rewrite_zp(output, mutate)
    codes = {item.code for item in ZpValidator().validate(output).issues}
    assert "INVALID_REFERENCE" in codes
    assert "CHECKSUM_MISMATCH" not in codes

