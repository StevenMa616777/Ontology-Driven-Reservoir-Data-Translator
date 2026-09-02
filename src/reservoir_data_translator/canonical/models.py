"""Pydantic models for the platform-independent canonical data layer.

These models define storage shape and basic type constraints only. Unit
conversion, ontology relationship checks, engineering rules, and target-platform
requirements belong to later deterministic pipeline stages.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints


NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
Confidence = Annotated[FiniteFloat, Field(ge=0, le=1)]


class CanonicalModel(BaseModel):
    """Shared strict configuration for every canonical object."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
    )


class Provenance(CanonicalModel):
    """Trace one canonical value back to its source block and extraction step."""

    source_id: NonEmptyString
    source_file: NonEmptyString | None = None
    source_block_id: NonEmptyString | None = None
    source_location: NonEmptyString | None = None
    raw_text: str | None = None
    extraction_method: NonEmptyString


class PhysicalValue(CanonicalModel):
    """A finite numeric value with an explicit physical unit and traceability."""

    value: FiniteFloat
    unit: NonEmptyString
    provenance: Provenance | None = None
    confidence: Confidence | None = None


class RelativePermeabilityPoint(CanonicalModel):
    """One point in an oil-water relative-permeability table."""

    sw: PhysicalValue
    krw: PhysicalValue
    kro: PhysicalValue
    pcow: PhysicalValue | None = None


class RelativePermeabilityModel(CanonicalModel):
    """Platform-independent oil-water SCAL table and its sample context."""

    id: NonEmptyString
    sample_id: NonEmptyString | None = None
    phase_system: list[NonEmptyString]
    displacement_type: NonEmptyString | None = None
    points: list[RelativePermeabilityPoint]


class PVTPoint(CanonicalModel):
    """One pressure coordinate and its available PVT dependent variables."""

    pressure: PhysicalValue
    formation_volume_factor: PhysicalValue | None = None
    viscosity: PhysicalValue | None = None
    compressibility: PhysicalValue | None = None
    viscosibility: PhysicalValue | None = None


class PVTModel(CanonicalModel):
    """A platform-independent constant or tabular PVT representation."""

    model_type: Literal["table", "constant"]
    points: list[PVTPoint]


class RockModel(CanonicalModel):
    """Rock properties in canonical units, without simulator keywords."""

    compressibility: PhysicalValue | None = None
    reference_pressure: PhysicalValue | None = None


class FluidPhaseModel(CanonicalModel):
    """Canonical properties shared by the supported oil, water, and gas phases."""

    density: PhysicalValue | None = None
    pvt: PVTModel | None = None


class FluidSystemModel(CanonicalModel):
    """Supported fluid phases; absent source data remains explicitly absent."""

    oil: FluidPhaseModel | None = None
    water: FluidPhaseModel | None = None
    gas: FluidPhaseModel | None = None


class SCALModel(CanonicalModel):
    """Special-core-analysis tables available to the simulation case."""

    relative_permeability: list[RelativePermeabilityModel] = Field(
        default_factory=list
    )


class WellConstraint(CanonicalModel):
    """An operating pressure bound attached to a well control."""

    constraint_type: Literal["minimum_bhp", "maximum_bhp"]
    value: PhysicalValue


class WellControl(CanonicalModel):
    """A platform-independent well target and its operating constraints."""

    control_type: Literal[
        "liquid_rate",
        "oil_rate",
        "water_rate",
        "gas_rate",
        "water_injection_rate",
        "gas_injection_rate",
        "bhp",
    ]
    target: PhysicalValue
    constraints: list[WellConstraint]


class WellModel(CanonicalModel):
    """A named well and its controls for the v0.1 MVP representation."""

    id: NonEmptyString
    well_type: Literal[
        "producer",
        "water_injector",
        "gas_injector",
        "unknown",
    ]
    controls: list[WellControl]


class SimulationSchedule(CanonicalModel):
    """Simulation duration and requested report cadence."""

    duration: PhysicalValue | None = None
    report_interval: PhysicalValue | None = None


class ReservoirSimulationModel(CanonicalModel):
    """Root model and sole platform-independent canonical source of truth."""

    schema_version: NonEmptyString
    rock: RockModel
    fluids: FluidSystemModel
    scal: SCALModel
    wells: list[WellModel]
    schedule: SimulationSchedule
