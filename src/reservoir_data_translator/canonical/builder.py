"""Deterministically assemble the canonical model from semantic mappings."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from pydantic import ValidationError

from reservoir_data_translator.ontology import OntologyConcept, OntologyRegistry
from reservoir_data_translator.semantic.unit_normalizer import (
    UnitNormalizationError,
    UnitNormalizer,
)

from .models import PhysicalValue, ReservoirSimulationModel
from .mapping_contract import get_canonical_mapping_contract

if TYPE_CHECKING:
    from reservoir_data_translator.semantic.models import SemanticMapping


@dataclass(frozen=True, slots=True)
class _PathToken:
    name: str
    selector: str | None = None


class CanonicalBuildError(ValueError):
    """Structured failure raised instead of guessing during canonical assembly."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        mapping_index: int | None = None,
    ) -> None:
        self.code = code
        self.path = path
        self.mapping_index = mapping_index
        super().__init__(message)


_PATH_SEGMENT = re.compile(
    r"^(?P<name>[a-z][a-z0-9_]*)(?:\[(?P<selector>[^\[\]]+)\])?$"
)
class CanonicalBuilder:
    """Build a v0.1 canonical model without LLM calls or inferred values.

    Collection selectors in canonical paths are stable grouping keys, for
    example ``wells[A15]`` and ``points[0]``. The final Pydantic model contains
    lists sorted by those selectors, so output does not depend on mapping order.
    """

    _COLLECTIONS = {
        "relative_permeability",
        "points",
        "wells",
        "controls",
        "constraints",
    }

    _WELL_TYPES = {
        "well.producer": "producer",
        "well.water_injector": "water_injector",
        "well.gas_injector": "gas_injector",
    }

    def __init__(
        self,
        registry: OntologyRegistry,
        *,
        unit_normalizer: UnitNormalizer | None = None,
    ) -> None:
        self._registry = registry
        self._unit_normalizer = unit_normalizer or UnitNormalizer()

    def build(
        self,
        mappings: Iterable[SemanticMapping],
        *,
        schema_version: str = "0.1.0",
    ) -> ReservoirSimulationModel:
        """Validate and assemble mappings into the sole canonical source of truth."""

        document: dict[str, Any] = {
            "schema_version": schema_version,
            "rock": {},
            "fluids": {},
            "scal": {"relative_permeability": {}},
            "wells": {},
            "schedule": {},
        }
        for index, mapping in enumerate(mappings):
            try:
                concept = self._registry.get_concept(mapping.ontology_concept)
            except KeyError as exc:
                raise CanonicalBuildError(
                    "UNKNOWN_ONTOLOGY_CONCEPT",
                    f"Unknown ontology concept {mapping.ontology_concept!r}",
                    path=mapping.canonical_path,
                    mapping_index=index,
                ) from exc

            path_contract = get_canonical_mapping_contract(concept.concept_id)
            if path_contract is None:
                raise CanonicalBuildError(
                    "UNSUPPORTED_CANONICAL_MAPPING",
                    f"Concept {concept.concept_id!r} has no v0.1 builder mapping",
                    path=mapping.canonical_path,
                    mapping_index=index,
                )
            if not path_contract.accepts(mapping.canonical_path):
                raise CanonicalBuildError(
                    "CANONICAL_PATH_MISMATCH",
                    (
                        f"Canonical path {mapping.canonical_path!r} is not valid for "
                        f"concept {concept.concept_id!r}"
                    ),
                    path=mapping.canonical_path,
                    mapping_index=index,
                )

            try:
                value = self._mapping_value(mapping, concept)
                tokens = self._parse_path(mapping.canonical_path)
                self._validate_selector_semantics(tokens, mapping, concept)
                self._assign(document, tokens, value)
            except CanonicalBuildError as exc:
                if exc.mapping_index is None:
                    exc.mapping_index = index
                raise
            except UnitNormalizationError as exc:
                raise CanonicalBuildError(
                    exc.code,
                    str(exc),
                    path=mapping.canonical_path,
                    mapping_index=index,
                ) from exc

        materialized = self._materialize(document)
        self._add_collection_defaults(materialized)
        try:
            return ReservoirSimulationModel.model_validate(materialized)
        except ValidationError as exc:
            first = exc.errors(include_url=False)[0]
            path = self._format_location(first["loc"])
            raise CanonicalBuildError(
                "CANONICAL_MODEL_INVALID",
                f"Canonical model validation failed at {path}: {first['msg']}",
                path=path,
            ) from exc

    def _mapping_value(
        self,
        mapping: SemanticMapping,
        concept: OntologyConcept,
    ) -> Any:
        if concept.canonical_unit is not None:
            if mapping.source_unit is None:
                raise CanonicalBuildError(
                    "SOURCE_UNIT_REQUIRED",
                    f"Concept {concept.concept_id!r} requires an explicit source unit",
                    path=mapping.canonical_path,
                )
            if (
                mapping.canonical_unit is not None
                and mapping.canonical_unit != concept.canonical_unit
            ):
                raise CanonicalBuildError(
                    "CANONICAL_UNIT_MISMATCH",
                    (
                        f"Mapping declares canonical unit {mapping.canonical_unit!r}; "
                        f"ontology requires {concept.canonical_unit!r}"
                    ),
                    path=mapping.canonical_path,
                )
            normalized = self._unit_normalizer.normalize(
                mapping.value,
                mapping.source_unit,
                concept.canonical_unit,
            )
            return PhysicalValue(
                value=normalized,
                unit=concept.canonical_unit,
                provenance=mapping.provenance,
                confidence=mapping.confidence,
            ).model_dump()

        if mapping.source_unit is not None or mapping.canonical_unit is not None:
            raise CanonicalBuildError(
                "UNEXPECTED_UNIT",
                f"Non-physical concept {concept.concept_id!r} must not declare units",
                path=mapping.canonical_path,
            )
        if concept.concept_id in self._WELL_TYPES:
            expected = self._WELL_TYPES[concept.concept_id]
            if mapping.value != expected:
                raise CanonicalBuildError(
                    "ONTOLOGY_VALUE_MISMATCH",
                    f"Concept {concept.concept_id!r} requires value {expected!r}",
                    path=mapping.canonical_path,
                )
            return expected
        return deepcopy(mapping.value)

    @staticmethod
    def _parse_path(path: str) -> tuple[_PathToken, ...]:
        tokens: list[_PathToken] = []
        for segment in path.split("."):
            match = _PATH_SEGMENT.fullmatch(segment)
            if match is None:
                raise CanonicalBuildError(
                    "INVALID_CANONICAL_PATH",
                    f"Invalid canonical path segment {segment!r}",
                    path=path,
                )
            tokens.append(_PathToken(match.group("name"), match.group("selector")))
        return tuple(tokens)

    def _validate_selector_semantics(
        self,
        tokens: tuple[_PathToken, ...],
        mapping: SemanticMapping,
        concept: OntologyConcept,
    ) -> None:
        selectors = {token.name: token.selector for token in tokens if token.selector}
        if concept.concept_id == "well":
            well_id = selectors["wells"]
            if mapping.value != well_id:
                raise CanonicalBuildError(
                    "ENTITY_SELECTOR_MISMATCH",
                    f"Well value {mapping.value!r} does not match selector {well_id!r}",
                    path=mapping.canonical_path,
                )
        if concept.concept_id == "scal.relative_permeability":
            if not isinstance(mapping.value, Mapping):
                raise CanonicalBuildError(
                    "STRUCTURAL_VALUE_REQUIRED",
                    "Relative-permeability mapping must contain table metadata",
                    path=mapping.canonical_path,
                )
            table_id = selectors["relative_permeability"]
            if mapping.value.get("id", table_id) != table_id:
                raise CanonicalBuildError(
                    "ENTITY_SELECTOR_MISMATCH",
                    "Relative-permeability id does not match its path selector",
                    path=mapping.canonical_path,
                )
        if concept.concept_id.endswith(".pvt") and not isinstance(
            mapping.value,
            Mapping,
        ):
            raise CanonicalBuildError(
                "STRUCTURAL_VALUE_REQUIRED",
                "PVT mapping must contain model metadata",
                path=mapping.canonical_path,
            )

    def _assign(
        self,
        document: dict[str, Any],
        tokens: tuple[_PathToken, ...],
        value: Any,
    ) -> None:
        current = document
        for token in tokens[:-1]:
            container = current.setdefault(token.name, {})
            if not isinstance(container, dict):
                raise CanonicalBuildError(
                    "CANONICAL_PATH_CONFLICT",
                    f"Path component {token.name!r} is already a scalar value",
                )
            if token.selector is None:
                current = container
            else:
                child = container.setdefault(token.selector, {})
                if not isinstance(child, dict):
                    raise CanonicalBuildError(
                        "CANONICAL_PATH_CONFLICT",
                        f"Selector {token.selector!r} is already a scalar value",
                    )
                current = child

        final = tokens[-1]
        if final.selector is None:
            self._merge_assignment(current, final.name, value)
            return
        collection = current.setdefault(final.name, {})
        if not isinstance(collection, dict):
            raise CanonicalBuildError(
                "CANONICAL_PATH_CONFLICT",
                f"Collection {final.name!r} is already a scalar value",
            )
        self._merge_assignment(collection, final.selector, value)

    def _merge_assignment(
        self,
        container: dict[str, Any],
        key: str,
        value: Any,
    ) -> None:
        if key not in container:
            container[key] = deepcopy(value)
            return
        existing = container[key]
        if isinstance(existing, dict) and isinstance(value, Mapping):
            for nested_key, nested_value in value.items():
                self._merge_assignment(existing, str(nested_key), nested_value)
            return
        raise CanonicalBuildError(
            "DUPLICATE_CANONICAL_ASSIGNMENT",
            f"Canonical field {key!r} received more than one mapping",
        )

    def _materialize(self, value: Any, *, collection_name: str | None = None) -> Any:
        if not isinstance(value, dict):
            return value
        if collection_name in self._COLLECTIONS:
            materialized = []
            for selector in sorted(value, key=self._selector_sort_key):
                item = deepcopy(value[selector])
                if collection_name == "wells":
                    item.setdefault("id", selector)
                    item.setdefault("well_type", "unknown")
                    item.setdefault("controls", {})
                elif collection_name == "controls":
                    item.setdefault("control_type", selector)
                    item.setdefault("constraints", {})
                elif collection_name == "constraints":
                    item.setdefault("constraint_type", selector)
                elif collection_name == "relative_permeability":
                    item.setdefault("id", selector)
                    item.setdefault("points", {})
                materialized.append(self._materialize(item))
            return materialized
        return {
            key: self._materialize(item, collection_name=key)
            for key, item in value.items()
        }

    @staticmethod
    def _selector_sort_key(selector: str) -> tuple[int, int | str, str]:
        if selector.isdecimal():
            return (0, int(selector), selector)
        return (1, selector.casefold(), selector)

    @staticmethod
    def _add_collection_defaults(document: dict[str, Any]) -> None:
        for phase in document["fluids"].values():
            if isinstance(phase, dict) and isinstance(phase.get("pvt"), dict):
                phase["pvt"].setdefault("points", [])

    @staticmethod
    def _format_location(location: tuple[Any, ...]) -> str:
        path = ""
        for part in location:
            if isinstance(part, int):
                path += f"[{part}]"
            else:
                path += ("." if path else "") + str(part)
        return path or "$"
