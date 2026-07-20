from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

import binary_layer.migration as migration
from binary_layer.logical_fingerprint import LogicalFingerprint
from binary_layer.migration import MigrationError, SourceIdentity, migrate_v1_to_v2
from binary_layer.models import ValidationIssue, ValidationResult


FIXTURES = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


FAULT_CONDITIONS = (
    "source_validator_rejects",
    "source_validator_io_error",
    "source_identity_changes",
    "disk_preflight_fails",
    "temp_creation_fails",
    "arrays_parser_fails",
    "spool_add_fails",
    "spool_fsync_fails",
    "arrays_write_fails",
    "output_fsync_fails",
    "target_validator_rejects",
    "target_validator_io_error",
    "logical_fingerprint_mismatch",
    "source_hash_changes",
    "destination_appears",
    "commit_replace_fails",
    "keyboard_interrupt",
)


@pytest.mark.parametrize("condition", FAULT_CONDITIONS)
def test_additional_failure_conditions_are_atomic(
    condition: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.zp"
    source.write_bytes((FIXTURES / "valid_full_v1.zp").read_bytes())
    source_before = source.read_bytes()
    target = tmp_path / "target.zp"
    external_target = False

    if condition == "source_validator_rejects":
        monkeypatch.setattr(
            migration.ZpValidator,
            "validate",
            lambda _self, path: ValidationResult(
                False,
                [ValidationIssue("INJECTED", "failure")],
                0,
                Path(path),
                1,
            ),
        )
    elif condition == "source_validator_io_error":
        monkeypatch.setattr(
            migration.ZpValidator,
            "validate",
            lambda _self, _path: (_ for _ in ()).throw(OSError("validator io")),
        )
    elif condition == "source_identity_changes":
        original = migration._identity
        calls = 0

        def changed(path: Path) -> SourceIdentity:
            nonlocal calls
            calls += 1
            value = original(path)
            return replace(value, mtime_ns=value.mtime_ns + 1) if calls == 2 else value

        monkeypatch.setattr(migration, "_identity", changed)
    elif condition == "disk_preflight_fails":
        monkeypatch.setattr(
            migration,
            "_disk_budget",
            lambda *_args: (_ for _ in ()).throw(
                MigrationError("INJECTED_DISK", "disk", stage="disk_preflight", exit_code=5)
            ),
        )
    elif condition == "temp_creation_fails":
        monkeypatch.setattr(
            migration,
            "_new_sibling_temp",
            lambda *_args: (_ for _ in ()).throw(OSError("temp create")),
        )
    elif condition == "arrays_parser_fails":
        def fail_iter(_self):
            raise migration.V1ArraysStreamError("INJECTED_PARSE", "parse")
            yield

        monkeypatch.setattr(migration.V1ArraysStreamReader, "iter_arrays", fail_iter)
    elif condition == "spool_add_fails":
        monkeypatch.setattr(
            migration.V2ArraysMigrationWriter,
            "add",
            lambda *_args: (_ for _ in ()).throw(OSError("spool add")),
        )
    elif condition == "spool_fsync_fails":
        monkeypatch.setattr(
            migration.V2ArraysMigrationWriter,
            "flush_spool",
            lambda _self: (_ for _ in ()).throw(OSError("spool fsync")),
        )
    elif condition == "arrays_write_fails":
        monkeypatch.setattr(
            migration.V2ArraysMigrationWriter,
            "write_block",
            lambda *_args: (_ for _ in ()).throw(OSError("arrays write")),
        )
    elif condition == "output_fsync_fails":
        original_fsync = migration.os.fsync
        calls = 0

        def fail_second(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("output fsync")
            original_fsync(descriptor)

        monkeypatch.setattr(migration.os, "fsync", fail_second)
    elif condition in {"target_validator_rejects", "target_validator_io_error"}:
        original_validate = migration.ZpValidator.validate
        calls = 0

        def target_failure(self, path):
            nonlocal calls
            calls += 1
            if calls == 2:
                if condition == "target_validator_io_error":
                    raise OSError("target validator io")
                return ValidationResult(
                    False,
                    [ValidationIssue("INJECTED", "failure")],
                    9,
                    Path(path),
                    2,
                )
            return original_validate(self, path)

        monkeypatch.setattr(migration.ZpValidator, "validate", target_failure)
    elif condition == "logical_fingerprint_mismatch":
        original_target = migration._target_fingerprint

        def mismatch(path: Path) -> LogicalFingerprint:
            value = original_target(path)
            return replace(value, sha256="0" * 64)

        monkeypatch.setattr(migration, "_target_fingerprint", mismatch)
    elif condition == "source_hash_changes":
        original_hash = migration._hash_file
        calls = 0

        def changed_hash(path: Path) -> str:
            nonlocal calls
            calls += 1
            value = original_hash(path)
            return "0" * 64 if calls == 2 else value

        monkeypatch.setattr(migration, "_hash_file", changed_hash)
    elif condition == "destination_appears":
        external_target = True

        def appear(stage: str) -> None:
            if stage == "before_commit":
                target.write_bytes(b"external target")

        monkeypatch.setattr(migration, "_fault_point", appear)
    elif condition == "commit_replace_fails":
        original_replace = migration.os.replace

        def replace_failure(left, right) -> None:
            if Path(right) == target:
                raise OSError("commit replace")
            original_replace(left, right)

        monkeypatch.setattr(migration.os, "replace", replace_failure)
    elif condition == "keyboard_interrupt":
        monkeypatch.setattr(
            migration,
            "_fault_point",
            lambda stage: (_ for _ in ()).throw(KeyboardInterrupt())
            if stage == "after_temp_create"
            else None,
        )
    else:
        raise AssertionError(condition)

    with pytest.raises(MigrationError):
        migrate_v1_to_v2(source, target)

    assert source.read_bytes() == source_before
    if external_target:
        assert target.read_bytes() == b"external target"
    else:
        assert target.exists() is False
    assert list(tmp_path.glob(".*.migrating-*.tmp")) == []
    assert list(tmp_path.glob(".*.payload-*.tmp")) == []
    assert list(tmp_path.glob(".*.validating-*.zp")) == []

