"""In-memory query registry for the Company Ontology."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .convention import OntologyConvention
from .loader import OntologyLoader, OntologyMetadata
from .models import OntologyConcept
from .validator import OntologyValidationResult


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[_\W]+", " ", normalized).split())


def _compact(value: str) -> str:
    return value.replace(" ", "")


class OntologyRegistry:
    """Read-only ontology registry loaded once at application startup."""

    def __init__(
        self,
        metadata: OntologyMetadata,
        concepts: tuple[OntologyConcept, ...],
        convention: OntologyConvention,
        validation: OntologyValidationResult,
    ) -> None:
        self.metadata = metadata
        self.convention = convention
        self.validation = validation
        self._concepts: Mapping[str, OntologyConcept] = MappingProxyType(
            {concept.concept_id: concept for concept in concepts}
        )
        alias_entries: list[tuple[str, str, str]] = []
        exact_aliases: defaultdict[str, set[str]] = defaultdict(set)
        for concept in concepts:
            for alias in concept.aliases:
                normalized = _normalize_text(alias)
                compact = _compact(normalized)
                alias_entries.append((normalized, compact, concept.concept_id))
                exact_aliases[normalized].add(concept.concept_id)
                exact_aliases[compact].add(concept.concept_id)
        self._alias_entries = tuple(alias_entries)
        self._exact_aliases = MappingProxyType(
            {key: frozenset(value) for key, value in exact_aliases.items()}
        )

    @classmethod
    def load(cls, path: str | Path) -> "OntologyRegistry":
        bundle = OntologyLoader.load(path)
        return cls(
            bundle.metadata,
            bundle.concepts,
            bundle.convention,
            bundle.validation,
        )

    def get_concept(self, concept_id: str) -> OntologyConcept:
        """Return a concept by stable ID, raising KeyError when it is unknown."""

        return self._concepts[concept_id]

    def search_by_alias(self, text: str) -> list[OntologyConcept]:
        """Find concepts whose configured aliases match a field or source phrase.

        Exact normalized aliases rank first. Embedded matches support source text such
        as ``A15井采用定液生产制度`` while preserving all candidates when an
        intentionally broad alias belongs to more than one concept.
        """

        query = _normalize_text(text)
        if not query:
            return []
        compact_query = _compact(query)
        ranks: dict[str, int] = {}

        for key in (query, compact_query):
            for concept_id in self._exact_aliases.get(key, ()):
                ranks[concept_id] = 0

        for alias, compact_alias, concept_id in self._alias_entries:
            if concept_id in ranks:
                continue
            if alias and alias in query:
                ranks[concept_id] = 1
            elif compact_alias and compact_alias in compact_query:
                ranks[concept_id] = 2

        return [
            self._concepts[concept_id]
            for concept_id in sorted(
                ranks,
                key=lambda item: (ranks[item], -item.count("."), item),
            )
        ]

    def get_relationships(
        self,
        concept_id: str,
    ) -> Mapping[str, tuple[str, ...]]:
        """Return the read-only relationship mapping for a concept."""

        return self.get_concept(concept_id).relationships

    def validate_relationship(
        self,
        source: str,
        relation: str,
        target: str,
    ) -> bool:
        """Check a declared directed relationship between two known concepts."""

        source_concept = self.get_concept(source)
        self.get_concept(target)
        return target in source_concept.relationships.get(relation, ())

    def list_concepts(self) -> list[OntologyConcept]:
        """List every concept in stable concept-ID order."""

        return [self._concepts[key] for key in sorted(self._concepts)]

    def __len__(self) -> int:
        return len(self._concepts)
