from pathlib import Path
from typing import Iterable

from .exceptions import InvalidSourceError
from .models import SourceProfile


class SourceInspector:
    def inspect(self, input_files: Iterable[str | Path]) -> SourceProfile:
        paths = tuple(Path(item) for item in input_files)
        if len(paths) != 1:
            raise InvalidSourceError(f"P0 requires exactly one input file; got {len(paths)}")

        suffix = paths[0].suffix.lower()
        source_type = {".mzml": "mock_mzml", ".raw": "mock_raw"}.get(suffix, "unknown")
        known = source_type != "unknown"
        return SourceProfile(
            source_type=source_type,
            input_files=paths,
            file_count=1,
            has_spectra=known,
            has_chromatograms=False,
            has_identification=False,
            has_quantification=False,
            requires_pre_conversion=source_type == "mock_raw",
            notes=("P0 extension-only inspection; file content is not parsed.",),
        )

