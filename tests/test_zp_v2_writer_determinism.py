from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpWriter


def test_fixed_time_and_blocks_produce_identical_v2_files(
    pipeline_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = pipeline_factory(".mzML").blocks
    monkeypatch.setattr("binary_layer.writer.time.time", lambda: 1_700_000_000.125)
    first = tmp_path / "first.zp"
    second = tmp_path / "second.zp"
    ZpWriter().write(first, blocks, format_version=2)
    blocks.arrays.reverse()
    ZpWriter().write(second, blocks, format_version=2)
    assert first.read_bytes() == second.read_bytes()
