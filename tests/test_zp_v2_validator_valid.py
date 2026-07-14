from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpValidator, ZpWriter
from binary_layer.constants import (
    DEFAULT_ZP_WRITE_VERSION,
    SUPPORTED_ZP_VALIDATE_VERSIONS,
    ZP_VERSION,
)
from zp_v2_writer_support import build_real_blocks


@pytest.mark.parametrize(
    "fixture_name",
    [
        "accept_ms1_only_nonindexed_float32_uncompressed.mzML",
        "accept_ms2_precursor_metadata.mzML",
        "accept_tic_bpc_chromatograms.mzML",
    ],
)
def test_complete_v2_files_validate_all_nine_blocks(
    fixture_name: str, tmp_path: Path
) -> None:
    path = tmp_path / f"{Path(fixture_name).stem}.zp"
    ZpWriter().write(path, build_real_blocks(fixture_name), format_version=2)

    result = ZpValidator().validate(path)

    assert result.valid is True
    assert result.version == 2
    assert result.issues == []
    assert result.checked_blocks == 9


def test_b8_4_version_constants_preserve_v1_defaults() -> None:
    assert ZP_VERSION == 1
    assert DEFAULT_ZP_WRITE_VERSION == 1
    assert SUPPORTED_ZP_VALIDATE_VERSIONS == frozenset({1, 2})
