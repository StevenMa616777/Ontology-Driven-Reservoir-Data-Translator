"""Company Ontology loading and lookup APIs."""

from .convention import OntologyConvention, RelationshipRule
from .loader import LoadedOntology, OntologyLoadError, OntologyLoader, OntologyMetadata
from .models import OntologyConcept
from .registry import OntologyRegistry
from .validator import (
    OntologyIssue,
    OntologyValidationResult,
    OntologyValidator,
    ValidationSeverity,
)

__all__ = [
    "OntologyConcept",
    "OntologyConvention",
    "OntologyIssue",
    "OntologyLoadError",
    "OntologyLoader",
    "OntologyMetadata",
    "OntologyRegistry",
    "OntologyValidationResult",
    "OntologyValidator",
    "LoadedOntology",
    "RelationshipRule",
    "ValidationSeverity",
]
