from specs.zp_real_matrix.run_real_matrix import _production_hashes


def test_production_hash_snapshot_is_complete_and_repeatable() -> None:
    before = _production_hashes()
    after = _production_hashes()
    assert before == after
    assert "binary_layer/logical_fingerprint.py" in before
    assert "binary_layer/migration.py" in before
    assert "binary_layer/v1_arrays_stream_reader.py" in before
    assert "binary_layer/v2_arrays_migration_writer.py" in before
    assert all(len(value) == 64 for value in before.values())
