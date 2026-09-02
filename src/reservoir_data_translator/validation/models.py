"""Shared structured results for all canonical validation levels."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from reservoir_data_translator.canonical.models import CanonicalModel, NonEmptyString


ValidationLayer = Literal["schema", "ontology", "domain", "export"]


class ValidationIssue(CanonicalModel):
    """One deterministic, path-addressable validation finding."""

    code: NonEmptyString
    path: NonEmptyString
    message: NonEmptyString
    layer: ValidationLayer


class ValidationResult(CanonicalModel):
    """Uniform error/warning envelope used by every validation level."""

    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    valid: bool = True

    @model_validator(mode="before")
    @classmethod
    def derive_valid_from_errors(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        expected = not value.get("errors", [])
        if "valid" in value and value["valid"] != expected:
            raise ValueError("valid must equal whether the errors collection is empty")
        return {**value, "valid": expected}

    @classmethod
    def merge(cls, *results: "ValidationResult") -> "ValidationResult":
        """Combine level results while preserving deterministic issue order."""

        return cls(
            errors=[issue for result in results for issue in result.errors],
            warnings=[issue for result in results for issue in result.warnings],
        )
