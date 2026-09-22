"""Company Ontology loading and lookup APIs."""

from .convention import OntologyConvention, RelationshipRule
from .context import SemanticContext
from .loader import LoadedOntology, OntologyLoadError, OntologyLoader, OntologyMetadata
from .models import OntologyConcept
from .registry import OntologyRegistry
from .scopes import ScopeBinding, ScopeDefinition, ScopeRegistry
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
    "SemanticContext",
    "ScopeBinding",
    "ScopeDefinition",
    "ScopeRegistry",
    "ValidationSeverity",
]
