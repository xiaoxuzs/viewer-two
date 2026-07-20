from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET

import pyarrow as pa
import pyarrow.parquet as pq

from binary_layer.bottom_up_schema import DIANN_COLUMN_SPECS

MZML_NAMESPACE = "http://psi.hupo.org/ms/mzml"
NS = {"m": MZML_NAMESPACE}


def build_dia_bundle(root: Path, *, report_role: str = "all_report") -> Path:
    bundle = root / "bundle"
    diann = bundle / "diann"
    spectra = bundle / "spectra"
    diann.mkdir(parents=True)
    spectra.mkdir(parents=True)
    _write_dia_mzml(spectra / "run1.mzML")
    table = _report_table()
    pq.write_table(table, diann / f"{report_role}.parquet", row_group_size=2)
    if report_role == "all_report":
        pq.write_table(table, diann / "target_report.parquet", row_group_size=2)
    return bundle


def _write_dia_mzml(target: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "mzml" / "accept_ms2_precursor_metadata.mzML"
    tree = ET.parse(source)
    root = tree.getroot()
    run = root.find("m:run", NS)
    assert run is not None
    run.set("id", "run1")
    run.set("startTimeStamp", "2020-01-10T00:00:00Z")
    spectrum_list = run.find("m:spectrumList", NS)
    assert spectrum_list is not None
    spectra = spectrum_list.findall("m:spectrum", NS)
    assert len(spectra) == 2
    first_ms2 = spectra[1]
    _set_ms2(first_ms2, index=1, scan=2, rt=1.5, target_mz=500.0)
    second_ms2 = deepcopy(first_ms2)
    _set_ms2(second_ms2, index=2, scan=3, rt=2.5, target_mz=700.0)
    spectrum_list.append(second_ms2)
    spectrum_list.set("count", "3")
    ET.register_namespace("", MZML_NAMESPACE)
    tree.write(target, encoding="utf-8", xml_declaration=True)


def _set_ms2(
    spectrum: ET.Element,
    *,
    index: int,
    scan: int,
    rt: float,
    target_mz: float,
) -> None:
    spectrum.set("index", str(index))
    spectrum.set("id", f"controllerType=0 controllerNumber=1 scan={scan}")
    rt_param = spectrum.find(
        ".//m:cvParam[@accession='MS:1000016']",
        NS,
    )
    target_param = spectrum.find(
        ".//m:cvParam[@accession='MS:1000827']",
        NS,
    )
    selected_param = spectrum.find(
        ".//m:cvParam[@accession='MS:1000744']",
        NS,
    )
    selected_ion = spectrum.find(".//m:selectedIon", NS)
    assert rt_param is not None and target_param is not None and selected_param is not None
    assert selected_ion is not None
    rt_param.set("value", str(rt))
    target_param.set("value", str(target_mz))
    selected_param.set("value", str(target_mz))
    for child in list(selected_ion):
        if child.attrib.get("accession") == "MS:1000041":
            selected_ion.remove(child)


def _report_table() -> pa.Table:
    rows = [
        _row(
            precursor_id="ACDE2",
            sequence="ACDE",
            modified="AC(UniMod:4)DE",
            charge=2,
            mz=500.0,
            rt=1.5,
            group="P1;P2",
            names="Protein 1;Protein 2",
            genes="G1;G2",
            quantity=1000.0,
            pg_max_lfq=5000.0,
        ),
        _row(
            precursor_id="ACDE3",
            sequence="ACDE",
            modified="AC(UniMod:4)DE",
            charge=3,
            mz=500.5,
            rt=1.5,
            group="P1;P2",
            names="Protein 1;Protein 2",
            genes="G1;G2",
            quantity=900.0,
            pg_max_lfq=5000.0,
        ),
        _row(
            precursor_id="CPEP2",
            sequence="CPEP",
            modified="C(UniMod:4)PEP",
            charge=2,
            mz=700.0,
            rt=2.5,
            group="P2",
            names="Protein 2",
            genes="G2",
            quantity=800.0,
            pg_max_lfq=3000.0,
        ),
    ]
    arrays: dict[str, pa.Array] = {}
    for spec in DIANN_COLUMN_SPECS:
        values = [row[spec.source_name] for row in rows]
        dtype = {
            "integer": pa.int64(),
            "float": pa.float64(),
            "string": pa.string(),
        }[spec.value_kind]
        arrays[spec.source_name] = pa.array(values, type=dtype)
    return pa.table(arrays)


def _row(
    *,
    precursor_id: str,
    sequence: str,
    modified: str,
    charge: int,
    mz: float,
    rt: float,
    group: str,
    names: str,
    genes: str,
    quantity: float,
    pg_max_lfq: float,
) -> dict[str, object]:
    row: dict[str, object] = {}
    for spec in DIANN_COLUMN_SPECS:
        if spec.value_kind == "integer":
            row[spec.source_name] = 0
        elif spec.value_kind == "float":
            row[spec.source_name] = 0.005 if (
                spec.source_name.endswith("Q.Value") or spec.source_name in {"PEP", "PG.PEP"}
            ) else 0.0
        else:
            row[spec.source_name] = ""
    row.update(
        {
            "Run.Index": 0,
            "Run": "run1",
            "Precursor.Id": precursor_id,
            "Modified.Sequence": modified,
            "Stripped.Sequence": sequence,
            "Precursor.Charge": charge,
            "Precursor.Lib.Index": 1,
            "Decoy": 0,
            "Proteotypic": 1,
            "Precursor.Mz": mz,
            "Protein.Ids": group,
            "Protein.Group": group,
            "Protein.Names": names,
            "Genes": genes,
            "RT": rt,
            "Predicted.RT": rt,
            "RT.Start": rt - 0.1,
            "RT.Stop": rt + 0.1,
            "FWHM": 0.05,
            "Precursor.Quantity": quantity,
            "Precursor.Normalised": quantity,
            "Ms1.Area": quantity,
            "Ms1.Normalised": quantity,
            "Ms1.Apex.Area": quantity,
            "PG.TopN": pg_max_lfq,
            "PG.MaxLFQ": pg_max_lfq,
            "Genes.TopN": pg_max_lfq,
            "Genes.MaxLFQ": pg_max_lfq,
            "Genes.MaxLFQ.Unique": pg_max_lfq,
            "Q.Value": 0.005,
            "PG.Q.Value": 0.005,
            "Protein.Q.Value": 0.005,
        }
    )
    return row
