from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpValidator
from specs.zp_full.inspect_full_zp import inspect_full_zp
from specs.zp_full.logical_model import LogicalZpDocument, logical_equivalence
from zp_compatibility_support import write_pair
from zp_v2_writer_support import build_real_blocks


REAL_FIXTURES = (
    ("ms1_only", "accept_ms1_only_indexed_float64_zlib.mzML"),
    ("ms1_ms2", "accept_ms2_precursor_metadata.mzML"),
    ("tic_bpc", "accept_tic_bpc_chromatograms.mzML"),
)


@pytest.mark.parametrize(("_kind", "fixture"), REAL_FIXTURES, ids=[item[0] for item in REAL_FIXTURES])
def test_real_mzml_block_collection_has_exact_v1_v2_logical_parity(
    _kind: str,
    fixture: str,
    tmp_path: Path,
) -> None:
    output = tmp_path / _kind
    output.mkdir()
    paths = write_pair(output, build_real_blocks(fixture))
    reports = {version: inspect_full_zp(path) for version, path in paths.items()}
    models = {version: LogicalZpDocument.from_inspection(report) for version, report in reports.items()}
    results = {version: ZpValidator().validate(path) for version, path in paths.items()}
    equivalence = logical_equivalence(models[1], models[2])

    assert all(item.valid and item.checked_blocks == 9 and item.issues == [] for item in results.values())
    assert all(equivalence.values()), equivalence
    assert reports[1]["statistics"] == reports[2]["statistics"]
    assert [item["values"] for item in models[1].arrays] == [item["values"] for item in models[2].arrays]
