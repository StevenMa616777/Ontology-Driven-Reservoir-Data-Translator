from pathlib import Path
from shutil import copyfile

import pytest

from reservoir_data_translator.ontology import OntologyLoadError, OntologyRegistry

from conftest import ONTOLOGY_DIR


def test_loads_manifest_and_all_domain_files(registry: OntologyRegistry) -> None:
    assert registry.metadata.version == "0.1.0"
    assert registry.metadata.convention_version == "0.1.0"
    assert registry.metadata.namespace == "reservoir_simulation"
    assert len(registry) == 51

    source_files = {concept.source_file for concept in registry.list_concepts()}
    assert source_files == {
        "concepts/rock.yaml",
        "concepts/fluid.yaml",
        "concepts/scal.yaml",
        "concepts/well.yaml",
        "concepts/schedule.yaml",
        "concepts/condition.yaml",
    }


def test_loaded_concept_preserves_required_semantics(
    registry: OntologyRegistry,
) -> None:
    concept = registry.get_concept("scal.relative_permeability.krw")

    assert concept.name == "Water Relative Permeability"
    assert concept.parent == "scal.relative_permeability"
    assert concept.value_type == "float"
    assert concept.dimension == "dimensionless"
    assert concept.canonical_unit == "fraction"
    assert concept.constraints == {"minimum": 0, "maximum": 1}


def test_registry_can_load_from_manifest_path() -> None:
    registry = OntologyRegistry.load(ONTOLOGY_DIR / "ontology_v0.1.yaml")
    assert registry.get_concept("rock.compressibility").canonical_unit == "1/bar"


def test_registry_exposes_clean_convention_validation(
    registry: OntologyRegistry,
) -> None:
    assert registry.validation.valid
    assert registry.validation.errors == ()
    assert registry.validation.warnings == ()
    assert {issue.code for issue in registry.validation.infos} == {
        "HIERARCHY_EXCEPTION"
    }


def test_loader_rejects_unknown_relationship_target(tmp_path: Path) -> None:
    copyfile(
        ONTOLOGY_DIR / "conventions_v0.1.yaml",
        tmp_path / "conventions_v0.1.yaml",
    )
    (tmp_path / "concepts.yaml").write_text(
        """
concepts:
  - concept_id: reservoir_simulation
    name: Reservoir Simulation
    parent: null
    description: Test root.
    value_type: object
    dimension: null
    canonical_unit: null
    aliases: [reservoir simulation]
    constraints: {}
    relationships:
      applies_to: [missing.concept]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "ontology_v0.1.yaml").write_text(
        """
ontology:
  version: "0.1.0"
  name: Test
  namespace: test
  domain: Test
  convention_file: conventions_v0.1.yaml
  concept_files: [concepts.yaml]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(OntologyLoadError, match="UNKNOWN_RELATIONSHIP_TARGET"):
        OntologyRegistry.load(tmp_path)


def test_unknown_concept_raises_key_error(registry: OntologyRegistry) -> None:
    with pytest.raises(KeyError):
        registry.get_concept("not.a.real.concept")
