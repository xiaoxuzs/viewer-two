# P1-B8.5R3 candidate parity failure Fixtures

The R2 read-only probes found three domain-rule differences after the
GlobalMeta correction:

- The preserved Run-owned Spectrum count mismatch now returns
  `COUNT_MISMATCH` in both v1 and v2 after P1-B8.5R3A.
- The preserved missing required StringPool reference now returns
  `INVALID_REFERENCE` in both v1 and v2 after P1-B8.5R3B.
- The preserved non-bidirectional Precursor link now returns two ordered
  `INVALID_REFERENCE` issues in both v1 and v2 after P1-B8.5R3C: first the
  Spectrum-to-Precursor direction, then the Precursor-to-Spectrum direction.

Each pair starts from the same valid logical `BlockCollection`. One
length-preserving JSON mutation is applied and the affected block checksum and
top-level directory are canonically updated, so physical corruption does not
mask the domain difference. These are failure evidence, not accepted Goldens.
R3A changes only v1 Run statistics validation, R3B changes only v1 StringPool
required-reference validation, and R3C changes only v1 Precursor relationship
validation. P1-B8.5 still requires a complete compatibility and Golden rerun;
P1-B8.6 has not started. All six Fixture byte hashes remain unchanged.

Rebuild or check the evidence with:

```text
python specs/zp_full/build_candidate_parity_failures.py
python specs/zp_full/build_candidate_parity_failures.py --check
```
