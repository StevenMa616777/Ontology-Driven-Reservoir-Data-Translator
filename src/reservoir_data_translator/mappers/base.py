"""Platform mapper interface and deterministic registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import Iterable, Mapping

from reservoir_data_translator.canonical import ReservoirSimulationModel
from reservoir_data_translator.validation import PlatformExportValidator, ValidationResult

from .models import PlatformExportResult, PlatformIntermediateModel


class PlatformMappingError(ValueError):
    def __init__(self, code: str, message: str, *, platform: str) -> None:
        self.code = code
        self.platform = platform
        super().__init__(message)


class PlatformMapper(PlatformExportValidator, ABC):
    """Canonical -> intermediate -> text, with no model generation."""

    @abstractmethod
    def map(
        self,
        canonical_model: ReservoirSimulationModel,
    ) -> PlatformIntermediateModel:
        """Build the platform-specific intermediate representation."""

    @abstractmethod
    def render(self, mapped_model: PlatformIntermediateModel) -> str:
        """Render an already mapped model without canonical business logic."""

    def export(
        self,
        canonical_model: ReservoirSimulationModel,
    ) -> PlatformExportResult:
        validation = self.validate_export(canonical_model)
        if not validation.valid:
            codes = ", ".join(issue.code for issue in validation.errors)
            raise PlatformMappingError(
                "EXPORT_NOT_READY",
                f"{self.target_platform} export is not ready: {codes}",
                platform=self.target_platform,
            )
        mapped = self.map(canonical_model)
        return PlatformExportResult(
            platform=self.target_platform,
            validation=validation,
            mapped_model=mapped,
            content=self.render(mapped),
        )


class PlatformMapperRegistry:
    def __init__(self, mappers: Iterable[PlatformMapper]) -> None:
        index: dict[str, PlatformMapper] = {}
        for mapper in mappers:
            key = mapper.target_platform.strip().casefold()
            if not key:
                raise ValueError("target_platform must be a non-empty string")
            if key in index:
                raise ValueError(f"Duplicate platform mapper for {key!r}")
            index[key] = mapper
        self._mappers: Mapping[str, PlatformMapper] = MappingProxyType(index)

    def get(self, platform: str) -> PlatformMapper:
        key = platform.strip().casefold()
        try:
            return self._mappers[key]
        except KeyError as exc:
            raise KeyError(f"No mapper is configured for platform {platform!r}") from exc

    def list_platforms(self) -> tuple[str, ...]:
        return tuple(sorted(self._mappers))
