from __future__ import annotations

from specs.zp_full.compatibility_gate import run_gate


def test_unified_gate_requires_large_sample_and_all_pytest_matrices() -> None:
    report = run_gate(skip_large=True, run_pytest=False)

    assert report["full_logical_equal"]["logical_equal"] is True
    assert report["minimal_logical_equal"]["logical_equal"] is True
    assert report["writer_matrix"]["passed"] is True
    assert report["reader_matrix"]["passed"] is True
    assert report["validator_matrix"]["passed"] is True
    assert report["real_fixture_matrix"]["passed"] is True
    assert report["production_code_changed"] is False
    assert report["large_sample_matrix"]["status"] == "skipped"
    assert report["release_gate"] is False
