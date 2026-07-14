# ZP v2 arrays reference materials

This directory is a specification aid for P1-B7. It is not imported by
`binary_layer`, is not registered as a pipeline step, and does not write a
complete `.zp` file.

- `arrays_reference_codec.py` encodes, decodes, randomly reads, and validates
  only a version-2 `arrays` block.
- `build_golden_fixtures.py` deterministically rebuilds the two committed
  fixtures and their manifest.
- `inspect_arrays_block.py` prints a JSON inspection report for one arrays
  block.
- `fixtures/` contains specification fixtures, not production `.zp` files.

Run from the repository root:

```powershell
python specs/zp_v2/build_golden_fixtures.py
python specs/zp_v2/inspect_arrays_block.py specs/zp_v2/fixtures/valid_arrays_v2.bin
```

