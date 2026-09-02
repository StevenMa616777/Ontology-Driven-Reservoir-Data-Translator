"""Machine-readable ontology modeling convention."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class RelationshipRule:
    """Allowed endpoints and cardinality for one relationship type."""

    description: str
    source_value_types: frozenset[str]
    target_value_types: frozenset[str]
    allow_multiple: bool
    inverse: str | None


@dataclass(frozen=True, slots=True)
class SuspiciousAliasPattern:
    """Heuristic used to identify likely values or units in aliases."""

    pattern: str
    description: str


@dataclass(frozen=True, slots=True)
class OntologyConvention:
    """Controlled vocabularies and semantic checks loaded from YAML."""

    version: str
    concept_id_pattern: str
    root_concepts: frozenset[str]
    hierarchy_exceptions: Mapping[str, str]
    value_types: frozenset[str]
    lifecycle_statuses: frozenset[str]
    dimensions: Mapping[str, frozenset[str]]
    constraint_keys: frozenset[str]
    relationships: Mapping[str, RelationshipRule]
    source_specific_tokens: tuple[str, ...]
    suspicious_alias_patterns: tuple[SuspiciousAliasPattern, ...]

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> "OntologyConvention":
        payload = document.get("convention")
        if not isinstance(payload, dict):
            raise ValueError("Missing 'convention' mapping")

        required = {
            "version",
            "concept_id_pattern",
            "root_concepts",
            "hierarchy_exceptions",
            "value_types",
            "lifecycle_statuses",
            "dimensions",
            "constraint_keys",
            "relationships",
            "source_specific_tokens",
            "suspicious_alias_patterns",
        }
        missing = required - payload.keys()
        if missing:
            raise ValueError(f"Missing convention fields: {sorted(missing)}")

        cls._require_string(payload["version"], "convention.version")
        cls._require_string(
            payload["concept_id_pattern"],
            "convention.concept_id_pattern",
        )
        try:
            re.compile(payload["concept_id_pattern"])
        except re.error as exc:
            raise ValueError(f"Invalid convention.concept_id_pattern: {exc}") from exc
        root_concepts = cls._string_list(
            payload["root_concepts"],
            "convention.root_concepts",
            allow_empty=False,
        )
        value_types = cls._string_list(
            payload["value_types"],
            "convention.value_types",
            allow_empty=False,
        )
        lifecycle_statuses = cls._string_list(
            payload["lifecycle_statuses"],
            "convention.lifecycle_statuses",
            allow_empty=False,
        )
        constraint_keys = cls._string_list(
            payload["constraint_keys"],
            "convention.constraint_keys",
            allow_empty=False,
        )
        source_tokens = cls._string_list(
            payload["source_specific_tokens"],
            "convention.source_specific_tokens",
            allow_empty=True,
        )

        hierarchy_exceptions = payload["hierarchy_exceptions"]
        if not isinstance(hierarchy_exceptions, dict) or not all(
            isinstance(key, str)
            and key.strip()
            and isinstance(reason, str)
            and reason.strip()
            for key, reason in hierarchy_exceptions.items()
        ):
            raise ValueError(
                "convention.hierarchy_exceptions must map concept IDs to reasons"
            )

        dimensions_payload = payload["dimensions"]
        if not isinstance(dimensions_payload, dict) or not dimensions_payload:
            raise ValueError("convention.dimensions must be a non-empty mapping")
        dimensions: dict[str, frozenset[str]] = {}
        for dimension, definition in dimensions_payload.items():
            cls._require_string(dimension, "dimension name")
            if not isinstance(definition, dict):
                raise ValueError(f"Dimension {dimension!r} must be a mapping")
            units = cls._string_list(
                definition.get("canonical_units"),
                f"dimensions.{dimension}.canonical_units",
                allow_empty=False,
            )
            dimensions[dimension] = frozenset(units)

        relationship_payload = payload["relationships"]
        if not isinstance(relationship_payload, dict) or not relationship_payload:
            raise ValueError("convention.relationships must be a non-empty mapping")
        relationships: dict[str, RelationshipRule] = {}
        for name, definition in relationship_payload.items():
            cls._require_string(name, "relationship name")
            if not isinstance(definition, dict):
                raise ValueError(f"Relationship {name!r} must be a mapping")
            relation_required = {
                "description",
                "source_value_types",
                "target_value_types",
                "allow_multiple",
                "inverse",
            }
            relation_missing = relation_required - definition.keys()
            if relation_missing:
                raise ValueError(
                    f"Relationship {name!r} is missing {sorted(relation_missing)}"
                )
            cls._require_string(
                definition["description"],
                f"relationships.{name}.description",
            )
            if not isinstance(definition["allow_multiple"], bool):
                raise ValueError(
                    f"relationships.{name}.allow_multiple must be boolean"
                )
            inverse = definition["inverse"]
            if inverse is not None:
                cls._require_string(inverse, f"relationships.{name}.inverse")
            relationships[name] = RelationshipRule(
                description=definition["description"].strip(),
                source_value_types=frozenset(
                    cls._string_list(
                        definition["source_value_types"],
                        f"relationships.{name}.source_value_types",
                        allow_empty=False,
                    )
                ),
                target_value_types=frozenset(
                    cls._string_list(
                        definition["target_value_types"],
                        f"relationships.{name}.target_value_types",
                        allow_empty=False,
                    )
                ),
                allow_multiple=definition["allow_multiple"],
                inverse=inverse,
            )

        alias_patterns_payload = payload["suspicious_alias_patterns"]
        if not isinstance(alias_patterns_payload, list):
            raise ValueError("convention.suspicious_alias_patterns must be a list")
        alias_patterns: list[SuspiciousAliasPattern] = []
        for index, definition in enumerate(alias_patterns_payload):
            if not isinstance(definition, dict):
                raise ValueError(
                    f"suspicious_alias_patterns[{index}] must be a mapping"
                )
            cls._require_string(
                definition.get("pattern"),
                f"suspicious_alias_patterns[{index}].pattern",
            )
            cls._require_string(
                definition.get("description"),
                f"suspicious_alias_patterns[{index}].description",
            )
            alias_patterns.append(
                SuspiciousAliasPattern(
                    pattern=definition["pattern"],
                    description=definition["description"].strip(),
                )
            )

        unknown_relationship_types = {
            rule.inverse
            for rule in relationships.values()
            if rule.inverse is not None and rule.inverse not in relationships
        }
        if unknown_relationship_types:
            raise ValueError(
                "Unknown inverse relationships in convention: "
                f"{sorted(unknown_relationship_types)}"
            )
        invalid_endpoint_types = {
            endpoint
            for rule in relationships.values()
            for endpoint in rule.source_value_types | rule.target_value_types
            if endpoint not in value_types
        }
        if invalid_endpoint_types:
            raise ValueError(
                "Relationship rules use unknown value_types: "
                f"{sorted(invalid_endpoint_types)}"
            )
        for name, rule in relationships.items():
            if rule.inverse is not None and relationships[rule.inverse].inverse != name:
                raise ValueError(
                    f"Relationship {name!r} and inverse {rule.inverse!r} "
                    "must reference each other"
                )
        for alias_pattern in alias_patterns:
            try:
                re.compile(alias_pattern.pattern)
            except re.error as exc:
                raise ValueError(
                    f"Invalid suspicious alias pattern {alias_pattern.pattern!r}: {exc}"
                ) from exc

        return cls(
            version=payload["version"],
            concept_id_pattern=payload["concept_id_pattern"],
            root_concepts=frozenset(root_concepts),
            hierarchy_exceptions=MappingProxyType(dict(hierarchy_exceptions)),
            value_types=frozenset(value_types),
            lifecycle_statuses=frozenset(lifecycle_statuses),
            dimensions=MappingProxyType(dimensions),
            constraint_keys=frozenset(constraint_keys),
            relationships=MappingProxyType(relationships),
            source_specific_tokens=tuple(token.casefold() for token in source_tokens),
            suspicious_alias_patterns=tuple(alias_patterns),
        )

    @staticmethod
    def _require_string(value: Any, path: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{path} must be a non-empty string")

    @classmethod
    def _string_list(
        cls,
        value: Any,
        path: str,
        *,
        allow_empty: bool,
    ) -> list[str]:
        if not isinstance(value, list) or (
            not allow_empty and not value
        ) or not all(isinstance(item, str) and item.strip() for item in value):
            qualifier = "" if allow_empty else "non-empty "
            raise ValueError(f"{path} must be a {qualifier}list of strings")
        if len(value) != len(set(value)):
            raise ValueError(f"{path} must not contain duplicates")
        return value
