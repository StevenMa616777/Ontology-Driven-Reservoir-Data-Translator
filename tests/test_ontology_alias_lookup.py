from reservoir_data_translator.ontology import OntologyRegistry


def _ids(registry: OntologyRegistry, query: str) -> list[str]:
    return [concept.concept_id for concept in registry.search_by_alias(query)]


def test_alias_lookup_is_case_insensitive(registry: OntologyRegistry) -> None:
    assert _ids(registry, "krw") == ["scal.relative_permeability.krw"]


def test_alias_lookup_handles_field_name_separators(
    registry: OntologyRegistry,
) -> None:
    assert _ids(registry, "Liquid Rate") == ["well.control.liquid_rate"]
    assert _ids(registry, "LiquidRate") == ["well.control.liquid_rate"]
    assert _ids(registry, "liquid_rate") == ["well.control.liquid_rate"]


def test_alias_lookup_finds_alias_embedded_in_chinese_source_text(
    registry: OntologyRegistry,
) -> None:
    matches = _ids(registry, "A15井采用定液生产制度，日产液500方。")
    assert matches[0] == "well.control.liquid_rate"


def test_unknown_alias_returns_empty_list(registry: OntologyRegistry) -> None:
    assert registry.search_by_alias("XYZ_COEFF") == []


def test_schedule_values_are_not_ontology_aliases(
    registry: OntologyRegistry,
) -> None:
    assert registry.search_by_alias("quarterly") == []
    assert registry.search_by_alias("每季度") == []
