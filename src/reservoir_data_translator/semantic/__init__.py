"""Ontology retrieval, guarded semantic mapping, and unit normalization."""

from importlib import import_module

from .models import (
    AmbiguousMappingDraft,
    AmbiguousSemanticMapping,
    MappedMappingDraft,
    SemanticMapping,
    SemanticMappingBatch,
    SemanticMappingDraft,
    SemanticMappingOutcome,
    SemanticModelResponse,
    UnmappedMappingDraft,
    UnmappedSemanticMapping,
)
from .unit_normalizer import (
    IncompatibleUnitError,
    InvalidMagnitudeError,
    UnitNormalizationError,
    UnitNormalizer,
    UnsupportedUnitError,
)
from .source_mapping import (
    SourceMappingDefinition,
    SourceMappingEntry,
    SourceMappingMatch,
    SourceMappingRegistry,
)

__all__ = [
    "AmbiguousMappingDraft",
    "AmbiguousSemanticMapping",
    "DeepSeekCallTrace",
    "DeepSeekProvider",
    "capture_deepseek_traces",
    "IncompatibleUnitError",
    "InvalidMagnitudeError",
    "MappedMappingDraft",
    "OntologyCandidate",
    "OntologyRetriever",
    "SemanticAgentContractError",
    "SemanticMapping",
    "SemanticMappingAgent",
    "SemanticMappingBatch",
    "SemanticMappingDraft",
    "SemanticMappingOutcome",
    "SemanticModelProvider",
    "SemanticProviderError",
    "SemanticModelResponse",
    "SourceMappingDefinition",
    "SourceMappingEntry",
    "SourceMappingMatch",
    "SourceMappingRegistry",
    "UnitNormalizationError",
    "UnitNormalizer",
    "UnmappedMappingDraft",
    "UnmappedSemanticMapping",
    "UnsupportedUnitError",
]


_LAZY_EXPORTS = {
    "DeepSeekCallTrace": (".deepseek", "DeepSeekCallTrace"),
    "DeepSeekProvider": (".deepseek", "DeepSeekProvider"),
    "capture_deepseek_traces": (".deepseek", "capture_deepseek_traces"),
    "OntologyCandidate": (".retriever", "OntologyCandidate"),
    "OntologyRetriever": (".retriever", "OntologyRetriever"),
    "SemanticAgentContractError": (
        ".mapping_agent",
        "SemanticAgentContractError",
    ),
    "SemanticMappingAgent": (".mapping_agent", "SemanticMappingAgent"),
    "SemanticModelProvider": (".provider", "SemanticModelProvider"),
    "SemanticProviderError": (".provider", "SemanticProviderError"),
}


def __getattr__(name: str) -> object:
    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
