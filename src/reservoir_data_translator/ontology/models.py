"""Immutable runtime representation of ontology concepts."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Copy a shallow YAML mapping into a read-only runtime representation."""

    frozen: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            frozen[key] = _freeze_mapping(item)
        elif isinstance(item, list):
            frozen[key] = tuple(item)
        else:
            frozen[key] = item
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class OntologyConcept:
    """One externally defined Company Ontology concept."""

    concept_id: str
    name: str
    parent: str | None
    description: str
    value_type: str
    dimension: str | None
    canonical_unit: str | None
    aliases: tuple[str, ...]
    constraints: Mapping[str, Any]
    relationships: Mapping[str, tuple[str, ...]]
    source_file: str
    status: str = "active"
    replaced_by: str | None = None

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        source_file: str,
    ) -> "OntologyConcept":
        relationships = {
            relation: tuple(targets)
            for relation, targets in payload["relationships"].items()
        }
        return cls(
            concept_id=payload["concept_id"],
            name=payload["name"],
            parent=payload["parent"],
            description=payload["description"].strip(),
            value_type=payload["value_type"],
            dimension=payload["dimension"],
            canonical_unit=payload["canonical_unit"],
            aliases=tuple(payload["aliases"]),
            constraints=_freeze_mapping(payload["constraints"]),
            relationships=MappingProxyType(relationships),
            source_file=source_file,
            status=payload.get("status", "active"),
            replaced_by=payload.get("replaced_by"),
        )
