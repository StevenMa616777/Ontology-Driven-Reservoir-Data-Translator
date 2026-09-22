from dataclasses import replace
import json

from reservoir_data_translator.ontology import (
    OntologyConcept,
    OntologyRegistry,
    OntologyValidationResult,
    OntologyValidator,
)
from reservoir_data_translator.ontology.validator import main

from conftest import ONTOLOGY_DIR


def _replace_concept(
    concepts: list[OntologyConcept],
    target_concept_id: str,
    **changes: object,
) -> list[OntologyConcept]:
    return [
        replace(concept, **changes)
        if concept.concept_id == target_concept_id
        else concept
        for concept in concepts
    ]


def _validate(
    registry: OntologyRegistry,
    concepts: list[OntologyConcept],
    *,
    version: str = "0.1.0",
) -> OntologyValidationResult:
    return OntologyValidator(registry.convention).validate(
        concepts,
        ontology_version=version,
    )


def _codes(result: OntologyValidationResult) -> set[str]:
    return {issue.code for issue in result.issues}


def test_validator_rejects_duplicate_concept_ids(
    registry: OntologyRegistry,
) -> None:
    concepts = registry.list_concepts()
    concepts.append(registry.get_concept("rock.compressibility"))

    result = _validate(registry, concepts)

    assert "DUPLICATE_CONCEPT_ID" in _codes(result)
    assert not result.valid


def test_validator_enforces_value_type_dimension_and_unit_vocabularies(
    registry: OntologyRegistry,
) -> None:
    concepts = _replace_concept(
        registry.list_concepts(),
        "physical.viscosity",
        value_type="number",
        canonical_unit="Pa.s",
    )
    concepts = _replace_concept(
        concepts,
        "physical.density",
        dimension="mass_density",
    )

    result = _validate(registry, concepts)

    assert {
        "UNKNOWN_VALUE_TYPE",
        "UNKNOWN_DIMENSION",
        "INCOMPATIBLE_CANONICAL_UNIT",
    } <= _codes(result)


def test_validator_enforces_concept_id_parent_hierarchy(
    registry: OntologyRegistry,
) -> None:
    concepts = _replace_concept(
        registry.list_concepts(),
        "physical.relative_permeability",
        parent="fluid.pvt",
    )

    result = _validate(registry, concepts)

    assert "CONCEPT_HIERARCHY_MISMATCH" in _codes(result)


def test_validator_enforces_relationship_vocabulary(
    registry: OntologyRegistry,
) -> None:
    concepts = _replace_concept(
        registry.list_concepts(),
        "physical.relative_permeability",
        relationships={
            "depends_on": ("physical.water_saturation",)
        },
    )

    result = _validate(registry, concepts)

    assert "UNKNOWN_RELATIONSHIP" in _codes(result)


def test_validator_enforces_relationship_endpoint_types(
    registry: OntologyRegistry,
) -> None:
    concepts = _replace_concept(
        registry.list_concepts(),
        "well.control.liquid_rate",
        relationships={"applies_to": ("physical.pressure",)},
    )

    result = _validate(registry, concepts)

    assert "INVALID_RELATIONSHIP_TARGET_TYPE" in _codes(result)


def test_validator_detects_alias_values_duplicates_and_collisions(
    registry: OntologyRegistry,
) -> None:
    concepts = _replace_concept(
        registry.list_concepts(),
        "schedule.report_interval",
        aliases=("report interval", "quarterly", "REPORT INTERVAL"),
    )
    concepts = _replace_concept(
        concepts,
        "physical.density",
        aliases=("water density", "dynamic viscosity"),
    )

    result = _validate(registry, concepts)

    assert {
        "ALIAS_LOOKS_LIKE_VALUE",
        "DUPLICATE_NORMALIZED_ALIAS",
        "CROSS_CONCEPT_ALIAS_COLLISION",
    } <= _codes(result)
    assert len(result.warnings) >= 2


def test_core_vocabulary_does_not_require_context_specific_table_edges(
    registry: OntologyRegistry,
) -> None:
    result = _validate(registry, registry.list_concepts())
    assert result.valid
    assert "TABLE_COORDINATE_MISSING" not in _codes(result)
    assert registry.get_relationships("physical.pressure") == {}
    assert registry.get_relationships("physical.viscosity") == {}


def test_validator_requires_inverse_reference_relationship(
    registry: OntologyRegistry,
) -> None:
    concepts = _replace_concept(
        registry.list_concepts(),
        "rock.compressibility",
        relationships={"referenced_at": ("physical.pressure",)},
    )

    result = _validate(registry, concepts)

    assert "INVERSE_RELATIONSHIP_MISSING" in _codes(result)


def test_validator_rejects_invalid_constraints_and_version(
    registry: OntologyRegistry,
) -> None:
    concepts = _replace_concept(
        registry.list_concepts(),
        "physical.relative_permeability",
        constraints={"minimum": 1, "maximum": 0},
    )

    result = _validate(registry, concepts, version="0.1")

    assert {"INVALID_CONSTRAINT_RANGE", "INVALID_ONTOLOGY_VERSION"} <= _codes(
        result
    )


def test_validator_detects_parent_cycles(registry: OntologyRegistry) -> None:
    concepts = _replace_concept(
        registry.list_concepts(),
        "rock",
        parent="rock.compressibility",
    )

    result = _validate(registry, concepts)

    assert "PARENT_CYCLE" in _codes(result)


def test_validator_enforces_deprecation_migration_target(
    registry: OntologyRegistry,
) -> None:
    concepts = _replace_concept(
        registry.list_concepts(),
        "rock.compressibility",
        status="deprecated",
        replaced_by=None,
    )

    result = _validate(registry, concepts)

    assert "DEPRECATED_REPLACEMENT_MISSING" in _codes(result)


def test_validator_detects_source_specific_concept_pollution(
    registry: OntologyRegistry,
) -> None:
    concepts = _replace_concept(
        registry.list_concepts(),
        "physical.pressure",
        concept_id="eclipse.reference_pressure",
    )

    result = _validate(registry, concepts)

    assert "SOURCE_SPECIFIC_CONCEPT_ID" in _codes(result)


def test_validation_cli_returns_structured_json(
    capsys: object,
) -> None:
    exit_code = main([str(ONTOLOGY_DIR), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["warnings"] == []
