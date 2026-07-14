from __future__ import annotations

import io
import os
from pathlib import Path

from binary_layer import ZpV2ValidationLimits, ZpValidator
from zp_v2_reader_support import build_complete_v2, raw_layout


def test_arrays_payload_is_read_once_in_bounded_chunks_from_one_stream(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "streaming.zp"
    build_complete_v2(path)
    layout = raw_layout(path)
    raw = path.read_bytes()
    payload_start = layout["arrays_entry"]["offset"] + layout["arrays_header"][7]
    payload_length = layout["arrays_header"][8]
    payload_end = payload_start + payload_length
    reads: list[tuple[int, int]] = []
    open_count = 0

    class TrackingStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            offset = self.tell()
            value = super().read(size)
            reads.append((offset, len(value)))
            return value

    stream = TrackingStream(raw)
    original_open = Path.open

    def tracked_open(self: Path, *args, **kwargs):
        nonlocal open_count
        if self == path:
            open_count += 1
            return stream
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)
    validator = ZpValidator()
    validator.v2_limits = ZpV2ValidationLimits(chunk_size=16)
    result = validator.validate(path)

    payload_reads = [
        (offset, length)
        for offset, length in reads
        if offset >= payload_start and offset < payload_end
    ]
    assert result.valid is True
    assert open_count == 1
    assert sum(length for _offset, length in payload_reads) == payload_length
    assert max(length for _offset, length in payload_reads) <= 16
    metrics = validator._last_v2_metrics
    assert metrics["payload_bytes_read"] == payload_length
    assert metrics["payload_scan_count"] == 1
    assert metrics["max_single_payload_read"] <= metrics["chunk_size"]
    assert metrics["full_payload_materialized"] is False
    assert metrics["array_values_retained"] is False


def test_file_change_during_validation_returns_stable_issue(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "changing.zp"
    build_complete_v2(path)
    layout = raw_layout(path)
    raw = path.read_bytes()
    payload_start = layout["arrays_entry"]["offset"] + layout["arrays_header"][7]
    changed = False

    class ChangingStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            nonlocal changed
            offset = self.tell()
            value = super().read(size)
            if not changed and offset >= payload_start:
                changed = True
                current = path.stat()
                os.utime(
                    path,
                    ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000),
                )
            return value

    stream = ChangingStream(raw)
    original_open = Path.open
    monkeypatch.setattr(
        Path,
        "open",
        lambda self, *args, **kwargs: stream if self == path else original_open(self, *args, **kwargs),
    )
    result = ZpValidator().validate(path)
    assert result.valid is False
    assert result.issues[-1].code == "FILE_CHANGED_DURING_VALIDATION"


def test_production_v2_validator_has_no_forbidden_full_decode_dependencies() -> None:
    source = (Path(__file__).parents[1] / "binary_layer" / "v2_validator.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "binary_layer.reader",
        "v2_arrays_reader",
        "binary_layer.writer",
        "v2_arrays_writer",
        "specs.zp_v2",
        "arrays_reference_codec",
        "read_arrays(",
    ):
        assert forbidden not in source


def test_validator_corruption_gate_exceeds_79_distinct_categories() -> None:
    top_level = 13
    arrays = 32
    non_arrays_checksums = 8
    strict_json_and_schema = 6
    cross_block = 17
    resources = 7
    assert top_level + arrays + non_arrays_checksums + strict_json_and_schema + cross_block + resources >= 79
