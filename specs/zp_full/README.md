# P1-B8.5 complete-file compatibility fixtures

`fixtures/` freezes deterministic Full and Minimal complete-file Goldens for
both ZP v1 and explicit ZP v2. The paired files are written from the same
logical `BlockCollection`; only the allowed version-specific physical fields
differ.

`inspect_full_zp.py` is a standard-library-only independent checker. It does
not import the production Writer, Reader, Validator, v2 arrays implementation,
Registry, Pipeline, or the P1-B7 reference Codec. It independently checks the
24-byte Header, canonical EOF directory, nine ordered blocks, top-level
checksums, canonical JSON, v1 JSON arrays, v2 binary arrays Header/directory,
zero padding, float64 payloads, per-array checksums, and core business
relationships.

Regenerate or verify from the repository root:

```text
python specs/zp_full/build_full_golden_fixtures.py
python specs/zp_full/build_full_golden_fixtures.py --check
python specs/zp_full/inspect_full_zp.py specs/zp_full/fixtures/valid_full_v2.zp
```

Pytest only verifies committed bytes and never rewrites these files.
