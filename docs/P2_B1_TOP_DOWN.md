# P2-B1 Viewer-compatible Top-Down bundle conversion

P2-B1 adds one `real_top_down_bundle` source type to the existing unified
conversion service. It does not add a second top-level API, change either
physical `.zp` layout, or integrate the live Viewer application.

## Fact sources

The input and field contracts were derived from these read-only Viewer files:

- `back/app/ingest/universal_prsm_js_adapter.py`
- `back/app/ingest/universal_toppic_adapter.py`
- `back/app/services/prsm_files.py` and `back/app/services/js_parser.py`
- `back/app/schemas/protein.py`
- `back/docs/universal_schema.sql`
- `back/docs/developer/Top-Down模块.md`
- `back/app/api/routes/prsms.py`, `proteoforms.py`, and `proteins.py`
- `front/src/api/types.ts`
- `front/src/features/prsm/parse.ts`
- Viewer PrSM import, import-planner, and PrSM-file tests

Two user-identified TD directories were inspected. The PXD045330 directory is
a complete single-run conversion bundle: one mzML, Viewer PrSM detail files,
TopPIC PrSM/proteoform tables, TopFD/TopPIC feature and msalign files, raw PrSM,
and a FASTA. The histone49 HTML export is also genuine Top-Down data and is a
useful Viewer/TopMSV schema fact source, but it contains two cutoff trees and
TopFD spectrum JSON rather than a RAW or mzML spectrum source. It therefore is
not, by itself, an admissible P2-B1 conversion bundle.

## Frozen bundle contract

Automatic discovery requires all of the following facts to agree:

- exactly one Viewer-supported PrSM detail directory (`prsm<ID>.js|json|txt`);
- all details reference one `ms_header.spectrum_file_name` run;
- exactly one matching `.mzML` or Thermo `.raw` spectrum source;
- exactly one matching non-`_single` TopPIC proteoform TSV;
- fragment matches come from the same PrSM detail records;
- optional matching PrSM TSV, FASTA, feature, raw PrSM, and msalign roles are
  included when present.

An explicit relative-path JSON manifest is also accepted:

```json
{
  "schema_name": "top_down_bundle_manifest",
  "schema_version": 1,
  "run_name": "run-name",
  "roles": {
    "spectrum_source": "run.mzML",
    "prsm_result": "data/prsms",
    "fragment_match_result": "data/prsms",
    "proteoform_result": "run_ms2_toppic_proteoform.tsv"
  }
}
```

Supported optional roles are `prsm_summary_result`, `protein_database`,
`feature_result`, `raw_prsm_result`, and `msalign_result`. Absolute paths,
unknown roles, missing roles, conflicting role candidates, multiple referenced
runs, and a spectrum filename mismatch are rejected. A directory that merely
contains an arbitrary TSV is never classified as Top-Down.

## Field coverage

All Viewer-used fields are strongly mapped where their semantics are stable.
Every complete source detail document and every TopPIC TSV column/row is also
preserved with its relative source-file label and original column/key name.
Thus optional or future columns cannot be silently dropped.

| Source | Viewer use | Typed target | Complete preservation |
|---|---|---|---|
| PrSM root: `prsm_id`, p/e/FDR, matched fragment/peak counts | identification identity, filtering, score display | `TopDownPrsm` | typed plus complete `source_fields.prsm_detail` |
| `ms.ms_header`: spectrum filename, MS1/MS2 ids/scans, precursor mass/mz/charge, feature intensity | run creation, Spectrum association, precursor/detail display | `TopDownSpectrumReference`, `TopDownPrsm`, `TopDownFeature` | typed plus original header |
| `annotated_protein`: sequence/proteoform ids, name, description, mass, N-acetylation | protein/proteoform import and detail display | `TopDownProteoform` | typed plus complete detail document |
| annotation: protein length, start/end, annotated sequence, residues, cleavages | sequence and cleavage map | `TopDownProteoform.residues/cleavages` | typed plus complete detail document |
| annotation mass shifts: id, interval, annotation, shift, type | modification rendering/localization | `TopDownModification` | typed plus original mass-shift object |
| deconvoluted peak: spectrum/peak id, mass, m/z, intensity, charge | spectrum evidence display | `TopDownPeak` | typed plus original peak object |
| matched ion: ion type/positions/sort, theoretical mass, error, ppm, match shift | fragment match rendering | `TopDownFragmentMatch` | typed plus original matched-ion object |
| TopPIC PrSM/proteoform TSV, including all parameter lines and all columns | summary import, score/feature/protein fields | selected stable fields on typed entities | every parameter, column, row, and unknown column in `source_tables` |

TopPIC colon-delimited merged precursor values are handled exactly as the
current Viewer `to_float`/`to_int` path: the scalar typed value is `null`, while
the original string remains in `source_fields`. Source zero m/z/mass values are
normalized to `null`, not retained as an invented missing-value sentinel.

## Extension schemas

The nine physical blocks remain unchanged. Six ordered schema-version-1
extensions are added inside `extensions`:

- `top_down_metadata`
- `top_down_proteoforms`
- `top_down_prsms`
- `top_down_modifications`
- `top_down_fragment_matches` (including deconvoluted peaks)
- `top_down_features`

Each payload contains stable owner `top_down`, `schema_name`, integer
`schema_version`, `record_count`, and deterministic records. Source paths are
relative labels; extensions contain no current time, username, temporary path,
database id, or random UUID.

## Pipeline and validation

The fixed plan is:

```text
file_validate -> hash_input -> real_top_down -> string_pool_build
-> index_build -> zp_write -> zp_validate
```

`RealTopDownTool` delegates spectrum construction to `RealMzmlParseTool` or
`RealThermoRawParseTool`, associates every PrSM by scan/native identity, and
returns only `BlockCollection`. `ZpWriter` remains the only `.zp` writer.

`TopDownExtensionValidator` composes after the physical `ZpValidator`. It checks
schema identity/version, counts, deterministic order, ID uniqueness, all entity
and core Spectrum references, finite numerics, mass/mz/charge constraints,
modification intervals, and fragment-to-peak ownership. The public
`validate_zp` exposes physical issues separately from `top_down_issues`.

## Public API

```python
from pathlib import Path

from binary_layer import ConversionOptions, convert_source_to_zp

result = convert_source_to_zp(
    source_path=Path("top-down-bundle"),
    target_path=Path("top-down.zp"),
    format_version=2,
    options=ConversionOptions(temporary_directory=Path("temporary")),
)
```

High-level reads are provided by `TopDownReader` and the functions
`get_top_down_summary`, `get_proteoform`, `get_prsm`,
`get_prsms_for_spectrum`, and `get_fragment_matches`. The physical v1/v2 Reader
dispatch is unchanged.

The real acceptance command is:

```powershell
python scripts/run_top_down_acceptance.py <bundle> E:\viewer-two-data\top_down
```

It writes the explicit v2 output under `output`, temporary work under
`temporary`, a concise log under `logs`, and the count/ID/fixed-seed field
comparison report under `results`.
