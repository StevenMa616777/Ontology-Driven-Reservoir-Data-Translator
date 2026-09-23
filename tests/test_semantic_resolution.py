"""Semantic identity, context and storage are resolved independently and strictly."""

import pytest

from reservoir_data_translator.canonical.mapping_contract import (
    SemanticResolutionError,
    get_canonical_mapping_contract,
    list_canonical_mapping_contracts,
    normalize_semantic_identity,
    resolve_canonical_path,
)
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.ontology.context import SemanticContext


def test_one_pressure_concept_resolves_distinct_contexts() -> None:
    assert resolve_canonical_path(
        "physical.pressure", {"scope": "rock", "role": "reference"}, {},
    ) == "rock.reference_pressure"
    for phase in ("oil", "water", "gas"):
        assert resolve_canonical_path(
            "physical.pressure",
            {"scope": "pvt", "phase": phase, "role": "coordinate"},
            {"point_index": 2},
        ) == f"fluids.{phase}.pvt.points[2].pressure"


@pytest.mark.parametrize("rule", list_canonical_mapping_contracts(), ids=lambda rule: rule.path_template)
def test_every_canonical_rule_is_declared_and_round_trips(rule, registry: OntologyRegistry) -> None:
    registry.get_concept(rule.concept_id)
    registry.scopes.validate(rule.concept_id, rule.context)
    values = {"well_id": "A15", "table_id": "ow-1", "point_index": 2,
              "control_type": "water_injection_rate"}
    selectors = {name: values[name] for name in rule.selector_names}
    path = resolve_canonical_path(rule.concept_id, rule.context, selectors)
    assert path == rule.path_template.format(**selectors)
    assert rule.extract_selectors(path) == selectors
    assert get_canonical_mapping_contract(rule.concept_id, rule.context) == rule
    assert resolve_canonical_path(rule.concept_id, rule.context, selectors, path) == path


@pytest.mark.parametrize(("old_id", "new_id", "context", "path"), [
    ("fluid.gas.pvt.pressure", "physical.pressure",
     {"scope": "pvt", "phase": "gas", "role": "coordinate"},
     "fluids.gas.pvt.points[0].pressure"),
    ("fluid.oil.density", "physical.density",
     {"scope": "fluid_properties", "phase": "oil", "role": "property"},
     "fluids.oil.density"),
    ("rock.reference_pressure", "physical.pressure",
     {"scope": "rock", "role": "reference"}, "rock.reference_pressure"),
    ("scal.relative_permeability.kro", "physical.relative_permeability",
     {"scope": "relative_permeability", "phase": "oil", "role": "property", "phase_system": "oil_water"},
     "scal.relative_permeability[ow-1].points[0].kro"),
])
def test_legacy_identity_is_explicitly_migrated(old_id, new_id, context, path) -> None:
    assert normalize_semantic_identity(old_id) == (new_id, SemanticContext(**context))
    assert resolve_canonical_path(old_id, None, {}, path) == path


def test_legacy_phase_cannot_be_overridden() -> None:
    with pytest.raises(SemanticResolutionError) as error:
        normalize_semantic_identity("fluid.oil.pvt.pressure", {
            "scope": "pvt", "phase": "gas", "role": "coordinate",
        })
    assert error.value.code == "SEMANTIC_CONTEXT_CONFLICT"


@pytest.mark.parametrize("field", ["pressure", "temperature"])
def test_reference_condition_migration_does_not_invent_storage(field, registry) -> None:
    old_id = f"condition.reference.{field}"
    new_id, context = normalize_semantic_identity(old_id)
    assert new_id == f"physical.{field}"
    assert context == SemanticContext(scope="reference_condition", role="reference")
    registry.scopes.validate(new_id, context)
    assert get_canonical_mapping_contract(old_id) is None
    with pytest.raises(SemanticResolutionError) as error:
        resolve_canonical_path(old_id, None, {}, "rock.reference_pressure")
    assert error.value.code == "UNSUPPORTED_CANONICAL_MAPPING"


@pytest.mark.parametrize("phase", ["oil", "gas"])
@pytest.mark.parametrize("field", ["compressibility", "viscosibility"])
def test_scope_binding_does_not_imply_canonical_mapping_support(phase, field, registry) -> None:
    concept_id = f"physical.{field}"
    context = SemanticContext(scope="pvt", phase=phase, role="property")
    registry.scopes.validate(concept_id, context)
    assert get_canonical_mapping_contract(concept_id, context) is None
    with pytest.raises(SemanticResolutionError) as error:
        resolve_canonical_path(concept_id, context, {"point_index": 0})
    assert error.value.code == "UNSUPPORTED_CANONICAL_MAPPING"
    # No old ontology ID existed for these combinations.
    assert get_canonical_mapping_contract(f"fluid.{phase}.pvt.{field}") is None


def test_generic_context_never_inferred_from_storage_path() -> None:
    with pytest.raises(SemanticResolutionError) as error:
        resolve_canonical_path("physical.pressure", None, {"point_index": 0},
                               "fluids.gas.pvt.points[0].pressure")
    assert error.value.code == "SEMANTIC_CONTEXT_REQUIRED"
    assert get_canonical_mapping_contract("physical.pressure") is None


def test_context_and_supplied_path_must_agree() -> None:
    with pytest.raises(SemanticResolutionError) as error:
        resolve_canonical_path("physical.pressure", {
            "scope": "pvt", "phase": "gas", "role": "coordinate",
        }, {"point_index": 0}, "fluids.oil.pvt.points[0].pressure")
    assert error.value.code == "CANONICAL_PATH_MISMATCH"


@pytest.mark.parametrize("context", [
    {"scope": "pvt", "role": "coordinate"},
    {"scope": "pvt", "phase": "gas", "role": "property"},
    {"scope": "rock", "phase": "oil", "role": "reference"},
    {"scope": "made_up", "role": "coordinate"},
])
def test_ambiguous_or_incompatible_context_has_no_fallback(context) -> None:
    with pytest.raises(SemanticResolutionError) as error:
        resolve_canonical_path("physical.pressure", context, {})
    assert error.value.code == "UNSUPPORTED_CANONICAL_MAPPING"


@pytest.mark.parametrize("selectors", [
    {"point_index": True}, {"point_index": -1}, {"point_index": "01"},
    {"point_index": 1.1}, {"point_index": "0].pressure"},
    {"point_index": 0, "well_id": "A15"}, {"phase": "gas"},
])
def test_resolver_rejects_malformed_or_extraneous_selectors(selectors) -> None:
    with pytest.raises(SemanticResolutionError) as error:
        resolve_canonical_path("physical.pressure", {
            "scope": "pvt", "phase": "gas", "role": "coordinate",
        }, selectors)
    assert error.value.code == "INVALID_CANONICAL_SELECTORS"


@pytest.mark.parametrize("well_id", ["A.1", "A[1]", "A/1", "A 1", "A\n", ""])
def test_unsafe_entity_selectors_are_rejected(well_id) -> None:
    with pytest.raises(SemanticResolutionError) as error:
        resolve_canonical_path("well", None, {"well_id": well_id})
    assert error.value.code == "INVALID_CANONICAL_SELECTORS"


def test_new_ir_requires_separate_grouping_selectors() -> None:
    with pytest.raises(SemanticResolutionError) as error:
        resolve_canonical_path("physical.pressure", {
            "scope": "pvt", "phase": "gas", "role": "coordinate",
        }, {}, "fluids.gas.pvt.points[0].pressure")
    assert error.value.code == "CANONICAL_SELECTORS_REQUIRED"


def test_explicit_selectors_cannot_disagree_with_supplied_path() -> None:
    with pytest.raises(SemanticResolutionError) as error:
        resolve_canonical_path("well", None, {"well_id": "A15"}, "wells[B15].id")
    assert error.value.code == "CANONICAL_PATH_MISMATCH"


def test_minimum_bhp_remains_bound_to_production_control() -> None:
    with pytest.raises(SemanticResolutionError) as error:
        resolve_canonical_path("well.constraint.minimum_bhp", None, {},
                               "wells[C1].controls[water_injection_rate].constraints[minimum_bhp].value")
    assert error.value.code == "CANONICAL_PATH_MISMATCH"
