"""FastAPI request/response contracts for the staged translation pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from reservoir_data_translator.canonical import (
    CanonicalModel,
    ReservoirSimulationModel,
)
from reservoir_data_translator.canonical.models import NonEmptyString
from reservoir_data_translator.ingestion import RawDocument
from reservoir_data_translator.mappers import PlatformIntermediateModel
from reservoir_data_translator.semantic import SemanticMapping, SemanticMappingBatch
from reservoir_data_translator.validation import ValidationResult


class SourceInput(CanonicalModel):
    file_name: NonEmptyString = "source.txt"
    content: str
    content_encoding: Literal["utf-8", "base64"] = "utf-8"
    source_id: NonEmptyString | None = None


class SemanticMapRequest(CanonicalModel):
    document: RawDocument
    source_system: NonEmptyString | None = None


class CanonicalBuildRequest(CanonicalModel):
    mappings: list[SemanticMapping]
    schema_version: NonEmptyString = "0.1.0"


class ValidateRequest(CanonicalModel):
    canonical_model: ReservoirSimulationModel
    target_platform: NonEmptyString | None = None


class ExportRequest(CanonicalModel):
    canonical_model: ReservoirSimulationModel


class TargetArtifact(CanonicalModel):
    platform: NonEmptyString
    content: str
    mapped_model: PlatformIntermediateModel


class ExportResponse(CanonicalModel):
    validation: ValidationResult
    export_validation: ValidationResult | None = None
    target: TargetArtifact | None = None


class TranslateRequest(CanonicalModel):
    source: SourceInput | str
    target_platform: NonEmptyString
    source_system: NonEmptyString | None = None
    schema_version: NonEmptyString = "0.1.0"


class TranslationTraceEvent(CanonicalModel):
    stage: NonEmptyString
    status: Literal["success", "review_required", "failed"]
    detail: str | None = None


class TranslateResult(CanonicalModel):
    translation_id: NonEmptyString
    status: Literal[
        "success",
        "review_required",
        "validation_failed",
        "export_failed",
    ]
    source: RawDocument
    semantic_mapping: SemanticMappingBatch
    canonical_model: ReservoirSimulationModel | None = None
    validation: ValidationResult | None = None
    export_validation: ValidationResult | None = None
    target: TargetArtifact | None = None
    trace: list[TranslationTraceEvent] = Field(default_factory=list)
