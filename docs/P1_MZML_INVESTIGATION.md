# P1-A real mzML investigation

Status: **P1-A decision record; no real mzML production support has been implemented.**

Date: 2026-07-13 (Asia/Shanghai)

## 1. Investigation goal

Determine, from the frozen `.zp` version-1 code, one real mzML sample, the PSI mzML schema/CV, and parser behavior, which mzML concepts are:

- directly representable;
- representable after explicit normalization;
- preservable in a versioned standard extension;
- rejected by the P1-B support profile;
- structurally dependent on a future v2; or
- still unverified by a real sample.

P1-A does not implement `RealMzmlParseTool`, change `SourceInspector`, add a pipeline step, alter a core block, change the `.zp` format, or modify Writer/Reader/Validator behavior.

## 2. Frozen P0 v1 contract

The baseline remains `ZP format version 1 prototype baseline`: `ZPMS`, version 1, `<4sHBBQQ>`, nine required blocks, trailing directory, exact-byte SHA-256, JSON-list arrays, array-ID-only Spectrum references, seconds for RT, required integer scan number and charge, single input/run/output, BlockTool-only block mutation, one Writer, and business-free Runner/Registry.

P1-B may use the existing `extensions` block only through a documented extension schema with its own version. It may not reinterpret a v1 core field. Nullable scan/charge, one-to-many precursor links, new core array types, or changed block meaning require a format-version decision.

## 3. Investigation environment

- Workspace: `E:\viewer-two`
- Python: 3.12.7
- Baseline: `python -m pytest` -> 42 passed, 0 failed, 0 skipped
- Installed investigation parser: Pyteomics 4.7.5
- Installed XML dependency: lxml 6.1.1
- Not installed: pymzML
- Existing Viewer backend declaration: `pyteomics>=4.7.5`, `lxml>=6.1.0`
- Read-only probe: `scripts/probe_mzml.py`

Primary sources consulted on 2026-07-13:

- [HUPO-PSI mzML repository](https://github.com/HUPO-PSI/mzML)
- [PSI mzML 1.1.1 schema](https://raw.githubusercontent.com/HUPO-PSI/mzML/master/schema/schema_1.1/mzML1.1.1.xsd)
- [PSI indexed mzML 1.1.1 schema](https://github.com/HUPO-PSI/mzML/blob/master/schema/schema_1.1/mzML1.1.1_idx.xsd)
- [PSI-MS controlled vocabulary](https://github.com/HUPO-PSI/psi-ms-CV)
- [Pyteomics mzML API](https://pyteomics.readthedocs.io/en/latest/api/mzml.html)
- [Pyteomics XML data access](https://pyteomics.readthedocs.io/en/latest/data.html)
- [pymzML documentation](https://pymzml.github.io/pymzML/)
- [Python ElementTree iterative parsing](https://docs.python.org/3/library/xml.etree.elementtree.html#xml.etree.ElementTree.iterparse)

The online Pyteomics documentation currently describes v5.0, while the verified local behavior is 4.7.5. P1-B must test and constrain its actual dependency version instead of assuming perfect API equivalence.

## 4. Local sample situation

Bounded search covered this repository and only obvious adjacent `data/sample/test/mzml` directories. Repository matches were 4-17 byte P0 placeholders and are not real mzML. One real adjacent sample was found and was not copied or modified:

```text
E:\viewer-TD\test\xzx_PXD045330\20191118_rvg262_LT_110516-13_1000-1100_Techrep01.mzML
size=31,408,514 bytes
indexed=True
run_count=1
spectrum_count=2,048
chromatogram_count=1
MS1=997
MS2=1,051
peak_count_total=2,379,436
```

Observed coverage:

- The file declares `MS:1000768` Thermo nativeID format; all spectra have a scan number extractable from that format, RT, and finite m/z/intensity arrays.
- All MS2 spectra have one precursor, one selected ion, selected ion m/z, charge, intensity, isolation window, precursor spectrum reference, activation, and collision energy.
- All spectra are positive and centroided.
- RT values and the chromatogram time array explicitly use minutes.
- Spectrum arrays are float64, zlib-compressed, nonempty, and equal-length.
- The TIC Chromatogram has float64 time/intensity arrays plus an int64 `ms level` non-standard auxiliary array.
- No missing scan, RT, charge, selected-ion m/z, selected-ion intensity, zero charge, multiple precursor, multiple selected ion, empty array, or non-finite array was observed.

First MS1: native ID `controllerType=0 controllerNumber=1 scan=1`, index 0, 106 peaks, RT `0.001079628833 minute`.

First MS2: native ID `controllerType=0 controllerNumber=1 scan=3`, index 2, 24 peaks, RT `0.0568828683 minute`, precursor spectrumRef to scan 1, selected ion m/z `1038.268920898438`, charge 1, isolation offsets 1.5/1.5, beam-type collision-induced dissociation, collision energy 23.

Probe measurements varied from about 15.5 to 26.2 seconds across repeated runs, with about 42.9 MB Python `tracemalloc` peak. This excludes most native NumPy allocation and is investigation evidence, not a P1-B performance promise.

Not sample-verified: non-indexed mzML, float32 arrays, no/zlib/Numpress variants beyond the observed zlib data, negative mode, profile spectra, missing fields, multiple precursors/ions, MS3+, DIA, ion mobility, SRM/MRM, calibration spectra, empty spectra, multiple runs, and multiple files.

Minimum additional sample set before P1-B acceptance:

1. non-indexed centroid MS1/MS2;
2. explicit seconds RT;
3. float32 arrays and no compression;
4. profile data with a larger peak count;
5. missing scan number;
6. missing charge and missing selected-ion intensity;
7. multiple precursor and multiple selected ion;
8. MS3+;
9. DIA or ion-mobility auxiliary arrays;
10. TIC/BPC and SRM/MRM chromatograms;
11. empty spectrum/arrays;
12. a substantially larger file for memory limits.

## 5. mzML structure overview

The PSI schema defines an mzML run as one coherent consecutive scan set, with spectrum and optional chromatogram lists. Spectrum IDs are native identifiers with a required zero-based `index` and `defaultArrayLength`. Precursor spectrum reference, isolation window, selected-ion list, and activation are separate concepts. Binary arrays carry their own semantic CV term, dtype, compression, optional unit, and encoded bytes. Indexed mzML wraps mzML and adds byte offsets for spectra/chromatograms.

The key design consequence is that a parser-library dictionary is not a domain model. P1-B must translate immediately into explicit domain values and versioned extension records; it must not store Pyteomics-specific nested dictionaries.

## 6. Run field investigation

- `run@id` can become `RunBlock.run_id`; absent human name can use the preserved ID as `run_name` by explicit normalization.
- Input path can become `RunBlock.source_file`; original mzML `sourceFile` IDs, URI, checksum, and nativeID format belong in extension metadata.
- Spectrum/chromatogram counts map directly; start/end RT derive from normalized Spectrum RT extrema.
- `defaultInstrumentConfigurationRef`, software definitions, data-processing definitions, sample reference, source-file reference, and run start timestamp do not have core fields.
- The proposed `mzml_metadata` extension v1 stores normalized run/source/instrument/software/data-processing objects and their explicit references. `StringPoolBlock` remains derived storage, not a semantic substitute.

## 7. Spectrum field investigation

Direct or normalized core mapping is limited to ID, run link, MS level, proven scan number, RT seconds, optional precursor link, and the two array IDs. Spectrum `index` can be checked against preserved list order but is not a substitute for scan number.

Polarity, centroid/profile, TIC, base peak, observed m/z range, scan window, filter string, instrument/data-processing reference, and source `defaultArrayLength` are meaningful and must not be discarded. For the strict P1-B subset they go into normalized records in `mzml_metadata` extension v1, keyed by internal `spectrum_id` and retaining CV accession/name/value/unit where applicable.

Recommended internal ID rule: preserve the mzML `spectrum@id` verbatim as `native_id`; create a deterministic collision-free internal `spectrum_id` from run ID and source zero-based index. Store the source ID and index in extension metadata. This prevents assumptions that every native ID is safe as an internal key while preserving it losslessly.

## 8. Binary arrays investigation

Current v1 only permits `array_type` in `mz`, `intensity`, `time` and `dtype="float64"`. P1-B therefore:

- decodes required arrays with the parser;
- accepts source float32/float64 for supported arrays;
- converts values to Python float and emits v1 float64 JSON;
- preserves source dtype, compression, semantic CV identity, unit, and source length in extension metadata;
- treats float32-to-float64 as exact value widening, not permission to invent precision;
- rejects non-finite values and negative m/z;
- requires m/z/intensity equal length and consistency with source `defaultArrayLength`;
- allows both required arrays to be empty only when source length is explicitly zero;
- rejects a required array that is absent;
- rejects an unrecognized binary array unless its semantic CV term, dtype, unit, compression, owner, and values fit the predeclared `mzml_auxiliary_arrays` extension v1 schema.

The sample TIC `ms level` int64 auxiliary array proves that "unknown arrays can be ignored" is unacceptable. It can be preserved only through the auxiliary-array extension; arbitrary library dictionaries are prohibited.

## 9. Precursor investigation

The current model conflates fewer concepts than mzML supplies, so mapping must be explicit:

- `SpectrumBlock.precursor_id` links the child MSn Spectrum to one `PrecursorBlock`.
- `PrecursorBlock.spectrum_id` identifies the child MSn Spectrum that owns the precursor record.
- selected ion m/z maps to `precursor_mz` only when there is exactly one precursor and one selected ion.
- selected ion charge maps to required `charge`.
- selected ion peak intensity maps to required `intensity`.
- mzML `precursor@spectrumRef` is the source/parent Spectrum reference and has no core field; preserve it in `mzml_metadata` extension.
- isolation target/offsets and activation/collision energy are separate metadata and also belong in the extension.

Missing selected-ion m/z, charge, or peak intensity cannot be fixed by an extension because the core fields remain required. P1-B rejects the complete conversion atomically. Multiple precursor or selected-ion structures cannot be represented by the one `precursor_id` link without choosing one; P1-B rejects them and v2 must define one-to-many semantics.

## 10. Chromatogram investigation

`ChromatogramBlock` can represent native ID/type/run plus one time and one intensity array. P1-B final scope supports TIC and BPC when both arrays exist, lengths match, time units are explicit/convertible to seconds, and no precursor/product semantics would be lost.

The local sample TIC is structurally representable after converting minutes to seconds, but its additional int64 `ms level` array must be preserved in `mzml_auxiliary_arrays` extension v1. SRM/MRM, selected-ion-current, or chromatograms with precursor/product information are rejected in P1-B until a defined extension and real samples prove lossless handling. Nonempty chromatograms are never silently ignored; before the P1-B5 stage they cause an explicit unsupported error.

## 11. MSn and special data

| Data family | Decision |
|---|---|
| MS1 | P1-B support for explicit scan/RT and required arrays |
| MS2 | P1-B support only for exactly one complete precursor and selected ion |
| MS3+ | `UNVERIFIED`; reject in P1-B, revisit with samples and immediate-parent semantics |
| DIA | reject in P1-B; isolation-window semantics are essential and not core |
| Ion mobility | reject unless a specific versioned auxiliary-array extension and sample test exist |
| Centroid | supported; observed sample |
| Profile | structurally possible but `UNVERIFIED`; reject in initial P1-B support profile |
| Empty spectrum | conditionally representable when both arrays and declared length are zero; sample pending |
| Calibration spectrum | reject in P1-B unless its acquisition semantics are defined in extension |
| SIM/SRM | reject in P1-B; not equivalent to ordinary MS1/MS2 |
| Vendor CV/user params | normalized CV extension if non-binary and schema-safe; never raw parser dict |

## 12. Current Block mapping matrix

Classification values are `DIRECT`, `NORMALIZE`, `EXTENSION`, `REJECT_IN_P1`, `REQUIRES_V2`, and `UNVERIFIED`.

| mzML concept | Source location or CV semantics | Level | Required | Multi-value | Possible missing case | Current v1 target Block | Current v1 target field | Unit normalization | Lossless now | P1 handling strategy | extensions | v2 | Validator impact | Data-loss risk | Notes / classification |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Input filename/hash | input and FileDescription | file | yes for pipeline | sourceFile may repeat | source metadata absent | GlobalMeta | source_file_name/hash | none | yes for actual input | preserve actual path/hash | source-file details | no | hash already present | low | DIRECT |
| Run ID/name | run@id | run | ID yes | no in P1 | human name absent | Run | run_id/run_name | none | yes | ID direct; normalized name fallback | no | no | unique run ID | low | NORMALIZE |
| Spectrum count | spectrumList@count | run | yes | no | declaration inconsistent | Run/GlobalMeta | spectrum_count | none | yes | compare declaration/actual | no | no | add count consistency | medium | DIRECT |
| Chromatogram count | chromatogramList@count | run | list optional | no | list absent | Run/GlobalMeta | chromatogram_count | none | yes | absent=0; compare actual | no | no | add count consistency | low | DIRECT |
| Run RT range | derived from scans | run | no | no | no RT | Run | start_rt/end_rt | explicit unit -> seconds | yes if all RT known | min/max normalized RT | no | no | finite/order/count | high | NORMALIZE |
| Source-file references | sourceFileList/defaultSourceFileRef | file/run | optional | yes | absent | none | none | URI unchanged | no | preserve normalized records | yes | no | extension schema | high | EXTENSION |
| Instrument config | instrumentConfigurationList/ref | file/run/scan | schema requires definitions | yes | scan inherits default | none | none | CV units | no | resolve refs and preserve | yes | no | extension refs | high | EXTENSION |
| Software definitions | softwareList | file | required by processing refs | yes | incomplete CV | none | none | none | no | preserve normalized records | yes | no | extension schema | medium | EXTENSION |
| Data processing | dataProcessingList/list defaults | file/list/item | required defaults | yes | item inherits list | none | none | CV units | no | resolve/default then preserve | yes | no | extension refs | high | EXTENSION |
| Spectrum native ID | spectrum@id | spectrum | yes | no | malformed native format | Spectrum | native_id | none | yes | preserve verbatim | source index also | no | native ID uniqueness | low | DIRECT |
| Internal spectrum ID | generated from run/index | spectrum | yes in v1 | no | collision | Spectrum | spectrum_id | none | yes | deterministic generated ID | source index | no | uniqueness | low | NORMALIZE |
| Source index | spectrum@index | spectrum | yes | no | nonconsecutive invalid source | Index | position/order | zero-based | yes if order preserved | validate then preserve extension | yes | no | index consistency | medium | NORMALIZE |
| MS level | spectrum CV | spectrum | required for P1 | no | absent | Spectrum | ms_level | integer | yes | positive integer | no | no | already positive; add profile rules | high | DIRECT |
| Extractable scan number | nativeID plus nativeID format | spectrum | required by v1 | no | format not scan-based | Spectrum | scan_number | integer | conditional | prove extraction from declared format | extraction provenance | no | uniqueness in run | medium | NORMALIZE |
| Missing scan number | nativeID not scan-equivalent | spectrum | possible | no | common format variance | none | required scan_number | forbidden sentinel | no | reject whole file | no | yes, nullable field | critical | REQUIRES_V2 |
| Scan start time | scan CV with explicit unit | scan | required by P1 profile | scanList may repeat | absent/unknown unit | Spectrum | rt | seconds/minutes -> seconds | conditional | reject unknown unit | source value/unit | no | finite/nonnegative | high | NORMALIZE |
| Polarity | scan CV | spectrum | optional | conflicting terms invalid | absent | none | none | none | no | preserve normalized enum | yes | no | extension enum | medium | EXTENSION |
| Centroid/profile | spectrum CV | spectrum | expected | conflict possible | absent | none | none | none | no | centroid supported; profile initially reject | yes | maybe future core | extension enum | high | EXTENSION |
| Total ion current | spectrum CV | spectrum | optional | no | absent | none | none | detector units | no | preserve value/unit | yes | no | finite extension value | low | EXTENSION |
| Base peak m/z/intensity | spectrum CV | spectrum | optional | pair | one missing | none | none | m/z + detector units | no | preserve independently | yes | no | finite/mz nonnegative | low | EXTENSION |
| Lowest/highest observed m/z | spectrum CV | spectrum | optional | pair | absent | none | none | m/z | no | preserve | yes | no | ordering/nonnegative | low | EXTENSION |
| Scan window | scanWindowList | scan | optional | yes | absent | none | none | m/z | no | preserve ordered windows | yes | no | lower<=upper | high | EXTENSION |
| Filter string | scan param | scan | optional | possible | absent | StringPool only derived | none semantic | none | no | preserve as metadata string | yes | no | extension type | medium | EXTENSION |
| Scan instrument ref | scan attribute/default | scan | optional item, default run | no | inherited | none | none | none | no | resolve and preserve effective ref | yes | no | ref integrity | high | EXTENSION |
| Spectrum processing ref | item/list default | spectrum | optional item | no | inherited | none | none | none | no | resolve and preserve effective ref | yes | no | ref integrity | high | EXTENSION |
| defaultArrayLength | spectrum attribute | spectrum | yes | no | inconsistent | none | none | count | no explicit core | validate against arrays; preserve | yes | no | add length check | high | EXTENSION |
| m/z array | binaryDataArray CV | spectrum | required by P1 | one required | absent | Array | type=mz, values | source float -> float64 | yes for float32/64 values | decode/validate/widen | provenance | no | existing type/ref/negative | critical | NORMALIZE |
| intensity array | binaryDataArray CV | spectrum | required by P1 | one required | absent | Array | type=intensity, values | source float -> float64 | yes for float32/64 values | decode/validate/widen | provenance | no | existing type/ref/finite | critical | NORMALIZE |
| time array | binaryDataArray CV | chromatogram | required for supported chrom | one | absent | Array | type=time, values | explicit time -> seconds | conditional | decode/convert | provenance | no | type/ref/finite | critical | NORMALIZE |
| Source float32 | dtype CV | array | optional encoding | no | n/a | Array | dtype=float64 | exact widening | values yes, dtype no | preserve source dtype | yes | no | extension enum | low | NORMALIZE |
| Source float64 | dtype CV | array | optional encoding | no | n/a | Array | dtype=float64 | none | yes | direct numeric decode | provenance | no | already allowed | low | DIRECT |
| Compression | compression CV | array | required semantic | one method | unknown method | none | none | decompressed | values yes, provenance no | parser decodes; preserve method | yes | no | extension enum | medium | EXTENSION |
| Empty supported array | defaultArrayLength=0 | array | possible | no | binary empty | Array | values=[] | none | yes | allow only aligned explicit zero | provenance | no | add declared-length check | medium | NORMALIZE |
| Non-finite array value | decoded array | array | possible corrupt/input | yes | NaN/Inf | none | forbidden JSON | none | no | reject file | no | no | already rejected | critical | REJECT_IN_P1 |
| Unknown/auxiliary array | semantic CV/user param | spectrum/chrom | optional | yes | parser key unknown | Extension | payload only | preserve declared units | conditional | accept only declared auxiliary schema; else reject | yes | core type expansion maybe | extension schema | critical | EXTENSION |
| Required-array length mismatch | source arrays/declaration | spectrum | invalid for P1 | no | corrupt source | none | none | none | no | reject file | no | no | existing pair; add declaration | critical | REJECT_IN_P1 |
| Child precursor link | precursorList owner | MSn spectrum | required for P1 MS2 | one only | no precursor | Spectrum | precursor_id | none | conditional | create one link | metadata | no | add MS-level rule | high | DIRECT |
| Parent spectrum reference | precursor@spectrumRef | precursor | optional in schema | one per precursor | absent/external | none | none | resolve ID | no | preserve source/external ref | yes | core link maybe | extension ref | high | EXTENSION |
| Selected ion m/z | selectedIonList | precursor | required by P1 | may repeat | absent | Precursor | precursor_mz | m/z | conditional | exactly one required | provenance | no | add finite/nonnegative | critical | NORMALIZE |
| Charge present | selected ion CV | precursor | required by v1 | may repeat | absent | Precursor | charge | integer | yes | exactly one required | no | no | add allowed range/policy | critical | DIRECT |
| Charge missing | selected ion | precursor | possible | no | common | none | required charge | no sentinel | no | reject whole file | no | yes, nullable | critical | REQUIRES_V2 |
| Selected-ion intensity | selected ion CV | precursor | required by current model | may repeat | absent | Precursor | intensity | finite source units | conditional | require for P1-B v1 | provenance | nullable may need v2 | add finite | high | NORMALIZE |
| Isolation window | precursor/isolationWindow | precursor | optional schema, essential DIA | one per precursor | absent | none | none | m/z | no | preserve target/lower/upper | yes | maybe core later | extension shape | high | EXTENSION |
| Activation/collision | precursor/activation | precursor | optional | multiple terms | absent | none | none | energy explicit unit | no | preserve normalized terms | yes | maybe core later | extension CV/unit | high | EXTENSION |
| Multiple precursors | precursorList | MSn | possible | yes | n/a | one Spectrum precursor_id | one link | none | no | reject; do not choose first | no | yes, one-to-many | parser precondition | critical | REQUIRES_V2 |
| Multiple selected ions | selectedIonList | precursor | possible | yes | n/a | one Precursor row | scalar fields | none | no | reject; do not choose first | no | yes, one-to-many | parser precondition | critical | REQUIRES_V2 |
| TIC/BPC chromatogram | chromatogram CV | chromatogram | optional | yes list | absent | Chromatogram | type/native/run/array IDs | time -> seconds | yes under subset | support TIC/BPC | metadata | no | pair lengths/types | medium | NORMALIZE |
| SRM/MRM/SIC | chromatogram CV | chromatogram | optional | yes | n/a | partial Chromatogram | insufficient precursor/product | explicit units | no | reject P1-B | possible future extension | maybe | parser profile | critical | REJECT_IN_P1 |
| Chrom precursor/product | precursor/product | chromatogram | optional | possible | absent | none | none | m/z | no | reject until schema/sample | yes later | maybe | extension refs | critical | UNVERIFIED |
| MS3+ | MS level + precursor | spectrum | possible | yes levels | sample absent | partial Spectrum | ms_level scalar | RT/mz normal | unproven | reject initial P1-B | metadata possible | maybe one-to-many | parser profile | high | UNVERIFIED |
| DIA windows | isolation metadata | spectrum | acquisition-specific | many | n/a | no core isolation | none | m/z | no | reject P1-B | not enough alone | likely v2/domain extension | parser profile | critical | REJECT_IN_P1 |
| Ion mobility | scan/binary arrays | scan/array | optional | many | n/a | unsupported array types | none | mobility units | no | reject without declared extension | possible | likely v2 for performance | parser profile | critical | REJECT_IN_P1 |
| Profile spectrum | representation + dense arrays | spectrum | possible | no | sample absent | core arrays fit | same | none | structurally yes | reject initial P1-B pending scale sample | representation | no format need | parser profile | high | UNVERIFIED |
| Empty spectrum | default length and arrays | spectrum | possible | no | sample absent | Spectrum + Arrays | empty lists | none | structurally yes | support only explicit aligned zero | provenance | no | add declaration rule | medium | UNVERIFIED |
| Calibration/SIM | spectrum CV | spectrum | possible | varies | sample absent | partial | insufficient semantics | varies | no | reject P1-B | future extension | maybe | parser profile | high | UNVERIFIED |
| Vendor/private CV/userParam | params | any | optional | yes | unknown semantics | Extension | normalized payload | declared units | conditional | semantic tuple, not parser dict; binary unknown rejects | yes | no unless core required | extension schema | high | EXTENSION |

## 13. v1 hard conflicts

### Scan number

Options A/B/C are combined as a strict support predicate: P1-B supports only files where every Spectrum has an unambiguous scan number proven from the declared nativeID format; encountering one unsupported Spectrum rejects the whole atomic conversion. Option E (`index`) is rejected because the schema defines it as a zero-based position, not a scan number. No sentinel and no Optional reinterpretation are allowed. Option D is the v2 path.

### Charge

P1-B requires one explicit charge on the single selected ion. Missing charge rejects the whole conversion. Omitting `PrecursorBlock` would discard MS2 precursor semantics, and storing "missing" in extensions does not satisfy required `charge`. No guessing and no zero sentinel. Nullable charge requires v2.

### Single precursor

Exactly one precursor and one selected ion are required. Multiple values reject the file. Selecting the first is prohibited. v2 must define ordered one-to-many precursor and selected-ion links.

### RT seconds

P1-B reads the CV-qualified unit. Explicit seconds pass unchanged; explicit minutes multiply by 60. Unknown, missing, conflicting, or unsupported units reject conversion. The local sample proves minute handling is necessary. Source value/unit are preserved in extension metadata.

## 14. SourceInspector migration

Recommendation: **Option A**.

```text
.mzML/.mzml -> real_mzml
mock_mzml -> tests explicitly construct SourceProfile
```

Do not add a magic test extension and do not add an Inspector runtime mode. Production defaults become safe, Inspector remains extension-only, and mock tests remain deterministic through a helper. PlanBuilder maps `real_mzml` to the real plan and retains `mock_mzml` for explicit profiles. Registry registers both named tools. Runner remains unchanged because the plan, not Runner, chooses the step.

P1-B files affected: `inspector.py`, `plan.py`, `registry.py`, new `tools/real_mzml.py`, parser-specific exceptions, `pyproject.toml`, tests, README/docs. Core Blocks, Writer, Reader, and format constants are not modified for the strict v1 subset.

## 15. Parser dependency comparison

| Dimension | Pyteomics | pymzML | Self-written iterative XML |
|---|---|---|---|
| Standard mzML | yes | yes | only what we implement |
| indexed mzML | indexed reader/direct ID support | indexed file handlers/seeking | manual wrapper/index handling |
| binary decode | automatic, optional lazy record | automatic Spectrum/Chromatogram API | manual base64/compression/dtype |
| dtype/compression | exposed/decoded; source metadata obtainable | exposed through API | fully manual CV dispatch |
| iteration/memory | iterative parser documented and locally proven | iterable Reader | ElementTree/lxml iterparse possible |
| precursor/scan | human-readable nested structures | object accessors | manual XML/CV/ref resolution |
| Chromatogram | `iterfind("chromatogram")` locally proven | explicit Chromatogram class | manual |
| Windows | locally proven | expected cross-platform, not installed | stdlib cross-platform |
| Current Viewer alignment | already declared and used | new dependency/API | diverges from Viewer |
| Maintenance risk | adapter and version drift | adapter plus new stack | highest; format/CV/compression burden |
| Leakage risk | dict flattening must be contained | object API must be contained | custom structures may become accidental API |

Recommendation: **Pyteomics**, initially tested against `>=4.7.5,<5` unless P1-B explicitly validates v5. Use iterative reading and an immediate adapter into local scalar/domain structures. Do not pass Pyteomics dictionaries into Blocks or extensions. Do not add the dependency during P1-A.

## 16. P1-B support boundary

P1-B supports:

- one local `.mzML`/`.mzml`, one run, one `.zp`;
- indexed or non-indexed mzML after both receive real tests;
- centroid MS1 and MS2;
- every Spectrum has explicit MS level, provable scan number, and explicit seconds/minutes RT;
- required m/z/intensity arrays in float32/float64, zlib or uncompressed after tested decoding;
- finite values, nonnegative m/z, equal lengths, declared-length consistency;
- MS1 with no precursor;
- MS2 with exactly one precursor and one selected ion, all scalar PrecursorBlock fields present;
- TIC/BPC Chromatogram with explicit time/intensity arrays and units;
- normalized metadata and recognized auxiliary arrays in versioned extensions.

P1-B rejects:

- missing/ambiguous scan number, RT/unit, charge, selected-ion m/z, or selected-ion intensity;
- multiple precursor or selected ion;
- MS3+, DIA, ion mobility, profile, calibration, SIM/SRM/MRM/SIC in the initial profile;
- missing required arrays, length mismatch, non-finite values, negative m/z;
- unsupported compression/dtype or unknown binary arrays outside the declared auxiliary schema;
- multiple runs/files and external precursor sources not resolvable under the profile;
- any source construct that would otherwise be silently discarded.

P1-B preserves but does not make core fields:

- run/source/instrument/software/data-processing metadata;
- polarity/representation/summary values/scan windows/filter/refs;
- precursor source reference/isolation/activation;
- source array dtype/compression/unit/default length;
- normalized non-binary CV/user parameters.

Must wait for v2: nullable scan/charge/intensity if required semantics change, ordered multiple precursors/selected ions, multi-run baseline expansion, and any new core on-disk array/type model.

## 17. RealMzmlParseTool interface

```text
class: RealMzmlParseTool
base: BaseBlockTool
name: real_mzml_parse
category: block_tool (inherited/fixed)
inputs: validated readable single real_mzml source; context.metadata["input_sha256"] string
output: one complete BlockCollection committed atomically to context.blocks
```

Exceptions should be dedicated and semantic: parser/decode failure, unsupported mzML structure, missing required mzML field, unit normalization error, and block mapping error, all ultimately wrapped by Runner as `StepExecutionError`.

The tool reads metadata only for hash/input context and never modifies `source_profile`, `metadata`, `artifacts`, or `logs`. It never writes `.zp`, invokes Writer/Validator, modifies input, performs RAW conversion, repairs values, or drops unsupported structures.

Implementation strategy:

1. create local lists/maps and a local `BlockCollection` candidate;
2. iterate spectra; normalize/validate each record and release parser/NumPy objects;
3. inspect chromatograms according to the explicit profile;
4. validate cross-record references and counts;
5. create versioned extension payloads from local domain records;
6. assign `context.blocks = candidate` only after successful EOF and all checks.

Memory is not bounded by iteration because v1 Writer requires a complete BlockCollection and JSON arrays; iteration only avoids retaining the XML tree and parser dictionaries.

## 18. P1-B ConversionPlan

```text
file_validate
hash_input
real_mzml_parse
string_pool_build
index_build
zp_write
zp_validate
```

No extra full-file Schema precheck step is recommended: it would decode/scan twice and still could not decide all semantic constraints. `RealMzmlParseTool` performs a cheap root/version/profile check and then one semantic iteration, accumulating a candidate off-context. Schema validation may be an optional test/diagnostic path, not a required production step in P1-B.

Runner remains a generic executor. The parser fails before block commit; Writer never runs after Runner records the failure.

## 19. Validator incremental requirements

Already covered:

- required blocks/directory/checksum/schema basics;
- ID uniqueness for run/spectrum/precursor/array;
- core references, array type, pair length, finite values, nonnegative m/z;
- positive MS level, nonnegative scan/RT, supported core dtype/type;
- chromatogram array reference types and index positions.

Add in P1-B6:

- GlobalMeta/Run counts equal actual collections;
- Run RT finite, ordered, and equal Spectrum extrema under P1 profile;
- MS1 has no precursor; supported MS2 has exactly one two-way-owned precursor;
- `PrecursorBlock.spectrum_id` matches the Spectrum that references that precursor;
- precursor m/z/intensity finite, m/z nonnegative, charge in a documented nonzero range;
- scan number uniqueness within run and native ID uniqueness;
- chromatogram time/intensity lengths match;
- known extension type/version/payload schema and owner references;
- source declared array length in extension matches core arrays;
- extension marker confirms normalized RT unit is seconds.

Parser responsibility:

- source XML/CV/ref resolution, source-unit recognition, compression/dtype decode, profile acceptance, and absence of unsupported structures.

v2 responsibility:

- nullable required core fields, one-to-many precursor/selected-ion links, new core array types/dtypes, and multi-run semantics.

Not format validation:

- scientific plausibility of intensity, acquisition-method quality, or whether a collision energy is experimentally sensible.

## 20. Performance risk

For `S` spectra and `P` total peaks, v1 retains dataclasses plus two or more Python float lists. A Python float/list representation is much larger than source float32/64 arrays. Canonical JSON creates another complete bytes representation per logical block. Writer hashes all encoded bytes; Reader loads/parses the complete arrays block; Validator reads/hashes/parses it again; a single-Spectrum array request is therefore O(total arrays block), not O(one Spectrum).

The 31.4 MB sample already has 2.38 million peaks. The probe only iterated NumPy arrays and reported ~42.9 MB traced Python peak, excluding most NumPy native buffers. Conversion to Python lists plus canonical JSON will be substantially higher. Temporary disk must cover at least the final uncompressed JSON `.zp.tmp`, which may exceed source mzML size materially. A failed parser must not leave a candidate assigned to context.

P1-B scale tests must record source bytes, spectra, total peaks, output bytes, elapsed parse/write/validate/read-one-spectrum time, process peak RSS, Python traced peak, temporary-file peak, and failure cleanup. Required fixtures: small, this 31 MB sample, and at least one much larger profile or high-peak file. Passing the current sample is not proof of production scalability.

## 21. Format-version judgment

The strict P1-B subset can remain `.zp` version 1 because all mandatory core values are present and richer metadata fits explicitly versioned extensions. Defining extension schemas does not change the nine-block layout.

Require v2 before: nullable scan/charge or selected-ion intensity, multiple precursor/selected-ion core links, multi-run baseline expansion, new core array types/dtypes, array-level on-disk random access, or changed meaning of any frozen field.

## 22. Implementation steps

Execute `P1_MZML_IMPLEMENTATION_PLAN.md` in order. The schema/profile decision and fixtures precede parser code. Do not add fields opportunistically while parsing a new sample. Each stage retains mock coverage and has an explicit rollback point.

## 23. Blockers

- Samples for every rejection boundary are not yet available.
- pymzML is not installed, so its comparison is documentation-only.
- Pyteomics v5 behavior is not locally verified; local evidence is 4.7.5.
- No process-RSS measurement was captured; `tracemalloc` excludes most NumPy memory.
- Profile, MS3+, DIA, ion mobility, multiple precursor/ion, missing charge/scan, and SRM/MRM conclusions remain static-schema decisions pending samples.
- Extension schemas must be written and reviewed before P1-B parser implementation.

## 24. Final recommendation

Proceed to P1-B1 only. Adopt Pyteomics behind a local adapter, acquire the minimum rejection/normalization fixture set, freeze two versioned extension schemas (`mzml_metadata` and `mzml_auxiliary_arrays`), and add decision tests before changing SourceInspector. Keep v1 core Blocks unchanged. Reject the complete conversion whenever a source cannot be represented without guessing or silent loss.
