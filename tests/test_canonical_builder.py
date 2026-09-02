from collections.abc import Iterable
from typing import Any

import pytest

from reservoir_data_translator.canonical import (
    CanonicalBuildError,
    CanonicalBuilder,
    Provenance,
    ReservoirSimulationModel,
)
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.semantic import SemanticMapping


def _mapping(
    concept: str,
    path: str,
    value: Any,
    *,
    source_unit: str | None = None,
    canonical_unit: str | None = None,
    block: str = "demo-block",
) -> SemanticMapping:
    return SemanticMapping(
        source_text=f"manual mapping for {concept}",
        source_block_id=block,
        ontology_concept=concept,
        canonical_path=path,
        value=value,
        source_unit=source_unit,
        canonical_unit=canonical_unit,
        confidence=0.98,
        provenance=Provenance(
            source_id="demo-material",
            source_file="demo_material.txt",
            source_block_id=block,
            source_location="manual fixture",
            raw_text=f"manual mapping for {concept}",
            extraction_method="manual_fixture",
        ),
    )


def _demo_mappings() -> list[SemanticMapping]:
    physical = [
        _mapping(
            "rock.compressibility",
            "rock.compressibility",
            1e-5,
            source_unit="1/bar",
            canonical_unit="1/bar",
        ),
        _mapping(
            "rock.reference_pressure",
            "rock.reference_pressure",
            20,
            source_unit="MPa",
            canonical_unit="bar",
        ),
        _mapping(
            "fluid.oil.density",
            "fluids.oil.density",
            0.85,
            source_unit="g/cm3",
            canonical_unit="kg/m3",
        ),
        _mapping(
            "fluid.water.density",
            "fluids.water.density",
            1.0,
            source_unit="g/cm3",
            canonical_unit="kg/m3",
        ),
        _mapping(
            "fluid.gas.density",
            "fluids.gas.density",
            0.8,
            source_unit="kg/m3",
            canonical_unit="kg/m3",
        ),
    ]
    for index, (pressure, fvf, viscosity) in enumerate(
        [(100, 1.20, 2.5), (200, 1.18, 2.2)]
    ):
        physical.extend(
            [
                _mapping(
                    "fluid.oil.pvt.pressure",
                    f"fluids.oil.pvt.points[{index}].pressure",
                    pressure,
                    source_unit="bar",
                    canonical_unit="bar",
                ),
                _mapping(
                    "fluid.oil.pvt.formation_volume_factor",
                    f"fluids.oil.pvt.points[{index}].formation_volume_factor",
                    fvf,
                    source_unit="rm3/sm3",
                    canonical_unit="rm3/sm3",
                ),
                _mapping(
                    "fluid.oil.pvt.viscosity",
                    f"fluids.oil.pvt.points[{index}].viscosity",
                    viscosity,
                    source_unit="cP",
                    canonical_unit="cP",
                ),
            ]
        )
    for index, (sw, krw, kro, pcow) in enumerate(
        [(0.15, 0.0, 0.9, 0.35), (0.9, 0.8, 0.0, 0.0)]
    ):
        for concept, field, value, unit in [
            ("scal.relative_permeability.water_saturation", "sw", sw, "fraction"),
            ("scal.relative_permeability.krw", "krw", krw, "fraction"),
            ("scal.relative_permeability.kro", "kro", kro, "fraction"),
            ("scal.relative_permeability.pcow", "pcow", pcow, "bar"),
        ]:
            physical.append(
                _mapping(
                    concept,
                    f"scal.relative_permeability[ow-relperm-1].points[{index}].{field}",
                    value,
                    source_unit=unit,
                    canonical_unit=unit,
                )
            )

    return [
        *physical,
        _mapping(
            "fluid.oil.pvt",
            "fluids.oil.pvt",
            {"model_type": "table"},
        ),
        _mapping(
            "scal.relative_permeability",
            "scal.relative_permeability[ow-relperm-1]",
            {
                "id": "ow-relperm-1",
                "sample_id": "X-12",
                "phase_system": ["oil", "water"],
                "displacement_type": "water_displacing_oil",
            },
        ),
        _mapping("well", "wells[A15].id", "A15"),
        _mapping("well.producer", "wells[A15].well_type", "producer"),
        _mapping(
            "well.control.liquid_rate",
            "wells[A15].controls[liquid_rate].target",
            500,
            source_unit="m3/day",
            canonical_unit="m3/day",
        ),
        _mapping(
            "well.constraint.minimum_bhp",
            "wells[A15].controls[liquid_rate].constraints[minimum_bhp].value",
            80,
            source_unit="bar",
            canonical_unit="bar",
        ),
        _mapping("well", "wells[C1].id", "C1"),
        _mapping(
            "well.water_injector",
            "wells[C1].well_type",
            "water_injector",
        ),
        _mapping(
            "well.control.water_injection_rate",
            "wells[C1].controls[water_injection_rate].target",
            800,
            source_unit="m3/day",
            canonical_unit="m3/day",
        ),
        _mapping(
            "well.constraint.maximum_bhp",
            (
                "wells[C1].controls[water_injection_rate]."
                "constraints[maximum_bhp].value"
            ),
            420,
            source_unit="bar",
            canonical_unit="bar",
        ),
        _mapping(
            "schedule.duration",
            "schedule.duration",
            5,
            source_unit="year",
            canonical_unit="day",
        ),
        _mapping(
            "schedule.report_interval",
            "schedule.report_interval",
            3,
            source_unit="month",
            canonical_unit="day",
        ),
    ]


@pytest.fixture
def demo_mappings() -> list[SemanticMapping]:
    return _demo_mappings()


def test_builder_constructs_normalized_canonical_demo(
    registry: OntologyRegistry,
    demo_mappings: list[SemanticMapping],
) -> None:
    model = CanonicalBuilder(registry).build(demo_mappings)

    assert isinstance(model, ReservoirSimulationModel)
    assert model.rock.reference_pressure.value == pytest.approx(200)
    assert model.fluids.oil is not None
    assert model.fluids.oil.density is not None
    assert model.fluids.oil.density.value == pytest.approx(850)
    assert model.fluids.oil.density.unit == "kg/m3"
    assert model.fluids.oil.pvt is not None
    assert len(model.fluids.oil.pvt.points) == 2
    assert model.scal.relative_permeability[0].sample_id == "X-12"
    assert [well.id for well in model.wells] == ["A15", "C1"]
    assert model.wells[1].controls[0].constraints[0].constraint_type == "maximum_bhp"
    assert model.schedule.duration is not None
    assert model.schedule.duration.value == 1825
    assert model.schedule.report_interval is not None
    assert model.schedule.report_interval.value == 90
    assert model.schedule.duration.provenance is not None
    assert model.schedule.duration.provenance.source_id == "demo-material"


def test_builder_output_is_independent_of_mapping_order(
    registry: OntologyRegistry,
    demo_mappings: list[SemanticMapping],
) -> None:
    builder = CanonicalBuilder(registry)
    forward = builder.build(demo_mappings)
    reverse = builder.build(reversed(demo_mappings))

    assert forward == reverse


def test_builder_rejects_invented_concept(registry: OntologyRegistry) -> None:
    mapping = _mapping(
        "fluid.oil.magic_number",
        "fluids.oil.magic_number",
        1,
    )

    with pytest.raises(CanonicalBuildError) as error:
        CanonicalBuilder(registry).build([mapping])

    assert error.value.code == "UNKNOWN_ONTOLOGY_CONCEPT"


def test_builder_rejects_concept_path_mismatch(registry: OntologyRegistry) -> None:
    mapping = _mapping(
        "well.control.liquid_rate",
        "wells[A15].controls[water_injection_rate].target",
        500,
        source_unit="m3/day",
        canonical_unit="m3/day",
    )

    with pytest.raises(CanonicalBuildError) as error:
        CanonicalBuilder(registry).build([mapping])

    assert error.value.code == "CANONICAL_PATH_MISMATCH"


def test_builder_rejects_canonical_unit_not_owned_by_ontology(
    registry: OntologyRegistry,
) -> None:
    mapping = _mapping(
        "rock.reference_pressure",
        "rock.reference_pressure",
        200,
        source_unit="bar",
        canonical_unit="psi",
    )

    with pytest.raises(CanonicalBuildError) as error:
        CanonicalBuilder(registry).build([mapping])

    assert error.value.code == "CANONICAL_UNIT_MISMATCH"


def test_builder_does_not_silently_discard_duplicate_evidence(
    registry: OntologyRegistry,
) -> None:
    mapping = _mapping(
        "schedule.duration",
        "schedule.duration",
        5,
        source_unit="year",
        canonical_unit="day",
    )

    with pytest.raises(CanonicalBuildError) as error:
        CanonicalBuilder(registry).build([mapping, mapping])

    assert error.value.code == "DUPLICATE_CANONICAL_ASSIGNMENT"
