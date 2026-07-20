from __future__ import annotations

import json
from pathlib import Path

from binary_layer import ZpValidator


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "specs" / "zp_full" / "failures" / "global_meta_count"
CANDIDATE_DIR = ROOT / "specs" / "zp_full" / "failures" / "candidate_parity"


def test_global_meta_count_failure_evidence_records_corrected_validator_parity() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    records = {item["format_version"]: item for item in manifest["fixtures"]}
    assert records[1]["validator_valid"] is False
    assert records[1]["validator_issue_codes"] == ["COUNT_MISMATCH"]
    assert records[2]["validator_valid"] is False
    assert records[2]["validator_issue_codes"] == ["COUNT_MISMATCH"]

    for version in (1, 2):
        record = records[version]
        result = ZpValidator().validate(FIXTURE_DIR / record["file"])
        assert result.valid is record["validator_valid"]
        assert [issue.code for issue in result.issues] == record["validator_issue_codes"]
        assert result.checked_blocks == record["validator_checked_blocks"] == 9


def test_candidate_parity_evidence_records_all_three_domains_as_corrected() -> None:
    manifest = json.loads((CANDIDATE_DIR / "manifest.json").read_text(encoding="utf-8"))
    probes = {item["probe"]: item for item in manifest["probes"]}
    expected = {
        "run_count": {1: (False, ["COUNT_MISMATCH"]), 2: (False, ["COUNT_MISMATCH"])},
        "string_pool": {
            1: (False, ["INVALID_REFERENCE"]),
            2: (False, ["INVALID_REFERENCE"]),
        },
        "precursor_bidirectional": {
            1: (False, ["INVALID_REFERENCE", "INVALID_REFERENCE"]),
            2: (False, ["INVALID_REFERENCE", "INVALID_REFERENCE"]),
        },
    }

    for probe_name, versions in expected.items():
        records = {item["format_version"]: item for item in probes[probe_name]["fixtures"]}
        for version, (valid, codes) in versions.items():
            record = records[version]
            assert record["validator_valid"] is valid
            assert record["validator_issue_codes"] == codes
            result = ZpValidator().validate(CANDIDATE_DIR / record["file"])
            assert result.valid is valid
            assert [issue.code for issue in result.issues] == codes
            assert result.checked_blocks == record["validator_checked_blocks"] == 9
