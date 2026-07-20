from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from binary_layer import ZpReader, ZpValidator, migrate_v1_to_v2


FIXTURES = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.mark.parametrize("kind", ["full", "minimal"])
def test_migration_is_byte_identical_to_frozen_v2_golden(
    kind: str,
    tmp_path: Path,
) -> None:
    source = FIXTURES / f"valid_{kind}_v1.zp"
    expected = FIXTURES / f"valid_{kind}_v2.zp"
    target = tmp_path / f"migrated-{kind}.zp"
    source_before = source.read_bytes()

    result = migrate_v1_to_v2(source, target)

    assert source.read_bytes() == source_before
    assert target.read_bytes() == expected.read_bytes()
    assert result.source_version == 1
    assert result.target_version == 2
    assert result.source_sha256 == _sha256(source)
    assert result.target_sha256 == _sha256(target)
    assert result.source_logical_fingerprint == result.target_logical_fingerprint
    assert result.arrays_scan_count == 1
    assert result.max_live_array_count == 1
    assert result.payload_spool_bytes == result.numeric_value_count * 8
    assert result.payload_copy_bytes == result.payload_spool_bytes
    validation = ZpValidator().validate(target)
    assert validation.valid is True
    assert validation.checked_blocks == 9
    assert validation.issues == []


def test_production_migration_never_calls_reader_read_arrays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(_self: ZpReader):
        raise AssertionError("migration must not materialize all arrays through ZpReader")

    monkeypatch.setattr(ZpReader, "read_arrays", forbidden)
    result = migrate_v1_to_v2(
        FIXTURES / "valid_full_v1.zp",
        tmp_path / "streamed.zp",
    )
    assert result.array_count == 6
    assert result.numeric_value_count == 22

