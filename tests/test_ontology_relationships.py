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


def test_reference_condition_relationship_is_machine_readable_and_inverse(
    registry: OntologyRegistry,
) -> None:
    assert registry.validate_relationship(
        "fluid.oil.density",
        "referenced_at",
        "condition.reference",
    )
    assert registry.validate_relationship(
        "condition.reference",
        "reference_for",
        "fluid.oil.density",
    )


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
