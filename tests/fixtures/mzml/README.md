# P1-B1 mzML fixtures

These tiny files are deterministic structural fixtures for Pyteomics compatibility and domain-admission tests. They are not production samples and are not converted to Blocks or `.zp`.

- `accept_indexed_float64_zlib.mzML`: indexed MS1/MS2, float64 and zlib.
- `accept_nonindexed_float32_uncompressed.mzML`: non-indexed MS1/MS2, float32 and no compression.
- `accept_tic_bpc_chromatograms.mzML`: TIC and BPC; TIC includes the observed auxiliary `MS:1000786` value-name `ms level`, int64, dimensionless array.
- `reject_*.mzML`: one focused rejection boundary per file.

`MS:1000786` is the generic PSI-MS `non-standard data array` term. The schema whitelist therefore also requires the exact semantic value-name `ms level`, chromatogram ownership, int64 dtype, and a declared unit; it does not admit arbitrary arrays using the same accession.

Regenerate deterministically from the repository root:

```bash
python tests/fixtures/mzml/build_fixtures.py
```

The builder uses only Python's standard library, never imports conversion code, and never writes `.zp`.
