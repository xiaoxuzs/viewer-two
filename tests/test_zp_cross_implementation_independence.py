from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def test_production_and_independent_checker_import_boundaries() -> None:
    validator_imports = _imports(ROOT / "binary_layer" / "validator.py") | _imports(
        ROOT / "binary_layer" / "v2_validator.py"
    )
    reader_imports = _imports(ROOT / "binary_layer" / "reader.py") | _imports(
        ROOT / "binary_layer" / "v2_arrays_reader.py"
    )
    inspector_imports = _imports(ROOT / "specs" / "zp_full" / "inspect_full_zp.py")

    assert not any(name.endswith(("reader", "writer")) for name in validator_imports)
    assert not any(name.endswith("writer") for name in reader_imports)
    assert not any(name.startswith("binary_layer") for name in inspector_imports)
    assert not any("arrays_reference_codec" in name for name in inspector_imports)


def test_production_package_does_not_import_specs_or_reference_codec() -> None:
    for path in (ROOT / "binary_layer").rglob("*.py"):
        imports = _imports(path)
        assert not any(name.startswith("specs") or "arrays_reference_codec" in name for name in imports), path
