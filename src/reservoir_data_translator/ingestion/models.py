"""Format-neutral source document models.

The ingestion layer deliberately preserves source structure without assigning
reservoir-domain meaning.  A parser may identify text, table, and key/value
shapes because those are properties of a file format; it must not decide that a
column named ``Rate`` is a particular canonical control.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Mapping

from pydantic import Field, model_validator

from reservoir_data_translator.canonical.models import (
    CanonicalModel,
    Confidence,
    FiniteFloat,
    NonEmptyString,
)

'''
    key_value:
        原始数据类似 pressure_unit=bar 时
        解析出的 Block 的 content 是
        content={
            "key": "pressure_unit",
            "value": "bar",
        }
'''
BlockType = Literal["text", "table", "key_value", "figure"]


class ExtractionEvidence(CanonicalModel):
    """How a format-level block was extracted from its source evidence."""

    method: NonEmptyString
    engine: NonEmptyString | None = None
    engine_version: NonEmptyString | None = None
    model_version: NonEmptyString | None = None
    languages: list[NonEmptyString] = Field(default_factory=list)
    confidence: Confidence | None = None
    layout_label: NonEmptyString | None = None
    render_dpi: Annotated[int, Field(ge=1)] | None = None
    preprocessing: list[NonEmptyString] = Field(default_factory=list)
    quality_flags: list[NonEmptyString] = Field(default_factory=list)


class BoundingBox(CanonicalModel):
    """One rectangular region in top-left PDF page coordinates, in points."""

    x0: FiniteFloat
    top: FiniteFloat
    x1: FiniteFloat
    bottom: FiniteFloat
    coordinate_system: Literal["pdf_top_left_points"] = "pdf_top_left_points"

    @model_validator(mode="after")
    def coordinates_are_ordered(self) -> "BoundingBox":
        if self.x1 < self.x0:
            raise ValueError("bounding box x1 must be greater than or equal to x0")
        if self.bottom < self.top:
            raise ValueError("bounding box bottom must be greater than or equal to top")
        return self


class CharacterSpan(CanonicalModel):
    """A half-open character range within a deterministic parent region text."""

    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def end_is_not_before_start(self) -> "CharacterSpan":
        if self.end < self.start:
            raise ValueError("character span end must be greater than or equal to start")
        return self


class SourceRegionPart(CanonicalModel):
    """Original extraction region contributing evidence or context to a chunk."""

    region_id: NonEmptyString
    bbox: BoundingBox
    extraction_method: NonEmptyString
    text: str | None = None
    source_span: CharacterSpan | None = None
    role: Literal["evidence", "context"] = "evidence"
    confidence: Confidence | None = None
    quality_flags: list[NonEmptyString] = Field(default_factory=list)


class SourceRegion(CanonicalModel):
    """Structured provenance for one format-level block or bounded child block."""

    region_id: NonEmptyString
    parent_region_id: NonEmptyString
    page: Annotated[int, Field(ge=1)]
    bbox: BoundingBox
    parts: list[SourceRegionPart] = Field(default_factory=list)
    reading_order: Annotated[int, Field(ge=1)]
    extraction_method: NonEmptyString
    source_span: CharacterSpan | None = None
    row_start: Annotated[int, Field(ge=1)] | None = None
    row_end: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def table_row_range_is_complete(self) -> "SourceRegion":
        if (self.row_start is None) != (self.row_end is None):
            raise ValueError("row_start and row_end must be provided together")
        if (
            self.row_start is not None
            and self.row_end is not None
            and self.row_end < self.row_start
        ):
            raise ValueError("row_end must be greater than or equal to row_start")
        return self


class RawBlock(CanonicalModel):
    """One addressable, format-level unit of source content."""

    block_id: NonEmptyString
    block_type: BlockType
    content: Any
    section_title: str | None = None
    source_location: NonEmptyString | None = None
    source_region: SourceRegion | None = None
    extraction_evidence: ExtractionEvidence | None = None

    @model_validator(mode="after")
    def validate_content_shape(self) -> "RawBlock":
        if self.block_type == "text" and not isinstance(self.content, str):
            raise ValueError("text block content must be a string")
        if self.block_type == "table":
            self._validate_table(self.content)
        if self.block_type == "key_value":
            self._validate_key_value(self.content)
        if self.block_type == "figure":
            self._validate_figure(self.content)
        return self

    def searchable_text(self) -> str:
        """Return a deterministic textual projection for retrieval and prompts."""

        if isinstance(self.content, str):
            return (self.section_title + "\n" if self.section_title else "") + self.content
        return json.dumps(
            self.content,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )

    @staticmethod
    def _validate_table(content: Any) -> None:
        if not isinstance(content, Mapping):
            raise ValueError("table block content must be a mapping")
        columns = content.get("columns")
        rows = content.get("rows")
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise ValueError("table block content requires list columns and rows")
        for row in rows:
            if not isinstance(row, list):
                raise ValueError("each table row must be a list")
            if len(row) != len(columns):
                raise ValueError("table row width must match the column count")

    @staticmethod
    def _validate_key_value(content: Any) -> None:
        if not isinstance(content, Mapping):
            raise ValueError("key_value block content must be a mapping")
        if set(content) != {"key", "value"}:
            raise ValueError("key_value block content requires exactly key and value")
        if not isinstance(content["key"], str) or not content["key"].strip():
            raise ValueError("key_value block key must be a non-empty string")

    @staticmethod
    def _validate_figure(content: Any) -> None:
        if not isinstance(content, Mapping):
            raise ValueError("figure block content must be a mapping")
        if not isinstance(content.get("figure_index"), int) or content["figure_index"] < 1:
            raise ValueError("figure block content requires a positive figure_index")


class RawDocument(CanonicalModel):
    """A parsed source file before any semantic or unit interpretation."""

    source_id: NonEmptyString
    source_type: NonEmptyString
    file_name: NonEmptyString
    blocks: list[RawBlock]

    @model_validator(mode="after")
    def block_ids_are_unique(self) -> "RawDocument":
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("RawDocument block_id values must be unique")
        return self
