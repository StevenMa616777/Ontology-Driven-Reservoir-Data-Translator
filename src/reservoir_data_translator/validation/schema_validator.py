"""L1 Pydantic/schema validation for canonical payloads."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from reservoir_data_translator.canonical import ReservoirSimulationModel

from .models import ValidationIssue, ValidationResult


def format_location(location: tuple[Any, ...]) -> str:
    path = ""
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += ("." if path else "") + str(part)
    return path or "$"


class SchemaValidator:
    """Validate required fields, types, enums, and strict schema shape."""

    def validate(
        self,
        payload: ReservoirSimulationModel | Mapping[str, Any] | str | bytes,
    ) -> ValidationResult:
        result, _ = self.validate_with_model(payload)
        return result

    def validate_with_model(
        self,
        payload: ReservoirSimulationModel | Mapping[str, Any] | str | bytes,
    ) -> tuple[ValidationResult, ReservoirSimulationModel | None]:
        try:
            if isinstance(payload, ReservoirSimulationModel):
                model = payload
            elif isinstance(payload, (str, bytes)):
                model = ReservoirSimulationModel.model_validate_json(payload)
            else:
                model = ReservoirSimulationModel.model_validate(payload)
        except ValidationError as exc:
            issues = []
            for error in exc.errors(include_url=False):
                error_type = error["type"]
                if error_type == "missing":
                    code = "REQUIRED_FIELD_MISSING"
                elif error_type == "extra_forbidden":
                    code = "UNKNOWN_FIELD"
                else:
                    code = "SCHEMA_VALIDATION_ERROR"
                issues.append(
                    ValidationIssue(
                        code=code,
                        path=format_location(error["loc"]),
                        message=error["msg"],
                        layer="schema",
                    )
                )
            return ValidationResult(errors=issues), None
        return ValidationResult(), model
