"""Deterministic ontology candidate retrieval for raw source blocks."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable

from reservoir_data_translator.ingestion import RawBlock
from reservoir_data_translator.ontology import OntologyConcept, OntologyRegistry

from .source_mapping import SourceMappingRegistry


_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "columns",
        "data",
        "for",
        "in",
        "is",
        "key",
        "of",
        "on",
        "or",
        "rows",
        "the",
        "to",
        "value",
        "with",
    }
)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)
    normalized = normalized.casefold()
    return " ".join(re.sub(r"[_\W]+", " ", normalized).split())


def _compact(value: str) -> str:
    return value.replace(" ", "")


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _normalize(value).split()
        if len(token) > 1 and token not in _STOP_WORDS
    )


@dataclass(frozen=True, slots=True)
class OntologyCandidate:
    """A ranked ontology concept plus deterministic retrieval evidence."""

    concept: OntologyConcept
    score: float
    match_type: str
    matched_terms: tuple[str, ...]

    @property
    def concept_id(self) -> str:
        return self.concept.concept_id

    @property
    def name(self) -> str:
        return self.concept.name

    def as_prompt_dict(self) -> dict[str, object]:
        """Serialize only stable ontology facts needed by a model provider."""

        return {
            "concept_id": self.concept.concept_id,
            "name": self.concept.name,
            "description": self.concept.description,
            "value_type": self.concept.value_type,
            "dimension": self.concept.dimension,
            "canonical_unit": self.concept.canonical_unit,
            "constraints": dict(self.concept.constraints),
            "relationships": {
                relation: list(targets)
                for relation, targets in self.concept.relationships.items()
            },
            "retrieval": {
                "score": self.score,
                "match_type": self.match_type,
                "matched_terms": list(self.matched_terms),
            },
        }


class OntologyRetriever:
    """Rank alias matches first, then deterministic lexical overlap.

    The interface returns scored candidates so an embedding-backed strategy can
    be added later without changing the semantic agent contract.
    """

    def __init__(
        self,
        registry: OntologyRegistry,
        *,
        default_top_k: int = 8,
        source_mappings: Iterable[SourceMappingRegistry] = (),
    ) -> None:
        if default_top_k < 1:
            raise ValueError("default_top_k must be at least 1")
        self._registry = registry
        self.default_top_k = default_top_k
        self._source_mappings = tuple(source_mappings)

    def retrieve(
        self,
        source: RawBlock | str,
        *,
        top_k: int | None = None,
    ) -> list[OntologyCandidate]:
        """Return only positively matched active concepts in stable rank order."""

        limit = self.default_top_k if top_k is None else top_k
        if limit < 1:
            raise ValueError("top_k must be at least 1")
        text = source.searchable_text() if isinstance(source, RawBlock) else source
        if not isinstance(text, str) or not text.strip():
            return []

        normalized_query = _normalize(text)
        compact_query = _compact(normalized_query)
        query_tokens = _tokens(text)
        ranked_by_id: dict[str, OntologyCandidate] = {}

        def retain(candidate: OntologyCandidate) -> None:
            current = ranked_by_id.get(candidate.concept_id)
            if current is None or (
                candidate.score,
                candidate.match_type,
                candidate.matched_terms,
            ) > (
                current.score,
                current.match_type,
                current.matched_terms,
            ):
                ranked_by_id[candidate.concept_id] = candidate

        for source_mapping in self._source_mappings:
            for match in source_mapping.search(text):
                concept = self._registry.get_concept(match.concept_id)
                if concept.status != "active":
                    continue
                retain(
                    OntologyCandidate(
                        concept=concept,
                        score=0.99 if match.exact else 0.98,
                        match_type="source_mapping",
                        matched_terms=(match.source_term,),
                    )
                )

        for concept in self._registry.list_concepts():
            if concept.status != "active":
                continue
            alias_matches: list[tuple[float, str]] = []
            for alias in concept.aliases:
                normalized_alias = _normalize(alias)
                compact_alias = _compact(normalized_alias)
                if not compact_alias:
                    continue
                if compact_query == compact_alias:
                    alias_matches.append((1.0, alias))
                elif (
                    normalized_alias in normalized_query
                    or compact_alias in compact_query
                ):
                    alias_matches.append((0.9, alias))

            if alias_matches:
                best_score = max(score for score, _ in alias_matches)
                terms = tuple(
                    sorted(
                        {alias for score, alias in alias_matches if score == best_score},
                        key=lambda item: (_normalize(item), item),
                    )
                )
                retain(
                    OntologyCandidate(
                        concept=concept,
                        score=best_score,
                        match_type="alias",
                        matched_terms=terms,
                    )
                )
                continue

            concept_text = " ".join(
                (
                    concept.concept_id.replace(".", " "),
                    concept.name,
                    concept.description,
                    *concept.aliases,
                )
            )
            overlap = query_tokens & _tokens(concept_text)
            if not overlap:
                continue
            query_coverage = len(overlap) / max(len(query_tokens), 1)
            score = round(min(0.79, 0.45 + 0.34 * query_coverage), 6)
            retain(
                OntologyCandidate(
                    concept=concept,
                    score=score,
                    match_type="keyword",
                    matched_terms=tuple(sorted(overlap)),
                )
            )

        ranked = list(ranked_by_id.values())
        ranked.sort(
            key=lambda candidate: (
                -candidate.score,
                -candidate.concept_id.count("."),
                candidate.concept_id,
            )
        )
        return ranked[:limit]

    def retrieve_concepts(
        self,
        source: RawBlock | str,
        *,
        top_k: int | None = None,
    ) -> list[OntologyConcept]:
        """Convenience projection for callers that do not need scores."""

        return [candidate.concept for candidate in self.retrieve(source, top_k=top_k)]
