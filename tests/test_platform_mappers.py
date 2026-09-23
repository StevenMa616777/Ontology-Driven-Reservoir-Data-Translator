from pathlib import Path

import pytest

from reservoir_data_translator.canonical import PhysicalValue, ReservoirSimulationModel
from reservoir_data_translator.mappers import (
    CMGDemoMapper,
    EclipseDemoMapper,
    PlatformMapperRegistry,
    PlatformMappingError,
    PlatformMappingRegistry,
)
from reservoir_data_translator.ontology import OntologyRegistry, SemanticContext
from reservoir_data_translator.validation import ExportValidator, ValidationEngine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def platform_mappers(registry: OntologyRegistry):
    eclipse = EclipseDemoMapper(
        PlatformMappingRegistry.load(
            PROJECT_ROOT / "mappings" / "eclipse.yaml",
            registry,
        )
    )
    cmg = CMGDemoMapper(
        PlatformMappingRegistry.load(
            PROJECT_ROOT / "mappings" / "cmg.yaml",
            registry,
        )
    )
    return eclipse, cmg


def test_eclipse_mapper_separates_mapping_from_rendering(
    canonical_demo: ReservoirSimulationModel,
    platform_mappers,
) -> None:
    eclipse, _ = platform_mappers

    validation = eclipse.validate_export(canonical_demo)
    mapped = eclipse.map(canonical_demo)
    content = eclipse.render(mapped)

    assert validation.valid
    assert "ECLIPSE_HOST_DECK_CONTEXT_REQUIRED" in {
        warning.code for warning in validation.warnings
    }
    assert [block.keyword for block in mapped.blocks] == [
        "ROCK",
        "DENSITY",
        "PVDO",
        "PVTW",
        "SWOF",
        "WCONPROD",
        "WCONINJE",
        "TSTEP",
    ]
    assert "SWOF\n" in content
    assert "PVTW\n" in content
    assert "300 1.02 4.2e-05 0.45 1* /" in content
    assert "WCONPROD\n" in content
    assert "'A15' 'OPEN' 'LRAT'" in content
    assert "WCONINJE\n" in content
    assert "'C1' 'WATER' 'OPEN' 'RATE'" in content
    assert mapped.blocks[0].records[0].source_paths == [
        "rock.reference_pressure",
        "rock.compressibility",
    ]


def test_eclipse_mapper_accepts_single_constant_water_pvt(
    canonical_demo: ReservoirSimulationModel,
    platform_mappers,
) -> None:
    eclipse, _ = platform_mappers
    assert canonical_demo.fluids.water is not None
    assert canonical_demo.fluids.water.pvt is not None
    canonical_demo.fluids.water.pvt.model_type = "constant"

    exported = eclipse.export(canonical_demo)

    assert "PVTW" in exported.content


def test_cmg_and_eclipse_consume_same_canonical_without_mutation(
    canonical_demo: ReservoirSimulationModel,
    platform_mappers,
) -> None:
    eclipse, cmg = platform_mappers
    before = canonical_demo.model_dump_json()

    eclipse_export = eclipse.export(canonical_demo)
    cmg_export = cmg.export(canonical_demo)

    assert canonical_demo.model_dump_json() == before
    assert eclipse_export.mapped_model.platform == "eclipse"
    assert cmg_export.mapped_model.platform == "cmg"
    assert "*WELL 'A15'" in cmg_export.content
    assert "*OPERATE 'A15' *MAX *STL 500" in cmg_export.content
    assert "*INJECTOR 'C1' *WATER" in cmg_export.content
    assert "CMG_DEMO_NONWELL_DATA_NOT_EXPORTED" in {
        warning.code for warning in cmg_export.validation.warnings
    }


def test_mappers_integrate_with_l4_validation(
    registry: OntologyRegistry,
    canonical_demo: ReservoirSimulationModel,
    platform_mappers,
) -> None:
    eclipse, cmg = platform_mappers
    engine = ValidationEngine(
        registry,
        export_validator=ExportValidator([eclipse, cmg]),
    )

    assert engine.validate(canonical_demo, target_platform="eclipse").valid
    assert engine.validate(canonical_demo, target_platform="cmg").valid


def test_export_validation_blocks_unsupported_or_empty_content(
    canonical_demo: ReservoirSimulationModel,
    platform_mappers,
) -> None:
    eclipse, cmg = platform_mappers
    canonical_demo.wells[0].controls[0].control_type = "gas_rate"

    with pytest.raises(PlatformMappingError) as error:
        eclipse.export(canonical_demo)
    assert error.value.code == "EXPORT_NOT_READY"

    canonical_demo.wells = []
    cmg_result = cmg.validate_export(canonical_demo)
    assert not cmg_result.valid
    assert cmg_result.errors[0].code == "CMG_NO_EXPORTABLE_WELLS"


def test_platform_mapper_registry_routes_targets(platform_mappers) -> None:
    eclipse, cmg = platform_mappers
    registry = PlatformMapperRegistry([eclipse, cmg])

    assert registry.list_platforms() == ("cmg", "eclipse")
    assert registry.get("ECLIPSE") is eclipse
    with pytest.raises(KeyError, match="petrel"):
        registry.get("petrel")


def test_platform_rules_distinguish_reused_pvt_concept_by_context(
    registry: OntologyRegistry,
) -> None:
    mappings = PlatformMappingRegistry.load(PROJECT_ROOT / "mappings" / "eclipse.yaml", registry)

    for phase, keyword in (("oil", "PVDO"), ("water", "PVTW"), ("gas", "PVDG")):
        context = SemanticContext(scope="pvt", phase=phase, role="model")
        assert mappings.target_for("fluid.pvt", context) == keyword
        assert mappings.supports("fluid.pvt", context)
    with pytest.raises(KeyError, match="context"):
        mappings.target_for("fluid.pvt")
    assert not mappings.supports("fluid.pvt")


def test_platform_rule_rejects_invalid_scope_and_duplicate_identity(
    registry: OntologyRegistry,
) -> None:
    rule = {
        "concept_id": "fluid.pvt",
        "semantic_context": {"scope": "rock", "phase": "gas", "role": "model"},
        "target_type": "PVDG",
    }
    payload = {
        "platform": "eclipse", "version": "test", "dialect": "test",
        "mappings": {"pvt_gas": rule},
    }
    with pytest.raises(ValueError):
        PlatformMappingRegistry.from_mapping(payload, registry)

    rule["semantic_context"]["scope"] = "pvt"
    payload["mappings"]["same_semantics"] = dict(rule)
    with pytest.raises(ValueError, match="Duplicate semantic output mapping"):
        PlatformMappingRegistry.from_mapping(payload, registry)

    rule.pop("semantic_context")
    payload["mappings"] = {"pvt_unspecified": rule}
    with pytest.raises(ValueError, match="explicit semantic_context"):
        PlatformMappingRegistry.from_mapping(payload, registry)


@pytest.mark.parametrize("field", ["compressibility", "viscosibility"])
@pytest.mark.parametrize("phase_name", ["oil", "gas"])
def test_eclipse_export_refuses_to_drop_unsupported_pvt_properties(
    canonical_demo: ReservoirSimulationModel,
    platform_mappers,
    field: str,
    phase_name: str,
) -> None:
    eclipse, _ = platform_mappers
    phase = getattr(canonical_demo.fluids, phase_name)
    if phase.pvt is None:
        phase.pvt = canonical_demo.fluids.oil.pvt.model_copy(deep=True)
    setattr(phase.pvt.points[0], field, PhysicalValue(value=1e-5, unit="1/bar"))

    validation = eclipse.validate_export(canonical_demo)

    assert any(
        issue.code == "ECLIPSE_PVT_PROPERTY_UNSUPPORTED"
        and issue.path == f"fluids.{phase_name}.pvt.points[0].{field}"
        for issue in validation.errors
    )
    with pytest.raises(PlatformMappingError):
        eclipse.export(canonical_demo)
