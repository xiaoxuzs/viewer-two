from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import migrate_v1_to_v2
from binary_layer.migration import MigrationError


FIXTURES = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


@pytest.mark.parametrize(
    ("source_name", "expected_code"),
    [
        ("valid_full_v2.zp", "SOURCE_VERSION_NOT_V1"),
        ("valid_minimal_v2.zp", "SOURCE_VERSION_NOT_V1"),
    ],
)
def test_only_v1_sources_are_accepted(
    source_name: str,
    expected_code: str,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.zp"
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(FIXTURES / source_name, target)
    assert captured.value.code == expected_code
    assert target.exists() is False


def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "existing.zp"
    target.write_bytes(b"keep me")
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(FIXTURES / "valid_full_v1.zp", target)
    assert captured.value.code == "DESTINATION_EXISTS"
    assert target.read_bytes() == b"keep me"


def test_same_path_is_rejected_without_touching_source(tmp_path: Path) -> None:
    source = tmp_path / "same.zp"
    source.write_bytes((FIXTURES / "valid_full_v1.zp").read_bytes())
    before = source.read_bytes()
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(source, source)
    assert captured.value.code == "SOURCE_DESTINATION_ALIAS"
    assert source.read_bytes() == before


def test_invalid_source_leaves_no_target_or_temporary_files(tmp_path: Path) -> None:
    source = tmp_path / "invalid.zp"
    source.write_bytes(b"not a zp")
    target = tmp_path / "target.zp"
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(source, target)
    assert captured.value.code == "SOURCE_VALIDATION_FAILED"
    assert target.exists() is False
    assert list(tmp_path.glob(".*.migrating-*.tmp")) == []
    assert list(tmp_path.glob(".*.payload-*.tmp")) == []


@pytest.mark.parametrize(
    "fault_stage",
    [
        "after_source_validation",
        "after_temp_create",
        "after_global_meta",
        "before_arrays_scan",
        "after_arrays_scan",
        "after_arrays_write",
        "after_top_directory",
        "after_temp_fsync",
        "after_target_validation",
        "after_fingerprint",
        "before_commit",
    ],
)
def test_injected_failures_are_atomic(
    fault_stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.zp"
    source.write_bytes((FIXTURES / "valid_full_v1.zp").read_bytes())
    source_before = source.read_bytes()
    target = tmp_path / "target.zp"

    def fail(stage: str) -> None:
        if stage == fault_stage:
            raise OSError(f"injected {stage}")

    monkeypatch.setattr("binary_layer.migration._fault_point", fail)
    with pytest.raises(MigrationError):
        migrate_v1_to_v2(source, target)

    assert source.read_bytes() == source_before
    assert target.exists() is False
    assert list(tmp_path.glob(".*.migrating-*.tmp")) == []
    assert list(tmp_path.glob(".*.payload-*.tmp")) == []

