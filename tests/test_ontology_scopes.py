from dataclasses import FrozenInstanceError
from pathlib import Path
from shutil import copytree

import pytest
from pydantic import ValidationError
import yaml

from reservoir_data_translator.ontology import (
    OntologyLoadError,
    OntologyRegistry,
    ScopeRegistry,
    SemanticContext,
)

from conftest import ONTOLOGY_DIR


def _scope_document() -> dict:
    return yaml.safe_load((ONTOLOGY_DIR / "scopes.yaml").read_text(encoding="utf-8"))


def _load_document(tmp_path: Path, registry: OntologyRegistry, document: dict) -> ScopeRegistry:
    path = tmp_path / "scopes.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return ScopeRegistry.load(
        path,
        {concept.concept_id for concept in registry.list_concepts()},
        concepts=registry.list_concepts(),
        convention=registry.convention,
    )


def test_physical_quantity_is_reused_across_scopes_and_phases(registry: OntologyRegistry) -> None:
    contexts = [binding.context for binding in registry.scopes.bindings if binding.concept_id == "physical.pressure"]
    assert set(contexts) == {
        *(SemanticContext(scope="pvt", phase=phase, role="coordinate") for phase in ("oil", "water", "gas")),
        SemanticContext(scope="rock", role="reference"),
        SemanticContext(scope="reference_condition", role="reference"),
    }
    for context in contexts:
        registry.scopes.validate("physical.pressure", context)
    assert all(not concept.concept_id.startswith(("fluid.oil.pvt", "fluid.water.pvt", "fluid.gas.pvt")) for concept in registry.list_concepts())
    assert registry.get_concept("rock.compressibility").description.startswith("Rock pore-volume")


def test_scopes_hold_relationships_without_global_pressure_dependency(registry: OntologyRegistry) -> None:
    viscosity = next(binding for binding in registry.scopes.bindings if binding.concept_id == "physical.viscosity" and binding.context.phase == "gas")
    assert viscosity.relationships == {"dependent_on": ("physical.pressure",)}
    assert registry.get_relationships("physical.viscosity") == {}
    assert registry.get_relationships("physical.pressure") == {}


@pytest.mark.parametrize("context", [
    SemanticContext(scope="pvt", role="coordinate"),
    SemanticContext(scope="pvt", phase="oil", role="reference"),
    SemanticContext(scope="rock", phase="oil", role="reference"),
    SemanticContext(scope="relative_permeability", phase="water", role="coordinate", phase_system="oil_water"),
    SemanticContext(scope="unknown", role="coordinate"),
])
def test_undeclared_context_cannot_be_guessed(registry: OntologyRegistry, context: SemanticContext) -> None:
    with pytest.raises(ValueError, match="Undeclared semantic context"):
        registry.scopes.validate("physical.pressure", context)


def test_semantic_context_and_scope_bindings_are_immutable(registry: OntologyRegistry) -> None:
    binding = registry.scopes.bindings[0]
    with pytest.raises(ValidationError):
        binding.context.scope = "rock"
    with pytest.raises(FrozenInstanceError):
        binding.concept_id = "physical.pressure"
    with pytest.raises(TypeError):
        binding.relationships["applies_to"] = ("well",)
    with pytest.raises(ValidationError):
        SemanticContext(scope="pvt", canonical_path="fluids.gas.pvt")
    with pytest.raises(ValidationError):
        SemanticContext(scope="pvt", phase="steam")


def test_detection_preserves_multiple_domains_within_one_block(registry: OntologyRegistry) -> None:
    detected = registry.scopes.detect("气相PVT包含 gas viscosity；A15井定液500方；模拟周期5年")
    assert {"pvt", "well_controls", "schedule"} <= set(detected)
    assert registry.scopes.detect("flower soap tropical example") == ()
    assert "pvt" in registry.scopes.detect("pressure Bo MUO")
    assert {"pvt", "well_controls"} <= set(registry.scopes.detect("OilPVT LiquidRate"))


def test_phase_system_allows_oil_permeability_against_water_saturation(registry: OntologyRegistry) -> None:
    context = SemanticContext(scope="relative_permeability", phase="oil", role="property", phase_system="oil_water")
    registry.scopes.validate("physical.relative_permeability", context)
    with pytest.raises(ValueError):
        registry.scopes.validate("physical.relative_permeability", context.model_copy(update={"phase_system": "gas_water"}))


@pytest.mark.parametrize("mutation, message", [
    ("duplicate_scope", "Duplicate scope"),
    ("duplicate_binding", "Duplicate binding"),
    ("unknown_concept", "Unknown binding concept"),
    ("unknown_target", "Unknown scoped relationship target"),
    ("unknown_relation", "Unknown scoped relationship"),
    ("mismatched_scope", "context scope does not match"),
    ("unknown_phase", "Input should be"),
    ("storage_path", "Invalid binding fields"),
    ("coordinate_missing", "lacks a compatible coordinate"),
    ("dependent_missing", "lacks a compatible dependent variable"),
    ("inverse_missing", "Missing scoped inverse"),
    ("target_type", "Invalid target type"),
    ("coordinate_role", "requires role 'coordinate'"),
])
def test_scopes_fail_closed_on_invalid_definitions(tmp_path: Path, registry: OntologyRegistry, mutation: str, message: str) -> None:
    document = _scope_document()
    pvt = document["scopes"][0]
    coordinate = next(item for item in pvt["bindings"] if item["concept_id"] == "physical.pressure" and item["context"]["phase"] == "oil")
    if mutation == "duplicate_scope":
        document["scopes"].append(pvt)
    elif mutation == "duplicate_binding":
        pvt["bindings"].append(coordinate)
    elif mutation == "unknown_concept":
        coordinate["concept_id"] = "missing.concept"
    elif mutation == "unknown_target":
        coordinate["relationships"] = {"coordinate_for": ["missing.table"]}
    elif mutation == "unknown_relation":
        coordinate["relationships"] = {"made_up_relation": ["fluid.pvt"]}
    elif mutation == "mismatched_scope":
        coordinate["context"]["scope"] = "rock"
    elif mutation == "unknown_phase":
        coordinate["context"]["phase"] = "steam"
    elif mutation == "storage_path":
        coordinate["canonical_path"] = "fluids.oil.pvt.points[i].pressure"
    elif mutation == "coordinate_missing":
        coordinate["relationships"] = {}
    elif mutation == "dependent_missing":
        for item in pvt["bindings"]:
            if item["context"].get("phase") == "oil" and item["context"].get("role") == "property":
                item["relationships"] = {}
    elif mutation == "inverse_missing":
        rock = next(scope for scope in document["scopes"] if scope["scope_id"] == "rock")
        rock["bindings"][1]["relationships"] = {}
    elif mutation == "target_type":
        coordinate["relationships"] = {"coordinate_for": ["well"]}
    elif mutation == "coordinate_role":
        coordinate["context"]["role"] = "property"
    with pytest.raises(ValueError, match=message):
        _load_document(tmp_path, registry, document)


def test_scope_yaml_does_not_construct_python_objects(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text("!!python/object/apply:builtins.str ['bad']", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot read semantic scopes"):
        ScopeRegistry.load(path, {"physical.pressure"})


def test_manifest_scope_file_cannot_escape_ontology_directory(tmp_path: Path) -> None:
    root = tmp_path / "ontology"
    copytree(ONTOLOGY_DIR, root)
    manifest_path = root / "ontology_v0.1.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["ontology"]["scope_file"] = "../scopes.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(OntologyLoadError, match="Scope file escapes ontology root"):
        OntologyRegistry.load(root)
