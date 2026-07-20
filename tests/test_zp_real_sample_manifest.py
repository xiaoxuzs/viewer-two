import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "specs" / "zp_real_matrix"


def test_manifest_schema_has_required_gate_and_sample_fields() -> None:
    schema = json.loads((SPEC_DIR / "manifest.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["stage"]["const"] == "P1-B8.7"
    assert {"available", "missing", "accepted", "rejected", "failed"} <= set(
        schema["properties"]["counts"]["required"]
    )
    sample_required = schema["properties"]["samples"]["items"]["required"]
    assert {"sample_id", "file_name", "coverage_tags", "admission", "admission_reasons"} <= set(sample_required)


def test_manifest_contract_contains_no_permanent_path_field() -> None:
    schema_text = (SPEC_DIR / "manifest.schema.json").read_text(encoding="utf-8")
    assert "absolute_path" not in schema_text
    assert "source_path" not in schema_text
    assert "target_path" not in schema_text
