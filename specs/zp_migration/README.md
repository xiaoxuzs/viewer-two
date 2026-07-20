# P1-B8.6 v1 to v2 migration gate

This directory freezes the offline, non-in-place v1 to v2 migration contract.
The Full and Minimal migration Goldens intentionally reuse the P1-B8.5
complete-file fixtures: a successful migration must reproduce the paired v2
Golden byte for byte while leaving the v1 input unchanged.

The production migration path is:

```text
validated v1 source
-> bounded-memory canonical JSON arrays scan
-> one float64 payload spool
-> sibling temporary v2 file
-> full v2 validation
-> independent logical fingerprint comparison
-> source identity/hash recheck
-> atomic os.replace commit
```

Run one migration:

```text
python -m binary_layer.migration --input source-v1.zp --output target-v2.zp --json
```

Run the unified P1-B8.6 release gate:

```text
python specs/zp_migration/compatibility_gate.py
```

The default Writer and Pipeline remain v1. This tool never migrates in place,
never overwrites an existing destination, and never calls
`ZpReader.read_arrays()`.

Post-P1 phases may extend source inspection, planning, Registry bindings, and
source-specific Tools. The migration freeze therefore permits the explicitly
listed P2 integration modules in `compatibility_gate.py`, while continuing to
hash-protect Writer, Reader, both Validators, migration code, arrays codecs,
serialization, Runner, and the frozen format constants.

For the 31,408,514-byte acceptance sample only, the gate also measures one
complete `ZpReader.read_arrays()` -> `BlockCollection` -> v2 Writer reference
run. That reference is never used by `migrate_v1_to_v2`; the gate requires the
streaming conversion RSS peak to be at most 80% of the reference peak and
requires both outputs to be byte-identical to the direct v2 Writer output.

CLI exit codes are stable:

| Code | Meaning |
|---:|---|
| 0 | migration committed successfully |
| 2 | arguments, path safety, or existing destination |
| 3 | source read/layout/version/full-validation failure |
| 4 | conversion, spool, temporary-write, or unexpected internal failure |
| 5 | resource preflight failure or interruption |
| 6 | temporary target full-validation failure |
| 7 | logical fingerprint failure |
| 8 | source changed or atomic commit failed |
