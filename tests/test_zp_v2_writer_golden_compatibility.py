from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from binary_layer import (
    ArrayBlock,
    BlockCollection,
    GlobalMetaBlock,
    IndexBlock,
    StringPoolBlock,
    ZpWriter,
)
from zp_v2_writer_support import parse_v2_file


FIXTURE = Path(__file__).parents[1] / "specs" / "zp_v2" / "fixtures" / "valid_arrays_v2.bin"


def test_arrays_region_extracted_from_complete_v2_file_matches_golden(tmp_path: Path) -> None:
    arrays = [
        ArrayBlock("spectrum_000001:mz", "mz", "float64", [0.0, 100.125, 2500.75]),
        ArrayBlock("chromatogram_000001:time", "time", "float64", [0.0, 0.125, 12.75]),
        ArrayBlock("spectrum_000001:intensity", "intensity", "float64", [0.0, -2.5, 1500.25]),
    ]
    blocks = BlockCollection(
        global_meta=GlobalMetaBlock(
            format_version=1,
            source_type="fixture",
            source_file_name="fixture.mzML",
            source_file_hash="0" * 64,
            run_count=0,
            spectrum_count=0,
            chromatogram_count=0,
            array_count=3,
            created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
            generator_name="test",
            generator_version="1",
        ),
        arrays=arrays,
        string_pool=StringPoolBlock([]),
        indexes=IndexBlock([], [], []),
    )
    target = tmp_path / "golden-container.zp"
    ZpWriter().write(target, blocks, format_version=2)
    assert parse_v2_file(target)["payloads"]["arrays"] == FIXTURE.read_bytes()
