# P1-B8.5 GlobalMeta count-parity failure Fixture

These two files come from the same valid logical `BlockCollection`. After
writing, only `global_meta.run_count` is changed from `1` to `0`; the canonical
block bytes, block SHA-256, and canonical top-level directory are updated so
no physical corruption masks the domain rule.

P1-B8.5R2 corrected the production v1 Validator omission. Both files now
reject with `COUNT_MISMATCH` and nine checked blocks, while the committed
Fixture bytes remain unchanged. This is permanent regression evidence, not a
completed full-file Golden release gate; P1-B8.5 still requires a full rerun.

Rebuild or check the evidence with:

```text
python specs/zp_full/build_global_meta_count_failure.py
python specs/zp_full/build_global_meta_count_failure.py --check
```
