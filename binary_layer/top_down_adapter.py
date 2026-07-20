from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

from .conversion_exceptions import TopDownConversionError
from .top_down_schema import (
    TOP_DOWN_SCHEMA_VERSION,
    TopDownBundle,
    TopDownBundleManifest,
    TopDownCleavage,
    TopDownCleavageMatch,
    TopDownDocument,
    TopDownFeature,
    TopDownFragmentMatch,
    TopDownModification,
    TopDownPeak,
    TopDownProteoform,
    TopDownPrsm,
    TopDownResidue,
    TopDownSourceTable,
    TopDownSpectrumReference,
)

_PRSM_NAME = re.compile(r"^prsm(\d+)$", re.IGNORECASE)
_JS_ASSIGNMENT = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*", re.DOTALL)
_MISSING_TEXT = frozenset({"", "-", "na", "n/a", "nan", "null", "none"})
_REQUIRED_ROLES = frozenset(
    {"spectrum_source", "prsm_result", "proteoform_result", "fragment_match_result"}
)
_OPTIONAL_ROLES = frozenset(
    {
        "prsm_summary_result",
        "protein_database",
        "feature_result",
        "raw_prsm_result",
        "msalign_result",
    }
)
_PROTON_MASS = 1.00727646677


class TopDownAdapter:
    def inspect_bundle(self, source: Path) -> TopDownBundle:
        source = source.resolve(strict=False)
        if source.is_file() and source.suffix.lower() == ".json":
            return self._bundle_from_manifest(source)
        if not source.is_dir():
            raise TopDownConversionError(
                "TOP_DOWN_BUNDLE_NOT_FOUND",
                f"Top-Down source must be a bundle directory or manifest: {source}",
            )
        return self._discover_bundle(source)

    def load(self, bundle: TopDownBundle) -> TopDownDocument:
        detail_records = [self._load_prsm_detail(path, bundle) for path in bundle.prsm_detail_files]
        prsm_ids = [record[0] for record in detail_records]
        if len(prsm_ids) != len(set(prsm_ids)):
            duplicate = next(item for item in prsm_ids if prsm_ids.count(item) > 1)
            raise TopDownConversionError(
                "TOP_DOWN_DUPLICATE_PRSM_ID",
                f"Duplicate logical PrSM ID: {duplicate}",
            )

        source_tables = self._load_source_tables(bundle)
        table_rows = self._rows_by_prsm_id(source_tables)
        proteoforms: list[TopDownProteoform] = []
        prsms: list[TopDownPrsm] = []
        modifications: list[TopDownModification] = []
        peaks: list[TopDownPeak] = []
        fragments: list[TopDownFragmentMatch] = []
        features: list[TopDownFeature] = []
        proteoform_ids: set[str] = set()

        for prsm_id, raw, source_file in detail_records:
            summary_rows = table_rows.get(prsm_id, ())
            normalized = self._normalize_prsm(
                bundle,
                prsm_id,
                raw,
                source_file,
                summary_rows,
            )
            proteoform, prsm, record_modifications, record_peaks, record_fragments, feature = normalized
            if proteoform.proteoform_id in proteoform_ids:
                raise TopDownConversionError(
                    "TOP_DOWN_DUPLICATE_PROTEOFORM_ID",
                    f"Duplicate logical Proteoform ID: {proteoform.proteoform_id}",
                )
            proteoform_ids.add(proteoform.proteoform_id)
            proteoforms.append(proteoform)
            prsms.append(prsm)
            modifications.extend(record_modifications)
            peaks.extend(record_peaks)
            fragments.extend(record_fragments)
            features.append(feature)

        return TopDownDocument(
            schema_name="top_down_document",
            schema_version=TOP_DOWN_SCHEMA_VERSION,
            bundle=bundle,
            proteoforms=tuple(sorted(proteoforms, key=lambda item: _id_key(item.proteoform_id))),
            prsms=tuple(sorted(prsms, key=lambda item: _id_key(item.prsm_id))),
            modifications=tuple(
                sorted(
                    modifications,
                    key=lambda item: (
                        _id_key(item.proteoform_id),
                        item.left_position,
                        item.right_position,
                        _id_key(item.modification_id),
                    ),
                )
            ),
            peaks=tuple(sorted(peaks, key=lambda item: (_id_key(item.prsm_id), _id_key(item.source_peak_id)))),
            fragment_matches=tuple(
                sorted(
                    fragments,
                    key=lambda item: (
                        _id_key(item.prsm_id),
                        item.ion_type,
                        item.ordinal,
                        item.charge if item.charge is not None else -1,
                        _id_key(item.fragment_match_id),
                    ),
                )
            ),
            features=tuple(sorted(features, key=lambda item: _id_key(item.feature_id))),
            source_tables=source_tables,
            source_field_coverage={
                "prsm_detail": (
                    "prsm_id",
                    "p_value",
                    "e_value",
                    "fdr",
                    "matched_fragment_number",
                    "matched_peak_number",
                    "ms.ms_header",
                    "ms.peaks",
                    "annotated_protein",
                ),
                "top_down_prsm_table": tuple(source_tables[0].columns) if source_tables else (),
            },
        )

    def _discover_bundle(self, root: Path) -> TopDownBundle:
        prsm_dirs = self._prsm_directories(root)
        markers = prsm_dirs or list(root.rglob("*_toppic_prsm.tsv")) or list(root.rglob("*.toppic_raw_prsm"))
        if not markers:
            raise TopDownConversionError(
                "TOP_DOWN_BUNDLE_NOT_FOUND",
                f"Directory does not contain a supported Viewer Top-Down bundle: {root}",
            )
        if len(prsm_dirs) != 1:
            raise TopDownConversionError(
                "TOP_DOWN_AMBIGUOUS_ROLE",
                "Exactly one supported PrSM detail directory is required",
                details={"role": "prsm_result", "candidate_count": len(prsm_dirs)},
            )
        prsm_files = self._prsm_files(prsm_dirs[0])
        self._validate_prsm_file_ids(prsm_files)
        referenced_names = self._referenced_spectrum_names(prsm_files)
        if len(referenced_names) != 1:
            raise TopDownConversionError(
                "TOP_DOWN_MULTIPLE_RUNS_NOT_SUPPORTED",
                "PrSM details must all reference one spectrum run",
                details={"run_count": len(referenced_names)},
            )
        referenced_name = next(iter(referenced_names))
        run_name = _run_key(referenced_name)

        spectra = sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in {".mzml", ".raw"}
            ),
            key=lambda path: path.as_posix().encode("utf-8"),
        )
        matching_spectra = [path for path in spectra if _run_key(path.name) == run_name]
        if not spectra:
            raise TopDownConversionError(
                "TOP_DOWN_SPECTRUM_SOURCE_MISSING",
                "Top-Down bundle has PrSM details but no RAW or mzML spectrum source",
            )
        if len(matching_spectra) != 1:
            if len(matching_spectra) > 1:
                code = "TOP_DOWN_AMBIGUOUS_ROLE"
            elif len(spectra) > 1:
                code = "TOP_DOWN_MULTIPLE_RUNS_NOT_SUPPORTED"
            else:
                code = "TOP_DOWN_SPECTRUM_REFERENCE_NOT_FOUND"
            raise TopDownConversionError(
                code,
                f"PrSM spectrum reference {referenced_name!r} does not resolve to exactly one source",
                details={
                    "candidate_count": len(matching_spectra),
                    "spectrum_source_count": len(spectra),
                },
            )
        spectrum_source = matching_spectra[0]

        proteoform = self._choose_result_file(
            root,
            "proteoform_result",
            "*_toppic_proteoform.tsv",
            run_name,
            exclude_suffix="_single.tsv",
            required=True,
        )
        assert proteoform is not None
        prsm_summary = self._choose_result_file(
            root,
            "prsm_summary_result",
            "*_toppic_prsm.tsv",
            run_name,
            exclude_suffix="_single.tsv",
            required=False,
        )
        protein_database = self._choose_optional_single(
            root,
            "protein_database",
            (".fasta", ".fa", ".faa"),
        )
        feature = self._choose_preferred_run_file(root, "feature_result", ".feature", run_name)
        raw_prsm = self._choose_preferred_run_file(root, "raw_prsm_result", ".toppic_raw_prsm", run_name)
        msalign = self._choose_preferred_run_file(root, "msalign_result", ".msalign", run_name)

        detected = [
            "spectrum_source",
            "prsm_result",
            "fragment_match_result",
            "proteoform_result",
        ]
        optional = {
            "prsm_summary_result": prsm_summary,
            "protein_database": protein_database,
            "feature_result": feature,
            "raw_prsm_result": raw_prsm,
            "msalign_result": msalign,
        }
        detected.extend(role for role, path in optional.items() if path is not None)
        source_files = _unique_paths(
            (
                spectrum_source,
                *prsm_files,
                proteoform,
                *(path for path in optional.values() if path is not None),
            )
        )
        return TopDownBundle(
            schema_name="top_down_bundle",
            schema_version=TOP_DOWN_SCHEMA_VERSION,
            input_path=root,
            root=root,
            run_name=run_name,
            spectrum_source=spectrum_source,
            spectrum_source_type=(
                "mzml" if spectrum_source.suffix.lower() == ".mzml" else "thermo_raw"
            ),
            prsm_detail_files=prsm_files,
            proteoform_result=proteoform,
            prsm_summary_result=prsm_summary,
            protein_database=protein_database,
            feature_result=feature,
            raw_prsm_result=raw_prsm,
            msalign_result=msalign,
            detected_roles=tuple(detected),
            source_files=source_files,
        )

    def _bundle_from_manifest(self, path: Path) -> TopDownBundle:
        try:
            raw = json.loads(_read_text(path))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TopDownConversionError(
                "TOP_DOWN_INVALID_MANIFEST",
                f"Cannot read Top-Down manifest: {exc}",
            ) from exc
        if (
            not isinstance(raw, dict)
            or raw.get("schema_name") != "top_down_bundle_manifest"
            or raw.get("schema_version") != 1
        ):
            raise TopDownConversionError(
                "TOP_DOWN_INVALID_MANIFEST",
                "Unsupported Top-Down manifest schema",
            )
        roles = raw.get("roles")
        run_name = raw.get("run_name")
        if not isinstance(roles, dict) or not isinstance(run_name, str) or not run_name.strip():
            raise TopDownConversionError(
                "TOP_DOWN_INVALID_MANIFEST",
                "Manifest requires run_name and roles",
            )
        manifest = TopDownBundleManifest(
            "top_down_bundle_manifest",
            1,
            run_name.strip(),
            dict(roles),
        )
        missing = sorted(_REQUIRED_ROLES - set(manifest.roles))
        if missing:
            raise TopDownConversionError(
                "TOP_DOWN_REQUIRED_ROLE_MISSING",
                f"Manifest is missing required roles: {', '.join(missing)}",
                details={"missing_required_roles": tuple(missing)},
            )
        unknown_roles = sorted(set(manifest.roles) - _REQUIRED_ROLES - _OPTIONAL_ROLES)
        if unknown_roles:
            raise TopDownConversionError(
                "TOP_DOWN_INVALID_MANIFEST",
                f"Manifest contains unsupported roles: {', '.join(unknown_roles)}",
            )
        resolved: dict[str, Path] = {}
        for role, value in manifest.roles.items():
            if not isinstance(value, str) or not value:
                raise TopDownConversionError(
                    "TOP_DOWN_INVALID_MANIFEST",
                    f"Role {role!r} must be a relative path",
                )
            candidate = Path(value)
            if candidate.is_absolute():
                raise TopDownConversionError(
                    "TOP_DOWN_INVALID_MANIFEST",
                    "Manifest role paths must be relative",
                )
            resolved[role] = (path.parent / candidate).resolve(strict=False)

        prsm_dir = resolved["prsm_result"]
        fragment_dir = resolved["fragment_match_result"]
        if prsm_dir != fragment_dir:
            raise TopDownConversionError(
                "TOP_DOWN_AMBIGUOUS_ROLE",
                "Viewer PrSM details are the fragment-match source; both roles must resolve to one directory",
            )
        prsm_files = self._prsm_files(prsm_dir)
        self._validate_prsm_file_ids(prsm_files)
        referenced = self._referenced_spectrum_names(prsm_files)
        spectrum_source = resolved["spectrum_source"]
        if {_run_key(item) for item in referenced} != {_run_key(spectrum_source.name)}:
            raise TopDownConversionError(
                "TOP_DOWN_RUN_NAME_MISMATCH",
                "Manifest spectrum source does not match PrSM headers",
            )
        for role in _REQUIRED_ROLES - {"prsm_result", "fragment_match_result"}:
            if not resolved[role].is_file():
                raise TopDownConversionError(
                    "TOP_DOWN_REQUIRED_ROLE_MISSING",
                    f"Manifest role is missing: {role}",
                )
        optional = {
            role: resolved.get(role)
            for role in (
                "prsm_summary_result",
                "protein_database",
                "feature_result",
                "raw_prsm_result",
                "msalign_result",
            )
        }
        for role, candidate in optional.items():
            if candidate is not None and not candidate.is_file():
                raise TopDownConversionError(
                    "TOP_DOWN_REQUIRED_ROLE_MISSING",
                    f"Manifest role is missing: {role}",
                )
        source_files = _unique_paths(
            (
                path,
                spectrum_source,
                *prsm_files,
                resolved["proteoform_result"],
                *(item for item in optional.values() if item is not None),
            )
        )
        return TopDownBundle(
            schema_name="top_down_bundle",
            schema_version=TOP_DOWN_SCHEMA_VERSION,
            input_path=path,
            root=path.parent,
            run_name=_run_key(manifest.run_name),
            spectrum_source=spectrum_source,
            spectrum_source_type=(
                "mzml" if spectrum_source.suffix.lower() == ".mzml" else "thermo_raw"
            ),
            prsm_detail_files=prsm_files,
            proteoform_result=resolved["proteoform_result"],
            prsm_summary_result=optional["prsm_summary_result"],
            protein_database=optional["protein_database"],
            feature_result=optional["feature_result"],
            raw_prsm_result=optional["raw_prsm_result"],
            msalign_result=optional["msalign_result"],
            manifest_path=path,
            detected_roles=tuple(manifest.roles),
            source_files=source_files,
        )

    @staticmethod
    def _prsm_directories(root: Path) -> list[Path]:
        candidates: list[Path] = []
        for directory in (root / "data" / "prsms", root / "data"):
            if TopDownAdapter._prsm_files(directory):
                candidates.append(directory)
        for directory in root.rglob("prsms"):
            if directory in candidates or not directory.is_dir():
                continue
            if TopDownAdapter._prsm_files(directory):
                candidates.append(directory)
        return sorted(candidates, key=lambda item: item.as_posix().encode("utf-8"))

    @staticmethod
    def _prsm_files(directory: Path) -> tuple[Path, ...]:
        if not directory.is_dir():
            return ()
        files = [
            path
            for path in directory.iterdir()
            if path.is_file()
            and _PRSM_NAME.fullmatch(path.stem)
            and path.suffix.lower() in {".js", ".json", ".txt"}
        ]
        return tuple(sorted(files, key=lambda path: (_id_key(path.stem[4:]), path.name)))

    @staticmethod
    def _validate_prsm_file_ids(files: tuple[Path, ...]) -> None:
        if not files:
            raise TopDownConversionError(
                "TOP_DOWN_REQUIRED_ROLE_MISSING",
                "PrSM detail directory is empty",
            )
        matches = [_PRSM_NAME.fullmatch(path.stem) for path in files]
        ids = [int(match.group(1)) for match in matches if match is not None]
        if len(ids) != len(set(ids)):
            raise TopDownConversionError(
                "TOP_DOWN_DUPLICATE_PRSM_ID",
                "Multiple PrSM detail files share one ID",
            )

    def _referenced_spectrum_names(self, files: tuple[Path, ...]) -> set[str]:
        result: set[str] = set()
        for path in files:
            prsm = _prsm_root(_load_js_object(path))
            header = _mapping(_mapping(prsm.get("ms")).get("ms_header"))
            value = _text(header.get("spectrum_file_name"))
            if value is None:
                raise TopDownConversionError(
                    "TOP_DOWN_SPECTRUM_REFERENCE_NOT_FOUND",
                    f"PrSM detail lacks ms_header.spectrum_file_name: {path.name}",
                )
            result.add(Path(value).name)
        return result

    @staticmethod
    def _choose_result_file(
        root: Path,
        role: str,
        pattern: str,
        run_name: str,
        *,
        exclude_suffix: str,
        required: bool,
    ) -> Path | None:
        candidates = [
            path
            for path in root.rglob(pattern)
            if not path.name.lower().endswith(exclude_suffix)
        ]
        matching = [path for path in candidates if _run_key(path.name) == run_name]
        if len(matching) == 1:
            return matching[0]
        if required and not matching:
            raise TopDownConversionError(
                "TOP_DOWN_REQUIRED_ROLE_MISSING",
                f"Missing required role: {role}",
            )
        if len(matching) > 1:
            raise TopDownConversionError(
                "TOP_DOWN_AMBIGUOUS_ROLE",
                f"Multiple files match role {role}",
                details={"role": role},
            )
        return None

    @staticmethod
    def _choose_optional_single(
        root: Path,
        role: str,
        suffixes: tuple[str, ...],
    ) -> Path | None:
        candidates = sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in suffixes
            ),
            key=lambda path: path.as_posix().encode("utf-8"),
        )
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        raise TopDownConversionError(
            "TOP_DOWN_AMBIGUOUS_ROLE",
            f"Multiple files match role {role}",
            details={"role": role},
        )

    @staticmethod
    def _choose_preferred_run_file(
        root: Path,
        role: str,
        suffix: str,
        run_name: str,
    ) -> Path | None:
        candidates = [
            path
            for path in root.rglob(f"*{suffix}")
            if _run_key(path.name) == run_name
        ]
        if not candidates:
            return None
        preferred = [
            path for path in candidates if "toppic" in {part.lower() for part in path.parts}
        ]
        selected = preferred or candidates
        if len(selected) != 1:
            if len({_sha256(path) for path in selected}) == 1:
                return sorted(selected, key=lambda path: path.as_posix().encode("utf-8"))[0]
            raise TopDownConversionError(
                "TOP_DOWN_AMBIGUOUS_ROLE",
                f"Multiple files match role {role}",
                details={"role": role},
            )
        return selected[0]

    def _load_prsm_detail(
        self,
        path: Path,
        bundle: TopDownBundle,
    ) -> tuple[str, dict[str, Any], str]:
        prsm = _prsm_root(_load_js_object(path))
        prsm_id = _text(prsm.get("prsm_id"))
        match = _PRSM_NAME.fullmatch(path.stem)
        if prsm_id is None or match is None or int(prsm_id) != int(match.group(1)):
            raise TopDownConversionError(
                "TOP_DOWN_INVALID_PRSM",
                f"PrSM ID does not match filename: {path.name}",
            )
        return str(int(prsm_id)), prsm, bundle.relative_label(path)

    def _load_source_tables(self, bundle: TopDownBundle) -> tuple[TopDownSourceTable, ...]:
        tables: list[TopDownSourceTable] = []
        for role, path in (
            ("prsm_summary_result", bundle.prsm_summary_result),
            ("proteoform_result", bundle.proteoform_result),
        ):
            if path is None:
                continue
            parameters, columns, rows = _read_toppic_tsv(path)
            tables.append(
                TopDownSourceTable(
                    role=role,
                    source_file=bundle.relative_label(path),
                    columns=columns,
                    parameters=parameters,
                    rows=rows,
                )
            )
        return tuple(tables)

    @staticmethod
    def _rows_by_prsm_id(
        tables: tuple[TopDownSourceTable, ...],
    ) -> dict[str, tuple[dict[str, str], ...]]:
        result: dict[str, list[dict[str, str]]] = {}
        for table in tables:
            if table.role != "prsm_summary_result":
                continue
            for row in table.rows:
                value = _text(row.get("Prsm ID"))
                if value is not None:
                    result.setdefault(str(int(value)), []).append(dict(row))
        return {key: tuple(value) for key, value in result.items()}

    def _normalize_prsm(
        self,
        bundle: TopDownBundle,
        prsm_id: str,
        raw: dict[str, Any],
        source_file: str,
        summary_rows: tuple[dict[str, str], ...],
    ) -> tuple[
        TopDownProteoform,
        TopDownPrsm,
        list[TopDownModification],
        list[TopDownPeak],
        list[TopDownFragmentMatch],
        TopDownFeature,
    ]:
        ms = _mapping(raw.get("ms"))
        header = _mapping(ms.get("ms_header"))
        annotated = _mapping(raw.get("annotated_protein"))
        annotation = _mapping(annotated.get("annotation"))
        spectrum_file_name = _required_text(
            header.get("spectrum_file_name"),
            "ms_header.spectrum_file_name",
        )
        if _run_key(spectrum_file_name) != bundle.run_name:
            raise TopDownConversionError(
                "TOP_DOWN_RUN_NAME_MISMATCH",
                f"PrSM {prsm_id} references a different run: {spectrum_file_name}",
            )
        scans = _integer_list(
            header.get("scans"),
            required=True,
            location="ms_header.scans",
        )
        sequence_id = _required_text(
            annotated.get("sequence_id"),
            "annotated_protein.sequence_id",
        )
        source_proteoform_id = _required_text(
            annotated.get("proteoform_id"),
            "annotated_protein.proteoform_id",
        )
        proteoform_id = str(int(source_proteoform_id))
        if proteoform_id != prsm_id:
            proteoform_id = f"{int(sequence_id)}:{int(source_proteoform_id)}"
        annotated_sequence = _required_text(
            annotation.get("annotated_seq"),
            "annotation.annotated_seq",
        )
        residues = tuple(
            TopDownResidue(
                _required_int(item.get("position"), "residue.position"),
                _required_text(item.get("acid"), "residue.acid"),
            )
            for item in _records(annotation.get("residue"))
        )
        sequence = "".join(item.acid for item in residues) or annotated_sequence
        cleavages = tuple(
            self._normalize_cleavage(item)
            for item in _records(annotation.get("cleavage"))
        )
        selected_summary = _select_summary_row(
            summary_rows,
            _text(annotated.get("sequence_name")),
        )
        theoretical_mass = (
            _nullable_float(selected_summary.get("Proteoform mass"))
            if selected_summary
            else None
        )
        experimental_mass = _nullable_float(annotated.get("proteoform_mass"))
        mass_error = (
            experimental_mass - theoretical_mass
            if experimental_mass is not None and theoretical_mass is not None
            else None
        )
        terminal_state = (
            "N_ACETYLATION" if _boolean(annotated.get("n_acetylation")) else "NONE"
        )

        source_fields = {
            "prsm_detail": {"source_file": source_file, "value": raw},
            "toppic_prsm_rows": {
                "source_file": (
                    bundle.relative_label(bundle.prsm_summary_result)
                    if bundle.prsm_summary_result
                    else None
                ),
                "rows": list(summary_rows),
            },
        }
        q_value = _probability_or_none(raw.get("fdr"))
        score = (
            _nullable_float(selected_summary.get("MIScore"))
            if selected_summary
            else None
        )
        prsm = TopDownPrsm(
            prsm_id=prsm_id,
            spectrum_id=None,
            spectrum_reference=TopDownSpectrumReference(
                run_name=bundle.run_name,
                spectrum_file_name=Path(spectrum_file_name).name,
                scan_numbers=scans,
                native_ids=_text_list(header.get("ids")),
                ms1_scan_numbers=_integer_list(header.get("ms1_scans")),
                ms1_ids=_text_list(header.get("ms1_ids")),
            ),
            proteoform_id=proteoform_id,
            precursor_mz=_positive_float_or_none(header.get("precursor_mz")),
            charge=_nullable_int(header.get("precursor_charge")),
            precursor_mass=_positive_float_or_none(header.get("precursor_mono_mass")),
            adjusted_mass=(
                _nullable_float(selected_summary.get("Adjusted precursor mass"))
                if selected_summary
                else None
            ),
            matched_fragment_count=_nullable_int(raw.get("matched_fragment_number")),
            matched_peak_count=_nullable_int(raw.get("matched_peak_number")),
            total_fragment_count=None,
            p_value=_nullable_float(raw.get("p_value")),
            e_value=_nullable_float(raw.get("e_value")),
            q_value=q_value,
            score=score,
            rank=None,
            feature_intensity=_nullable_float(header.get("feature_inte")),
            source_fields=source_fields,
        )
        record_modifications = self._normalize_modifications(
            prsm_id,
            proteoform_id,
            sequence,
            annotation,
            source_file,
        )
        proteoform = TopDownProteoform(
            proteoform_id=proteoform_id,
            sequence_id=str(int(sequence_id)),
            protein_accession=_required_text(
                annotated.get("sequence_name"),
                "annotated_protein.sequence_name",
            ),
            protein_description=_text(annotated.get("sequence_description")),
            sequence=sequence,
            start_position=_required_int(
                annotation.get("first_residue_position"),
                "annotation.first_residue_position",
            ),
            end_position=_required_int(
                annotation.get("last_residue_position"),
                "annotation.last_residue_position",
            ),
            protein_length=_required_int(
                annotation.get("protein_length"),
                "annotation.protein_length",
            ),
            experimental_mass=experimental_mass,
            theoretical_mass=theoretical_mass,
            mass_error=mass_error,
            terminal_state=terminal_state,
            best_prsm_id=prsm_id,
            score_summary={
                "p_value": prsm.p_value,
                "e_value": prsm.e_value,
                "q_value": prsm.q_value,
                "score": prsm.score,
            },
            annotated_sequence=annotated_sequence,
            residues=residues,
            cleavages=cleavages,
            modification_ids=tuple(
                item.modification_id for item in record_modifications
            ),
            source_fields=source_fields,
        )
        record_peaks, record_fragments = self._normalize_peaks(
            prsm_id,
            ms,
            source_file,
        )
        feature = TopDownFeature(
            feature_id=f"feature:{prsm_id}",
            source_feature_id=(
                _text(selected_summary.get("Feature ID")) if selected_summary else None
            ),
            prsm_id=prsm_id,
            spectrum_id=None,
            intensity=prsm.feature_intensity,
            score=(
                _nullable_float(selected_summary.get("Feature score"))
                if selected_summary
                else None
            ),
            min_rt_seconds=None,
            max_rt_seconds=None,
            apex_rt_seconds=(
                _minutes_to_seconds(selected_summary.get("Feature apex time"))
                if selected_summary
                else None
            ),
            source_fields={
                "prsm_detail_header": {
                    "source_file": source_file,
                    "columns": dict(header),
                },
                "toppic_prsm_row": {
                    "source_file": (
                        bundle.relative_label(bundle.prsm_summary_result)
                        if bundle.prsm_summary_result
                        else None
                    ),
                    "columns": selected_summary,
                },
            },
        )
        return (
            proteoform,
            prsm,
            record_modifications,
            record_peaks,
            record_fragments,
            feature,
        )

    @staticmethod
    def _normalize_cleavage(raw: dict[str, Any]) -> TopDownCleavage:
        matched = _mapping(raw.get("matched_peaks"))
        matches = tuple(
            TopDownCleavageMatch(
                ion_type=_required_text(item.get("ion_type"), "matched_peak.ion_type"),
                ion_position=_required_int(
                    item.get("ion_position"),
                    "matched_peak.ion_position",
                ),
                ion_display_position=_required_int(
                    item.get("ion_display_position"),
                    "matched_peak.ion_display_position",
                ),
                source_spectrum_id=_required_text(
                    item.get("spec_id"),
                    "matched_peak.spec_id",
                ),
                source_peak_id=_required_text(
                    item.get("peak_id"),
                    "matched_peak.peak_id",
                ),
                peak_charge=_nullable_int(item.get("peak_charge")),
            )
            for item in _records(matched.get("matched_peak"))
        )
        return TopDownCleavage(
            position=_required_int(raw.get("position"), "cleavage.position"),
            has_n_terminal_ion=_boolean(raw.get("exist_n_ion")),
            has_c_terminal_ion=_boolean(raw.get("exist_c_ion")),
            matched_peaks=matches,
        )

    @staticmethod
    def _normalize_modifications(
        prsm_id: str,
        proteoform_id: str,
        sequence: str,
        annotation: dict[str, Any],
        source_file: str,
    ) -> list[TopDownModification]:
        result: list[TopDownModification] = []
        for position, raw in enumerate(_records(annotation.get("mass_shift"))):
            left = _required_int(raw.get("left_position"), "mass_shift.left_position")
            right = _required_int(raw.get("right_position"), "mass_shift.right_position")
            source_id = _text(raw.get("id")) or str(position)
            residue_position = left if right - left == 1 else None
            residue = (
                sequence[residue_position]
                if residue_position is not None and 0 <= residue_position < len(sequence)
                else None
            )
            result.append(
                TopDownModification(
                    modification_id=f"modification:{proteoform_id}:{source_id}",
                    proteoform_id=proteoform_id,
                    prsm_id=prsm_id,
                    name=_text(raw.get("anno")) or "unknown",
                    mass_shift=_nullable_float(raw.get("shift")),
                    position=residue_position,
                    left_position=left,
                    right_position=right,
                    residue=residue,
                    modification_type=_text(raw.get("shift_type")) or "unknown",
                    localization={
                        "interval_semantics": "zero_based_half_open",
                        "left": left,
                        "right": right,
                    },
                    source_fields={
                        "prsm_detail": {
                            "source_file": source_file,
                            "columns": dict(raw),
                        }
                    },
                )
            )
        return result

    @staticmethod
    def _normalize_peaks(
        prsm_id: str,
        ms: dict[str, Any],
        source_file: str,
    ) -> tuple[list[TopDownPeak], list[TopDownFragmentMatch]]:
        peaks: list[TopDownPeak] = []
        fragments: list[TopDownFragmentMatch] = []
        peak_container = _mapping(ms.get("peaks"))
        for peak_position, raw_peak in enumerate(_records(peak_container.get("peak"))):
            source_peak_id = _text(raw_peak.get("peak_id")) or str(peak_position)
            peak_id = f"peak:{prsm_id}:{source_peak_id}"
            ions = _records(_mapping(raw_peak.get("matched_ions")).get("matched_ion"))
            charge = _nullable_int(raw_peak.get("charge"))
            observed_mz = _nullable_float(raw_peak.get("monoisotopic_mz"))
            intensity = _nullable_float(raw_peak.get("intensity"))
            peaks.append(
                TopDownPeak(
                    peak_id=peak_id,
                    prsm_id=prsm_id,
                    source_spectrum_id=_text(raw_peak.get("spec_id")) or "",
                    source_peak_id=source_peak_id,
                    monoisotopic_mass=_nullable_float(raw_peak.get("monoisotopic_mass")),
                    observed_mz=observed_mz,
                    intensity=intensity,
                    charge=charge,
                    matched_ion_count=len(ions),
                    source_fields={
                        "prsm_detail": {
                            "source_file": source_file,
                            "columns": dict(raw_peak),
                        }
                    },
                )
            )
            for ion_position, ion in enumerate(ions):
                theoretical_mass = _nullable_float(ion.get("theoretical_mass"))
                theoretical_mz = (
                    (theoretical_mass + charge * _PROTON_MASS) / charge
                    if theoretical_mass is not None and charge is not None and charge > 0
                    else None
                )
                fragments.append(
                    TopDownFragmentMatch(
                        fragment_match_id=(
                            f"fragment:{prsm_id}:{source_peak_id}:{ion_position}"
                        ),
                        prsm_id=prsm_id,
                        peak_id=peak_id,
                        ion_type=_required_text(
                            ion.get("ion_type"),
                            "matched_ion.ion_type",
                        ),
                        ordinal=_required_int(
                            ion.get("ion_position"),
                            "matched_ion.ion_position",
                        ),
                        ion_display_position=_required_int(
                            ion.get("ion_display_position"),
                            "matched_ion.ion_display_position",
                        ),
                        ion_left_position=_required_int(
                            ion.get("ion_left_position"),
                            "matched_ion.ion_left_position",
                        ),
                        ion_sort_name=_required_text(
                            ion.get("ion_sort_name"),
                            "matched_ion.ion_sort_name",
                        ),
                        charge=charge,
                        theoretical_mass=theoretical_mass,
                        theoretical_mz=theoretical_mz,
                        observed_mz=observed_mz,
                        mass_error=_nullable_float(ion.get("mass_error")),
                        ppm=_nullable_float(ion.get("ppm")),
                        intensity=intensity,
                        matched_peak_index=_nullable_int(source_peak_id),
                        match_shift=_nullable_float(ion.get("match_shift")),
                        neutral_loss=None,
                        source_fields={
                            "prsm_detail": {
                                "source_file": source_file,
                                "columns": dict(ion),
                            }
                        },
                    )
                )
        return peaks, fragments


def _load_js_object(path: Path) -> dict[str, Any]:
    try:
        text = _read_text(path)
        body = _JS_ASSIGNMENT.sub("", text, count=1).strip()
        if body.endswith(";"):
            body = body[:-1].rstrip()
        value = json.loads(body)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TopDownConversionError(
            "TOP_DOWN_INVALID_PRSM",
            f"Cannot parse {path.name}: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise TopDownConversionError(
            "TOP_DOWN_INVALID_PRSM",
            f"PrSM document must be an object: {path.name}",
        )
    return value


def _prsm_root(value: dict[str, Any]) -> dict[str, Any]:
    direct = value.get("prsm")
    if isinstance(direct, dict):
        return direct
    wrapper = value.get("prsm_data")
    if isinstance(wrapper, dict) and isinstance(wrapper.get("prsm"), dict):
        return wrapper["prsm"]
    return value


def _read_toppic_tsv(
    path: Path,
) -> tuple[dict[str, str], tuple[str, ...], tuple[dict[str, str], ...]]:
    text = _read_text(path)
    lines = text.splitlines()
    header_position = next(
        (position for position, line in enumerate(lines) if line.startswith("Data file name\t")),
        None,
    )
    if header_position is None:
        raise TopDownConversionError(
            "TOP_DOWN_INVALID_RESULT_TABLE",
            f"TopPIC table header not found: {path.name}",
        )
    parameters: dict[str, str] = {}
    for line in lines[:header_position]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parameters[key.strip()] = value.strip().lstrip("\t")
    reader = csv.DictReader(lines[header_position:], delimiter="\t")
    if reader.fieldnames is None:
        raise TopDownConversionError(
            "TOP_DOWN_INVALID_RESULT_TABLE",
            f"Missing columns: {path.name}",
        )
    rows = tuple(
        {
            str(key): "" if value is None else value
            for key, value in row.items()
        }
        for row in reader
    )
    return parameters, tuple(reader.fieldnames), rows


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise UnicodeError(f"unsupported text encoding: {path.name}")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [item for item in values if isinstance(item, dict)]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return None if result.lower() in _MISSING_TEXT else result


def _required_text(value: Any, location: str) -> str:
    result = _text(value)
    if result is None:
        raise TopDownConversionError(
            "TOP_DOWN_INVALID_FIELD",
            f"Missing required text at {location}",
        )
    return result


def _nullable_float(value: Any) -> float | None:
    text = _text(value)
    if text is None:
        return None
    # Viewer uses ``to_float`` for these fields.  TopPIC can encode merged
    # precursor/features as colon-delimited values; Viewer intentionally
    # exposes those as null while retaining the original detail document.
    if ":" in text:
        return None
    try:
        result = float(text)
    except (TypeError, ValueError) as exc:
        raise TopDownConversionError(
            "TOP_DOWN_INVALID_NUMERIC",
            f"Invalid numeric value: {value!r}",
        ) from exc
    return result if math.isfinite(result) else None


def _probability_or_none(value: Any) -> float | None:
    result = _nullable_float(value)
    return result if result is not None and 0 <= result <= 1 else None


def _positive_float_or_none(value: Any) -> float | None:
    result = _nullable_float(value)
    return result if result is not None and result > 0 else None


def _nullable_int(value: Any) -> int | None:
    result = _nullable_float(value)
    if result is None:
        return None
    if not result.is_integer():
        raise TopDownConversionError(
            "TOP_DOWN_INVALID_NUMERIC",
            f"Expected integer value: {value!r}",
        )
    return int(result)


def _required_int(value: Any, location: str) -> int:
    result = _nullable_int(value)
    if result is None:
        raise TopDownConversionError(
            "TOP_DOWN_INVALID_FIELD",
            f"Missing required integer at {location}",
        )
    return result


def _text_list(value: Any) -> tuple[str, ...]:
    text = _text(value)
    if text is None:
        return ()
    return tuple(item for item in re.split(r"[;,\s]+", text) if item)


def _integer_list(
    value: Any,
    *,
    required: bool = False,
    location: str = "value",
) -> tuple[int, ...]:
    result = tuple(_required_int(item, location) for item in _text_list(value))
    if required and not result:
        raise TopDownConversionError(
            "TOP_DOWN_INVALID_FIELD",
            f"Missing required integer list at {location}",
        )
    return result


def _boolean(value: Any) -> bool:
    text = _text(value)
    if text is None:
        return False
    normalized = text.lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise TopDownConversionError(
        "TOP_DOWN_INVALID_BOOLEAN",
        f"Invalid boolean value: {value!r}",
    )


def _run_key(value: str) -> str:
    name = Path(value).name
    lowered = name.lower()
    for suffix in (
        "_ms2_toppic_proteoform_single.tsv",
        "_ms2_toppic_proteoform.tsv",
        "_ms2_toppic_prsm_single.tsv",
        "_ms2_toppic_prsm.tsv",
        "_ms2.toppic_raw_prsm",
        "_ms2.feature",
        "_ms2.msalign",
        ".mzml",
        ".raw",
    ):
        if lowered.endswith(suffix):
            return name[: -len(suffix)].casefold()
    return Path(name).stem.casefold()


def _id_key(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value.rsplit(":", 1)[-1]))
    except ValueError:
        return (1, value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    unique = {
        path.resolve(strict=False): path.resolve(strict=False)
        for path in paths
    }
    return tuple(
        sorted(unique.values(), key=lambda path: path.as_posix().encode("utf-8"))
    )


def _select_summary_row(
    rows: tuple[dict[str, str], ...],
    accession: str | None,
) -> dict[str, str] | None:
    if not rows:
        return None
    if accession is not None:
        matching = [row for row in rows if row.get("Protein accession") == accession]
        if len(matching) == 1:
            return matching[0]
    return rows[0]


def _minutes_to_seconds(value: Any) -> float | None:
    result = _nullable_float(value)
    return result * 60.0 if result is not None else None
