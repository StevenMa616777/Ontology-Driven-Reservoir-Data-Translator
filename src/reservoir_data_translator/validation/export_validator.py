"""L4 target-specific export-readiness delegation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import Iterable, Mapping

from reservoir_data_translator.canonical import ReservoirSimulationModel

from .models import ValidationIssue, ValidationResult


class PlatformExportValidator(ABC):
    """Contract implemented by each future deterministic platform mapper."""

    @property
    @abstractmethod
    def target_platform(self) -> str:
        """Stable target-platform identifier."""

    @abstractmethod
    def validate_export(
        self,
        canonical_model: ReservoirSimulationModel,
    ) -> ValidationResult:
        """Return missing requirements and target-specific warnings."""


class ExportValidator:
    """Route L4 validation without claiming canonical validity means export-ready."""

    def __init__(
        self,
        validators: Iterable[PlatformExportValidator] = (),
    ) -> None:
        index: dict[str, PlatformExportValidator] = {}
        for validator in validators:
            key = validator.target_platform.strip().casefold()
            if not key:
                raise ValueError("target_platform must be a non-empty string")
            if key in index:
                raise ValueError(f"Duplicate export validator for {key!r}")
            index[key] = validator
        self._validators: Mapping[str, PlatformExportValidator] = MappingProxyType(
            index
        )

    def validate(
        self,
        model: ReservoirSimulationModel,
        target_platform: str,
    ) -> ValidationResult:
        key = target_platform.strip().casefold()
        validator = self._validators.get(key)
        if validator is None:
            return ValidationResult(
                errors=[
                    ValidationIssue(
                        code="EXPORT_VALIDATOR_NOT_CONFIGURED",
                        path="target_platform",
                        message=(
                            f"No export validator is configured for "
                            f"{target_platform!r}."
                        ),
                        layer="export",
                    )
                ]
            )
        result = validator.validate_export(model)
        if any(
            issue.layer != "export"
            for issue in [*result.errors, *result.warnings]
        ):
            raise ValueError("Platform export validators must return export-layer issues")
        return result
