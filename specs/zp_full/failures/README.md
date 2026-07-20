# P1-B8.5 audit failure Fixture

These two small files come from the same logical `BlockCollection`. Their only
domain corruption is a `mzml_auxiliary_arrays` record whose `owner_id` is
`missing_chromatogram` while the document contains no Chromatogram.

The P1-B8.5 audit originally found that the production v1 Validator accepted
the v1 file while the production v2 Validator reported `INVALID_REFERENCE`.
P1-B8.5R corrected the v1 omission: both files now report
`INVALID_REFERENCE`, with nine checked blocks, while the Fixture bytes remain
unchanged.

This directory remains the permanent regression evidence, not an accepted
full-file Golden release gate. P1-B8.5 remains incomplete until its complete
compatibility suite is rerun. Rebuild or check the evidence with:

```text
python specs/zp_full/build_extension_owner_failure.py
python specs/zp_full/build_extension_owner_failure.py --check
```

`candidate_parity/` records the three additional read-only probe failures
found after P1-B8.5R2: Run-owned counts, StringPool required references, and
bidirectional Precursor links. R3A corrected Run counts and R3B corrected
StringPool references without changing Fixture bytes. Bidirectional Precursor
parity remains unresolved and blocks the B8.5 rerun pending R3C. Check them
with:

```text
python specs/zp_full/build_candidate_parity_failures.py --check
```
