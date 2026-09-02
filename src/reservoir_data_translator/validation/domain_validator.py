"""L3 deterministic physical and engineering validation."""

from __future__ import annotations

from collections import Counter
from typing import Any

from reservoir_data_translator.canonical import ReservoirSimulationModel
from reservoir_data_translator.ontology import OntologyRegistry

from .models import ValidationIssue, ValidationResult
from .traversal import iter_physical_values


class DomainValidator:
    """Apply hard physical rules as errors and empirical trends as warnings."""

    def __init__(self, registry: OntologyRegistry) -> None:
        self._registry = registry

    def validate(self, model: ReservoirSimulationModel) -> ValidationResult:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []

        for observation in iter_physical_values(model):
            concept = self._registry.get_concept(observation.concept_id)
            value = observation.value.value
            constraints = concept.constraints
            violation = self._constraint_violation(value, constraints)
            if violation is not None:
                errors.append(
                    ValidationIssue(
                        code="VALUE_OUT_OF_RANGE",
                        path=observation.path,
                        message=f"{concept.name} {violation}",
                        layer="domain",
                    )
                )

        well_counts = Counter(well.id for well in model.wells)
        for well_id in sorted(well_counts):
            if well_counts[well_id] > 1:
                errors.append(
                    ValidationIssue(
                        code="DUPLICATE_ENTITY_ID",
                        path="wells",
                        message=f"Well id {well_id!r} appears more than once.",
                        layer="domain",
                    )
                )

        relperm_concept = self._registry.get_concept("scal.relative_permeability")
        minimum_points = int(relperm_concept.constraints.get("minimum_points", 0))
        for table_index, table in enumerate(model.scal.relative_permeability):
            prefix = f"scal.relative_permeability[{table_index}].points"
            if len(table.points) < minimum_points:
                errors.append(
                    ValidationIssue(
                        code="TABLE_TOO_SHORT",
                        path=prefix,
                        message=(
                            f"Relative-permeability table requires at least "
                            f"{minimum_points} points."
                        ),
                        layer="domain",
                    )
                )
            self._validate_relperm_trends(table.points, prefix, errors, warnings)

        for phase_name in ("oil", "water", "gas"):
            phase = getattr(model.fluids, phase_name)
            if phase is None or phase.pvt is None:
                continue
            prefix = f"fluids.{phase_name}.pvt.points"
            if phase.pvt.model_type == "table" and not phase.pvt.points:
                errors.append(
                    ValidationIssue(
                        code="TABLE_EMPTY",
                        path=prefix,
                        message=f"{phase_name.title()} table PVT requires at least one point.",
                        layer="domain",
                    )
                )
            pressures = [point.pressure.value for point in phase.pvt.points]
            if len(pressures) != len(set(pressures)):
                errors.append(
                    ValidationIssue(
                        code="DUPLICATE_COORDINATE",
                        path=prefix,
                        message="PVT pressure coordinates must be unique.",
                        layer="domain",
                    )
                )
            elif any(left >= right for left, right in zip(pressures, pressures[1:])):
                warnings.append(
                    ValidationIssue(
                        code="COORDINATE_ORDER_WARNING",
                        path=prefix,
                        message="PVT pressure coordinates are expected to increase.",
                        layer="domain",
                    )
                )

        duration = model.schedule.duration
        interval = model.schedule.report_interval
        if (
            duration is not None
            and interval is not None
            and interval.value > duration.value
        ):
            warnings.append(
                ValidationIssue(
                    code="REPORT_INTERVAL_EXCEEDS_DURATION",
                    path="schedule.report_interval",
                    message="Report interval exceeds the total simulation duration.",
                    layer="domain",
                )
            )

        return ValidationResult(errors=errors, warnings=warnings)

    @staticmethod
    def _constraint_violation(
        value: float,
        constraints: Any,
    ) -> str | None:
        if "minimum" in constraints and value < constraints["minimum"]:
            return f"must be greater than or equal to {constraints['minimum']}."
        if "maximum" in constraints and value > constraints["maximum"]:
            return f"must be less than or equal to {constraints['maximum']}."
        if (
            "exclusive_minimum" in constraints
            and value <= constraints["exclusive_minimum"]
        ):
            return f"must be greater than {constraints['exclusive_minimum']}."
        if (
            "exclusive_maximum" in constraints
            and value >= constraints["exclusive_maximum"]
        ):
            return f"must be less than {constraints['exclusive_maximum']}."
        return None

    @staticmethod
    def _validate_relperm_trends(
        points: list[Any],
        path: str,
        errors: list[ValidationIssue],
        warnings: list[ValidationIssue],
    ) -> None:
        saturations = [point.sw.value for point in points]
        if len(saturations) != len(set(saturations)):
            errors.append(
                ValidationIssue(
                    code="DUPLICATE_COORDINATE",
                    path=path,
                    message="Water-saturation coordinates must be unique.",
                    layer="domain",
                )
            )
            return
        if any(left >= right for left, right in zip(saturations, saturations[1:])):
            warnings.append(
                ValidationIssue(
                    code="COORDINATE_ORDER_WARNING",
                    path=path,
                    message="Water saturation is expected to increase by row.",
                    layer="domain",
                )
            )

        ordered = sorted(points, key=lambda point: point.sw.value)
        krw = [point.krw.value for point in ordered]
        kro = [point.kro.value for point in ordered]
        if any(left > right for left, right in zip(krw, krw[1:])):
            warnings.append(
                ValidationIssue(
                    code="KRW_TREND_WARNING",
                    path=path,
                    message="Krw is generally expected to increase with Sw.",
                    layer="domain",
                )
            )
        if any(left < right for left, right in zip(kro, kro[1:])):
            warnings.append(
                ValidationIssue(
                    code="KRO_TREND_WARNING",
                    path=path,
                    message="Kro is generally expected to decrease with Sw.",
                    layer="domain",
                )
            )
