from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from binary_layer import (
    ConversionOptions,
    SourceInspector,
    convert_source_to_zp,
    get_fragment_matches,
    get_prsm,
    get_proteoform,
    get_top_down_interpretation_provenance,
    get_top_down_summary,
    inspect_source,
    open_zp,
)
from binary_layer.conversion_exceptions import TopDownConversionError
from binary_layer.top_down_interpretation_adapter import TopDownInterpretationAdapter
from top_down_support import (
    MZML_FIXTURE,
    build_top_down_bundle,
    build_top_down_intermediate_bundle,
    write_fake_prsmup,
    write_prsm,
)


def _options(
    tmp_path: Path,
    script: Path,
    *,
    keep: bool = False,
    timeout: float = 10.0,
) -> ConversionOptions:
    return ConversionOptions(
        requested_conversion_kind="top_down",
        top_down_interpreter_script=script,
        python_executable=Path(sys.executable),
        temporary_directory=tmp_path / "temporary",
        keep_generated_interpretation=keep,
        interpretation_timeout_seconds=timeout,
        generated_interpretation_directory=tmp_path / "generated" if keep else None,
    )


def _mass_shift_records(count: int) -> list[dict[str, str]]:
    return [
        {
            "id": str(index),
            "left_position": str(index % 2),
            "right_position": str((index % 2) + 1),
            "anno": f"fixture modification {index}",
            "shift": str(15.5 + index),
            "shift_type": "unexpected",
            "unknown_modification_field": f"retained-{index}",
        }
        for index in range(count)
    ]


def _write_two_prsm_xml(path: Path, counts: tuple[int, int]) -> None:
    records = []
    for prsm_id, count in zip((1, 2), counts, strict=True):
        mass_shifts = "".join("<mass_shift />" for _ in range(count))
        records.append(
            "<prsm><file_name>run_ms2.msalign</file_name>"
            f"<prsm_id>{prsm_id}</prsm_id><spectrum_scan>2</spectrum_scan>"
            "<proteoform><mass_shift_list>"
            f"{mass_shifts}</mass_shift_list></proteoform></prsm>"
        )
    path.write_text("<prsm_list>" + "".join(records) + "</prsm_list>", encoding="utf-8")


def _write_multi_prsmup(
    path: Path,
    *,
    generated_counts: tuple[int, int],
) -> Path:
    bodies: dict[str, str] = {}
    for prsm_id, count in zip((1, 2), generated_counts, strict=True):
        seed = path.with_name(f"prsm{prsm_id}.seed")
        write_prsm(
            seed,
            prsm_id=prsm_id,
            spectrum_file_name="run.mzML",
            scan_number=2,
            mass_shifts=_mass_shift_records(count),
        )
        bodies[f"prsm{prsm_id}.js"] = seed.read_text(encoding="utf-8")
        seed.unlink()
    path.write_text(
        "import argparse, pathlib\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--prsm-xml', required=True)\n"
        "parser.add_argument('--msalign', required=True)\n"
        "parser.add_argument('--out-dir', required=True)\n"
        "parser.add_argument('--limit', required=True, type=int)\n"
        "args = parser.parse_args()\n"
        f"bodies = {bodies!r}\n"
        "out = pathlib.Path(args.out_dir)\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "for name, body in list(bodies.items())[:args.limit]:\n"
        "    (out / name).write_text(body, encoding='utf-8')\n",
        encoding="utf-8",
        newline="",
    )
    return path


def _generated_modification_counts(root: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for path in root.rglob("prsm*.js"):
        payload = json.loads(path.read_text(encoding="utf-8").split("=", 1)[1].rstrip(";\r\n"))
        prsm = payload["prsm"]
        prsm_id = str(prsm["prsm_id"])
        mass_shift = prsm["annotated_protein"]["annotation"].get("mass_shift")
        result[prsm_id] = len(mass_shift) if isinstance(mass_shift, list) else int(mass_shift is not None)
    return result


def test_precomputed_prsm_js_keeps_priority_over_intermediate_inputs(tmp_path: Path) -> None:
    source = build_top_down_bundle(tmp_path / "bundle")
    intermediate = build_top_down_intermediate_bundle(tmp_path / "intermediate")
    shutil.copyfile(
        intermediate / "toppic" / "run_ms2_toppic_prsm.xml",
        source / "run_ms2_toppic_prsm.xml",
    )
    shutil.copyfile(
        intermediate / "topfd" / "run_ms2.msalign",
        source / "run_ms2.msalign",
    )

    profile = SourceInspector().inspect([source])

    assert profile.source_type == "real_top_down_bundle"


def test_intermediate_bundle_is_discovered_by_content_and_references(tmp_path: Path) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    (source / "run_ms2_toppic_prsm.tsv").write_text(
        "Prsm ID\n1\n",
        encoding="utf-8",
    )

    profile = inspect_source(source)

    assert profile.source_type == "real_top_down_intermediate_bundle"
    assert profile.run_count == 1
    assert profile.spectrum_source_type == "mzml"
    assert profile.detected_roles == (
        "spectrum_source",
        "toppic_prsm_xml",
        "topfd_ms2_msalign",
    )
    assert profile.top_down_intermediate_bundle is not None
    assert profile.top_down_intermediate_bundle.input_pairs[0].pairing_evidence == "xml_file_reference"


def test_only_mzml_is_rejected_when_full_top_down_is_requested(tmp_path: Path) -> None:
    source = tmp_path / "run.mzML"
    shutil.copyfile(MZML_FIXTURE, source)

    with pytest.raises(TopDownConversionError) as captured:
        inspect_source(source, requested_conversion_kind="top_down")

    assert captured.value.code == "TOP_DOWN_INTERPRETATION_INPUTS_MISSING"
    assert "mzML alone cannot produce PrSM interpretation" in captured.value.message
    assert inspect_source(source).source_type == "real_mzml"


@pytest.mark.parametrize(
    ("remove_name", "expected_code"),
    [
        ("run.mzML", "TOP_DOWN_SPECTRUM_SOURCE_MISSING"),
        ("run_ms2_toppic_prsm.xml", "PRSMUP_INPUT_XML_MISSING"),
        ("run_ms2.msalign", "PRSMUP_INPUT_MSALIGN_MISSING"),
    ],
)
def test_intermediate_required_roles_are_not_inferred(
    tmp_path: Path,
    remove_name: str,
    expected_code: str,
) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    target = next(source.rglob(remove_name))
    target.unlink()

    with pytest.raises(TopDownConversionError) as captured:
        SourceInspector().inspect([source])

    assert captured.value.code == expected_code


def test_multiple_mzml_runs_are_rejected(tmp_path: Path) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    shutil.copyfile(MZML_FIXTURE, source / "other.mzML")

    with pytest.raises(TopDownConversionError) as captured:
        SourceInspector().inspect([source])

    assert captured.value.code == "TOP_DOWN_MULTIPLE_RUNS_NOT_SUPPORTED"


def test_xml_msalign_pair_ambiguity_is_rejected(tmp_path: Path) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    xml = source / "toppic" / "run_ms2_toppic_prsm.xml"
    xml.write_text(xml.read_text(encoding="utf-8").replace("run_ms2.msalign", "unknown.msalign"), encoding="utf-8")
    second = source / "other_ms2.msalign"
    shutil.copyfile(source / "topfd" / "run_ms2.msalign", second)

    with pytest.raises(TopDownConversionError) as captured:
        SourceInspector().inspect([source])

    assert captured.value.code == "PRSMUP_INPUT_PAIR_AMBIGUOUS"


def test_intermediate_bundle_converts_through_fake_prsmup_and_p2_b1(
    tmp_path: Path,
) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "中文 中间包")
    script = write_fake_prsmup(tmp_path / "fake prsmup.py")
    target = tmp_path / "output" / "intermediate.zp"

    result = convert_source_to_zp(
        source,
        target,
        format_version=2,
        options=_options(tmp_path, script),
    )

    assert result.plan.source_type == "real_top_down_intermediate_bundle"
    assert "real_top_down_intermediate_parse" in result.plan.required_steps
    assert result.validation.valid is True
    assert result.validation.top_down_valid is True
    assert result.validation.top_down_issues == []
    assert open_zp(target).read_header().version == 2
    assert result.converter_name == "prsmup.py"
    assert result.converter_exit_code == 0
    assert result.cleanup_result == "removed"
    assert result.intermediate_path is None
    assert result.performance["interpretation_generated_prsm_count"] == 1
    assert not list((tmp_path / "temporary").glob("*"))
    summary = get_top_down_summary(target)
    assert summary["prsm_count"] == 1
    assert summary["proteoform_count"] == 1
    assert get_prsm(target, "1")["spectrum_id"] == "spectrum_000002"
    assert (
        get_prsm(target, "1")["source_fields"]["prsm_detail"]["value"][
            "viewer_unused_field"
        ]
        == "preserved-detail-value"
    )
    assert get_proteoform(target, "1")["best_prsm_id"] == "1"
    assert get_fragment_matches(target, "1")[0]["ion_type"] == "B"
    provenance = get_top_down_interpretation_provenance(target)
    assert provenance is not None
    assert provenance["interpretation_origin"] == "generated_from_toppic_topfd"
    assert provenance["generator_name"] == "prsmup.py"
    assert provenance["generated_prsm_count"] == 1
    assert provenance["toppic_prsm_xml_file_name"] == "toppic/run_ms2_toppic_prsm.xml"
    assert ":\\" not in json.dumps(provenance)


def test_prsmup_subprocess_calls_are_argument_lists_with_shell_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    script = write_fake_prsmup(tmp_path / "prsmup.py")
    target = tmp_path / "shell-false.zp"
    original_run = subprocess.run
    calls: list[tuple[object, object]] = []

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args[0], kwargs.get("shell")))
        return original_run(*args, **kwargs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr("binary_layer.top_down_interpretation_adapter.subprocess.run", run)

    convert_source_to_zp(
        source,
        target,
        format_version=2,
        options=_options(tmp_path, script),
    )

    assert len(calls) == 2
    assert all(isinstance(command, list) and shell is False for command, shell in calls)


def test_generated_interpretation_can_be_retained_outside_bundle(tmp_path: Path) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    script = write_fake_prsmup(tmp_path / "prsmup.py")
    target = tmp_path / "retained.zp"

    result = convert_source_to_zp(
        source,
        target,
        format_version=2,
        options=_options(tmp_path, script, keep=True),
    )

    assert result.cleanup_result == "retained"
    assert result.intermediate_path is not None
    assert result.intermediate_path.is_dir()
    assert list(result.intermediate_path.rglob("prsm1.js"))
    assert not list((tmp_path / "temporary").glob("*"))


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("nonzero", "PRSMUP_EXECUTION_FAILED"),
        ("missing", "PRSMUP_OUTPUT_MISSING"),
        ("empty", "PRSMUP_OUTPUT_EMPTY"),
        ("malformed", "PRSMUP_OUTPUT_MALFORMED"),
        ("duplicate", "PRSMUP_OUTPUT_DUPLICATE_ID"),
    ],
)
def test_prsmup_failures_have_stable_codes_and_no_pseudo_success(
    tmp_path: Path,
    mode: str,
    expected_code: str,
) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / mode)
    script = write_fake_prsmup(tmp_path / f"{mode}.py", mode=mode)
    target = tmp_path / f"{mode}.zp"

    with pytest.raises(TopDownConversionError) as captured:
        convert_source_to_zp(
            source,
            target,
            format_version=2,
            options=_options(tmp_path, script),
        )

    assert captured.value.code == expected_code
    assert not target.exists()
    assert not list(tmp_path.glob(".*.partial.zp"))
    assert not list((tmp_path / "temporary").glob("*"))


def test_prsmup_timeout_is_stable_and_cleans_working_directory(tmp_path: Path) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    script = write_fake_prsmup(tmp_path / "timeout.py", mode="timeout")

    with pytest.raises(TopDownConversionError) as captured:
        convert_source_to_zp(
            source,
            tmp_path / "timeout.zp",
            format_version=2,
            options=_options(tmp_path, script, timeout=0.1),
        )

    assert captured.value.code == "PRSMUP_EXECUTION_TIMEOUT"
    assert not list((tmp_path / "temporary").glob("*"))


def test_missing_script_and_python_are_rejected_before_execution(tmp_path: Path) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    bundle = TopDownInterpretationAdapter().inspect_bundle(source)
    adapter = TopDownInterpretationAdapter()

    with pytest.raises(TopDownConversionError) as missing_script:
        adapter.generate(bundle, adapter.options_from_conversion(_options(tmp_path, tmp_path / "missing.py")))
    assert missing_script.value.code == "PRSMUP_SCRIPT_NOT_FOUND"

    script = write_fake_prsmup(tmp_path / "prsmup.py")
    options = _options(tmp_path, script)
    missing_python = ConversionOptions(
        requested_conversion_kind="top_down",
        top_down_interpreter_script=script,
        python_executable=tmp_path / "missing-python.exe",
        temporary_directory=options.temporary_directory,
    )
    with pytest.raises(TopDownConversionError) as captured:
        adapter.generate(bundle, adapter.options_from_conversion(missing_python))
    assert captured.value.code == "PRSMUP_EXECUTION_FAILED"


def test_generated_prsm_with_unknown_spectrum_reference_is_rejected(tmp_path: Path) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    script = write_fake_prsmup(tmp_path / "bad-scan.py", scan_number=999)

    with pytest.raises(TopDownConversionError) as captured:
        convert_source_to_zp(
            source,
            tmp_path / "bad-scan.zp",
            format_version=2,
            options=_options(tmp_path, script),
        )

    assert captured.value.code == "PRSMUP_OUTPUT_SPECTRUM_REFERENCE_INVALID"


def test_top_pic_modification_loss_is_rejected(tmp_path: Path) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    xml = source / "toppic" / "run_ms2_toppic_prsm.xml"
    xml.write_text(
        xml.read_text(encoding="utf-8").replace(
            "</mass_shift_list>",
            "<mass_shift /></mass_shift_list>",
        ),
        encoding="utf-8",
    )
    script = write_fake_prsmup(tmp_path / "prsmup.py")

    with pytest.raises(TopDownConversionError) as captured:
        convert_source_to_zp(
            source,
            tmp_path / "modification-loss.zp",
            format_version=2,
            options=_options(tmp_path, script),
        )

    assert captured.value.code == "PRSMUP_OUTPUT_MALFORMED"
    assert captured.value.details == {
        "xml_modification_count": 2,
        "generated_modification_count": 1,
        "mismatched_prsm_ids": ["1"],
        "per_prsm_counts": {"1": {"xml": 2, "generated": 1}},
    }


def test_modification_counts_match_xml_generated_js_and_final_blocks_by_prsm(
    tmp_path: Path,
) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    xml = source / "toppic" / "run_ms2_toppic_prsm.xml"
    _write_two_prsm_xml(xml, (2, 1))
    script = _write_multi_prsmup(tmp_path / "prsmup.py", generated_counts=(2, 1))
    target = tmp_path / "complete-modifications.zp"

    result = convert_source_to_zp(
        source,
        target,
        format_version=2,
        options=_options(tmp_path, script, keep=True),
    )

    assert result.intermediate_path is not None
    profile = inspect_source(source)
    pair = profile.top_down_intermediate_bundle.input_pairs[0]
    xml_counts = dict(pair.modification_counts_by_prsm)
    generated_counts = _generated_modification_counts(result.intermediate_path)
    block_counts = {
        prsm_id: len(get_proteoform(target, prsm_id)["modification_ids"])
        for prsm_id in ("1", "2")
    }
    assert xml_counts == generated_counts == block_counts == {"1": 2, "2": 1}
    assert pair.modification_count == get_top_down_summary(target)["modification_count"] == 3
    shutil.rmtree(result.intermediate_path)


def test_equal_global_modification_count_cannot_hide_per_prsm_mismatch(
    tmp_path: Path,
) -> None:
    source = build_top_down_intermediate_bundle(tmp_path / "bundle")
    xml = source / "toppic" / "run_ms2_toppic_prsm.xml"
    _write_two_prsm_xml(xml, (2, 1))
    script = _write_multi_prsmup(tmp_path / "prsmup.py", generated_counts=(1, 2))

    with pytest.raises(TopDownConversionError) as captured:
        convert_source_to_zp(
            source,
            tmp_path / "per-prsm-mismatch.zp",
            format_version=2,
            options=_options(tmp_path, script),
        )

    assert captured.value.code == "PRSMUP_OUTPUT_MALFORMED"
    assert captured.value.details == {
        "xml_modification_count": 3,
        "generated_modification_count": 3,
        "mismatched_prsm_ids": ["1", "2"],
        "per_prsm_counts": {
            "1": {"xml": 2, "generated": 1},
            "2": {"xml": 1, "generated": 2},
        },
    }
