from __future__ import annotations

from pathlib import Path

from specs.zp_migration.compatibility_gate import _run_full_reader_writer_reference, run_gate


FIXTURES = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


def test_full_reader_writer_reference_is_measured_and_matches_v2_golden(tmp_path: Path) -> None:
    target = tmp_path / "reference-v2.zp"
    metrics = _run_full_reader_writer_reference(
        FIXTURES / "valid_full_v1.zp",
        target,
        monitor_directory=tmp_path,
    )

    assert target.read_bytes() == (FIXTURES / "valid_full_v2.zp").read_bytes()
    assert metrics["read_arrays_called"] is True
    assert metrics["peak_rss"] > 0
    assert metrics["total_seconds"] >= 0


def test_migration_gate_requires_large_b8_5_pytest_and_frozen_snapshot() -> None:
    report = run_gate(
        skip_large=True,
        run_pytest=False,
        skip_b8_5=True,
    )
    assert report["golden_migration_matrix"]["passed"] is True
    assert report["cli_matrix"]["passed"] is True
    assert report["real_fixture_matrix"]["passed"] is True
    assert report["production_freeze"]["existing_frozen_modules_unchanged"] is True
    assert report["fault_injection_matrix"]["status"] == "not_run"
    assert report["large_sample_matrix"]["status"] == "skipped"
    assert report["b8_5_release_gate"] is False
    assert report["release_gate"] is False
