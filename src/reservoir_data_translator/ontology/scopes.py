"""Validated task scopes and contextual uses of reusable ontology concepts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Collection, Mapping, Sequence
import unicodedata

import yaml

from .context import SemanticContext
from .convention import OntologyConvention
from .models import OntologyConcept


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return " ".join(re.sub(r"[_\W]+", " ", text.casefold()).split())


def _matches(alias: str, text: str) -> bool:
    """Avoid English abbreviation substrings while supporting Chinese phrases."""
    if not alias:
        return False
    if any("\u4e00" <= char <= "\u9fff" for char in alias):
        return alias.replace(" ", "") in text.replace(" ", "")
    return re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text) is not None


@dataclass(frozen=True, slots=True)
class ScopeBinding:
    concept_id: str
    context: SemanticContext
    aliases: tuple[str, ...]
    relationships: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class ScopeDefinition:
    scope_id: str
    name: str
    description: str
    aliases: tuple[str, ...]
    phases: tuple[str, ...]
    bindings: tuple[ScopeBinding, ...]


class ScopeRegistry:
    """Declared semantic contexts; no path templates or storage-schema knowledge."""

    def __init__(self, scopes: Sequence[ScopeDefinition]) -> None:
        self._scopes = MappingProxyType({scope.scope_id: scope for scope in scopes})
        self.bindings = tuple(binding for scope in scopes for binding in scope.bindings)
        self._allowed = frozenset(
            (binding.concept_id, binding.context) for binding in self.bindings
        )

    @classmethod
    def load(
        cls,
        path: str | Path,
        concept_ids: Collection[str],
        *,
        concepts: Sequence[OntologyConcept] = (),
        convention: OntologyConvention | None = None,
    ) -> "ScopeRegistry":
        path = Path(path)
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"Cannot read semantic scopes {path}: {exc}") from exc
        if not isinstance(document, dict) or set(document) != {"scopes"}:
            raise ValueError("Scope document must contain only a 'scopes' list")
        payloads = document["scopes"]
        if not isinstance(payloads, list) or not payloads:
            raise ValueError("scopes must be a non-empty list")
        ids = frozenset(concept_ids)
        seen_scopes: set[str] = set()
        seen_bindings: set[tuple[str, SemanticContext]] = set()
        scopes: list[ScopeDefinition] = []
        for payload in payloads:
            required = {"scope_id", "name", "description", "aliases", "phases", "bindings"}
            if not isinstance(payload, dict) or set(payload) != required:
                raise ValueError(f"Scope fields must be {sorted(required)}")
            scope_id = cls._string(payload["scope_id"], "scope_id")
            if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", scope_id):
                raise ValueError(f"Invalid scope_id {scope_id!r}")
            if scope_id in seen_scopes:
                raise ValueError(f"Duplicate scope {scope_id!r}")
            seen_scopes.add(scope_id)
            aliases = cls._strings(payload["aliases"], f"{scope_id}.aliases")
            phases = cls._strings(payload["phases"], f"{scope_id}.phases")
            if set(phases) - {"oil", "water", "gas"}:
                raise ValueError(f"Unknown phase in scope {scope_id!r}")
            binding_payloads = payload["bindings"]
            if not isinstance(binding_payloads, list) or not binding_payloads:
                raise ValueError(f"Scope {scope_id!r} requires non-empty bindings")
            bindings: list[ScopeBinding] = []
            for item in binding_payloads:
                required_binding = {"concept_id", "context", "aliases"}
                allowed_binding = required_binding | {"relationships"}
                if (
                    not isinstance(item, dict)
                    or not required_binding <= set(item)
                    or set(item) - allowed_binding
                ):
                    raise ValueError(f"Invalid binding fields in scope {scope_id!r}")
                concept_id = cls._string(item["concept_id"], "binding.concept_id")
                if concept_id not in ids:
                    raise ValueError(f"Unknown binding concept {concept_id!r}")
                context = SemanticContext.model_validate(item["context"])
                if context.scope != scope_id:
                    raise ValueError(f"Binding context scope does not match {scope_id!r}")
                if context.phase is not None and context.phase not in phases:
                    raise ValueError(
                        f"Phase {context.phase!r} is not allowed in scope {scope_id!r}"
                    )
                key = (concept_id, context)
                if key in seen_bindings:
                    raise ValueError(
                        f"Duplicate binding for {concept_id!r} and {context.model_dump()}"
                    )
                seen_bindings.add(key)
                relations = item.get("relationships", {})
                if not isinstance(relations, dict):
                    raise ValueError("Binding relationships must be a mapping")
                parsed_relations: dict[str, tuple[str, ...]] = {}
                for relation, targets in relations.items():
                    cls._string(relation, "relationship name")
                    parsed = cls._strings(targets, f"relationships.{relation}")
                    if not parsed:
                        raise ValueError(f"Relationship {relation!r} requires targets")
                    if set(parsed) - ids:
                        raise ValueError(
                            f"Unknown scoped relationship target in {relation!r}: "
                            f"{sorted(set(parsed) - ids)}"
                        )
                    parsed_relations[relation] = parsed
                bindings.append(
                    ScopeBinding(
                        concept_id,
                        context,
                        cls._strings(item["aliases"], "binding.aliases"),
                        MappingProxyType(parsed_relations),
                    )
                )
            scopes.append(
                ScopeDefinition(
                    scope_id,
                    cls._string(payload["name"], "scope.name"),
                    cls._string(payload["description"], "scope.description"),
                    aliases,
                    phases,
                    tuple(bindings),
                )
            )
        registry = cls(scopes)
        if concepts and convention is not None:
            registry._validate_relationships(
                {concept.concept_id: concept for concept in concepts}, convention
            )
        return registry

    @staticmethod
    def _string(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
        return value.strip()

    @classmethod
    def _strings(cls, values: Any, label: str) -> tuple[str, ...]:
        if not isinstance(values, list):
            raise ValueError(f"{label} must be a list")
        result = tuple(cls._string(value, label) for value in values)
        normalized = tuple(_normalize(value) for value in result)
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"Duplicate values in {label}")
        return result

    def get_scope(self, scope_id: str) -> ScopeDefinition:
        return self._scopes[scope_id]

    def list_scopes(self) -> list[ScopeDefinition]:
        return [self._scopes[key] for key in sorted(self._scopes)]

    def validate(self, concept_id: str, context: SemanticContext) -> None:
        if not isinstance(context, SemanticContext):
            raise ValueError("A validated SemanticContext is required")
        if (concept_id, context) not in self._allowed:
            raise ValueError(
                f"Undeclared semantic context for {concept_id!r}: "
                f"{context.model_dump(exclude_none=True)}"
            )

    def detect(self, text: str) -> tuple[str, ...]:
        normalized = _normalize(text)
        matched: list[str] = []
        for scope in self.list_scopes():
            aliases = (
                *scope.aliases,
                *(alias for binding in scope.bindings for alias in binding.aliases),
            )
            if any(_matches(_normalize(alias), normalized) for alias in aliases):
                matched.append(scope.scope_id)
        return tuple(matched)

    @staticmethod
    def _compatible(left: SemanticContext, right: SemanticContext) -> bool:
        if left.scope != right.scope:
            return False
        if left.phase_system is not None and right.phase_system is not None:
            # A phase-system curve can relate oil permeability to water saturation.
            return left.phase_system == right.phase_system
        return left.phase is None or right.phase is None or left.phase == right.phase

    def _validate_relationships(
        self,
        concepts: Mapping[str, OntologyConcept],
        convention: OntologyConvention,
    ) -> None:
        relation_roles = {
            "coordinate_for": "coordinate",
            "dependent_on": "property",
            "referenced_at": "property",
            "reference_for": "reference",
        }
        for scope in self.list_scopes():
            for binding in scope.bindings:
                for relation, targets in binding.relationships.items():
                    rule = convention.relationships.get(relation)
                    if rule is None:
                        raise ValueError(f"Unknown scoped relationship {relation!r}")
                    expected_role = relation_roles.get(relation)
                    if expected_role is not None and binding.context.role != expected_role:
                        raise ValueError(
                            f"Scoped relationship {relation!r} requires role {expected_role!r}"
                        )
                    if concepts[binding.concept_id].value_type not in rule.source_value_types:
                        raise ValueError(f"Invalid source type for scoped relationship {relation!r}")
                    if not rule.allow_multiple and len(targets) > 1:
                        raise ValueError(f"Invalid cardinality for scoped relationship {relation!r}")
                    for target in targets:
                        if concepts[target].value_type not in rule.target_value_types:
                            raise ValueError(f"Invalid target type for scoped relationship {relation!r}")
                        compatible = [
                            other
                            for other in scope.bindings
                            if other.concept_id == target
                            and self._compatible(binding.context, other.context)
                        ]
                        target_roles = {
                            "coordinate_for": {"model"},
                            "dependent_on": {"coordinate", "reference"},
                        }.get(relation)
                        if target_roles is not None and not any(
                            other.context.role in target_roles for other in compatible
                        ):
                            raise ValueError(
                                f"Scoped relationship {relation!r} lacks compatible "
                                f"target binding for {target!r}"
                            )
                        if rule.inverse and not any(
                            binding.concept_id in other.relationships.get(rule.inverse, ())
                            for other in compatible
                        ):
                            raise ValueError(
                                f"Missing scoped inverse {rule.inverse!r} "
                                f"for {binding.concept_id!r}"
                            )
            # Check complete table structure only after validating every edge, so
            # invalid relationship declarations are diagnosed at their origin.
            for binding in scope.bindings:
                if concepts[binding.concept_id].value_type != "table":
                    continue
                if binding.context.role != "model":
                    raise ValueError(f"Scoped table {binding.concept_id!r} requires role 'model'")
                coordinates = [
                    other
                    for other in scope.bindings
                    if binding.concept_id in other.relationships.get("coordinate_for", ())
                    and self._compatible(binding.context, other.context)
                ]
                if not coordinates:
                    raise ValueError(f"Scoped table {binding.concept_id!r} lacks a compatible coordinate")
                coordinate_ids = {coordinate.concept_id for coordinate in coordinates}
                if not any(
                    coordinate_ids.intersection(other.relationships.get("dependent_on", ()))
                    and self._compatible(binding.context, other.context)
                    for other in scope.bindings
                ):
                    raise ValueError(
                        f"Scoped table {binding.concept_id!r} lacks a compatible dependent variable"
                    )
