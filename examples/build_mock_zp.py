from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_layer import PipelineContext, PipelineRunner, PlanBuilder, SourceProfile, ZpReader, build_default_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a complete mock mzML .zp file")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source = output_dir / "mock.mzML"
    source.write_text("mock mzML input\n", encoding="utf-8")

    profile = SourceProfile(
        source_type="mock_mzml",
        input_files=(source,),
        file_count=1,
        has_spectra=True,
        has_chromatograms=False,
        has_identification=False,
        has_quantification=False,
        requires_pre_conversion=False,
        notes=("Explicit P0 mock mzML profile for the example.",),
    )
    plan = PlanBuilder().build(profile)
    context = PipelineContext(profile, metadata={"output_path": output_dir / "mock_run.zp"})
    PipelineRunner().run(plan, build_default_registry(), context)

    output_path = Path(context.artifacts["output_zp_path"])
    result = context.artifacts["validation_result"]
    reader = ZpReader(output_path)
    spectrum, mz_array, intensity_array = reader.read_spectrum_arrays("spectrum_2")
    print(f"created={output_path}")
    print(f"valid={result.valid}")
    print(f"spectrum_id={spectrum.spectrum_id}")
    print(f"ms_level={spectrum.ms_level}")
    print(f"mz_count={len(mz_array.values)}")
    print(f"mz_first={mz_array.values[0]}")
    print(f"intensity_count={len(intensity_array.values)}")
    print(f"intensity_first={intensity_array.values[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
