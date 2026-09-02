import json
import math

import pytest
from pydantic import ValidationError

from reservoir_data_translator.canonical import (
    FluidPhaseModel,
    FluidSystemModel,
    PVTModel,
    PVTPoint,
    PhysicalValue,
    Provenance,
    RelativePermeabilityModel,
    RelativePermeabilityPoint,
    ReservoirSimulationModel,
    RockModel,
    SCALModel,
    SimulationSchedule,
    WellConstraint,
    WellControl,
    WellModel,
)


def _value(
    value: float,
    unit: str,
    *,
    provenance: Provenance | None = None,
) -> PhysicalValue:
    return PhysicalValue(
        value=value,
        unit=unit,
        provenance=provenance,
        confidence=0.98 if provenance else None,
    )


def _canonical_demo() -> ReservoirSimulationModel:
    source = Provenance(
        source_id="demo-material",
        source_file="demo_material.txt",
        source_block_id="block-scal-1",
        source_location="lines 12-18",
        raw_text="Sw Krw Krow Pcow(bar)\n0.15 0.000 0.900 0.35",
        extraction_method="manual_fixture",
    )
    return ReservoirSimulationModel(
        schema_version="0.1.0",
        rock=RockModel(
            compressibility=_value(0.00001, "1/bar", provenance=source),
            reference_pressure=_value(200, "bar", provenance=source),
        ),
        fluids=FluidSystemModel(
            oil=FluidPhaseModel(
                density=_value(850, "kg/m3", provenance=source),
                pvt=PVTModel(
                    model_type="table",
                    points=[
                        PVTPoint(
                            pressure=_value(100, "bar", provenance=source),
                            formation_volume_factor=_value(
                                1.20,
                                "rm3/sm3",
                                provenance=source,
                            ),
                            viscosity=_value(2.5, "cP", provenance=source),
                        ),
                        PVTPoint(
                            pressure=_value(200, "bar", provenance=source),
                            formation_volume_factor=_value(
                                1.18,
                                "rm3/sm3",
                                provenance=source,
                            ),
                            viscosity=_value(2.2, "cP", provenance=source),
                        ),
                    ],
                ),
            ),
            water=FluidPhaseModel(
                density=_value(1000, "kg/m3", provenance=source)
            ),
            gas=FluidPhaseModel(
                density=_value(0.8, "kg/m3", provenance=source)
            ),
        ),
        scal=SCALModel(
            relative_permeability=[
                RelativePermeabilityModel(
                    id="ow-relperm-1",
                    sample_id="X-12",
                    phase_system=["oil", "water"],
                    displacement_type="water_displacing_oil",
                    points=[
                        RelativePermeabilityPoint(
                            sw=_value(0.15, "fraction", provenance=source),
                            krw=_value(0, "fraction", provenance=source),
                            kro=_value(0.9, "fraction", provenance=source),
                            pcow=_value(0.35, "bar", provenance=source),
                        ),
                        RelativePermeabilityPoint(
                            sw=_value(0.90, "fraction", provenance=source),
                            krw=_value(0.8, "fraction", provenance=source),
                            kro=_value(0, "fraction", provenance=source),
                            pcow=_value(0, "bar", provenance=source),
                        ),
                    ],
                )
            ]
        ),
        wells=[
            WellModel(
                id="A15",
                well_type="producer",
                controls=[
                    WellControl(
                        control_type="liquid_rate",
                        target=_value(500, "m3/day", provenance=source),
                        constraints=[
                            WellConstraint(
                                constraint_type="minimum_bhp",
                                value=_value(80, "bar", provenance=source),
                            )
                        ],
                    )
                ],
            )
        ],
        schedule=SimulationSchedule(
            duration=_value(1825, "day", provenance=source),
            report_interval=_value(90, "day", provenance=source),
        ),
    )


def test_canonical_demo_serializes_and_round_trips_as_json() -> None:
    model = _canonical_demo()

    encoded = model.model_dump_json(indent=2)
    decoded = json.loads(encoded)
    restored = ReservoirSimulationModel.model_validate_json(encoded)

    assert decoded["wells"][0]["id"] == "A15"
    assert decoded["fluids"]["oil"]["pvt"]["points"][0]["pressure"] == {
        "value": 100.0,
        "unit": "bar",
        "provenance": decoded["rock"]["compressibility"]["provenance"],
        "confidence": 0.98,
    }
    assert restored == model


def test_provenance_preserves_raw_source_text_exactly() -> None:
    raw_text = "  A15\t井采用定液 500 方/天\n"
    provenance = Provenance(
        source_id="source-1",
        raw_text=raw_text,
        extraction_method="manual",
    )

    assert provenance.raw_text == raw_text


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_physical_value_rejects_confidence_outside_unit_interval(
    confidence: float,
) -> None:
    with pytest.raises(ValidationError):
        PhysicalValue(value=1, unit="bar", confidence=confidence)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_physical_value_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValidationError):
        PhysicalValue(value=value, unit="bar")


def test_models_reject_unknown_fields_and_invalid_enum_values() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PhysicalValue(value=1, unit="bar", simulator_keyword="BHP")

    with pytest.raises(ValidationError):
        WellModel(id="A15", well_type="oil_well", controls=[])


def test_canonical_models_do_not_apply_later_domain_validation() -> None:
    point = RelativePermeabilityPoint(
        sw=_value(1.1, "fraction"),
        krw=_value(0.2, "fraction"),
        kro=_value(0.8, "fraction"),
    )

    assert point.sw.value == 1.1
