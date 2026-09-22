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
class TableTreeNode:
    node_id: str
    parent_id: str | None
    bbox_pixels: PixelBoundingBox
    depth: int
    status: Literal["candidate", "leaf", "split", "rejected"]
    children_ids: tuple[str, ...] = ()
    confidence: float | None = None
    reason: str | None = None
    table: OcrTable | None = None
    evidence_ref: str | None = None


@dataclass(frozen=True)
class TableTree:
    tree_id: str
    root_id: str
    nodes: tuple[TableTreeNode, ...]
    leaf_ids: tuple[str, ...]
    review_required: bool = False
    review_reasons: tuple[str, ...] = ()
    review_status: Literal["not_required", "pending", "accepted", "excluded"] = "not_required"
    context_assignments: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    model_calls: int = 0
    processed_pixels: int = 0
    duration_ms: float = 0.0


@dataclass(frozen=True)
class TableLeaf:
    node_id: str
    bbox_pixels: PixelBoundingBox
    table: OcrTable
    confidence: float | None = None
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableAnalysisResult:
    tree: TableTree
    leaves: tuple[TableLeaf, ...]


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
    raw_content: str | None = None
    parent_region_id: str | None = None
    table_tree_id: str | None = None


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
    table_trees: tuple[TableTree, ...] = ()
    stage_timings_ms: Mapping[str, float] = field(default_factory=dict)
    contract_version: str = "1.0"
