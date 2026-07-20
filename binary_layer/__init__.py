from .blocks import (
    ArrayBlock,
    BlockCollection,
    ChromatogramBlock,
    ExtensionBlock,
    GlobalMetaBlock,
    IndexBlock,
    PrecursorBlock,
    RunBlock,
    SpectrumBlock,
    StringPoolBlock,
)
from .constants import (
    DEFAULT_ZP_WRITE_VERSION,
    KNOWN_ZP_VERSIONS,
    SUPPORTED_ZP_READ_VERSIONS,
    SUPPORTED_ZP_VALIDATE_VERSIONS,
    SUPPORTED_ZP_WRITE_VERSIONS,
    ZP_VERSION,
    ZP_VERSION_V1,
    ZP_VERSION_V2,
)
from .exceptions import (
    UnsupportedVersionError,
    ZpV2ArrayReadError,
    ZpV2ArrayWriteError,
    ZpV2ResourceLimitError,
    ZpVersionNotImplementedError,
)
from .inspector import SourceInspector
from .models import (
    ConversionOptions,
    ConversionPlan,
    ConversionResult,
    PipelineContext,
    SourceFileIdentity,
    SourceProfile,
    ValidationResult,
)
from .plan import PlanBuilder
from .reader import ZpReader
from .registry import StepRegistry, build_default_registry
from .runner import PipelineRunner
from .validator import ZpValidator
from .writer import ZpWriter
from .v2_arrays_writer import ZpV2ArrayWriteLimits
from .v2_arrays_reader import ZpV2ArrayReadLimits
from .v2_validator import ZpV2ValidationLimits
from .tools.real_mzml import RealMzmlParseTool
from .tools.real_thermo_raw import RealThermoRawParseTool
from .tools.real_top_down import RealTopDownTool
from .tools.real_top_down_intermediate import RealTopDownIntermediateTool
from .tools.real_dia_result import RealDiaResultTool
from .dia_result_bundle import DiaResultBundle, DiaResultBundleInspector
from .dia_result_adapter import DiaResultAdapter
from .dia_spectrum_association import DiaSpectrumAssociator
from .bottom_up_reader import (
    BottomUpReader,
    get_bottom_up_fragment_matches,
    get_bottom_up_identification,
    get_bottom_up_identifications_for_spectrum,
    get_bottom_up_modifications_for_identification,
    get_bottom_up_peptide,
    get_bottom_up_protein,
    get_bottom_up_protein_group,
    get_bottom_up_quantification_summary,
    get_bottom_up_summary,
)
from .bottom_up_validator import BottomUpExtensionValidator
from .top_down_adapter import TopDownAdapter
from .top_down_interpretation_adapter import TopDownInterpretationAdapter
from .top_down_interpretation_schema import (
    GeneratedPrsmArtifact,
    TopDownIntermediateBundle,
    TopDownInterpretationOptions,
    TopDownInterpretationResult,
)
from .top_down_reader import (
    TopDownReader,
    get_fragment_matches,
    get_proteoform,
    get_prsm,
    get_prsms_for_spectrum,
    get_top_down_interpretation_provenance,
    get_top_down_summary,
)
from .top_down_schema import TopDownBundle, TopDownBundleManifest
from .top_down_validator import TopDownExtensionValidator
from .service import convert_source_to_zp, inspect_source, open_zp, validate_zp


def __getattr__(name: str):
    if name in {"MigrationError", "MigrationResult", "migrate_v1_to_v2"}:
        from .migration import MigrationError, MigrationResult, migrate_v1_to_v2

        return {
            "MigrationError": MigrationError,
            "MigrationResult": MigrationResult,
            "migrate_v1_to_v2": migrate_v1_to_v2,
        }[name]
    raise AttributeError(name)

__all__ = [
    "ArrayBlock", "BlockCollection", "ChromatogramBlock", "ConversionOptions", "ConversionPlan",
    "ConversionResult",
    "ExtensionBlock", "GlobalMetaBlock", "IndexBlock", "PipelineContext",
    "PipelineRunner", "PlanBuilder", "PrecursorBlock", "RunBlock", "SourceInspector",
    "SourceFileIdentity", "SourceProfile", "SpectrumBlock", "StepRegistry", "StringPoolBlock",
    "ValidationResult", "ZpReader", "ZpValidator", "ZpWriter", "RealMzmlParseTool",
    "RealThermoRawParseTool", "convert_source_to_zp", "inspect_source", "open_zp", "validate_zp",
    "RealTopDownTool", "TopDownAdapter", "TopDownBundle", "TopDownBundleManifest",
    "RealTopDownIntermediateTool", "TopDownInterpretationAdapter",
    "RealDiaResultTool", "DiaResultBundle", "DiaResultBundleInspector",
    "DiaResultAdapter", "DiaSpectrumAssociator", "BottomUpReader",
    "BottomUpExtensionValidator", "get_bottom_up_summary",
    "get_bottom_up_identification", "get_bottom_up_identifications_for_spectrum",
    "get_bottom_up_peptide", "get_bottom_up_protein", "get_bottom_up_protein_group",
    "get_bottom_up_modifications_for_identification",
    "get_bottom_up_fragment_matches", "get_bottom_up_quantification_summary",
    "TopDownIntermediateBundle", "TopDownInterpretationOptions",
    "TopDownInterpretationResult", "GeneratedPrsmArtifact",
    "TopDownExtensionValidator", "TopDownReader", "get_top_down_summary",
    "get_proteoform", "get_prsm", "get_prsms_for_spectrum", "get_fragment_matches",
    "get_top_down_interpretation_provenance",
    "build_default_registry", "DEFAULT_ZP_WRITE_VERSION", "KNOWN_ZP_VERSIONS",
    "SUPPORTED_ZP_READ_VERSIONS", "SUPPORTED_ZP_VALIDATE_VERSIONS",
    "SUPPORTED_ZP_WRITE_VERSIONS", "UnsupportedVersionError", "ZP_VERSION",
    "ZP_VERSION_V1", "ZP_VERSION_V2", "ZpV2ArrayWriteError",
    "ZpV2ArrayWriteLimits", "ZpV2ArrayReadError", "ZpV2ArrayReadLimits",
    "ZpV2ResourceLimitError", "ZpV2ValidationLimits", "ZpVersionNotImplementedError",
    "MigrationError", "MigrationResult", "migrate_v1_to_v2",
]
