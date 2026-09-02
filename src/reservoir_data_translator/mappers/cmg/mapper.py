"""Deterministic CMG IMEX-style demo control mapper.

The design does not freeze a licensed CMG release or complete dataset grammar,
so this mapper intentionally emits a clearly labelled demo control fragment.
It never presents the fragment as a standalone simulator-ready dataset.
"""

from __future__ import annotations

from reservoir_data_translator.canonical import ReservoirSimulationModel
from reservoir_data_translator.validation import ValidationIssue, ValidationResult

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


class CMGDemoMapper(PlatformMapper):
    """Map supported well controls to an IMEX-style demo fragment."""

    def __init__(self, mappings: PlatformMappingRegistry) -> None:
        if mappings.platform.casefold() != "cmg":
            raise ValueError("CMGDemoMapper requires a cmg mapping registry")
        self._mappings = mappings

    @property
    def target_platform(self) -> str:
        return "cmg"

    def validate_export(
        self,
        canonical_model: ReservoirSimulationModel,
    ) -> ValidationResult:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        if not canonical_model.wells:
            errors.append(
                self._issue(
                    "CMG_NO_EXPORTABLE_WELLS",
                    "wells",
                    "CMG demo mapper requires at least one supported well control.",
                )
            )
        for well_index, well in enumerate(canonical_model.wells):
            if not well.controls:
                errors.append(
                    self._issue(
                        "CMG_WELL_CONTROL_MISSING",
                        f"wells[{well_index}].controls",
                        "A demo CMG well requires at least one control.",
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
                            "CMG_CONTROL_UNSUPPORTED",
                            f"wells[{well_index}].controls[{control_index}]",
                            (
                                f"Demo mapper cannot export {well.well_type!r} with "
                                f"{control.control_type!r}."
                            ),
                        )
                    )
        if self._has_nonwell_data(canonical_model):
            warnings.append(
                self._issue(
                    "CMG_DEMO_NONWELL_DATA_NOT_EXPORTED",
                    "$",
                    (
                        "Rock, fluid, SCAL, and schedule rendering require a frozen "
                        "CMG product/version contract and are omitted by this demo."
                    ),
                )
            )
        warnings.append(
            self._issue(
                "CMG_HOST_DATASET_CONTEXT_REQUIRED",
                "wells",
                "The control fragment requires wells/completions in a host CMG dataset.",
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
                "CMG export requirements are not satisfied.",
                platform=self.target_platform,
            )
        blocks: list[PlatformBlock] = []
        for well_index, well in enumerate(canonical_model.wells):
            prefix = f"wells[{well_index}]"
            blocks.append(
                PlatformBlock(
                    keyword=self._mappings.target_for("well"),
                    section="CMG_DEMO",
                    records=[
                        PlatformRecord(
                            values=[_token(well.id, quoted=True)],
                            source_paths=[f"{prefix}.id"],
                        )
                    ],
                )
            )
            type_concept = (
                "well.producer"
                if well.well_type == "producer"
                else "well.water_injector"
            )
            blocks.append(
                PlatformBlock(
                    keyword=self._mappings.target_for(type_concept),
                    section="CMG_DEMO",
                    records=[
                        PlatformRecord(
                            values=[
                                _token(well.id, quoted=True),
                                *(
                                    [_token("WATER")]
                                    if well.well_type == "water_injector"
                                    else []
                                ),
                            ],
                            source_paths=[f"{prefix}.well_type"],
                        )
                    ],
                )
            )
            for control_index, control in enumerate(well.controls):
                control_prefix = f"{prefix}.controls[{control_index}]"
                operation = "STL" if well.well_type == "producer" else "STW"
                blocks.append(
                    PlatformBlock(
                        keyword=self._mappings.target_for(
                            "well.control.liquid_rate"
                            if well.well_type == "producer"
                            else "well.control.water_injection_rate"
                        ),
                        section="CMG_DEMO",
                        records=[
                            PlatformRecord(
                                values=[
                                    _token(well.id, quoted=True),
                                    _token("MAX"),
                                    _token(operation),
                                    _token(control.target.value),
                                ],
                                source_paths=[f"{control_prefix}.target"],
                            )
                        ],
                    )
                )
                for constraint_index, constraint in enumerate(control.constraints):
                    mode = "MIN" if constraint.constraint_type == "minimum_bhp" else "MAX"
                    concept = f"well.constraint.{constraint.constraint_type}"
                    blocks.append(
                        PlatformBlock(
                            keyword=self._mappings.target_for(concept),
                            section="CMG_DEMO",
                            records=[
                                PlatformRecord(
                                    values=[
                                        _token(well.id, quoted=True),
                                        _token(mode),
                                        _token("BHP"),
                                        _token(constraint.value.value),
                                    ],
                                    source_paths=[
                                        f"{control_prefix}.constraints[{constraint_index}].value"
                                    ],
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
                "Mapped model is not a CMG intermediate representation.",
                platform=self.target_platform,
            )
        lines = [
            "** Generated deterministic CMG demo control fragment",
            f"** Dialect: {mapped_model.dialect}",
            "** Not a standalone dataset; host well/completion context is required.",
        ]
        for block in mapped_model.blocks:
            for record in block.records:
                values = " ".join(self._render_token(token) for token in record.values)
                lines.append(f"*{block.keyword} {values}".rstrip())
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_token(token: PlatformToken) -> str:
        if token.value is None:
            return "*"
        if isinstance(token.value, str):
            escaped = token.value.replace("'", "''")
            return f"'{escaped}'" if token.quoted else f"*{escaped}"
        return format(float(token.value), ".12g")

    @staticmethod
    def _issue(code: str, path: str, message: str) -> ValidationIssue:
        return ValidationIssue(code=code, path=path, message=message, layer="export")

    @staticmethod
    def _has_nonwell_data(model: ReservoirSimulationModel) -> bool:
        return any(
            (
                model.rock.compressibility is not None,
                model.rock.reference_pressure is not None,
                model.fluids.oil is not None,
                model.fluids.water is not None,
                model.fluids.gas is not None,
                bool(model.scal.relative_permeability),
                model.schedule.duration is not None,
                model.schedule.report_interval is not None,
            )
        )


CMGMapper = CMGDemoMapper
