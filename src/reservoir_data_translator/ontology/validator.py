"""Convention-aware validation for ontology definitions."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .convention import OntologyConvention
from .models import OntologyConcept


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True, slots=True)
class OntologyIssue:
    """One structured convention finding."""

    severity: ValidationSeverity
    code: str
    message: str
    concept_id: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        payload = asdict(self)
        payload["severity"] = self.severity.value
        return payload


@dataclass(frozen=True, slots=True)
class OntologyValidationResult:
    """Structured validator result grouped by severity."""

    issues: tuple[OntologyIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> tuple[OntologyIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[OntologyIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity is ValidationSeverity.WARNING
        )

    @property
    def infos(self) -> tuple[OntologyIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is ValidationSeverity.INFO
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "infos": [issue.to_dict() for issue in self.infos],
        }


def _normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[_\W]+", " ", normalized).split())


class OntologyValidator:
    """Validate concepts against a loaded ontology convention."""

    SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

    def __init__(self, convention: OntologyConvention) -> None:
        self.convention = convention
        self._concept_id_pattern = re.compile(convention.concept_id_pattern)
        self._alias_patterns = tuple(
            (re.compile(item.pattern, re.IGNORECASE), item.description)
            for item in convention.suspicious_alias_patterns
        )

    def validate(
        self,
        concepts: Sequence[OntologyConcept],
        *,
        ontology_version: str,
    ) -> OntologyValidationResult:
        concept_by_id = {concept.concept_id: concept for concept in concepts}
        issues: list[OntologyIssue] = []

        if not self.SEMVER_PATTERN.fullmatch(ontology_version):
            issues.append(
                OntologyIssue(
                    ValidationSeverity.ERROR,
                    "INVALID_ONTOLOGY_VERSION",
                    "Ontology version must use MAJOR.MINOR.PATCH semantic versioning.",
                    path="ontology.version",
                )
            )
        if not self.SEMVER_PATTERN.fullmatch(self.convention.version):
            issues.append(
                OntologyIssue(
                    ValidationSeverity.ERROR,
                    "INVALID_CONVENTION_VERSION",
                    "Convention version must use MAJOR.MINOR.PATCH semantic versioning.",
                    path="convention.version",
                )
            )

        self._validate_identity_and_references(concepts, concept_by_id, issues)

        for concept in concepts:
            self._validate_concept(concept, concept_by_id, issues)

        self._validate_cross_concept_aliases(concepts, issues)
        self._validate_inverse_relationships(concepts, concept_by_id, issues)
        self._validate_tables(concepts, concept_by_id, issues)

        severity_order = {
            ValidationSeverity.ERROR: 0,
            ValidationSeverity.WARNING: 1,
            ValidationSeverity.INFO: 2,
        }
        return OntologyValidationResult(
            tuple(
                sorted(
                    issues,
                    key=lambda item: (
                        severity_order[item.severity],
                        item.concept_id or "",
                        item.code,
                        item.path or "",
                    ),
                )
            )
        )

    def _validate_identity_and_references(
        self,
        concepts: Sequence[OntologyConcept],
        concept_by_id: Mapping[str, OntologyConcept],
        issues: list[OntologyIssue],
    ) -> None:
        occurrences: dict[str, list[OntologyConcept]] = {}
        for concept in concepts:
            occurrences.setdefault(concept.concept_id, []).append(concept)
        for concept_id, duplicates in occurrences.items():
            if len(duplicates) > 1:
                files = sorted(concept.source_file for concept in duplicates)
                issues.append(
                    OntologyIssue(
                        ValidationSeverity.ERROR,
                        "DUPLICATE_CONCEPT_ID",
                        f"concept_id {concept_id!r} is defined multiple times in {files}.",
                        concept_id=concept_id,
                        path="concept_id",
                    )
                )

        for root_id in self.convention.root_concepts:
            if root_id not in concept_by_id:
                issues.append(
                    OntologyIssue(
                        ValidationSeverity.ERROR,
                        "ROOT_CONCEPT_MISSING",
                        f"Configured root concept {root_id!r} is not defined.",
                        concept_id=root_id,
                        path="convention.root_concepts",
                    )
                )

        for concept in concepts:
            if concept.parent is not None and concept.parent not in concept_by_id:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "UNKNOWN_PARENT",
                        f"Parent concept {concept.parent!r} does not exist.",
                        concept,
                        "parent",
                    )
                )
            for relation, targets in concept.relationships.items():
                for index, target in enumerate(targets):
                    if target not in concept_by_id:
                        issues.append(
                            self._issue(
                                ValidationSeverity.ERROR,
                                "UNKNOWN_RELATIONSHIP_TARGET",
                                f"Relationship target {target!r} does not exist.",
                                concept,
                                f"relationships.{relation}[{index}]",
                            )
                        )

        reported_cycles: set[tuple[str, ...]] = set()
        for start in concept_by_id:
            path: list[str] = []
            positions: dict[str, int] = {}
            current: str | None = start
            while current is not None and current in concept_by_id:
                if current in positions:
                    cycle = path[positions[current] :]
                    cycle_key = tuple(sorted(cycle))
                    if cycle_key not in reported_cycles:
                        reported_cycles.add(cycle_key)
                        issues.append(
                            OntologyIssue(
                                ValidationSeverity.ERROR,
                                "PARENT_CYCLE",
                                f"Parent hierarchy contains a cycle: {cycle + [current]}.",
                                concept_id=current,
                                path="parent",
                            )
                        )
                    break
                positions[current] = len(path)
                path.append(current)
                current = concept_by_id[current].parent

    def _validate_concept(
        self,
        concept: OntologyConcept,
        concepts: Mapping[str, OntologyConcept],
        issues: list[OntologyIssue],
    ) -> None:
        concept_id = concept.concept_id
        if not self._concept_id_pattern.fullmatch(concept_id):
            issues.append(
                self._issue(
                    ValidationSeverity.ERROR,
                    "INVALID_CONCEPT_ID",
                    "concept_id must use the configured lowercase snake-case namespace.",
                    concept,
                    "concept_id",
                )
            )

        if concept.parent is None:
            if concept_id not in self.convention.root_concepts:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "UNDECLARED_ROOT_CONCEPT",
                        "A concept without a parent must be declared as an ontology root.",
                        concept,
                        "parent",
                    )
                )
        elif concept.parent not in self.convention.root_concepts:
            expected_prefix = f"{concept.parent}."
            if not concept_id.startswith(expected_prefix):
                reason = self.convention.hierarchy_exceptions.get(concept_id)
                if reason:
                    issues.append(
                        self._issue(
                            ValidationSeverity.INFO,
                            "HIERARCHY_EXCEPTION",
                            reason,
                            concept,
                            "parent",
                        )
                    )
                else:
                    issues.append(
                        self._issue(
                            ValidationSeverity.ERROR,
                            "CONCEPT_HIERARCHY_MISMATCH",
                            f"concept_id must start with {expected_prefix!r} for this parent.",
                            concept,
                            "parent",
                        )
                    )

        if concept.value_type not in self.convention.value_types:
            issues.append(
                self._issue(
                    ValidationSeverity.ERROR,
                    "UNKNOWN_VALUE_TYPE",
                    f"Unknown controlled value_type {concept.value_type!r}.",
                    concept,
                    "value_type",
                )
            )

        self._validate_lifecycle(concept, concepts, issues)
        self._validate_dimension_and_unit(concept, issues)
        self._validate_constraints(concept, issues)
        self._validate_aliases(concept, issues)
        self._validate_relationships(concept, concepts, issues)
        self._validate_source_pollution(concept, issues)

    def _validate_lifecycle(
        self,
        concept: OntologyConcept,
        concepts: Mapping[str, OntologyConcept],
        issues: list[OntologyIssue],
    ) -> None:
        if concept.status not in self.convention.lifecycle_statuses:
            issues.append(
                self._issue(
                    ValidationSeverity.ERROR,
                    "UNKNOWN_LIFECYCLE_STATUS",
                    f"Unknown lifecycle status {concept.status!r}.",
                    concept,
                    "status",
                )
            )
            return
        if concept.status == "deprecated" and concept.replaced_by is None:
            issues.append(
                self._issue(
                    ValidationSeverity.ERROR,
                    "DEPRECATED_REPLACEMENT_MISSING",
                    "A deprecated concept requires a replaced_by migration target.",
                    concept,
                    "replaced_by",
                )
            )
        if concept.status != "deprecated" and concept.replaced_by is not None:
            issues.append(
                self._issue(
                    ValidationSeverity.ERROR,
                    "ACTIVE_CONCEPT_HAS_REPLACEMENT",
                    "Only deprecated concepts may declare replaced_by.",
                    concept,
                    "replaced_by",
                )
            )
        if concept.replaced_by is not None:
            if concept.replaced_by == concept.concept_id:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "SELF_REPLACEMENT",
                        "A deprecated concept cannot replace itself.",
                        concept,
                        "replaced_by",
                    )
                )
            elif concept.replaced_by not in concepts:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "UNKNOWN_REPLACEMENT",
                        f"Replacement concept {concept.replaced_by!r} does not exist.",
                        concept,
                        "replaced_by",
                    )
                )

    def _validate_dimension_and_unit(
        self,
        concept: OntologyConcept,
        issues: list[OntologyIssue],
    ) -> None:
        if concept.dimension is None:
            if concept.canonical_unit is not None:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "UNIT_WITHOUT_DIMENSION",
                        "canonical_unit requires a controlled physical dimension.",
                        concept,
                        "canonical_unit",
                    )
                )
            return

        allowed_units = self.convention.dimensions.get(concept.dimension)
        if allowed_units is None:
            issues.append(
                self._issue(
                    ValidationSeverity.ERROR,
                    "UNKNOWN_DIMENSION",
                    f"Unknown controlled dimension {concept.dimension!r}.",
                    concept,
                    "dimension",
                )
            )
            return
        if concept.canonical_unit is None:
            issues.append(
                self._issue(
                    ValidationSeverity.ERROR,
                    "MISSING_CANONICAL_UNIT",
                    "A dimensional concept requires a canonical_unit.",
                    concept,
                    "canonical_unit",
                )
            )
        elif concept.canonical_unit not in allowed_units:
            issues.append(
                self._issue(
                    ValidationSeverity.ERROR,
                    "INCOMPATIBLE_CANONICAL_UNIT",
                    f"Unit {concept.canonical_unit!r} is not allowed for "
                    f"dimension {concept.dimension!r}.",
                    concept,
                    "canonical_unit",
                )
            )

    def _validate_constraints(
        self,
        concept: OntologyConcept,
        issues: list[OntologyIssue],
    ) -> None:
        for inclusive, exclusive in (
            ("minimum", "exclusive_minimum"),
            ("maximum", "exclusive_maximum"),
        ):
            if inclusive in concept.constraints and exclusive in concept.constraints:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "CONFLICTING_CONSTRAINT_KEYS",
                        f"Use either {inclusive!r} or {exclusive!r}, not both.",
                        concept,
                        "constraints",
                    )
                )
        for key, value in concept.constraints.items():
            if key not in self.convention.constraint_keys:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "UNKNOWN_CONSTRAINT",
                        f"Unknown controlled constraint {key!r}.",
                        concept,
                        f"constraints.{key}",
                    )
                )
            if key in {"minimum_points", "maximum_points"}:
                if concept.value_type != "table":
                    issues.append(
                        self._issue(
                            ValidationSeverity.ERROR,
                            "POINT_CONSTRAINT_ON_NON_TABLE",
                            f"{key} is only valid for table concepts.",
                            concept,
                            f"constraints.{key}",
                        )
                    )
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    issues.append(
                        self._issue(
                            ValidationSeverity.ERROR,
                            "INVALID_POINT_CONSTRAINT",
                            f"{key} must be a positive integer.",
                            concept,
                            f"constraints.{key}",
                        )
                    )
            elif not isinstance(value, (int, float)) or isinstance(value, bool):
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "NON_NUMERIC_CONSTRAINT",
                        f"{key} must be numeric.",
                        concept,
                        f"constraints.{key}",
                    )
                )

        lower = concept.constraints.get(
            "exclusive_minimum",
            concept.constraints.get("minimum"),
        )
        upper = concept.constraints.get(
            "exclusive_maximum",
            concept.constraints.get("maximum"),
        )
        if (
            isinstance(lower, (int, float))
            and not isinstance(lower, bool)
            and isinstance(upper, (int, float))
            and not isinstance(upper, bool)
            and lower >= upper
        ):
            issues.append(
                self._issue(
                    ValidationSeverity.ERROR,
                    "INVALID_CONSTRAINT_RANGE",
                    "The lower constraint bound must be smaller than the upper bound.",
                    concept,
                    "constraints",
                )
            )

        point_minimum = concept.constraints.get("minimum_points")
        point_maximum = concept.constraints.get("maximum_points")
        if (
            isinstance(point_minimum, int)
            and not isinstance(point_minimum, bool)
            and isinstance(point_maximum, int)
            and not isinstance(point_maximum, bool)
            and point_minimum > point_maximum
        ):
            issues.append(
                self._issue(
                    ValidationSeverity.ERROR,
                    "INVALID_POINT_CONSTRAINT_RANGE",
                    "minimum_points must not exceed maximum_points.",
                    concept,
                    "constraints",
                )
            )

    def _validate_aliases(
        self,
        concept: OntologyConcept,
        issues: list[OntologyIssue],
    ) -> None:
        normalized_aliases: set[str] = set()
        for index, alias in enumerate(concept.aliases):
            normalized = _normalize_alias(alias)
            if normalized in normalized_aliases:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "DUPLICATE_NORMALIZED_ALIAS",
                        f"Alias {alias!r} duplicates another alias after normalization.",
                        concept,
                        f"aliases[{index}]",
                    )
                )
            normalized_aliases.add(normalized)
            for pattern, description in self._alias_patterns:
                if pattern.search(alias.strip()):
                    issues.append(
                        self._issue(
                            ValidationSeverity.WARNING,
                            "ALIAS_LOOKS_LIKE_VALUE",
                            f"{description} Alias: {alias!r}.",
                            concept,
                            f"aliases[{index}]",
                        )
                    )

    def _validate_relationships(
        self,
        concept: OntologyConcept,
        concepts: Mapping[str, OntologyConcept],
        issues: list[OntologyIssue],
    ) -> None:
        for relation, targets in concept.relationships.items():
            rule = self.convention.relationships.get(relation)
            if rule is None:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "UNKNOWN_RELATIONSHIP",
                        f"Unknown controlled relationship {relation!r}.",
                        concept,
                        f"relationships.{relation}",
                    )
                )
                continue
            if concept.value_type not in rule.source_value_types:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "INVALID_RELATIONSHIP_SOURCE_TYPE",
                        f"{relation!r} does not allow source value_type "
                        f"{concept.value_type!r}.",
                        concept,
                        f"relationships.{relation}",
                    )
                )
            if not rule.allow_multiple and len(targets) > 1:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "RELATIONSHIP_CARDINALITY_ERROR",
                        f"{relation!r} does not allow multiple targets.",
                        concept,
                        f"relationships.{relation}",
                    )
                )
            for index, target_id in enumerate(targets):
                target = concepts.get(target_id)
                if target is not None and target.value_type not in rule.target_value_types:
                    issues.append(
                        self._issue(
                            ValidationSeverity.ERROR,
                            "INVALID_RELATIONSHIP_TARGET_TYPE",
                            f"{relation!r} does not allow target value_type "
                            f"{target.value_type!r}.",
                            concept,
                            f"relationships.{relation}[{index}]",
                        )
                    )

    def _validate_source_pollution(
        self,
        concept: OntologyConcept,
        issues: list[OntologyIssue],
    ) -> None:
        normalized_id_tokens = set(concept.concept_id.replace(".", "_").split("_"))
        for token in self.convention.source_specific_tokens:
            if token in normalized_id_tokens:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "SOURCE_SPECIFIC_CONCEPT_ID",
                        f"Source-specific token {token!r} is not allowed in concept_id.",
                        concept,
                        "concept_id",
                    )
                )
            name_tokens = set(_normalize_alias(concept.name).split())
            if token in name_tokens:
                issues.append(
                    self._issue(
                        ValidationSeverity.WARNING,
                        "SOURCE_SPECIFIC_NAME",
                        f"Name contains source-specific token {token!r}.",
                        concept,
                        "name",
                    )
                )
            description_tokens = set(_normalize_alias(concept.description).split())
            if token in description_tokens:
                issues.append(
                    self._issue(
                        ValidationSeverity.WARNING,
                        "SOURCE_SPECIFIC_DESCRIPTION",
                        f"Description contains source-specific token {token!r}.",
                        concept,
                        "description",
                    )
                )
            for index, alias in enumerate(concept.aliases):
                if token in set(_normalize_alias(alias).split()):
                    issues.append(
                        self._issue(
                            ValidationSeverity.WARNING,
                            "SOURCE_SPECIFIC_ALIAS",
                            f"Alias {alias!r} appears source-specific; prefer mappings/.",
                            concept,
                            f"aliases[{index}]",
                        )
                    )

    def _validate_cross_concept_aliases(
        self,
        concepts: Sequence[OntologyConcept],
        issues: list[OntologyIssue],
    ) -> None:
        owners: dict[str, set[str]] = {}
        display: dict[str, str] = {}
        for concept in concepts:
            for alias in concept.aliases:
                normalized = _normalize_alias(alias)
                owners.setdefault(normalized, set()).add(concept.concept_id)
                display.setdefault(normalized, alias)
        for normalized, concept_ids in owners.items():
            if len(concept_ids) > 1:
                issues.append(
                    OntologyIssue(
                        ValidationSeverity.WARNING,
                        "CROSS_CONCEPT_ALIAS_COLLISION",
                        f"Alias {display[normalized]!r} maps to multiple concepts: "
                        f"{sorted(concept_ids)}.",
                        path="aliases",
                    )
                )

    def _validate_inverse_relationships(
        self,
        concepts: Sequence[OntologyConcept],
        concept_by_id: Mapping[str, OntologyConcept],
        issues: list[OntologyIssue],
    ) -> None:
        for concept in concepts:
            for relation, targets in concept.relationships.items():
                rule = self.convention.relationships.get(relation)
                if rule is None or rule.inverse is None:
                    continue
                for target_id in targets:
                    target = concept_by_id.get(target_id)
                    if target is None:
                        continue
                    inverse_targets = target.relationships.get(rule.inverse, ())
                    if concept.concept_id not in inverse_targets:
                        issues.append(
                            self._issue(
                                ValidationSeverity.ERROR,
                                "INVERSE_RELATIONSHIP_MISSING",
                                f"{target_id!r} must declare inverse {rule.inverse!r} "
                                f"back to {concept.concept_id!r}.",
                                concept,
                                f"relationships.{relation}",
                            )
                        )

    def _validate_tables(
        self,
        concepts: Sequence[OntologyConcept],
        concept_by_id: Mapping[str, OntologyConcept],
        issues: list[OntologyIssue],
    ) -> None:
        coordinates_by_table: dict[str, set[str]] = {}
        for concept in concepts:
            for table_id in concept.relationships.get("coordinate_for", ()):
                coordinates_by_table.setdefault(table_id, set()).add(concept.concept_id)
                if concept.parent != table_id:
                    issues.append(
                        self._issue(
                            ValidationSeverity.WARNING,
                            "COORDINATE_TABLE_HIERARCHY_MISMATCH",
                            f"Coordinate parent is {concept.parent!r}, not table {table_id!r}.",
                            concept,
                            "relationships.coordinate_for",
                        )
                    )

        for table in (item for item in concepts if item.value_type == "table"):
            coordinates = coordinates_by_table.get(table.concept_id, set())
            if not coordinates:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "TABLE_COORDINATE_MISSING",
                        "A table concept requires at least one coordinate_for relationship.",
                        table,
                        "relationships",
                    )
                )
                continue
            dependents = [
                concept
                for concept in concepts
                if coordinates.intersection(concept.relationships.get("dependent_on", ()))
            ]
            if not dependents:
                issues.append(
                    self._issue(
                        ValidationSeverity.ERROR,
                        "TABLE_DEPENDENT_MISSING",
                        "A table concept requires at least one variable dependent on its coordinate.",
                        table,
                        "relationships",
                    )
                )

        for concept in concepts:
            for coordinate_id in concept.relationships.get("dependent_on", ()):
                coordinate = concept_by_id.get(coordinate_id)
                if coordinate is None:
                    continue
                coordinate_tables = coordinate.relationships.get("coordinate_for", ())
                if coordinate_tables and concept.parent not in coordinate_tables:
                    issues.append(
                        self._issue(
                            ValidationSeverity.WARNING,
                            "DEPENDENCY_TABLE_HIERARCHY_MISMATCH",
                            f"Dependent parent {concept.parent!r} is not one of coordinate "
                            f"tables {list(coordinate_tables)!r}.",
                            concept,
                            "relationships.dependent_on",
                        )
                    )

    @staticmethod
    def _issue(
        severity: ValidationSeverity,
        code: str,
        message: str,
        concept: OntologyConcept,
        field: str,
    ) -> OntologyIssue:
        return OntologyIssue(
            severity,
            code,
            message,
            concept_id=concept.concept_id,
            path=f"{concept.source_file}:{concept.concept_id}.{field}",
        )


def _render_text(result: OntologyValidationResult) -> str:
    lines = [
        f"Ontology validation: {'PASS' if result.valid else 'FAIL'} "
        f"errors={len(result.errors)} warnings={len(result.warnings)} "
        f"infos={len(result.infos)}",
    ]
    for issue in result.issues:
        location = issue.path or issue.concept_id or "ontology"
        lines.append(f"{issue.severity.value} {issue.code} {location}: {issue.message}")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    """Validate an ontology directory and return a merge-friendly exit code."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="ontology")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    from .loader import OntologyLoadError, OntologyLoader

    try:
        bundle = OntologyLoader.load(Path(args.path), enforce_convention=False)
    except OntologyLoadError as exc:
        if args.as_json:
            print(json.dumps({"valid": False, "load_error": str(exc)}, ensure_ascii=False))
        else:
            print(f"Ontology validation: FAIL\nERROR ONTOLOGY_LOAD_ERROR: {exc}")
        return 1

    if args.as_json:
        print(json.dumps(bundle.validation.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render_text(bundle.validation))
    return 0 if bundle.validation.valid else 1


if __name__ == "__main__":  # pragma: no cover - exercised through console entry point
    raise SystemExit(main())
