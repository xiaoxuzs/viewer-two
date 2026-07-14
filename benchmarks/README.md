# P1-B6 benchmark tools

These scripts measure the frozen `.zp` v1 production path without changing `binary_layer/`. Large generated mzML and `.zp` files belong under `benchmarks/generated/` and are ignored. Only the compact aggregate `results/p1_b6_summary.json` is retained after acceptance.

## Commands

Generate a deterministic supported scale sample:

```powershell
python -m benchmarks.generate_scale_mzml --spectrum-count 128 --peaks-per-spectrum 256 --ms2-ratio 0.5 --include-tic --dtype float64 --compression zlib --indexed --output benchmarks/generated/S1.mzML
```

Run one isolated conversion with hard resource limits:

```powershell
python -m benchmarks.benchmark_mzml_conversion --input benchmarks/generated/S1.mzML --input-kind synthetic --run-label S1 --result benchmarks/results/S1.json --max-rss-gb 6 --max-runtime-seconds 600 --max-output-gb 2
```

`benchmark_mzml_conversion` starts a fresh worker process. The parent enforces runtime, RSS and output limits; a breach records `RESOURCE_LIMIT_REACHED`, terminates the point, removes large output and prevents an orchestration layer from continuing to larger points. RSS uses optional `psutil`, with a Windows `GetProcessMemoryInfo` fallback. `tracemalloc` snapshots list the top 20 Python allocation sites but do not fully capture NumPy/native allocations. Snapshot wall time is reported separately and excluded from adjusted pipeline/Writer timings.

Run cold Reader operations, one fresh process per operation:

```powershell
python -m benchmarks.benchmark_zp_read --zp benchmarks/generated/sample.zp --result benchmarks/results/reader.json
```

Run the independent encoding comparison (never calls `ZpWriter`):

```powershell
python -m benchmarks.benchmark_array_encodings --zp benchmarks/generated/sample.zp --result benchmarks/results/array_encodings.json
```

Aggregate measured JSON files and verify frozen production hashes:

```powershell
python -m benchmarks.summarize_results --after-tests 262
```

## Measurement boundaries

- Production steps are retrieved from the existing `PlanBuilder`/`StepRegistry` and executed in plan order.
- Benchmark-only wrappers time existing parser/admission/candidate/serialization/validation functions; they do not change their values or branch behavior.
- Validator block checksum and JSON decode costs are also replayed block-by-block for attribution; the production Validator still runs in full.
- `read_spectrum_arrays` remains a full spectra/arrays JSON parse plus an in-memory ID-map build on every call. These results are not disk-level random I/O.
- Linear fits are descriptive within the measured range. Extrapolated 5M-100M peak rows are estimates, not tests.

