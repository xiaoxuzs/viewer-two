from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpValidator, migrate_v1_to_v2
from zp_compatibility_support import write_zp
from zp_v2_writer_support import build_real_blocks


@pytest.mark.parametrize(
    "fixture_name",
    [
        "accept_ms1_only_indexed_float64_zlib.mzML",
        "accept_ms2_precursor_metadata.mzML",
        "accept_tic_bpc_chromatograms.mzML",
    ],
)
def test_real_fixture_migration_matches_direct_v2_writer_bytes(
    fixture_name: str,
    tmp_path: Path,
) -> None:
    blocks = build_real_blocks(fixture_name)
    source = write_zp(tmp_path / "source.zp", blocks, 1)
    direct = write_zp(tmp_path / "direct.zp", blocks, 2)
    migrated = tmp_path / "migrated.zp"

    result = migrate_v1_to_v2(source, migrated)

    assert migrated.read_bytes() == direct.read_bytes()
    assert result.source_logical_fingerprint == result.target_logical_fingerprint
    assert result.arrays_scan_count == 1
    assert result.max_live_array_count == 1
    assert ZpValidator().validate(migrated).valid is True

