from __future__ import annotations

from binary_layer.logical_fingerprint import LogicalArrayFingerprint, build_logical_fingerprint


def _blocks() -> dict[str, object]:
    return {
        "global_meta": {"format_version": 1, "run_count": 0},
        "string_pool": {"strings": []},
        "core_runs": [],
        "core_spectra": [],
        "core_precursors": [],
        "core_chromatograms": [],
        "indexes": {"scan_index": [], "rt_index": [], "spectrum_id_index": []},
        "extensions": [],
    }


def test_fingerprint_ignores_only_format_version_and_array_order() -> None:
    arrays = [
        LogicalArrayFingerprint("b", "intensity", 1, "b" * 64),
        LogicalArrayFingerprint("a", "mz", 1, "a" * 64),
    ]
    v1 = _blocks()
    v2 = _blocks()
    v2["global_meta"] = {"format_version": 2, "run_count": 0}
    assert build_logical_fingerprint(v1, arrays).sha256 == build_logical_fingerprint(
        v2,
        reversed(arrays),
    ).sha256


def test_fingerprint_detects_business_or_array_hash_change() -> None:
    array = LogicalArrayFingerprint("a", "mz", 1, "a" * 64)
    baseline = build_logical_fingerprint(_blocks(), [array]).sha256
    changed_blocks = _blocks()
    changed_blocks["extensions"] = [{"payload": {"changed": True}}]
    assert build_logical_fingerprint(changed_blocks, [array]).sha256 != baseline
    changed_array = LogicalArrayFingerprint("a", "mz", 1, "b" * 64)
    assert build_logical_fingerprint(_blocks(), [changed_array]).sha256 != baseline

