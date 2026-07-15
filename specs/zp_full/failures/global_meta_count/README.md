# P1-B8.5 GlobalMeta count-parity failure Fixture

These two files come from the same valid logical `BlockCollection`. After
writing, only `global_meta.run_count` is changed from `1` to `0`; the canonical
block bytes, block SHA-256, and canonical top-level directory are updated so
no physical corruption masks the domain rule.

The production v1 Validator accepts the v1 file with no issues. The production
v2 Validator rejects the v2 file with `COUNT_MISMATCH`. This is a new semantic
drift found during the P1-B8.5 rerun, so the release gate remains stopped until
a separate production-correction stage aligns the validators.

Rebuild or check the evidence with:

```text
python specs/zp_full/build_global_meta_count_failure.py
python specs/zp_full/build_global_meta_count_failure.py --check
```
