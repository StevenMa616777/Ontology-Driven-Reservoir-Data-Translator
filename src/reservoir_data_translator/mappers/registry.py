"""External semantic concept/context to target-keyword mapping registry."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import Field
import yaml

from reservoir_data_translator.canonical.models import CanonicalModel, NonEmptyString
from reservoir_data_translator.ontology import OntologyRegistry, SemanticContext


class PlatformMappingEntry(CanonicalModel):
    target_type: NonEmptyString
    concept_id: NonEmptyString | None = None
    semantic_context: SemanticContext | None = None


class PlatformMappingDefinition(CanonicalModel):
    platform: NonEmptyString
    version: NonEmptyString
    dialect: NonEmptyString
    mappings: dict[NonEmptyString, PlatformMappingEntry] = Field(min_length=1)


class PlatformMappingRegistry:
    """Output rules keyed by semantic identity, with explicit reused contexts.

    YAML keys identify adapter rules. A missing ``concept_id`` retains the
    compact format for unique concepts such as well controls. A context can
    be omitted only when the configured ontology declares one possible use.
    """

    def __init__(
        self,
        definition: PlatformMappingDefinition,
        ontology: OntologyRegistry | None = None,
    ) -> None:
        default_contexts: dict[str, SemanticContext] = {}
        ambiguous_concepts: set[str] = set()
        if ontology is not None:
            contexts_by_concept: dict[str, set[SemanticContext]] = {}
            for binding in ontology.scopes.bindings:
                contexts_by_concept.setdefault(binding.concept_id, set()).add(binding.context)
            default_contexts = {
                concept_id: next(iter(contexts))
                for concept_id, contexts in contexts_by_concept.items()
                if len(contexts) == 1
            }
            ambiguous_concepts = {
                concept_id for concept_id, contexts in contexts_by_concept.items()
                if len(contexts) > 1
            }
        targets: dict[tuple[str, SemanticContext | None], str] = {}
        for rule_id, entry in definition.mappings.items():
            concept_id = entry.concept_id or rule_id
            context = entry.semantic_context or default_contexts.get(concept_id)
            if ontology is not None:
                ontology.get_concept(concept_id)
                if context is None and concept_id in ambiguous_concepts:
                    raise ValueError(f"Output rule {rule_id!r} requires an explicit semantic_context")
                if context is None:
                    raise ValueError(f"Output rule {rule_id!r} has no declared semantic scope")
                if context is not None:
                    ontology.scopes.validate(concept_id, context)
            key = (concept_id, context)
            if key in targets:
                raise ValueError(f"Duplicate semantic output mapping in rule {rule_id!r}")
            targets[key] = entry.target_type
        self.definition = definition
        self._targets: Mapping[tuple[str, SemanticContext | None], str] = MappingProxyType(targets)
        self._default_contexts: Mapping[str, SemanticContext] = MappingProxyType(default_contexts)

    @classmethod
    def load(
        cls,
        path: str | Path,
        ontology: OntologyRegistry | None = None,
    ) -> "PlatformMappingRegistry":
        with Path(path).open("r", encoding="utf-8") as handle:
            payload: Any = yaml.safe_load(handle)
        return cls(PlatformMappingDefinition.model_validate(payload), ontology)

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        ontology: OntologyRegistry | None = None,
    ) -> "PlatformMappingRegistry":
        return cls(PlatformMappingDefinition.model_validate(payload), ontology)

    @property
    def platform(self) -> str:
        return self.definition.platform

    @property
    def dialect(self) -> str:
        return self.definition.dialect

    def target_for(
        self,
        concept_id: str,
        context: SemanticContext | None = None,
    ) -> str:
        try:
            return self._targets[(concept_id, context or self._default_contexts.get(concept_id))]
        except KeyError as exc:
            raise KeyError(
                f"No {self.platform} output mapping for concept {concept_id!r} "
                f"with context {context!r}"
            ) from exc

    def supports(self, concept_id: str, context: SemanticContext | None = None) -> bool:
        return (concept_id, context or self._default_contexts.get(concept_id)) in self._targets
