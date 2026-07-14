from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpReader
from binary_layer.exceptions import ZpV2ArrayReadError
from zp_v2_reader_support import TrackingStream, build_complete_v2, corrupt_array_payload, raw_layout


def test_cached_single_array_read_reads_exact_target_payload_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "random.zp"
    blocks = build_complete_v2(path)
    reader = ZpReader(path)
    target_id = blocks.spectra[0].mz_array_id
    layout = raw_layout(path)
    target = next(item for item in layout["internal"]["entries"] if item["array_id"] == target_id)
    payload_start = layout["arrays_entry"]["offset"] + layout["arrays_header"][7]
    all_payload_ranges = {
        item["array_id"]: (payload_start + item["data_offset"], item["byte_length"])
        for item in layout["internal"]["entries"]
    }
    events: list[tuple[str, int, int]] = []
    original_open = Path.open

    def tracked_open(self: Path, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        return TrackingStream(stream, events) if self == path else stream

    monkeypatch.setattr(Path, "open", tracked_open)
    reader.read_array(blocks.spectra[0].intensity_array_id)
    events.clear()
    result = reader.read_array(target_id)

    reads = [(offset, length) for kind, offset, length in events if kind == "read"]
    assert result.array_id == target_id
    assert (all_payload_ranges[target_id][0], target["byte_length"]) in reads
    assert sum(length for offset, length in reads if offset >= payload_start) == target["byte_length"]
    for other_id, other_range in all_payload_ranges.items():
        if other_id != target_id:
            assert other_range not in reads


def test_unrelated_corrupt_payload_does_not_break_target_random_read(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-other.zp"
    blocks = build_complete_v2(path)
    target_id = blocks.spectra[0].mz_array_id
    corrupt_id = blocks.spectra[1].intensity_array_id
    corrupt_array_payload(path, corrupt_id)
    reader = ZpReader(path)

    assert reader.read_array(target_id).array_id == target_id
    with pytest.raises(ZpV2ArrayReadError) as captured:
        reader.read_array(corrupt_id)
    assert captured.value.code == "ARRAY_CHECKSUM_MISMATCH"
    with pytest.raises(ZpV2ArrayReadError) as captured:
        reader.read_arrays()
    assert captured.value.code in {"BLOCK_CHECKSUM_MISMATCH", "ARRAY_CHECKSUM_MISMATCH"}
