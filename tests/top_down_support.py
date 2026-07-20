from __future__ import annotations

import json
import shutil
from pathlib import Path

MZML_FIXTURE = Path(__file__).parent / "fixtures" / "mzml" / "accept_ms2_precursor_metadata.mzML"


def build_top_down_bundle(
    root: Path,
    *,
    spectrum_suffix: str = ".mzML",
    prsm_id: int = 1,
    scan_number: int = 2,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    spectrum = root / f"run{spectrum_suffix}"
    if spectrum_suffix.lower() == ".mzml":
        shutil.copyfile(MZML_FIXTURE, spectrum)
    else:
        spectrum.write_bytes(b"fake thermo raw")
    prsm_directory = root / "data" / "prsms"
    prsm_directory.mkdir(parents=True)
    write_prsm(
        prsm_directory / f"prsm{prsm_id}.js",
        prsm_id=prsm_id,
        spectrum_file_name=spectrum.name,
        scan_number=scan_number,
    )
    header = (
        "Data file name\tPrsm ID\tProteoform ID\tProtein accession\t"
        "Proteoform mass\tAdjusted precursor mass\tFeature ID\t"
        "Feature intensity\tFeature score\tFeature apex time\tMIScore\tUnknown column\n"
    )
    row = (
        f"run\t{prsm_id}\t{prsm_id}\tP00001\t300.0\t301.0\tF1\t"
        "1234.5\t0.75\t1.25\t42.0\tpreserved-value\n"
    )
    (root / "run_ms2_toppic_prsm.tsv").write_text(header + row, encoding="utf-8", newline="")
    (root / "run_ms2_toppic_proteoform.tsv").write_text(
        "Data file name\tPrsm ID\tProteoform ID\tUnexpected proteoform column\n"
        f"run\t{prsm_id}\t{prsm_id}\talso-preserved\n",
        encoding="utf-8",
        newline="",
    )
    return root


def write_prsm(
    path: Path,
    *,
    prsm_id: int,
    spectrum_file_name: str,
    scan_number: int,
    precursor_charge: str = "3",
    mass_shifts: list[dict[str, str]] | None = None,
) -> None:
    modification_records = mass_shifts if mass_shifts is not None else [
        {
            "id": "0",
            "left_position": "1",
            "right_position": "2",
            "anno": "fixture modification",
            "shift": "15.5",
            "shift_type": "unexpected",
            "unknown_modification_field": "retained",
        }
    ]
    value = {
        "prsm": {
            "prsm_id": str(prsm_id),
            "p_value": "0.001",
            "e_value": "0.002",
            "fdr": "0.01",
            "matched_fragment_number": "1",
            "matched_peak_number": "1",
            "viewer_unused_field": "preserved-detail-value",
            "ms": {
                "ms_header": {
                    "spectrum_file_name": spectrum_file_name,
                    "ms1_ids": "1",
                    "ms1_scans": "1",
                    "ids": "2",
                    "scans": str(scan_number),
                    "precursor_mono_mass": "299.0",
                    "precursor_charge": precursor_charge,
                    "precursor_mz": "100.0",
                    "feature_inte": "1234.5",
                },
                "peaks": {
                    "peak": [
                        {
                            "spec_id": "2",
                            "peak_id": "0",
                            "monoisotopic_mass": "149.0",
                            "monoisotopic_mz": "50.0",
                            "intensity": "500.0",
                            "charge": "3",
                            "unknown_peak_field": "retained",
                            "matched_ions": {
                                "matched_ion": [
                                    {
                                        "ion_type": "B",
                                        "ion_position": "1",
                                        "ion_display_position": "1",
                                        "ion_left_position": "0",
                                        "ion_sort_name": "B1",
                                        "theoretical_mass": "146.0",
                                        "mass_error": "3.0",
                                        "ppm": "20.0",
                                        "match_shift": "0.0",
                                        "unknown_ion_field": "retained",
                                    }
                                ]
                            },
                        }
                    ]
                },
            },
            "annotated_protein": {
                "sequence_id": "7",
                "proteoform_id": str(prsm_id),
                "sequence_name": "P00001",
                "sequence_description": "fixture protein",
                "proteoform_mass": "299.0",
                "n_acetylation": "false",
                "annotation": {
                    "protein_length": "3",
                    "first_residue_position": "0",
                    "last_residue_position": "2",
                    "annotated_seq": "ACD",
                    "residue": [
                        {"position": "0", "acid": "A"},
                        {"position": "1", "acid": "C"},
                        {"position": "2", "acid": "D"},
                    ],
                    "cleavage": [],
                    "mass_shift": modification_records,
                },
            },
        }
    }
    path.write_text(
        "prsm_data = " + json.dumps(value, ensure_ascii=False) + ";\r\n",
        encoding="utf-8",
        newline="",
    )


def build_top_down_intermediate_bundle(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MZML_FIXTURE, root / "run.mzML")
    toppic = root / "toppic"
    topfd = root / "topfd"
    toppic.mkdir()
    topfd.mkdir()
    (toppic / "run_ms2_toppic_prsm.xml").write_text(
        "<prsm_list><prsm><file_name>run_ms2.msalign</file_name>"
        "<prsm_id>1</prsm_id><spectrum_scan>2</spectrum_scan>"
        "<proteoform><mass_shift_list><mass_shift /></mass_shift_list></proteoform>"
        "</prsm></prsm_list>",
        encoding="utf-8",
    )
    (topfd / "run_ms2.msalign").write_text(
        "#TopFD fixture\n#File name:\trun.mzML\nBEGIN IONS\n"
        "FILE_NAME=run.mzML\nSPECTRUM_ID=2\nSCANS=2\n"
        "PRECURSOR_CHARGE=3\nPRECURSOR_MASS=299.0\nPRECURSOR_MZ=100.0\n"
        "149.0 500.0 3 1.0\nEND IONS\n",
        encoding="utf-8",
        newline="",
    )
    return root


def write_fake_prsmup(
    path: Path,
    *,
    mode: str = "valid",
    scan_number: int = 2,
) -> Path:
    seed = path.with_suffix(".seed")
    write_prsm(
        seed,
        prsm_id=1,
        spectrum_file_name="run.mzML",
        scan_number=scan_number,
    )
    body = seed.read_text(encoding="utf-8")
    seed.unlink()
    path.write_text(
        "import argparse, pathlib, sys, time\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--prsm-xml', required=True)\n"
        "parser.add_argument('--msalign', required=True)\n"
        "parser.add_argument('--out-dir', required=True)\n"
        "parser.add_argument('--limit', required=True, type=int)\n"
        "args = parser.parse_args()\n"
        f"mode = {mode!r}\n"
        f"body = {body!r}\n"
        "if mode == 'nonzero': sys.exit(7)\n"
        "if mode == 'timeout': time.sleep(5)\n"
        "out = pathlib.Path(args.out_dir)\n"
        "if mode != 'missing': out.mkdir(parents=True, exist_ok=True)\n"
        "if mode == 'empty': (out / 'prsm1.js').write_bytes(b'')\n"
        "elif mode == 'malformed': (out / 'prsm1.js').write_text('{', encoding='utf-8')\n"
        "elif mode == 'duplicate':\n"
        "    (out / 'prsm1.js').write_text(body, encoding='utf-8')\n"
        "    (out / 'prsm01.js').write_text(body, encoding='utf-8')\n"
        "elif mode not in {'missing', 'nonzero', 'timeout'}:\n"
        "    if args.limit < 1: sys.exit(8)\n"
        "    (out / 'prsm1.js').write_text(body, encoding='utf-8')\n",
        encoding="utf-8",
        newline="",
    )
    return path
