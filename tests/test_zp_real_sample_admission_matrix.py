from pathlib import Path

from specs.zp_real_matrix.inspection import inspect_sample


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


def test_fixture_only_accepted_inspection_is_stable() -> None:
    result = inspect_sample("fixture-accepted", FIXTURE_DIR / "accept_indexed_float64_zlib.mzML")
    assert result["admission"] == "accepted"
    assert result["admission_stable"] is True
    assert result["admission_reasons"] == []


def test_fixture_only_rejected_inspection_aggregates_stable_reasons() -> None:
    result = inspect_sample("fixture-rejected", FIXTURE_DIR / "reject_missing_charge.mzML")
    assert result["admission"] == "rejected"
    assert result["admission_stable"] is True
    assert result["admission_reasons"] == [
        {"code": "MISSING_PRECURSOR_CHARGE", "count": 1, "first_location": "spectrum[1]"}
    ]
