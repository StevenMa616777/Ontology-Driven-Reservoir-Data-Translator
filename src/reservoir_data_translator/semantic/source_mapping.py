"""External source-specific terminology mapped to stable ontology concepts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
from typing import Any

from pydantic import Field
import yaml

from reservoir_data_translator.canonical.models import CanonicalModel, NonEmptyString
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.ontology.context import SemanticContext
from reservoir_data_translator.canonical.mapping_contract import normalize_semantic_identity


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized).casefold()
    return " ".join(re.sub(r"[_\W]+", " ", normalized).split())


def _compact(value: str) -> str:
    return value.replace(" ", "")


class SourceMappingEntry(CanonicalModel):
    source_term: NonEmptyString
    concept_id: NonEmptyString
    context: SemanticContext | None = None


class SourceMappingDefinition(CanonicalModel):
    mapping_version: NonEmptyString
    source_system: NonEmptyString
    entries: list[SourceMappingEntry] = Field(min_length=1)
    activation_terms: list[NonEmptyString] = Field(default_factory=list)
    instructions: str | None = None
    requires_pressure_unit: bool = False


@dataclass(frozen=True, slots=True)
class SourceMappingMatch:
    source_term: str
    concept_id: str
    exact: bool
    context: SemanticContext | None = None


class SourceMappingRegistry:
    """Validated source vocabulary kept outside the Company Ontology."""

    def __init__(
        self,
        definition: SourceMappingDefinition,
        ontology: OntologyRegistry,
    ) -> None:
        seen: set[str] = set()
        entries: list[tuple[str, str, SourceMappingEntry]] = []
        for entry in definition.entries:
            concept_id, context = normalize_semantic_identity(entry.concept_id, entry.context)
            ontology.get_concept(concept_id)
            ontology.scopes.validate(concept_id, context)
            entry = entry.model_copy(update={"concept_id": concept_id, "context": context})
            normalized = _normalize(entry.source_term)
            key = _compact(normalized)
            if key in seen:
                raise ValueError(
                    f"Duplicate source term {entry.source_term!r} in "
                    f"{definition.source_system!r}"
                )
            seen.add(key)
            entries.append((normalized, key, entry))
        self.definition = definition
        self._entries = tuple(entries)

    @classmethod
    def load(
        cls,
        path: str | Path,
        ontology: OntologyRegistry,
    ) -> "SourceMappingRegistry":
        mapping_path = Path(path)
        with mapping_path.open("r", encoding="utf-8") as handle:
            payload: Any = yaml.safe_load(handle)
        return cls(SourceMappingDefinition.model_validate(payload), ontology)

    @property
    def source_system(self) -> str:
        return self.definition.source_system

    @property
    def automatic(self) -> bool:
        return bool(self.definition.activation_terms)

    def applies_to(self, text: str) -> bool:
        normalized = _normalize(text)
        return all(re.search(r"(?<![a-z0-9])" + re.escape(_normalize(term)) + r"(?![a-z0-9])", normalized)
                   for term in self.definition.activation_terms)

    def search(self, text: str) -> list[SourceMappingMatch]:
        normalized_query = _normalize(text)
        compact_query = _compact(normalized_query)
        if not compact_query:
            return []
        matches: list[SourceMappingMatch] = []
        for normalized_term, compact_term, entry in self._entries:
            exact = compact_query == compact_term
            contained = (
                re.search(r"(?<![a-z0-9])" + re.escape(normalized_term) + r"(?![a-z0-9])", normalized_query) is not None
                if normalized_term.isascii() else compact_term in compact_query
            )
            if exact or contained:
                matches.append(
                    SourceMappingMatch(
                        source_term=entry.source_term,
                        concept_id=entry.concept_id,
                        exact=exact,
                        context=entry.context,
                    )
                )
        return sorted(
            matches,
            key=lambda match: (
                not match.exact,
                -len(_compact(_normalize(match.source_term))),
                match.concept_id,
                match.source_term,
            ),
        )
