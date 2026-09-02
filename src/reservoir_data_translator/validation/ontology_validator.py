"""L2 validation of canonical instances against ontology semantics."""

from __future__ import annotations

from reservoir_data_translator.canonical import ReservoirSimulationModel
from reservoir_data_translator.ontology import OntologyRegistry

from .models import ValidationIssue, ValidationResult
from .traversal import (
    CONSTRAINT_CONCEPTS,
    CONTROL_CONCEPTS,
    WELL_TYPE_CONCEPTS,
    iter_physical_values,
)


class OntologyInstanceValidator:
    """Check canonical units and applies-to relationships for case instances."""

    def __init__(self, registry: OntologyRegistry) -> None:
        self._registry = registry

    def validate(self, model: ReservoirSimulationModel) -> ValidationResult:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []

        for observation in iter_physical_values(model):
            concept = self._registry.get_concept(observation.concept_id)
            if observation.value.unit != concept.canonical_unit:
                errors.append(
                    ValidationIssue(
                        code="ONTOLOGY_UNIT_ERROR",
                        path=f"{observation.path}.unit",
                        message=(
                            f"{observation.concept_id} requires canonical unit "
                            f"{concept.canonical_unit!r}, got "
                            f"{observation.value.unit!r}."
                        ),
                        layer="ontology",
                    )
                )

        for well_index, well in enumerate(model.wells):
            well_concept = WELL_TYPE_CONCEPTS.get(well.well_type)
            if well_concept is None:
                warnings.append(
                    ValidationIssue(
                        code="ONTOLOGY_CONTEXT_UNRESOLVED",
                        path=f"wells[{well_index}].well_type",
                        message=(
                            f"Well {well.id!r} has unknown type; applies-to "
                            "relationships require human review."
                        ),
                        layer="ontology",
                    )
                )

            for control_index, control in enumerate(well.controls):
                control_path = f"wells[{well_index}].controls[{control_index}]"
                control_concept = CONTROL_CONCEPTS.get(control.control_type)
                if control_concept is None:
                    errors.append(
                        ValidationIssue(
                            code="ONTOLOGY_CONCEPT_MISSING",
                            path=f"{control_path}.control_type",
                            message=(
                                f"Control type {control.control_type!r} has no "
                                "v0.1 ontology concept."
                            ),
                            layer="ontology",
                        )
                    )
                elif well_concept is not None and not self._applies_to(
                    control_concept,
                    well_concept,
                ):
                    errors.append(
                        ValidationIssue(
                            code="ONTOLOGY_RELATIONSHIP_ERROR",
                            path=f"{control_path}.control_type",
                            message=(
                                f"{control_concept} does not apply to {well_concept}."
                            ),
                            layer="ontology",
                        )
                    )

                for constraint_index, constraint in enumerate(control.constraints):
                    constraint_concept = CONSTRAINT_CONCEPTS[
                        constraint.constraint_type
                    ]
                    if well_concept is not None and not self._applies_to(
                        constraint_concept,
                        well_concept,
                    ):
                        errors.append(
                            ValidationIssue(
                                code="ONTOLOGY_RELATIONSHIP_ERROR",
                                path=(
                                    f"{control_path}.constraints[{constraint_index}]."
                                    "constraint_type"
                                ),
                                message=(
                                    f"{constraint_concept} does not apply to "
                                    f"{well_concept}."
                                ),
                                layer="ontology",
                            )
                        )

        return ValidationResult(errors=errors, warnings=warnings)

    def _applies_to(self, property_concept: str, entity_concept: str) -> bool:
        declared_targets = self._registry.get_relationships(property_concept).get(
            "applies_to",
            (),
        )
        return any(
            self._same_or_descendant(entity_concept, target)
            for target in declared_targets
        )

    def _same_or_descendant(self, concept_id: str, ancestor_id: str) -> bool:
        current: str | None = concept_id
        while current is not None:
            if current == ancestor_id:
                return True
            current = self._registry.get_concept(current).parent
        return False
