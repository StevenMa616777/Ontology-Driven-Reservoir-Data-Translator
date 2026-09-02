"""Structured target-platform intermediate and export models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from reservoir_data_translator.canonical.models import CanonicalModel, NonEmptyString
from reservoir_data_translator.validation import ValidationResult


class PlatformToken(CanonicalModel):
    value: str | int | float | None
    quoted: bool = False


class PlatformRecord(CanonicalModel):
    values: list[PlatformToken]
    source_paths: list[NonEmptyString] = Field(default_factory=list)


class PlatformBlock(CanonicalModel):
    keyword: NonEmptyString
    section: Literal["PROPS", "SCHEDULE", "CMG_DEMO"]
    records: list[PlatformRecord] = Field(min_length=1)


class PlatformIntermediateModel(CanonicalModel):
    platform: NonEmptyString
    dialect: NonEmptyString
    blocks: list[PlatformBlock]
    notes: list[NonEmptyString] = Field(default_factory=list)


class PlatformExportResult(CanonicalModel):
    platform: NonEmptyString
    validation: ValidationResult
    mapped_model: PlatformIntermediateModel
    content: str
