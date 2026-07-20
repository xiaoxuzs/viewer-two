from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from binary_layer import ZpValidator
from specs.zp_full.inspect_full_zp import InspectionError, inspect_full_zp
from zp_compatibility_support import (
    mutate_header,
    mutate_json_blocks,
    mutate_top_directory,
    mutate_v2_array_directory,
    mutate_v2_arrays_header,
)


FIXTURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


def _global_count(path: Path) -> None:
    mutate_json_blocks(path, lambda blocks: blocks["global_meta"].update(run_count=2))


def _run_count(path: Path) -> None:
    mutate_json_blocks(path, lambda blocks: blocks["core_runs"][0].update(spectrum_count=1))


def _string_pool(path: Path) -> None:
    mutate_json_blocks(path, lambda blocks: blocks["string_pool"]["strings"].remove("full-run"))


def _spectrum_reference(path: Path) -> None:
    mutate_json_blocks(path, lambda blocks: blocks["core_spectra"][0].update(mz_array_id="missing:mz"))


def _precursor_relationship(path: Path) -> None:
    mutate_json_blocks(path, lambda blocks: blocks["core_precursors"][0].update(spectrum_id="spectrum_000001"))


def _chromatogram_reference(path: Path) -> None:
    mutate_json_blocks(path, lambda blocks: blocks["core_chromatograms"][0].update(time_array_id="missing:time"))


def _index_reference(path: Path) -> None:
    mutate_json_blocks(path, lambda blocks: blocks["indexes"]["scan_index"][0].update(spectrum_id="missing"))


def _extension_owner(path: Path) -> None:
    mutate_json_blocks(
        path,
        lambda blocks: blocks["extensions"][1]["payload"]["arrays"][0].update(owner_id="missing"),
    )


DOMAIN_CORRUPTIONS = (
    ("global_meta_count", _global_count, "COUNT_MISMATCH"),
    ("run_statistics", _run_count, "COUNT_MISMATCH"),
    ("string_pool", _string_pool, "INVALID_REFERENCE"),
    ("spectrum_reference", _spectrum_reference, "INVALID_REFERENCE"),
    ("precursor_relationship", _precursor_relationship, "INVALID_REFERENCE"),
    ("chromatogram_reference", _chromatogram_reference, "INVALID_REFERENCE"),
    ("index_reference", _index_reference, "INVALID_REFERENCE"),
    ("extension_owner", _extension_owner, "INVALID_REFERENCE"),
)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize(("_name", "corrupt", "expected"), DOMAIN_CORRUPTIONS, ids=[item[0] for item in DOMAIN_CORRUPTIONS])
def test_domain_corruption_is_built_from_read_only_golden_copy(
    version: int,
    _name: str,
    corrupt,
    expected: str,
    tmp_path: Path,
) -> None:
    source = FIXTURE_DIR / f"valid_full_v{version}.zp"
    before = source.read_bytes()
    target = tmp_path / f"domain-v{version}.zp"
    shutil.copyfile(source, target)
    corrupt(target)

    result = ZpValidator().validate(target)
    assert result.valid is False
    assert expected in [issue.code for issue in result.issues]
    assert result.checked_blocks == 9
    with pytest.raises(InspectionError):
        inspect_full_zp(target)
    assert source.read_bytes() == before


def _wrong_arrays_encoding(path: Path, value: str) -> None:
    mutate_top_directory(
        path,
        lambda directory: next(item for item in directory if item["block_name"] == "arrays").update(
            encoding=value
        ),
    )


def _wrong_arrays_top_checksum(path: Path) -> None:
    mutate_top_directory(
        path,
        lambda directory: next(item for item in directory if item["block_name"] == "arrays").update(
            checksum="0" * 64
        ),
    )


V1_PHYSICAL = (
    ("header_version", lambda path: mutate_header(path, lambda header: header.__setitem__(1, 999))),
    ("arrays_encoding", lambda path: _wrong_arrays_encoding(path, "zp-arrays-v2")),
    ("arrays_checksum", _wrong_arrays_top_checksum),
)


@pytest.mark.parametrize(("_name", "corrupt"), V1_PHYSICAL, ids=[item[0] for item in V1_PHYSICAL])
def test_v1_physical_corruption_matrix(_name: str, corrupt, tmp_path: Path) -> None:
    target = tmp_path / "v1.zp"
    shutil.copyfile(FIXTURE_DIR / "valid_full_v1.zp", target)
    corrupt(target)

    result = ZpValidator().validate(target)
    assert result.valid is False
    assert result.issues
    with pytest.raises(InspectionError):
        inspect_full_zp(target)


def _arrays_header_magic(path: Path) -> None:
    mutate_v2_arrays_header(path, lambda header: header.__setitem__(0, b"BADMAGIC"))


def _internal_directory(path: Path) -> None:
    mutate_v2_array_directory(path, lambda directory, _payload: directory["entries"][0].update(data_offset=8))


def _per_array_checksum(path: Path) -> None:
    mutate_v2_array_directory(path, lambda directory, _payload: directory["entries"][0].update(checksum="0" * 64))


V2_PHYSICAL = (
    ("header_version", lambda path: mutate_header(path, lambda header: header.__setitem__(1, 999))),
    ("arrays_encoding", lambda path: _wrong_arrays_encoding(path, "json")),
    ("arrays_header", _arrays_header_magic),
    ("internal_directory", _internal_directory),
    ("top_arrays_checksum", _wrong_arrays_top_checksum),
    ("per_array_checksum", _per_array_checksum),
)


@pytest.mark.parametrize(("_name", "corrupt"), V2_PHYSICAL, ids=[item[0] for item in V2_PHYSICAL])
def test_v2_physical_corruption_matrix(_name: str, corrupt, tmp_path: Path) -> None:
    target = tmp_path / "v2.zp"
    shutil.copyfile(FIXTURE_DIR / "valid_full_v2.zp", target)
    corrupt(target)

    result = ZpValidator().validate(target)
    assert result.valid is False
    assert result.issues
    with pytest.raises(InspectionError):
        inspect_full_zp(target)
