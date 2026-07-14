from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpWriter
from binary_layer.exceptions import ZpWriteError


def test_mid_write_failure_removes_tmp_and_preserves_existing_target(
    pipeline_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = pipeline_factory(".mzML").blocks
    target = tmp_path / "existing.zp"
    target.write_bytes(b"existing target")

    def fail_after_temp_created(stream, _layout):
        stream.write(b"partial arrays")
        raise OSError("injected write failure")

    monkeypatch.setattr("binary_layer.writer.write_v2_arrays_block", fail_after_temp_created)
    with pytest.raises(ZpWriteError, match="injected write failure"):
        ZpWriter().write(target, blocks, format_version=2)

    assert target.read_bytes() == b"existing target"
    assert not target.with_name(target.name + ".tmp").exists()


def test_mid_write_failure_leaves_absent_target_absent(
    pipeline_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "absent.zp"
    monkeypatch.setattr("binary_layer.writer.write_v2_arrays_block", lambda *_args: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(ZpWriteError):
        ZpWriter().write(target, pipeline_factory(".mzML").blocks, format_version=2)
    assert not target.exists()
    assert not target.with_name(target.name + ".tmp").exists()
