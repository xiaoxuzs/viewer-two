from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from zp_v2_validator_support import mutate_json_block, resize_array
from zp_v2_writer_support import build_real_blocks


CASES = (
    ("missing spectrum mz", "ms2", "INVALID_REFERENCE"),
    ("missing spectrum intensity", "ms2", "INVALID_REFERENCE"),
    ("wrong spectrum array type", "ms2", "ARRAY_TYPE_MISMATCH"),
    ("spectrum length mismatch", "ms2", "ARRAY_LENGTH_MISMATCH"),
    ("ms2 missing precursor", "ms2", "INVALID_REFERENCE"),
    ("precursor missing spectrum", "ms2", "INVALID_REFERENCE"),
    ("spectrum missing precursor", "ms2", "INVALID_REFERENCE"),
    ("precursor points to ms1", "ms2", "INVALID_REFERENCE"),
    ("missing chromatogram time", "tic", "INVALID_REFERENCE"),
    ("missing chromatogram intensity", "tic", "INVALID_REFERENCE"),
    ("wrong chromatogram array type", "tic", "ARRAY_TYPE_MISMATCH"),
    ("chromatogram length mismatch", "tic", "ARRAY_LENGTH_MISMATCH"),
    ("chromatogram missing run", "tic", "INVALID_REFERENCE"),
    ("index missing spectrum", "ms2", "INVALID_REFERENCE"),
    ("extension missing owner", "tic", "INVALID_REFERENCE"),
    ("global count mismatch", "ms2", "COUNT_MISMATCH"),
    ("run count mismatch", "ms2", "COUNT_MISMATCH"),
)


def _build(path: Path, kind: str) -> None:
    fixture = (
        "accept_ms2_precursor_metadata.mzML"
        if kind == "ms2"
        else "accept_tic_bpc_chromatograms.mzML"
    )
    ZpWriter().write(path, build_real_blocks(fixture), format_version=2)


@pytest.mark.parametrize(("name", "kind", "expected_code"), CASES, ids=[item[0] for item in CASES])
def test_cross_block_corruption_is_rejected(
    name: str,
    kind: str,
    expected_code: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "corrupted.zp"
    _build(path, kind)

    if name == "missing spectrum mz":
        mutate_json_block(path, "core_spectra", lambda value: value[0].update(mz_array_id="missing"))
    elif name == "missing spectrum intensity":
        mutate_json_block(path, "core_spectra", lambda value: value[0].update(intensity_array_id="missing"))
    elif name == "wrong spectrum array type":
        mutate_json_block(path, "core_spectra", lambda value: value[0].update(mz_array_id=value[0]["intensity_array_id"]))
    elif name == "spectrum length mismatch":
        resize_array(path, "spectrum_000001:intensity", 1)
    elif name == "ms2 missing precursor":
        mutate_json_block(path, "core_spectra", lambda value: value[1].update(precursor_id=None))
    elif name == "precursor missing spectrum":
        mutate_json_block(path, "core_precursors", lambda value: value[0].update(spectrum_id="missing"))
    elif name == "spectrum missing precursor":
        mutate_json_block(path, "core_spectra", lambda value: value[1].update(precursor_id="missing"))
    elif name == "precursor points to ms1":
        mutate_json_block(path, "core_precursors", lambda value: value[0].update(spectrum_id=value[0]["spectrum_id"].replace("000002", "000001")))
    elif name == "missing chromatogram time":
        mutate_json_block(path, "core_chromatograms", lambda value: value[0].update(time_array_id="missing"))
    elif name == "missing chromatogram intensity":
        mutate_json_block(path, "core_chromatograms", lambda value: value[0].update(intensity_array_id="missing"))
    elif name == "wrong chromatogram array type":
        mutate_json_block(path, "core_chromatograms", lambda value: value[0].update(time_array_id=value[0]["intensity_array_id"]))
    elif name == "chromatogram length mismatch":
        resize_array(path, "chromatogram_000001:intensity", 1)
    elif name == "chromatogram missing run":
        mutate_json_block(path, "core_chromatograms", lambda value: value[0].update(run_id="missing"))
    elif name == "index missing spectrum":
        mutate_json_block(path, "indexes", lambda value: value["scan_index"][0].update(spectrum_id="missing"))
    elif name == "extension missing owner":
        def missing_owner(value):
            extension = next(item for item in value if item["extension_type"] == "mzml_auxiliary_arrays")
            extension["payload"]["arrays"][0]["owner_id"] = "missing"
        mutate_json_block(path, "extensions", missing_owner)
    elif name == "global count mismatch":
        mutate_json_block(path, "global_meta", lambda value: value.update(array_count=value["array_count"] + 1))
    elif name == "run count mismatch":
        mutate_json_block(path, "core_runs", lambda value: value[0].update(spectrum_count=value[0]["spectrum_count"] + 1))

    result = ZpValidator().validate(path)

    assert result.valid is False, name
    assert expected_code in {item.code for item in result.issues}, (
        name,
        [item.code for item in result.issues],
    )


@pytest.mark.parametrize("mutation", ["missing", "unknown"])
def test_strict_core_schema_rejects_missing_and_unknown_fields(
    mutation: str, tmp_path: Path
) -> None:
    path = tmp_path / f"{mutation}.zp"
    _build(path, "ms2")

    def mutate(value):
        if mutation == "missing":
            del value[0]["native_id"]
        else:
            value[0]["unknown"] = True

    mutate_json_block(path, "core_spectra", mutate)
    result = ZpValidator().validate(path)
    assert "INVALID_BLOCK_SCHEMA" in {item.code for item in result.issues}


def test_global_meta_version_must_match_header(tmp_path: Path) -> None:
    path = tmp_path / "version-mismatch.zp"
    _build(path, "ms2")
    mutate_json_block(path, "global_meta", lambda value: value.update(format_version=1))
    result = ZpValidator().validate(path)
    assert "FORMAT_VERSION_MISMATCH" in {item.code for item in result.issues}

