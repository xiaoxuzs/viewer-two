# P1-B8.7 real mzML matrix

This gate discovers no samples and downloads nothing. Operators pass local,
read-only real mzML files explicitly. Paths are used only during the process;
the permanent JSON contains a caller-provided sample ID, file name, size, and
SHA-256, never an absolute directory.

Run from the repository root:

```text
python specs/zp_real_matrix/run_real_matrix.py \
  --sample sample-a=E:\path\a.mzML \
  --sample sample-b=E:\path\b.mzML \
  --sample sample-c=E:\path\c.mzML \
  --run-pytest \
  --run-existing-gates
```

Admission inspection is repeated against the same immutable feature profile.
Rejected files never enter Inspector/Plan/Registry/Runner conversion and get
no `.zp` or migration temporary file. Accepted files run the production plan
through `index_build`, then the same `BlockCollection` is written explicitly
as v1 and v2. Both are fully validated and fingerprinted; v1 is migrated, the
migrated file must equal direct v2 byte for byte, and fixed-seed Reader samples
compare up to 100 Spectra, Arrays, and Precursors plus all small
Chromatogram sets. Outputs live under a temporary directory and are removed.

The gate records timings and RSS only. It does not use `tracemalloc`, set a
performance release threshold, change the default format, or integrate
Viewer. At least three distinct source SHA-256 values and all required
accepted-sample coverage tags are mandatory; otherwise the result is:

```text
release_gate=false
reason=insufficient_real_sample_matrix
```

Deterministic test Fixtures may exercise the runner helpers but never count as
real samples in `results/real_matrix_summary.json`.
