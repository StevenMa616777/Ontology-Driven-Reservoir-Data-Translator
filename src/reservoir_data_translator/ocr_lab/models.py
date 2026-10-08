"""Strict request contracts for the internal OCR Lab UI."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from reservoir_data_translator.canonical import CanonicalModel
from reservoir_data_translator.canonical.models import NonEmptyString


class CropBoundingBox(CanonicalModel):
    x0: int = Field(ge=0)
    y0: int = Field(ge=0)
    x1: int = Field(gt=0)
    y1: int = Field(gt=0)


class CropPreprocessing(CanonicalModel):
    scale: float = Field(default=1.0, ge=0.5, le=3.0, multiple_of=0.5)
    grayscale: bool = False
    contrast: float = Field(default=1.0, ge=0.25, le=4.0)
    sharpen: float = Field(default=0.0, ge=0.0, le=3.0)
    threshold: int | None = Field(default=None, ge=0, le=255)
    padding: int = Field(default=0, ge=0, le=256)


class CropPipelineRequest(CanonicalModel):
    composite_engine: NonEmptyString | None = None
    layout_engine: NonEmptyString | None = None
    text_detection_engine: NonEmptyString | None = None
    text_recognition_engine: NonEmptyString | None = None
    table_engine: NonEmptyString | None = None


class CropSourceGeometry(CanonicalModel):
    coordinate_space: Literal["pdf_top_left_points"] = "pdf_top_left_points"
    render_dpi: int = Field(gt=0, le=2400)
    page_width_points: float = Field(gt=0)
    page_height_points: float = Field(gt=0)
    region_bbox_points: tuple[float, float, float, float]
    crop_bbox_points: tuple[float, float, float, float]
    region_bbox_pixels: tuple[float, float, float, float]
    image_width_pixels: int = Field(gt=0)
    image_height_pixels: int = Field(gt=0)
    padding_points: float = Field(ge=0)


class CropProvenanceRequest(CanonicalModel):
    source_kind: Literal[
        "upload",
        "clipboard",
        "ocr_review",
        "manual_recrop",
        "reconstructed_from_pdf",
    ] = "upload"
    artifact_id: NonEmptyString | None = None
    issue_id: NonEmptyString | None = None
    page: int | None = Field(default=None, ge=1)
    region_number: NonEmptyString | None = None
    original_confidence: float | None = Field(default=None, ge=0, le=1)
    original_flags: list[NonEmptyString] = Field(default_factory=list)
    pixel_identical_to_original_ocr_input: bool = True
    source_geometry: CropSourceGeometry | None = None


class CropRunRequest(CanonicalModel):
    file_name: NonEmptyString = "crop.png"
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/tiff"]
    content_base64: NonEmptyString
    crop_kind: Literal["text", "table", "mixed", "unknown"] = "unknown"
    bbox: CropBoundingBox | None = None
    languages: list[NonEmptyString] = Field(default_factory=lambda: ["ch", "en"])
    expected_text: str | None = None
    notes: str | None = None
    preprocessing: CropPreprocessing = Field(default_factory=CropPreprocessing)
    pipeline: CropPipelineRequest
    provenance: CropProvenanceRequest = Field(default_factory=CropProvenanceRequest)
