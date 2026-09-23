import pytest

from reservoir_data_translator.ontology import OntologyRegistry


def test_get_relationships_returns_declared_targets(
    registry: OntologyRegistry,
) -> None:
    relationships = registry.get_relationships("well.control.liquid_rate")
    assert relationships == {"applies_to": ("well.producer",)}


def test_valid_relationship_is_accepted(registry: OntologyRegistry) -> None:
    assert registry.validate_relationship(
        "well.control.water_injection_rate",
        "applies_to",
        "well.water_injector",
    )


def test_reference_condition_relationship_is_scoped_and_inverse(
    registry: OntologyRegistry,
) -> None:
    bindings = registry.scopes.get_scope("rock").bindings
    compressibility = next(item for item in bindings if item.concept_id == "rock.compressibility")
    pressure = next(item for item in bindings if item.concept_id == "physical.pressure")
    assert compressibility.relationships == {"referenced_at": ("physical.pressure",)}
    assert pressure.relationships == {"reference_for": ("rock.compressibility",)}
    assert registry.get_relationships("physical.pressure") == {}


def test_semantically_invalid_relationship_is_rejected(
    registry: OntologyRegistry,
) -> None:
    assert not registry.validate_relationship(
        "well.control.water_injection_rate",
        "applies_to",
        "well.producer",
    )


def test_unknown_relationship_name_is_rejected(registry: OntologyRegistry) -> None:
    assert not registry.validate_relationship(
        "well.control.liquid_rate",
        "depends_on",
        "well.producer",
    )


def test_unknown_relationship_endpoint_raises_key_error(
    registry: OntologyRegistry,
) -> None:
    with pytest.raises(KeyError):
        registry.validate_relationship(
            "well.control.liquid_rate",
            "applies_to",
            "well.unknown",
        )
