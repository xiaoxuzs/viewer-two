from uuid import uuid4

from .constants import ZP_EXTENSION
from .exceptions import UnsupportedSourceError
from .models import ConversionPlan, SourceProfile

REAL_MZML_STEPS = (
    "file_validate",
    "hash_input",
    "real_mzml_parse",
    "string_pool_build",
    "index_build",
    "zp_write",
    "zp_validate",
)
MOCK_MZML_STEPS = (
    "file_validate",
    "hash_input",
    "mock_mzml_parse",
    "string_pool_build",
    "index_build",
    "zp_write",
    "zp_validate",
)
RAW_STEPS = (
    "file_validate",
    "hash_input",
    "mock_raw_to_mzml",
    "mock_mzml_parse",
    "string_pool_build",
    "index_build",
    "zp_write",
    "zp_validate",
)


class PlanBuilder:
    def build(self, profile: SourceProfile) -> ConversionPlan:
        plans = {
            "real_mzml": REAL_MZML_STEPS,
            "mock_mzml": MOCK_MZML_STEPS,
            "mock_raw": RAW_STEPS,
        }
        try:
            steps = plans[profile.source_type]
        except KeyError as exc:
            raise UnsupportedSourceError(f"Unsupported source type: {profile.source_type}") from exc
        return ConversionPlan(
            plan_id=str(uuid4()),
            source_type=profile.source_type,
            required_steps=steps,
            output_extension=ZP_EXTENSION,
            notes=("P1-B2 fixed conversion plan",),
        )
