# P2-B2 TopPIC/TopFD interpretation generation report

## 1. Conclusion

P2-B2.1 passed: the `prsmup.py` multiple-mass-shift truncation defect is fixed
at the generator, and the isolated PXD045330 diagnostic now reaches complete
44/44 PrSM and 43/43 Modification coverage, including equality for every
individual PrSM. P2-B2 implementation remains complete, but its formal
real-data acceptance is still blocked:

```text
reason=independent_real_bundle_not_found
```

The bounded local search found no complete mzML + TopPIC XML + TopFD MSALIGN
bundle different from PXD045330. PXD045330 remains diagnostic evidence, not a
second independent real acceptance dataset. No Bottom-Up or `.d` work was
started.

## 2. prsmup.py investigation

- Located script: `E:\viewer\mzml-demo\scripts\prsmup.py`
- Pre-fix SHA-256:
  `8a3190998ce4df0bcafafd25c6104f2d500ee475b723ec0ad027d0ae48bc7556`
- Fixed SHA-256:
  `efe60153c902a389f2719a9b7758c1beadb3cd3ff06f67d7c7012cb12a6d94cc`
- Entry: guarded `main()` CLI; importing does not execute conversion.
- Runtime: Python 3.10+ syntax; repository runtime is Python 3.12.7.
- Dependencies: Python standard library only (`argparse`, `json`,
  `xml.etree.ElementTree`, `pathlib`, `typing`).
- Required parameters: `--prsm-xml`, `--msalign`, and `--out-dir`.
- Optional parameters: `--tolerance-ppm` (default 10) and `--limit` (default
  10). The adapter explicitly supplies the complete XML PrSM count as
  `--limit`.
- Input granularity: one XML file and one MSALIGN file per invocation; it does
  not accept an input directory.
- Output: one `prsm<prsm_id>.js` per exported PrSM in `--out-dir`; an existing
  same-name file is overwritten by `Path.write_text`.
- CWD: no fixed input/output path and no functional CWD dependency.
- Determinism: no random, time, UUID, or unordered output field. Entries are
  stably sorted by E-value before export. The direct diagnostic and the Service
  invocation produced the same 44-file set with identical per-file SHA-256.
- Spectrum link: XML `spectrum_scan` is looked up against MSALIGN `SCANS`.
- Peaks: deconvoluted mass/intensity/charge come from MSALIGN.
- Matched ions: b/y masses and peak matches are recalculated by the script.
- Fixed defect: `build_prsm_js()` formerly selected only
  `entry["mass_shifts"][0]`. It now creates one independent object for every
  mass shift, assigns a stable per-PrSM ordinal ID, and preserves XML order.
  Zero-shift PrSMs still omit `mass_shift`; one-shift PrSMs still use the legacy
  single-object shape; multiple shifts use the P2-B1-compatible object list.

## 3. Bounded real-data search

The search was limited to `E:\viewer\shuju`, `E:\viewer-two-data`, and
`E:\飞书`.

| Root | TopPIC XML | TopFD MSALIGN | mzML | Complete non-PXD bundle |
|---|---:|---:|---:|---|
| Viewer data | 1 | 2 | 1 | no |
| viewer-two-data | 0 | 0 | 4 | no |
| Feishu data | 0 | 0 | 1 | no |

The only complete triple belongs to PXD045330, which the P2-B2 acceptance
criteria explicitly exclude. The histone49 tree has Top-Down result files but
no RAW or mzML spectrum source and is therefore not a complete P2-B2 input.

## 4. PXD045330 diagnostic input evidence (not formal acceptance)

An isolated temporary view excluded all existing `prsm*.js` files and used
only these three roles:

| Role | Size | SHA-256 |
|---|---:|---|
| mzML | 31,408,514 | `4b0293098e072384bd20d296e7a6e26cf7736a88124361128dd418151dd521d8` |
| TopPIC XML | 139,891 | `4ca8c85f61ccef8779488accabdd28093ac3b55224a190b326c24e5f1ffb24a0` |
| TopFD MSALIGN | 819,470 | `1487624fea5310043ed2bb94f4175014f053c38634005e5ae2e8e6774aeca83c` |

Classification was `source_type=real_top_down_intermediate_bundle`. The XML
contains 44 PrSMs and 43 mass shifts; MSALIGN contains 1,051 spectra. With an
explicit `--limit=44`, the fixed real script exited 0 and generated 44 JS
files, 496 Fragment matches, and 2,664 peaks. PrSM ID coverage is 100%, every
generated scan exists in MSALIGN, and all 44 PrSM-to-Spectrum associations are
valid.

The three-way completeness result is:

| Level | Modification count | Per-PrSM comparison |
|---|---:|---|
| TopPIC XML | 43 | reference |
| Generated JS | 43 | all 44 PrSM counts equal XML |
| Final Top-Down block | 43 | all 44 PrSM counts equal XML |

The generated document contains 44 Proteoforms, 44 PrSMs, 43 Modifications,
44 Features, 496 Fragment matches, 2,664 peaks, and 44 associated Spectra.
The temporary diagnostic v2 `.zp` was 46,912,804 bytes with SHA-256
`0916e216dbe3532025457898cb5e2e6f54cc11d337cd13f68c47bdd050af6075`.
Physical validation returned `valid=true`, `checked_blocks=9`, `issues=[]`;
Top-Down validation returned `valid=true`, seven Extensions, and `issues=[]`.
The unified Validator was also clean.

The provenance Extension records
`interpretation_origin=generated_from_toppic_topfd`,
`generator_name=prsmup.py`, Python 3.12.7, and the fixed generator SHA above.
It contains no absolute source or temporary path.

## 5. Generator regression and binary-layer gates

The generator repository now has standard-library tests using only temporary
fixtures. They cover zero, one, and three mass shifts; the legacy one-shift
object contract; same-mass/different-position preservation;
same-position/different-mass preservation; mixed PrSMs; `--limit` by PrSM;
independent objects; and byte-for-byte deterministic output.

The binary layer retains the global Modification count gate and adds an XML
per-PrSM count index to `TopDownInterpretationInputPair`. Generated block
counts are checked by PrSM. A regression proves that an equal global total
cannot hide a swapped 2/1 versus 1/2 distribution; it still fails with
`PRSMUP_OUTPUT_MALFORMED` and path-free per-PrSM details.

## 6. Input classification and pairing

Recognition priority is:

1. mzML plus supported precomputed `prsm*.js` tree ->
   `real_top_down_bundle`;
2. no precomputed JS, but mzML + TopPIC XML + TopFD MSALIGN ->
   `real_top_down_intermediate_bundle`;
3. ordinary mzML -> `real_mzml`, unless full Top-Down was explicitly
   requested;
4. result-only tree without RAW/mzML ->
   `TOP_DOWN_SPECTRUM_SOURCE_MISSING`.

Intermediate discovery reads only root files and one child-directory level.
It pairs by XML `file_name`, then XML scan-set coverage, normalized common
basename, and finally a unique candidate. Multiple runs and unresolved or
ambiguous pairs fail closed.

## 7. Implementation

- `top_down_interpretation_schema.py`: intermediate bundle, pair, options,
  execution result, artifact, provenance v1 models, and frozen per-PrSM XML
  Modification counts.
- `top_down_interpretation_adapter.py`: depth-one discovery, content pairing,
  isolated secure execution, complete-ID checks, hashing, retention, cleanup,
  and stable errors.
- `tools/real_top_down_intermediate.py`: invokes the interpreter, constructs an
  internal P2-B1-compatible bundle, delegates to `RealTopDownTool`, replaces
  precomputed provenance with generated provenance, validates both global and
  per-PrSM Modification completeness, and returns only blocks.
- `inspector.py`, `models.py`, `plan.py`, `registry.py`, `service.py`, and
  `tools/common.py`: unified entry, profile, plan, registration, aggregate
  identity, and execution report integration.
- `top_down_reader.py` and `top_down_validator.py`: provenance reader and
  optional backward-compatible business validation.
- `tests/test_top_down_interpretation.py`: discovery, security, failure,
  cleanup, Reader, global/per-PrSM completeness, and end-to-end Fixture gates.

The P2-B1 `TopDownProteoform`, `TopDownPrsm`, `TopDownModification`,
`TopDownFragmentMatch`, and `TopDownFeature` schemas were not replaced or
forked. Writer, physical Reader dispatch, physical Validators, arrays codecs,
Header, and nine-block ordering were not modified.

## 8. Public call

```python
from pathlib import Path

from binary_layer import ConversionOptions, convert_source_to_zp

result = convert_source_to_zp(
    source_path=Path("top-down-intermediate-bundle"),
    target_path=Path("top-down.zp"),
    format_version=2,
    options=ConversionOptions(
        requested_conversion_kind="top_down",
        top_down_interpreter_script=Path("prsmup.py"),
        python_executable=Path("python.exe"),
        temporary_directory=Path("temporary"),
        generated_interpretation_directory=Path("generated"),
        keep_generated_interpretation=True,
        interpretation_timeout_seconds=3600,
    ),
)
```

No separate public `convert_toppic_xml_to_zp()` entry exists.

## 9. Stable failure behavior

The implementation covers missing script/XML/MSALIGN/Python, ambiguous pair,
multiple run, nonzero exit, timeout, missing/empty/malformed output, duplicate
ID, incomplete XML ID coverage, invalid Spectrum reference, global or
per-PrSM Modification loss/misassignment, and cleanup failure. The
`PRSMUP_OUTPUT_MALFORMED` gate was retained and strengthened. Service writes
to a sibling partial `.zp`, validates it, and commits only after success; all
tested failures leave no target or partial success file.

## 10. Reader and provenance

The existing summary, PrSM, Proteoform, Spectrum-link, and Fragment helpers
read generated interpretations unchanged. New
`get_top_down_interpretation_provenance(path)` reports origin, generator,
script/Python identity, intermediate input hashes, and generated artifact
hashes. Provenance contains no absolute path, drive, user, current time,
temporary token, full command, or random UUID.

The real Reader sample for PrSM 8 resolved `spectrum_000237`, Proteoform 8,
two ordered Modifications (`[0,1)` +42.010565 Da and `[35,72)`
-147.8440228954 Da), and nine Fragment matches. The raw
`unexpected_shift_number=2` remains available in `source_fields`.

## 11. Diagnostic dual-chain comparison

This comparison is diagnostic and is not a second independent real-data
acceptance:

- The fixed generator produced the same 44 JS files and hashes when invoked
  directly and through the unified Service.
- The spectrum, precursor, chromatogram, index, and arrays blocks matched;
  all arrays hashes were equal. The Run and StringPool exact block hashes
  differed only because the isolated view labels the mzML by filename while
  the original package labels the same file through one containing directory.
  All other Run fields were equal.
- Existing Viewer `prsm*.js` contains 30 PrSMs, 23 Modifications, 392 Fragment
  matches, and 30 associated Spectra. The fixed generated chain contains
  44/43/496/44 respectively. The generated chain therefore adds 14 XML PrSMs
  absent from the existing result tree.
- For the 30 shared PrSMs, every Spectrum association and all 392 shared
  Fragment scientific records matched. The existing JS lacks the second
  mass shift for shared PrSM IDs 10, 16, 36, and 39. The remaining generated
  Modification differences belong to PrSMs absent from the existing JS tree.
- Precomputed TSV enrichment supplies adjusted mass, theoretical mass/mass
  error, and Feature ID/score/apex RT fields that are not present in the
  XML+MSALIGN generated JS path. Those field-level differences are recorded;
  they were not deleted or normalized to claim Top-Down logical equality.

## 12. Architecture boundary

```text
Tool writes zp=false
single Writer maintained=true
physical format changed=false
default version changed=false
second Top-Down schema created=false
Viewer code modified=false
Bottom-Up started=false
.d started=false
```

No Writer, physical Reader/Validator dispatch, Header, nine-block order,
arrays encoding, P2-B1 entity Schema, or default version was changed.

## 13. Phase status

```text
P2-B2 implementation completed
P2-B2 generator defect fixed
P2-B2 PXD045330 diagnostic passed
P2-B2 formal real acceptance blocked
reason=independent_real_bundle_not_found
```

The temporary diagnostic `.zp`, copied input view, and generated JS are
diagnostic artifacts only and are removed after evidence collection. Final
test and Git evidence is recorded in the task handoff.
