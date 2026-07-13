from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_layer import ZpReader, ZpValidator
from binary_layer.serialization import to_primitive


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a P0 .zp file")
    parser.add_argument("file", type=Path)
    parser.add_argument("--spectrum-id")
    args = parser.parse_args()
    reader = ZpReader(args.file)
    header = reader.read_header()
    directory = reader.read_directory()
    print("header=" + json.dumps(to_primitive(header), ensure_ascii=False, sort_keys=True))
    print("directory=" + json.dumps(to_primitive(directory), ensure_ascii=False, sort_keys=True))
    for entry in directory:
        payload = reader.read_block(entry.block_name)
        count = len(payload) if isinstance(payload, list) else 1
        print(f"block={entry.block_name} records={count}")
    spectra = reader.read_spectra()
    arrays = reader.read_arrays()
    result = ZpValidator().validate(args.file)
    print(f"valid={result.valid} issues={len(result.issues)} checked_blocks={result.checked_blocks}")
    print(f"spectrum_count={len(spectra)}")
    print(f"array_count={len(arrays)}")
    if args.spectrum_id:
        spectrum, mz_array, intensity_array = reader.read_spectrum_arrays(args.spectrum_id)
        print("spectrum=" + json.dumps(asdict(spectrum), ensure_ascii=False, sort_keys=True))
        print(f"mz_count={len(mz_array.values)} mz_first={mz_array.values[0] if mz_array.values else None}")
        print(f"intensity_count={len(intensity_array.values)} intensity_first={intensity_array.values[0] if intensity_array.values else None}")
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())

