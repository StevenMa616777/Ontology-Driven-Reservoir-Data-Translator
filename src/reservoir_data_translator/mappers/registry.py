"""External Canonical-concept to target-keyword mapping registry."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import Field
import yaml

from reservoir_data_translator.canonical.models import CanonicalModel, NonEmptyString
from reservoir_data_translator.ontology import OntologyRegistry


class PlatformMappingEntry(CanonicalModel):
    target_type: NonEmptyString


class PlatformMappingDefinition(CanonicalModel):
    platform: NonEmptyString
    version: NonEmptyString
    dialect: NonEmptyString
    mappings: dict[NonEmptyString, PlatformMappingEntry] = Field(min_length=1)


class PlatformMappingRegistry:
    """Immutable output mapping knowledge, separate from ontology aliases."""

    def __init__(
        self,
        definition: PlatformMappingDefinition,
        ontology: OntologyRegistry | None = None,
    ) -> None:
        if ontology is not None:
            for concept_id in definition.mappings:
                ontology.get_concept(concept_id)
        self.definition = definition
        self._targets: Mapping[str, str] = MappingProxyType(
            {
                concept_id: entry.target_type
                for concept_id, entry in definition.mappings.items()
            }
        )

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

    def target_for(self, concept_id: str) -> str:
        try:
            return self._targets[concept_id]
        except KeyError as exc:
            raise KeyError(
                f"No {self.platform} output mapping for concept {concept_id!r}"
            ) from exc

    def supports(self, concept_id: str) -> bool:
        return concept_id in self._targets
