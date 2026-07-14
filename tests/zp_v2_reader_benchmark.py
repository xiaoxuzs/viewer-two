from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).parents[1]
for import_root in (ROOT, ROOT / "tests"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from binary_layer import (
    ArrayBlock,
    BlockCollection,
    ChromatogramBlock,
    ExtensionBlock,
    GlobalMetaBlock,
    IndexBlock,
    PipelineContext,
    PipelineRunner,
    PlanBuilder,
    RunBlock,
    SourceInspector,
    SpectrumBlock,
    StringPoolBlock,
    ZpReader,
    ZpWriter,
    build_default_registry,
)
from zp_v2_reader_support import TrackingStream, raw_layout


def build_synthetic_blocks(spectrum_count: int, peaks_per_spectrum: int) -> BlockCollection:
    arrays: list[ArrayBlock] = []
    spectra: list[SpectrumBlock] = []
    for position in range(spectrum_count):
        spectrum_id = f"spectrum_{position:06d}"
        mz_id = f"{spectrum_id}:mz"
        intensity_id = f"{spectrum_id}:intensity"
        mz_values = [100.0 + point * 0.01 + position * 0.000001 for point in range(peaks_per_spectrum)]
        intensity_values = [float((position * 17 + point * 13) % 10_000) - 5.0 for point in range(peaks_per_spectrum)]
        arrays.extend(
            [
                ArrayBlock(mz_id, "mz", "float64", mz_values),
                ArrayBlock(intensity_id, "intensity", "float64", intensity_values),
            ]
        )
        spectra.append(
            SpectrumBlock(
                spectrum_id,
                "run_1",
                1 if position % 2 == 0 else 2,
                position + 1,
                f"scan={position + 1}",
                position * 0.25,
                None,
                mz_id,
                intensity_id,
            )
        )
    for kind, values in (("tic", [10.0, 20.0, 15.0]), ("bpc", [8.0, 18.0, 12.0])):
        arrays.extend(
            [
                ArrayBlock(f"chrom_{kind}:time", "time", "float64", [0.0, 0.25, 0.5]),
                ArrayBlock(f"chrom_{kind}:intensity", "intensity", "float64", values),
            ]
        )
    chromatograms = [
        ChromatogramBlock(
            f"chrom_{kind}",
            "run_1",
            kind,
            f"chrom_{kind}:time",
            f"chrom_{kind}:intensity",
            kind,
        )
        for kind in ("tic", "bpc")
    ]
    indexes = IndexBlock(
        scan_index=[{"scan_number": item.scan_number, "spectrum_id": item.spectrum_id} for item in spectra],
        rt_index=[{"rt": item.rt, "spectrum_id": item.spectrum_id} for item in spectra],
        spectrum_id_index=[{"spectrum_id": item.spectrum_id, "position": position} for position, item in enumerate(spectra)],
    )
    return BlockCollection(
        global_meta=GlobalMetaBlock(
            1,
            "synthetic_mzml",
            "deterministic-medium.mzML",
            "0" * 64,
            1,
            spectrum_count,
            len(chromatograms),
            len(arrays),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            "zp-reader-benchmark",
            "1",
        ),
        runs=[RunBlock("run_1", "deterministic-medium.mzML", "run", spectrum_count, 2, 0.0, max(0, spectrum_count - 1) * 0.25)],
        spectra=spectra,
        chromatograms=chromatograms,
        arrays=arrays,
        string_pool=StringPoolBlock([]),
        indexes=indexes,
        extensions=[ExtensionBlock("benchmark", "1", {"deterministic": True})],
    )


def timed(operation: Callable[[], object]) -> float:
    started = time.perf_counter()
    operation()
    return time.perf_counter() - started


def measured_io(path: Path, operation: Callable[[], object]) -> tuple[int, int, list[tuple[str, int, int]]]:
    events: list[tuple[str, int, int]] = []
    original_open = Path.open

    def tracked_open(self: Path, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        return TrackingStream(stream, events) if self == path else stream

    Path.open = tracked_open
    try:
        operation()
    finally:
        Path.open = original_open
    return sum(length for kind, _offset, length in events if kind == "read"), sum(kind == "seek" for kind, _offset, _length in events), events


def overlap_bytes(events: list[tuple[str, int, int]], start: int, length: int) -> int:
    end = start + length
    total = 0
    for kind, offset, read_length in events:
        if kind != "read":
            continue
        total += max(0, min(offset + read_length, end) - max(offset, start))
    return total


def run_profile(root: Path, name: str, spectrum_count: int, peaks: int) -> dict[str, object]:
    blocks = build_synthetic_blocks(spectrum_count, peaks)
    paths = {version: root / f"{name}-v{version}.zp" for version in (1, 2)}
    for version, path in paths.items():
        ZpWriter().write(path, blocks, format_version=version)
    ids = [item.spectrum_id for item in blocks.spectra]
    random_ids = random.Random(20260714).choices(ids, k=100)
    result: dict[str, object] = {"profile": name, "spectra": spectrum_count, "peaks_per_spectrum": peaks}
    for version in (1, 2):
        reader = ZpReader(paths[version])
        target = blocks.spectra[spectrum_count // 2]
        first_array = timed(lambda: reader.read_array(target.mz_array_id))
        cached_array = timed(lambda: reader.read_array(target.mz_array_id))
        single_spectrum = timed(lambda: reader.read_spectrum_arrays(target.spectrum_id))
        sequential_10 = timed(lambda: [reader.read_spectrum_arrays(item) for item in ids[:10]])
        random_100 = timed(lambda: [reader.read_spectrum_arrays(item) for item in random_ids])
        repeat_100 = timed(lambda: [reader.read_spectrum_arrays(target.spectrum_id) for _ in range(100)])
        chromatogram = timed(lambda: reader.read_chromatogram_arrays("chrom_tic"))
        if version == 2:
            reader.read_spectrum_arrays(target.spectrum_id)
        total_bytes, seeks, events = measured_io(paths[version], lambda: reader.read_spectrum_arrays(target.spectrum_id))
        version_result: dict[str, object] = {
            "file_size": paths[version].stat().st_size,
            "first_read_array_seconds": first_array,
            "cached_read_array_seconds": cached_array,
            "single_spectrum_seconds": single_spectrum,
            "sequential_10_seconds": sequential_10,
            "random_100_seconds": random_100,
            "repeat_100_seconds": repeat_100,
            "chromatogram_seconds": chromatogram,
            "single_spectrum_total_read_bytes": total_bytes,
            "single_spectrum_seek_count": seeks,
        }
        if version == 2:
            layout = raw_layout(paths[2])
            arrays_entry = layout["arrays_entry"]
            payload_start = arrays_entry["offset"] + layout["arrays_header"][7]
            payload_length = layout["arrays_header"][8]
            single_entry = next(
                item for item in layout["internal"]["entries"] if item["array_id"] == target.mz_array_id
            )
            io_reader = ZpReader(paths[2])
            first_array_total, first_array_seeks, _first_array_events = measured_io(
                paths[2], lambda: io_reader.read_array(target.mz_array_id)
            )
            cached_array_total, cached_array_seeks, cached_array_events = measured_io(
                paths[2], lambda: io_reader.read_array(target.mz_array_id)
            )
            cached_single_payload = overlap_bytes(cached_array_events, payload_start, payload_length)
            assert cached_single_payload == single_entry["byte_length"]
            target_entries = [
                item for item in layout["internal"]["entries"]
                if item["array_id"] in {target.mz_array_id, target.intensity_array_id}
            ]
            expected_payload = sum(item["byte_length"] for item in target_entries)
            actual_payload = overlap_bytes(events, payload_start, payload_length)
            assert actual_payload == expected_payload
            version_result.update(
                arrays_size=arrays_entry["length"],
                array_entry_count=len(layout["internal"]["entries"]),
                internal_directory_size=layout["arrays_header"][6],
                target_array_id=target.mz_array_id,
                target_array_byte_length=single_entry["byte_length"],
                first_read_array_total_bytes=first_array_total,
                first_read_array_seek_count=first_array_seeks,
                cached_read_array_total_bytes=cached_array_total,
                cached_read_array_seek_count=cached_array_seeks,
                cached_single_array_payload_bytes=cached_single_payload,
                cached_single_array_unrelated_payload_bytes_read=0,
                cached_payload_bytes=actual_payload,
                target_payload_bytes=expected_payload,
                unrelated_payload_bytes_read=0,
            )
        result[f"v{version}"] = version_result
    result["speedup_single_spectrum_v1_over_v2"] = result["v1"]["single_spectrum_seconds"] / result["v2"]["single_spectrum_seconds"]
    result["speedup_random_100_v1_over_v2"] = result["v1"]["random_100_seconds"] / result["v2"]["random_100_seconds"]
    return result


def run_real_source(root: Path, source: Path) -> dict[str, object]:
    v1_path = root / "real-v1.zp"
    v2_path = root / "real-v2.zp"
    profile = SourceInspector().inspect([source])
    plan = PlanBuilder().build(profile)
    context = PipelineContext(profile, metadata={"output_path": v1_path})
    pipeline_seconds = timed(lambda: PipelineRunner().run(plan, build_default_registry(), context))
    v2_write_seconds = timed(lambda: ZpWriter().write(v2_path, context.blocks, format_version=2))
    spectrum_ids = [item.spectrum_id for item in context.blocks.spectra]
    target = context.blocks.spectra[len(context.blocks.spectra) // 2]
    random_ids = random.Random(20260714).choices(spectrum_ids, k=100)
    reader = ZpReader(v2_path)
    first_array = timed(lambda: reader.read_array(target.mz_array_id))
    cached_array = timed(lambda: reader.read_array(target.mz_array_id))
    single = timed(lambda: reader.read_spectrum_arrays(target.spectrum_id))
    sequential_10 = timed(lambda: [reader.read_spectrum_arrays(item) for item in spectrum_ids[:10]])
    random_100 = timed(lambda: [reader.read_spectrum_arrays(item) for item in random_ids])
    repeat_100 = timed(lambda: [reader.read_spectrum_arrays(target.spectrum_id) for _ in range(100)])
    chromatogram = (
        timed(lambda: reader.read_chromatogram_arrays(context.blocks.chromatograms[0].chromatogram_id))
        if context.blocks.chromatograms
        else None
    )
    full_arrays = timed(reader.read_arrays)
    layout = raw_layout(v2_path)
    arrays_entry = layout["arrays_entry"]
    payload_start = arrays_entry["offset"] + layout["arrays_header"][7]
    payload_length = layout["arrays_header"][8]
    reader.read_spectrum_arrays(target.spectrum_id)
    total_bytes, seek_count, events = measured_io(v2_path, lambda: reader.read_spectrum_arrays(target.spectrum_id))
    payload_bytes = overlap_bytes(events, payload_start, payload_length)
    target_entries = [
        item for item in layout["internal"]["entries"]
        if item["array_id"] in {target.mz_array_id, target.intensity_array_id}
    ]
    expected_payload = sum(item["byte_length"] for item in target_entries)
    assert payload_bytes == expected_payload
    return {
        "profile": "real-31mb",
        "source": str(source),
        "source_size": source.stat().st_size,
        "pipeline_v1_seconds": pipeline_seconds,
        "v2_write_seconds": v2_write_seconds,
        "v1_file_size": v1_path.stat().st_size,
        "v2_file_size": v2_path.stat().st_size,
        "arrays_size": arrays_entry["length"],
        "internal_directory_size": layout["arrays_header"][6],
        "spectrum_count": len(context.blocks.spectra),
        "chromatogram_count": len(context.blocks.chromatograms),
        "array_entry_count": len(layout["internal"]["entries"]),
        "first_read_array_seconds": first_array,
        "cached_read_array_seconds": cached_array,
        "single_spectrum_seconds": single,
        "sequential_10_seconds": sequential_10,
        "random_100_seconds": random_100,
        "repeat_100_seconds": repeat_100,
        "chromatogram_seconds": chromatogram,
        "full_arrays_seconds": full_arrays,
        "target_spectrum_id": target.spectrum_id,
        "target_payload_bytes": expected_payload,
        "actual_payload_bytes": payload_bytes,
        "unrelated_payload_bytes_read": 0,
        "total_read_bytes": total_bytes,
        "seek_count": seek_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-source", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="zp-v2-reader-benchmark-") as temporary:
        root = Path(temporary)
        results = (
            [run_real_source(root, args.real_source)]
            if args.real_source is not None
            else [run_profile(root, "small", 12, 64), run_profile(root, "medium", 128, 512)]
        )
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
