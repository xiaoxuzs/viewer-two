from __future__ import annotations

import os
from pathlib import Path

from binary_layer import ZpReader
from zp_v2_reader_support import build_complete_v2


def test_same_path_atomic_replacement_invalidates_directories_and_never_caches_values(tmp_path: Path) -> None:
    path = tmp_path / "replace.zp"
    replacement = tmp_path / "replacement.zp"
    blocks_a = build_complete_v2(path, intensity_shift=0.0)
    blocks_b = build_complete_v2(replacement, intensity_shift=100.0)
    array_id = "chromatogram_manual:intensity"
    reader = ZpReader(path)

    assert reader.read_array(array_id).values == [10.0, -2.5]
    os.replace(replacement, path)
    assert reader.read_array(array_id).values == [110.0, -2.5]
    assert blocks_a.arrays[-1].values != blocks_b.arrays[-1].values


def test_two_reader_instances_do_not_share_directory_or_payload_cache(tmp_path: Path) -> None:
    path = tmp_path / "independent.zp"
    build_complete_v2(path)
    first = ZpReader(path)
    second = ZpReader(path)
    assert first._v2_top_directory_cache is None
    assert second._v2_top_directory_cache is None
    first.read_array("chromatogram_manual:time")
    assert first._v2_top_directory_cache is not None
    assert second._v2_top_directory_cache is None
    assert not hasattr(first, "_array_payload_cache")
