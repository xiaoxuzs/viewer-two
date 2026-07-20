from __future__ import annotations

import hashlib
import json
from pathlib import Path

from binary_layer import migrate_v1_to_v2


ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "specs" / "zp_migration" / "fixtures" / "manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_migration_manifest_and_two_runs_are_deterministic(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for record in manifest["fixtures"]:
        source = (MANIFEST.parent / record["source"]).resolve()
        golden = (MANIFEST.parent / record["target_golden"]).resolve()
        first = tmp_path / f"{record['kind']}-first.zp"
        second = tmp_path / f"{record['kind']}-second.zp"
        first_result = migrate_v1_to_v2(source, first)
        second_result = migrate_v1_to_v2(source, second)
        assert first.read_bytes() == second.read_bytes() == golden.read_bytes()
        assert _sha256(source) == record["source_sha256"]
        assert _sha256(first) == record["target_sha256"]
        assert first.stat().st_size == record["target_size"]
        assert first_result.source_logical_fingerprint == record["logical_fingerprint"]
        assert second_result.target_logical_fingerprint == record["logical_fingerprint"]

