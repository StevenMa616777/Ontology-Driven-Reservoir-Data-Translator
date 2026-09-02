from pathlib import Path

import pytest

from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.canonical import (
    FluidPhaseModel,
    FluidSystemModel,
    PVTModel,
    PVTPoint,
    PhysicalValue,
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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_DIR = PROJECT_ROOT / "ontology"


@pytest.fixture(scope="session")
def registry() -> OntologyRegistry:
    return OntologyRegistry.load(ONTOLOGY_DIR)


def _value(value: float, unit: str) -> PhysicalValue:
    return PhysicalValue(value=value, unit=unit)


@pytest.fixture
def canonical_demo() -> ReservoirSimulationModel:
    return ReservoirSimulationModel(
        schema_version="0.1.0",
        rock=RockModel(
            compressibility=_value(1e-5, "1/bar"),
            reference_pressure=_value(200, "bar"),
        ),
        fluids=FluidSystemModel(
            oil=FluidPhaseModel(
                density=_value(850, "kg/m3"),
                pvt=PVTModel(
                    model_type="table",
                    points=[
                        PVTPoint(
                            pressure=_value(100, "bar"),
                            formation_volume_factor=_value(1.2, "rm3/sm3"),
                            viscosity=_value(2.5, "cP"),
                        ),
                        PVTPoint(
                            pressure=_value(200, "bar"),
                            formation_volume_factor=_value(1.18, "rm3/sm3"),
                            viscosity=_value(2.2, "cP"),
                        ),
                    ],
                ),
            ),
            water=FluidPhaseModel(
                density=_value(1000, "kg/m3"),
                pvt=PVTModel(
                    model_type="table",
                    points=[
                        PVTPoint(
                            pressure=_value(300, "bar"),
                            formation_volume_factor=_value(1.02, "rm3/sm3"),
                            viscosity=_value(0.45, "cP"),
                            compressibility=_value(4.2e-5, "1/bar"),
                        )
                    ],
                ),
            ),
            gas=FluidPhaseModel(density=_value(0.8, "kg/m3")),
        ),
        scal=SCALModel(
            relative_permeability=[
                RelativePermeabilityModel(
                    id="ow-1",
                    sample_id="X-12",
                    phase_system=["oil", "water"],
                    points=[
                        RelativePermeabilityPoint(
                            sw=_value(0.15, "fraction"),
                            krw=_value(0, "fraction"),
                            kro=_value(0.9, "fraction"),
                            pcow=_value(0.35, "bar"),
                        ),
                        RelativePermeabilityPoint(
                            sw=_value(0.9, "fraction"),
                            krw=_value(0.8, "fraction"),
                            kro=_value(0, "fraction"),
                            pcow=_value(0, "bar"),
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
                        target=_value(500, "m3/day"),
                        constraints=[
                            WellConstraint(
                                constraint_type="minimum_bhp",
                                value=_value(80, "bar"),
                            )
                        ],
                    )
                ],
            ),
            WellModel(
                id="C1",
                well_type="water_injector",
                controls=[
                    WellControl(
                        control_type="water_injection_rate",
                        target=_value(800, "m3/day"),
                        constraints=[
                            WellConstraint(
                                constraint_type="maximum_bhp",
                                value=_value(420, "bar"),
                            )
                        ],
                    )
                ],
            ),
        ],
        schedule=SimulationSchedule(
            duration=_value(1825, "day"),
            report_interval=_value(90, "day"),
        ),
    )
