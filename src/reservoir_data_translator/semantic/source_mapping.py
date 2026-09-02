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


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[_\W]+", " ", normalized).split())


def _compact(value: str) -> str:
    return value.replace(" ", "")


class SourceMappingEntry(CanonicalModel):
    source_term: NonEmptyString
    concept_id: NonEmptyString


class SourceMappingDefinition(CanonicalModel):
    mapping_version: NonEmptyString
    source_system: NonEmptyString
    entries: list[SourceMappingEntry] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class SourceMappingMatch:
    source_term: str
    concept_id: str
    exact: bool


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
            ontology.get_concept(entry.concept_id)
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

    def search(self, text: str) -> list[SourceMappingMatch]:
        normalized_query = _normalize(text)
        compact_query = _compact(normalized_query)
        if not compact_query:
            return []
        matches: list[SourceMappingMatch] = []
        for normalized_term, compact_term, entry in self._entries:
            exact = compact_query == compact_term
            if exact or normalized_term in normalized_query or compact_term in compact_query:
                matches.append(
                    SourceMappingMatch(
                        source_term=entry.source_term,
                        concept_id=entry.concept_id,
                        exact=exact,
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
