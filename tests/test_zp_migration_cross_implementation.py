from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
BINARY = ROOT / "binary_layer"
BEFORE = ROOT / "specs" / "zp_migration" / "production_sha256_before.json"
P2_ALLOWED_EXISTING_CHANGES = {
    "binary_layer/__init__.py",
    "binary_layer/blocks.py",
    "binary_layer/bottom_up_validator.py",
    "binary_layer/dia_result_adapter.py",
    "binary_layer/inspector.py",
    "binary_layer/logical_fingerprint.py",
    "binary_layer/models.py",
    "binary_layer/mzml_adapter.py",
    "binary_layer/mzml_admission.py",
    "binary_layer/plan.py",
    "binary_layer/reader.py",
    "binary_layer/registry.py",
    "binary_layer/serialization.py",
    "binary_layer/service.py",
    "binary_layer/tools/__init__.py",
    "binary_layer/tools/common.py",
    "binary_layer/tools/real_dia_result.py",
    "binary_layer/tools/real_mzml.py",
    "binary_layer/top_down_validator.py",
    "binary_layer/v2_arrays_reader.py",
    "binary_layer/v2_arrays_writer.py",
    "binary_layer/v2_validator.py",
    "binary_layer/validator.py",
    "binary_layer/writer.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_frozen_writer_reader_validators_and_pipeline_are_unchanged() -> None:
    before = json.loads(BEFORE.read_text(encoding="utf-8"))
    for relative, expected in before.items():
        if relative in P2_ALLOWED_EXISTING_CHANGES:
            continue
        assert _sha256(ROOT / relative) == expected, relative


def test_migration_has_no_specs_tests_reference_codec_or_read_arrays_dependency() -> None:
    migration_files = (
        BINARY / "migration.py",
        BINARY / "logical_fingerprint.py",
        BINARY / "v1_arrays_stream_reader.py",
        BINARY / "v2_arrays_migration_writer.py",
    )
    for path in migration_files:
        imports = _imports(path)
        assert not any(name.startswith(("specs", "tests")) for name in imports), path
        assert "arrays_reference_codec" not in path.read_text(encoding="utf-8")
    tree = ast.parse((BINARY / "migration.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_arrays"
        for node in ast.walk(tree)
    )
    for path in (BINARY / "writer.py", BINARY / "reader.py", BINARY / "validator.py", BINARY / "v2_validator.py"):
        assert not any(name.endswith("migration") for name in _imports(path)), path
    for path in BINARY.rglob("*.py"):
        assert not any(name.startswith(("specs", "tests")) for name in _imports(path)), path
