"""Deterministic ECLIPSE/OPM-compatible demo INCLUDE mapper."""

from __future__ import annotations

import math

from reservoir_data_translator.canonical import ReservoirSimulationModel
from reservoir_data_translator.validation import (
    ValidationIssue,
    ValidationResult,
)

from ..base import PlatformMapper, PlatformMappingError
from ..models import (
    PlatformBlock,
    PlatformIntermediateModel,
    PlatformRecord,
    PlatformToken,
)
from ..registry import PlatformMappingRegistry


def _token(value: str | int | float | None, *, quoted: bool = False) -> PlatformToken:
    return PlatformToken(value=value, quoted=quoted)


def _record(
    values: list[PlatformToken],
    *source_paths: str,
) -> PlatformRecord:
    return PlatformRecord(values=values, source_paths=list(source_paths))


class EclipseDemoMapper(PlatformMapper):
    """Render a METRIC demo INCLUDE, not a complete standalone reservoir deck."""

    def __init__(self, mappings: PlatformMappingRegistry) -> None:
        if mappings.platform.casefold() != "eclipse":
            raise ValueError("EclipseDemoMapper requires an eclipse mapping registry")
        self._mappings = mappings

    @property
    def target_platform(self) -> str:
        return "eclipse"

    def validate_export(
        self,
        canonical_model: ReservoirSimulationModel,
    ) -> ValidationResult:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []

        for index, well in enumerate(canonical_model.wells):
            if len(well.id) > 8:
                errors.append(
                    self._issue(
                        "ECLIPSE_WELL_NAME_TOO_LONG",
                        f"wells[{index}].id",
                        "Demo ECLIPSE well names are limited to eight characters.",
                    )
                )
            for control_index, control in enumerate(well.controls):
                supported = (
                    well.well_type == "producer"
                    and control.control_type == "liquid_rate"
                ) or (
                    well.well_type == "water_injector"
                    and control.control_type == "water_injection_rate"
                )
                if not supported:
                    errors.append(
                        self._issue(
                            "ECLIPSE_CONTROL_UNSUPPORTED",
                            f"wells[{index}].controls[{control_index}]",
                            (
                                f"Demo mapper cannot export {well.well_type!r} with "
                                f"{control.control_type!r}."
                            ),
                        )
                    )

        for phase_name in ("oil", "gas"):
            phase = getattr(canonical_model.fluids, phase_name)
            if phase is None or phase.pvt is None:
                continue
            if phase.pvt.model_type != "table":
                errors.append(
                    self._issue(
                        "ECLIPSE_PVT_MODEL_UNSUPPORTED",
                        f"fluids.{phase_name}.pvt.model_type",
                        "Demo mapper supports table PVT only.",
                    )
                )
            for point_index, point in enumerate(phase.pvt.points):
                if point.formation_volume_factor is None or point.viscosity is None:
                    errors.append(
                        self._issue(
                            "ECLIPSE_PVT_POINT_INCOMPLETE",
                            f"fluids.{phase_name}.pvt.points[{point_index}]",
                            "Pressure, formation-volume factor, and viscosity are required.",
                        )
                    )

        if canonical_model.fluids.water is not None and (
            canonical_model.fluids.water.pvt is not None
        ):
            warnings.append(
                self._issue(
                    "ECLIPSE_WATER_PVT_NOT_EXPORTED",
                    "fluids.water.pvt",
                    "Canonical v0.1 lacks the full PVTW context; water PVT is omitted.",
                    warning=True,
                )
            )

        rock = canonical_model.rock
        if (rock.compressibility is None) != (rock.reference_pressure is None):
            warnings.append(
                self._issue(
                    "ECLIPSE_PARTIAL_ROCK_NOT_EXPORTED",
                    "rock",
                    "ROCK requires both reference pressure and compressibility.",
                    warning=True,
                )
            )

        densities = [
            getattr(canonical_model.fluids, phase).density
            if getattr(canonical_model.fluids, phase) is not None
            else None
            for phase in ("oil", "water", "gas")
        ]
        if any(value is not None for value in densities) and not all(
            value is not None for value in densities
        ):
            warnings.append(
                self._issue(
                    "ECLIPSE_PARTIAL_DENSITY_NOT_EXPORTED",
                    "fluids",
                    "DENSITY requires oil, water, and gas density together.",
                    warning=True,
                )
            )

        if canonical_model.wells:
            warnings.append(
                self._issue(
                    "ECLIPSE_HOST_DECK_CONTEXT_REQUIRED",
                    "wells",
                    "WCON records require wells already defined by the host deck.",
                    warning=True,
                )
            )

        if not errors and not self._has_exportable_content(canonical_model):
            errors.append(
                self._issue(
                    "ECLIPSE_NO_EXPORTABLE_CONTENT",
                    "$",
                    "Canonical model contains no section supported by the demo mapper.",
                )
            )
        return ValidationResult(errors=errors, warnings=warnings)

    def map(
        self,
        canonical_model: ReservoirSimulationModel,
    ) -> PlatformIntermediateModel:
        validation = self.validate_export(canonical_model)
        if not validation.valid:
            raise PlatformMappingError(
                "EXPORT_NOT_READY",
                "ECLIPSE export requirements are not satisfied.",
                platform=self.target_platform,
            )
        blocks: list[PlatformBlock] = []
        rock = canonical_model.rock
        if rock.reference_pressure is not None and rock.compressibility is not None:
            blocks.append(
                PlatformBlock(
                    keyword=self._mappings.target_for("rock.compressibility"),
                    section="PROPS",
                    records=[
                        _record(
                            [
                                _token(rock.reference_pressure.value),
                                _token(rock.compressibility.value),
                            ],
                            "rock.reference_pressure",
                            "rock.compressibility",
                        )
                    ],
                )
            )

        phases = [canonical_model.fluids.oil, canonical_model.fluids.water, canonical_model.fluids.gas]
        if all(phase is not None and phase.density is not None for phase in phases):
            blocks.append(
                PlatformBlock(
                    keyword=self._mappings.target_for("fluid.oil.density"),
                    section="PROPS",
                    records=[
                        _record(
                            [_token(phase.density.value) for phase in phases if phase is not None and phase.density is not None],
                            "fluids.oil.density",
                            "fluids.water.density",
                            "fluids.gas.density",
                        )
                    ],
                )
            )

        for phase_name in ("oil", "gas"):
            phase = getattr(canonical_model.fluids, phase_name)
            if phase is None or phase.pvt is None:
                continue
            concept = f"fluid.{phase_name}.pvt"
            records = [
                _record(
                    [
                        _token(point.pressure.value),
                        _token(point.formation_volume_factor.value),  # validated
                        _token(point.viscosity.value),  # validated
                    ],
                    f"fluids.{phase_name}.pvt.points[{index}]",
                )
                for index, point in enumerate(phase.pvt.points)
            ]
            if records:
                blocks.append(
                    PlatformBlock(
                        keyword=self._mappings.target_for(concept),
                        section="PROPS",
                        records=records,
                    )
                )

        for table_index, table in enumerate(canonical_model.scal.relative_permeability):
            blocks.append(
                PlatformBlock(
                    keyword=self._mappings.target_for("scal.relative_permeability"),
                    section="PROPS",
                    records=[
                        _record(
                            [
                                _token(point.sw.value),
                                _token(point.krw.value),
                                _token(point.kro.value),
                                _token(point.pcow.value if point.pcow is not None else None),
                            ],
                            f"scal.relative_permeability[{table_index}].points[{point_index}]",
                        )
                        for point_index, point in enumerate(table.points)
                    ],
                )
            )

        producer_records: list[PlatformRecord] = []
        injector_records: list[PlatformRecord] = []
        for well_index, well in enumerate(canonical_model.wells):
            for control_index, control in enumerate(well.controls):
                prefix = f"wells[{well_index}].controls[{control_index}]"
                if well.well_type == "producer":
                    minimum_bhp = next(
                        (
                            item.value.value
                            for item in control.constraints
                            if item.constraint_type == "minimum_bhp"
                        ),
                        None,
                    )
                    producer_records.append(
                        _record(
                            [
                                _token(well.id, quoted=True),
                                _token("OPEN", quoted=True),
                                _token("LRAT", quoted=True),
                                _token(None),
                                _token(None),
                                _token(None),
                                _token(control.target.value),
                                _token(None),
                                _token(minimum_bhp),
                            ],
                            prefix,
                        )
                    )
                else:
                    maximum_bhp = next(
                        (
                            item.value.value
                            for item in control.constraints
                            if item.constraint_type == "maximum_bhp"
                        ),
                        None,
                    )
                    injector_records.append(
                        _record(
                            [
                                _token(well.id, quoted=True),
                                _token("WATER", quoted=True),
                                _token("OPEN", quoted=True),
                                _token("RATE", quoted=True),
                                _token(control.target.value),
                                _token(None),
                                _token(maximum_bhp),
                            ],
                            prefix,
                        )
                    )
        if producer_records:
            blocks.append(
                PlatformBlock(
                    keyword=self._mappings.target_for("well.control.liquid_rate"),
                    section="SCHEDULE",
                    records=producer_records,
                )
            )
        if injector_records:
            blocks.append(
                PlatformBlock(
                    keyword=self._mappings.target_for("well.control.water_injection_rate"),
                    section="SCHEDULE",
                    records=injector_records,
                )
            )

        schedule = canonical_model.schedule
        if schedule.duration is not None and schedule.report_interval is not None:
            steps = self._time_steps(schedule.duration.value, schedule.report_interval.value)
            blocks.append(
                PlatformBlock(
                    keyword=self._mappings.target_for("schedule.duration"),
                    section="SCHEDULE",
                    records=[
                        _record(
                            [_token(step) for step in steps],
                            "schedule.duration",
                            "schedule.report_interval",
                        )
                    ],
                )
            )

        return PlatformIntermediateModel(
            platform=self.target_platform,
            dialect=self._mappings.dialect,
            blocks=blocks,
            notes=[issue.message for issue in validation.warnings],
        )

    def render(self, mapped_model: PlatformIntermediateModel) -> str:
        if mapped_model.platform.casefold() != self.target_platform:
            raise PlatformMappingError(
                "MAPPED_PLATFORM_MISMATCH",
                "Mapped model is not an ECLIPSE intermediate representation.",
                platform=self.target_platform,
            )
        lines = [
            "-- Generated deterministic demo INCLUDE",
            f"-- Dialect: {mapped_model.dialect}",
            "-- Requires a compatible host deck; no missing values were guessed.",
        ]
        current_section: str | None = None
        for block in mapped_model.blocks:
            if block.section != current_section:
                lines.extend(["", f"-- {block.section}"])
                current_section = block.section
            lines.append(block.keyword)
            rendered_records = [
                "  " + " ".join(self._render_token(token) for token in record.values)
                for record in block.records
            ]
            if block.keyword in {"PVDO", "PVDG", "SWOF"}:
                lines.extend(rendered_records)
                lines.append("/")
            elif block.keyword in {"WCONPROD", "WCONINJE"}:
                lines.extend(record + " /" for record in rendered_records)
                lines.append("/")
            else:
                lines.extend(record + " /" for record in rendered_records)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_token(token: PlatformToken) -> str:
        if token.value is None:
            return "1*"
        if isinstance(token.value, str):
            escaped = token.value.replace("'", "''")
            return f"'{escaped}'" if token.quoted else escaped
        return format(float(token.value), ".12g")

    @staticmethod
    def _time_steps(duration: float, interval: float) -> list[float]:
        count = int(math.floor(duration / interval))
        steps = [interval] * count
        remainder = duration - interval * count
        if remainder > 1e-9:
            steps.append(remainder)
        return steps

    @staticmethod
    def _issue(
        code: str,
        path: str,
        message: str,
        *,
        warning: bool = False,
    ) -> ValidationIssue:
        return ValidationIssue(code=code, path=path, message=message, layer="export")

    @staticmethod
    def _has_exportable_content(model: ReservoirSimulationModel) -> bool:
        rock = model.rock
        complete_rock = rock.compressibility is not None and rock.reference_pressure is not None
        densities = [
            getattr(model.fluids, name).density
            if getattr(model.fluids, name) is not None
            else None
            for name in ("oil", "water", "gas")
        ]
        return any(
            (
                complete_rock,
                all(value is not None for value in densities),
                model.fluids.oil is not None and model.fluids.oil.pvt is not None,
                model.fluids.gas is not None and model.fluids.gas.pvt is not None,
                bool(model.scal.relative_permeability),
                bool(model.wells),
                model.schedule.duration is not None and model.schedule.report_interval is not None,
            )
        )


EclipseMapper = EclipseDemoMapper
