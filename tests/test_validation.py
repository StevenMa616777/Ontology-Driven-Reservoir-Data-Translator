import json

import pytest

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
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.validation import (
    DomainValidator,
    ExportValidator,
    OntologyInstanceValidator,
    PlatformExportValidator,
    SchemaValidator,
    ValidationEngine,
    ValidationIssue,
    ValidationResult,
)


def _value(value: float, unit: str) -> PhysicalValue:
    return PhysicalValue(value=value, unit=unit)


def _valid_model() -> ReservoirSimulationModel:
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
            water=FluidPhaseModel(density=_value(1000, "kg/m3")),
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
                            krw=_value(0.0, "fraction"),
                            kro=_value(0.9, "fraction"),
                            pcow=_value(0.35, "bar"),
                        ),
                        RelativePermeabilityPoint(
                            sw=_value(0.9, "fraction"),
                            krw=_value(0.8, "fraction"),
                            kro=_value(0.0, "fraction"),
                            pcow=_value(0.0, "bar"),
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


def test_validation_result_serializes_design_envelope() -> None:
    result = ValidationResult(
        errors=[
            ValidationIssue(
                code="VALUE_OUT_OF_RANGE",
                path="rock.reference_pressure",
                message="Reference pressure must be positive.",
                layer="domain",
            )
        ]
    )
    payload = json.loads(result.model_dump_json())

    assert payload["valid"] is False
    assert payload["errors"][0]["code"] == "VALUE_OUT_OF_RANGE"
    assert payload["warnings"] == []
    assert ValidationResult.model_validate_json(result.model_dump_json()) == result


def test_l1_schema_validation_returns_path_addressable_errors() -> None:
    payload = _valid_model().model_dump()
    del payload["rock"]
    payload["simulator_keyword"] = "SWOF"

    result = SchemaValidator().validate(payload)

    assert not result.valid
    assert {issue.code for issue in result.errors} == {
        "REQUIRED_FIELD_MISSING",
        "UNKNOWN_FIELD",
    }
    assert {issue.path for issue in result.errors} == {"rock", "simulator_keyword"}


def test_l2_accepts_parent_relationship_for_water_injector_max_bhp(
    registry: OntologyRegistry,
) -> None:
    result = OntologyInstanceValidator(registry).validate(_valid_model())

    assert result.valid
    assert result.errors == []


def test_l2_rejects_invalid_control_relationship_and_noncanonical_unit(
    registry: OntologyRegistry,
) -> None:
    model = _valid_model()
    producer = model.wells[0]
    producer.controls[0].control_type = "water_injection_rate"
    producer.controls[0].target.unit = "bbl/day"

    result = OntologyInstanceValidator(registry).validate(model)

    assert {issue.code for issue in result.errors} == {
        "ONTOLOGY_RELATIONSHIP_ERROR",
        "ONTOLOGY_UNIT_ERROR",
    }


def test_l3_strong_rules_are_errors_and_empirical_trends_are_warnings(
    registry: OntologyRegistry,
) -> None:
    model = _valid_model()
    points = model.scal.relative_permeability[0].points
    points[0].sw.value = -0.1
    points[0].krw.value = 0.7
    points[1].krw.value = 0.2
    model.fluids.oil.density.value = 0  # type: ignore[union-attr]
    model.wells[0].controls[0].target.value = -1

    result = DomainValidator(registry).validate(model)

    assert not result.valid
    assert sum(issue.code == "VALUE_OUT_OF_RANGE" for issue in result.errors) == 3
    assert "KRW_TREND_WARNING" in {issue.code for issue in result.warnings}


def test_l3_detects_short_tables_duplicate_entities_and_coordinates(
    registry: OntologyRegistry,
) -> None:
    model = _valid_model()
    model.wells.append(model.wells[0].model_copy(deep=True))
    model.scal.relative_permeability[0].points = [
        model.scal.relative_permeability[0].points[0]
    ]
    model.fluids.oil.pvt.points[1].pressure.value = 100  # type: ignore[union-attr]

    result = DomainValidator(registry).validate(model)

    assert {
        "DUPLICATE_ENTITY_ID",
        "TABLE_TOO_SHORT",
        "DUPLICATE_COORDINATE",
    } <= {issue.code for issue in result.errors}


class _EclipseRequirementValidator(PlatformExportValidator):
    calls = 0

    @property
    def target_platform(self) -> str:
        return "eclipse"

    def validate_export(
        self,
        canonical_model: ReservoirSimulationModel,
    ) -> ValidationResult:
        self.calls += 1
        return ValidationResult(
            errors=[
                ValidationIssue(
                    code="EXPORT_REQUIREMENT_MISSING",
                    path="wells[0]",
                    message="WELSPECS/COMPDAT host-deck context is required.",
                    layer="export",
                )
            ]
        )


def test_l4_delegates_target_requirements_without_fabricating_values() -> None:
    validator = ExportValidator([_EclipseRequirementValidator()])

    result = validator.validate(_valid_model(), "eclipse")

    assert not result.valid
    assert result.errors[0].code == "EXPORT_REQUIREMENT_MISSING"


def test_l4_unknown_target_is_explicitly_not_export_ready() -> None:
    result = ExportValidator().validate(_valid_model(), "cmg")

    assert not result.valid
    assert result.errors[0].code == "EXPORT_VALIDATOR_NOT_CONFIGURED"


def test_validation_engine_aggregates_l1_to_l4(
    registry: OntologyRegistry,
) -> None:
    engine = ValidationEngine(
        registry,
        export_validator=ExportValidator([_EclipseRequirementValidator()]),
    )

    canonical_only = engine.validate(_valid_model())
    with_export = engine.validate(_valid_model(), target_platform="eclipse")

    assert canonical_only.valid
    assert not with_export.valid
    assert {issue.layer for issue in with_export.errors} == {"export"}


def test_validation_engine_stops_after_invalid_schema(
    registry: OntologyRegistry,
) -> None:
    result = ValidationEngine(registry).validate({"schema_version": "0.1.0"})

    assert not result.valid
    assert {issue.layer for issue in result.errors} == {"schema"}


def test_validation_engine_does_not_export_domain_invalid_model(
    registry: OntologyRegistry,
) -> None:
    platform_validator = _EclipseRequirementValidator()
    engine = ValidationEngine(
        registry,
        export_validator=ExportValidator([platform_validator]),
    )
    model = _valid_model()
    model.rock.reference_pressure.value = 0  # type: ignore[union-attr]

    result = engine.validate(model, target_platform="eclipse")

    assert not result.valid
    assert {issue.layer for issue in result.errors} == {"domain"}
    assert platform_validator.calls == 0
