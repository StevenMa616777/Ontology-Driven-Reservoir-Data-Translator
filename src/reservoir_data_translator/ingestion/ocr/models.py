"""Normalized OCR output independent of any concrete engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


PixelBoundingBox = tuple[float, float, float, float]
OcrRegionType = Literal["text", "table", "figure"]


@dataclass(frozen=True)
class OcrTable:
    columns: list[str]
    rows: list[list[str]]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OcrRegion:
    region_id: str
    region_type: OcrRegionType
    layout_label: str
    bbox_pixels: PixelBoundingBox
    reading_order: int
    text: str | None = None
    table: OcrTable | None = None
    confidence: float | None = None
    quality_flags: tuple[str, ...] = ()
    source_region_index: int | None = None  # One-based position in the raw page, matching the R label.


@dataclass(frozen=True)
class OcrPageResult:
    page_number: int
    image_width: int
    image_height: int
    regions: tuple[OcrRegion, ...]
    engine: str
    engine_version: str | None = None
    model_version: str | None = None
    preprocessing: tuple[str, ...] = ()
