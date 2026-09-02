"""Canonical physical-value traversal shared by instance validators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from reservoir_data_translator.canonical import PhysicalValue, ReservoirSimulationModel


CONTROL_CONCEPTS = {
    "liquid_rate": "well.control.liquid_rate",
    "water_injection_rate": "well.control.water_injection_rate",
}
CONSTRAINT_CONCEPTS = {
    "minimum_bhp": "well.constraint.minimum_bhp",
    "maximum_bhp": "well.constraint.maximum_bhp",
}
WELL_TYPE_CONCEPTS = {
    "producer": "well.producer",
    "water_injector": "well.water_injector",
    "gas_injector": "well.gas_injector",
}


@dataclass(frozen=True, slots=True)
class PhysicalObservation:
    concept_id: str
    path: str
    value: PhysicalValue


def iter_physical_values(
    model: ReservoirSimulationModel,
) -> Iterator[PhysicalObservation]:
    if model.rock.compressibility is not None:
        yield PhysicalObservation(
            "rock.compressibility",
            "rock.compressibility",
            model.rock.compressibility,
        )
    if model.rock.reference_pressure is not None:
        yield PhysicalObservation(
            "rock.reference_pressure",
            "rock.reference_pressure",
            model.rock.reference_pressure,
        )

    for phase_name in ("oil", "water", "gas"):
        phase = getattr(model.fluids, phase_name)
        if phase is None:
            continue
        if phase.density is not None:
            yield PhysicalObservation(
                f"fluid.{phase_name}.density",
                f"fluids.{phase_name}.density",
                phase.density,
            )
        if phase.pvt is None:
            continue
        for point_index, point in enumerate(phase.pvt.points):
            prefix = f"fluids.{phase_name}.pvt.points[{point_index}]"
            yield PhysicalObservation(
                f"fluid.{phase_name}.pvt.pressure",
                f"{prefix}.pressure",
                point.pressure,
            )
            if point.formation_volume_factor is not None:
                yield PhysicalObservation(
                    f"fluid.{phase_name}.pvt.formation_volume_factor",
                    f"{prefix}.formation_volume_factor",
                    point.formation_volume_factor,
                )
            if point.viscosity is not None:
                yield PhysicalObservation(
                    f"fluid.{phase_name}.pvt.viscosity",
                    f"{prefix}.viscosity",
                    point.viscosity,
                )
            if phase_name == "water" and point.compressibility is not None:
                yield PhysicalObservation(
                    f"fluid.{phase_name}.pvt.compressibility",
                    f"{prefix}.compressibility",
                    point.compressibility,
                )
            if phase_name == "water" and point.viscosibility is not None:
                yield PhysicalObservation(
                    f"fluid.{phase_name}.pvt.viscosibility",
                    f"{prefix}.viscosibility",
                    point.viscosibility,
                )

    scal_concepts = {
        "sw": "scal.relative_permeability.water_saturation",
        "krw": "scal.relative_permeability.krw",
        "kro": "scal.relative_permeability.kro",
        "pcow": "scal.relative_permeability.pcow",
    }
    for table_index, table in enumerate(model.scal.relative_permeability):
        for point_index, point in enumerate(table.points):
            for field, concept_id in scal_concepts.items():
                value = getattr(point, field)
                if value is not None:
                    yield PhysicalObservation(
                        concept_id,
                        (
                            f"scal.relative_permeability[{table_index}]."
                            f"points[{point_index}].{field}"
                        ),
                        value,
                    )

    for well_index, well in enumerate(model.wells):
        for control_index, control in enumerate(well.controls):
            control_concept = CONTROL_CONCEPTS.get(control.control_type)
            if control_concept is not None:
                yield PhysicalObservation(
                    control_concept,
                    f"wells[{well_index}].controls[{control_index}].target",
                    control.target,
                )
            for constraint_index, constraint in enumerate(control.constraints):
                constraint_concept = CONSTRAINT_CONCEPTS[constraint.constraint_type]
                yield PhysicalObservation(
                    constraint_concept,
                    (
                        f"wells[{well_index}].controls[{control_index}]."
                        f"constraints[{constraint_index}].value"
                    ),
                    constraint.value,
                )

    if model.schedule.duration is not None:
        yield PhysicalObservation(
            "schedule.duration",
            "schedule.duration",
            model.schedule.duration,
        )
    if model.schedule.report_interval is not None:
        yield PhysicalObservation(
            "schedule.report_interval",
            "schedule.report_interval",
            model.schedule.report_interval,
        )
