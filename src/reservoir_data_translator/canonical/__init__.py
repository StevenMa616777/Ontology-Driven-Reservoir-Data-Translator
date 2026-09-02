"""Platform-independent canonical reservoir simulation data models."""

from .builder import CanonicalBuildError, CanonicalBuilder
from .mapping_contract import (
    CanonicalMappingContract,
    accepts_canonical_path,
    get_canonical_mapping_contract,
)
from .models import (
    CanonicalModel,
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
from .schema import generate_json_schemas, write_json_schemas

__all__ = [
    "CanonicalBuildError",
    "CanonicalBuilder",
    "CanonicalModel",
    "CanonicalMappingContract",
    "FluidPhaseModel",
    "FluidSystemModel",
    "PVTModel",
    "PVTPoint",
    "PhysicalValue",
    "Provenance",
    "RelativePermeabilityModel",
    "RelativePermeabilityPoint",
    "ReservoirSimulationModel",
    "RockModel",
    "SCALModel",
    "SimulationSchedule",
    "WellConstraint",
    "WellControl",
    "WellModel",
    "accepts_canonical_path",
    "generate_json_schemas",
    "get_canonical_mapping_contract",
    "write_json_schemas",
]
