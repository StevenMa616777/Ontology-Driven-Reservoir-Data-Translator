from pathlib import Path

import pytest

from reservoir_data_translator.canonical import ReservoirSimulationModel
from reservoir_data_translator.mappers import (
    CMGDemoMapper,
    EclipseDemoMapper,
    PlatformMapperRegistry,
    PlatformMappingError,
    PlatformMappingRegistry,
)
from reservoir_data_translator.ontology import OntologyRegistry
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
        "SWOF",
        "WCONPROD",
        "WCONINJE",
        "TSTEP",
    ]
    assert "SWOF\n" in content
    assert "WCONPROD\n" in content
    assert "'A15' 'OPEN' 'LRAT'" in content
    assert "WCONINJE\n" in content
    assert "'C1' 'WATER' 'OPEN' 'RATE'" in content
    assert mapped.blocks[0].records[0].source_paths == [
        "rock.reference_pressure",
        "rock.compressibility",
    ]


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
