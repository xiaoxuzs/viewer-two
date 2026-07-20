from specs.zp_real_matrix.run_real_matrix import REQUIRED_COVERAGE, _version_state, evaluate_matrix


def _gates() -> dict[str, object]:
    return {
        "pytest": {"passed": True},
        "b8_5": {"release_gate": True},
        "b8_6": {"release_gate": True},
    }


def test_insufficient_unique_real_files_fail_closed() -> None:
    samples = [
        {
            "sample_id": "one",
            "source_sha256": "a" * 64,
            "admission": "accepted",
            "coverage_tags": list(REQUIRED_COVERAGE),
            "passed": True,
        },
        {
            "sample_id": "copy",
            "source_sha256": "a" * 64,
            "admission": "accepted",
            "coverage_tags": list(REQUIRED_COVERAGE),
            "passed": True,
        },
    ]
    result = evaluate_matrix(samples, True, _gates())
    assert result["release_gate"] is False
    assert result["reason"] == "insufficient_real_sample_matrix"
    assert result["counts"]["available"] == 1


def test_rejected_sample_must_be_stable_and_artifact_free() -> None:
    rejected = {
        "sample_id": "rejected",
        "source_sha256": "b" * 64,
        "admission": "rejected",
        "coverage_tags": [],
        "admission_stable": True,
        "conversion_attempted": False,
        "artifacts_created": False,
        "passed": True,
    }
    result = evaluate_matrix([rejected], True, _gates())
    assert result["rejected_samples_stable"] is True
    rejected["artifacts_created"] = True
    assert evaluate_matrix([rejected], True, _gates())["rejected_samples_stable"] is False


def test_version_state_remains_frozen() -> None:
    state = _version_state()
    assert state == {
        "ZP_VERSION": 1,
        "DEFAULT_ZP_WRITE_VERSION": 1,
        "SUPPORTED_ZP_WRITE_VERSIONS": [1, 2],
        "SUPPORTED_ZP_READ_VERSIONS": [1, 2],
        "SUPPORTED_ZP_VALIDATE_VERSIONS": [1, 2],
        "KNOWN_ZP_VERSIONS": [1, 2],
        "default_format_remains_v1": True,
        "viewer_integration_started": False,
        "performance_tuning_started": False,
    }
