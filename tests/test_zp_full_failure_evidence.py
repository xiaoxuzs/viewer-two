from __future__ import annotations

import json
from pathlib import Path

from binary_layer import ZpValidator


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "specs" / "zp_full" / "failures" / "global_meta_count"


def test_global_meta_count_failure_evidence_records_current_semantic_drift() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    records = {item["format_version"]: item for item in manifest["fixtures"]}
    assert records[1]["validator_valid"] is True
    assert records[1]["validator_issue_codes"] == []
    assert records[2]["validator_valid"] is False
    assert records[2]["validator_issue_codes"] == ["COUNT_MISMATCH"]

    for version in (1, 2):
        record = records[version]
        result = ZpValidator().validate(FIXTURE_DIR / record["file"])
        assert result.valid is record["validator_valid"]
        assert [issue.code for issue in result.issues] == record["validator_issue_codes"]
        assert result.checked_blocks == record["validator_checked_blocks"] == 9
