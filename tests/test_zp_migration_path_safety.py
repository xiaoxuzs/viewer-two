from __future__ import annotations

import os
from pathlib import Path

import pytest

from binary_layer import migrate_v1_to_v2
from binary_layer.migration import MigrationError


FIXTURES = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


@pytest.mark.parametrize(
    ("source_name", "target_name"),
    [
        ("source.bin", "target.zp"),
        ("source.zp", "target.bin"),
    ],
)
def test_paths_must_use_exact_zp_extension(
    source_name: str,
    target_name: str,
    tmp_path: Path,
) -> None:
    source = tmp_path / source_name
    source.write_bytes((FIXTURES / "valid_minimal_v1.zp").read_bytes())
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(source, tmp_path / target_name)
    assert captured.value.code == "INVALID_EXTENSION"


def test_missing_destination_parent_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(
            FIXTURES / "valid_minimal_v1.zp",
            tmp_path / "missing" / "target.zp",
        )
    assert captured.value.code == "DESTINATION_PARENT_INVALID"


def test_directory_source_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "directory.zp"
    source.mkdir()
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(source, tmp_path / "target.zp")
    assert captured.value.code == "SOURCE_NOT_REGULAR_FILE"


def test_existing_hardlink_alias_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.zp"
    source.write_bytes((FIXTURES / "valid_minimal_v1.zp").read_bytes())
    target = tmp_path / "alias.zp"
    os.link(source, target)
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(source, target)
    assert captured.value.code == "SOURCE_DESTINATION_ALIAS"
    assert target.read_bytes() == source.read_bytes()


def test_source_symlink_is_rejected_when_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = tmp_path / "real.zp"
    real.write_bytes((FIXTURES / "valid_minimal_v1.zp").read_bytes())
    link = tmp_path / "link.zp"
    try:
        link.symlink_to(real)
    except OSError:
        link.write_bytes(real.read_bytes())
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda self: True if self == link else original_is_symlink(self),
        )
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(link, tmp_path / "target.zp")
    assert captured.value.code == "SOURCE_SYMLINK_NOT_ALLOWED"


def test_disk_budget_failure_happens_before_temp_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.zp"
    source.write_bytes((FIXTURES / "valid_minimal_v1.zp").read_bytes())
    target = tmp_path / "target.zp"
    monkeypatch.setattr(
        "binary_layer.migration.shutil.disk_usage",
        lambda _path: shutil_usage(10**9, 10**9, 1),
    )
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(source, target)
    assert captured.value.code == "INSUFFICIENT_DISK_SPACE"
    assert target.exists() is False
    assert list(tmp_path.glob(".*.tmp")) == []


def shutil_usage(total: int, used: int, free: int):
    return type("DiskUsage", (), {"total": total, "used": used, "free": free})()
