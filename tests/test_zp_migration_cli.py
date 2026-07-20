from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import binary_layer.migration as migration


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "specs" / "zp_full" / "fixtures"


def _run(*arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "binary_layer.migration", *(str(item) for item in arguments)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_json_success(tmp_path: Path) -> None:
    target = tmp_path / "cli.zp"
    completed = _run(
        "--input",
        FIXTURES / "valid_minimal_v1.zp",
        "--output",
        target,
        "--json",
    )
    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["success"] is True
    assert report["source_version"] == 1
    assert report["target_version"] == 2
    assert report["source_logical_fingerprint"] == report["target_logical_fingerprint"]
    assert target.read_bytes() == (FIXTURES / "valid_minimal_v2.zp").read_bytes()
    assert "Traceback" not in completed.stderr


def test_cli_rejects_existing_destination_without_traceback(tmp_path: Path) -> None:
    target = tmp_path / "existing.zp"
    target.write_bytes(b"existing")
    completed = _run(
        "--input",
        FIXTURES / "valid_minimal_v1.zp",
        "--output",
        target,
        "--json",
    )
    assert completed.returncode == 2
    report = json.loads(completed.stdout)
    assert report["success"] is False
    assert report["error_code"] == "DESTINATION_EXISTS"
    assert target.read_bytes() == b"existing"
    assert "Traceback" not in completed.stderr


def test_cli_quiet_success_has_no_stdout(tmp_path: Path) -> None:
    completed = _run(
        "--input",
        FIXTURES / "valid_minimal_v1.zp",
        "--output",
        tmp_path / "quiet.zp",
        "--quiet",
    )
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_cli_invalid_source_uses_source_validation_exit_code(tmp_path: Path) -> None:
    source = tmp_path / "invalid.zp"
    source.write_bytes(b"invalid")
    completed = _run(
        "--input",
        source,
        "--output",
        tmp_path / "target.zp",
        "--json",
    )
    assert completed.returncode == 3
    report = json.loads(completed.stdout)
    assert report["error_code"] == "SOURCE_VALIDATION_FAILED"
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("exit_code", range(2, 9))
def test_cli_preserves_all_documented_migration_exit_codes(
    exit_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise migration.MigrationError(
            "INJECTED",
            "injected",
            stage="test",
            exit_code=exit_code,
        )

    monkeypatch.setattr(migration, "migrate_v1_to_v2", fail)
    assert migration.main(["--input", "source.zp", "--output", "target.zp", "--quiet"]) == exit_code
