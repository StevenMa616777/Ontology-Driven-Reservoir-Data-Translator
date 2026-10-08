"""Bounded, model-proposed subdivision of a table into logical leaves."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from .base import OcrBackendError
from .components import CropRunContext, LayoutResult, TextRecognitionResult
from .models import (
    OcrTable,
    PixelBoundingBox,
    TableAnalysisResult,
    TableLeaf,
    TableTree,
    TableTreeNode,
)
from .page_engine import _box


@dataclass(frozen=True)
class SubtableProposal:
    bbox_pixels: PixelBoundingBox
    confidence: float | None
    reason: str


class SubtableSplitter(Protocol):
    def propose(self, image: Any, *, context: CropRunContext) -> tuple[SubtableProposal, ...]: ...


class TableRecognizer(Protocol):
    def recognize(self, image: Any, *, context: CropRunContext) -> tuple[OcrTable, float | None]: ...


class RecursiveTableEngine:
    """Use a dedicated proposal model; never infer logical children from token size."""

    def __init__(
        self,
        recognizer: TableRecognizer,
        splitter: SubtableSplitter,
        *,
        max_depth: int = 3,
        max_nodes: int = 12,
        max_model_calls: int = 24,
        max_processed_pixels: int = 40_000_000,
        max_seconds: float = 60.0,
        require_boundary_review: bool = True,
    ) -> None:
        if (max_depth < 0 or max_nodes < 1 or max_model_calls < 1
                or max_processed_pixels < 1 or max_seconds <= 0):
            raise ValueError("Invalid recursive table budget")
        self.recognizer = recognizer
        self.splitter = splitter
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_model_calls = max_model_calls
        self.max_processed_pixels = max_processed_pixels
        self.max_seconds = max_seconds
        self.require_boundary_review = require_boundary_review

    def parse(
        self,
        image: Any,
        *,
        layout: LayoutResult,
        recognized_text: TextRecognitionResult,
        context: CropRunContext,
    ) -> TableAnalysisResult:
        del layout, recognized_text
        parent_id = str(context.parameters.get("element_id") or context.run_id)
        tree_id = f"{parent_id}:tree"
        nodes: list[TableTreeNode] = []
        leaves: list[TableLeaf] = []
        reasons: list[str] = []
        start = perf_counter()
        model_calls = 0
        processed_pixels = 0

        def charge(crop: Any) -> None:
            nonlocal model_calls, processed_pixels
            model_calls += 1
            processed_pixels += crop.width * crop.height
            if (model_calls > self.max_model_calls or processed_pixels > self.max_processed_pixels
                    or perf_counter() - start > self.max_seconds):
                raise OcrBackendError("TABLE_RECURSION_BUDGET_EXCEEDED", "Table recursion budget exceeded.")

        def visit(
            crop: Any, *, node_id: str, ancestor_id: str | None,
            left: int, top: int, depth: int,
        ) -> None:
            if len(nodes) >= self.max_nodes or perf_counter() - start > self.max_seconds:
                raise OcrBackendError("TABLE_RECURSION_BUDGET_EXCEEDED", "Table recursion budget exceeded.")
            node_box: PixelBoundingBox = (left, top, left + crop.width, top + crop.height)
            local_context = CropRunContext(
                run_id=context.run_id, crop_kind="table", languages=context.languages,
                parameters={**context.parameters, "table_node_id": node_id, "table_depth": depth},
            )
            parent_table: OcrTable | None = None
            parent_confidence: float | None = None
            recognition_error: OcrBackendError | None = None
            charge(crop)
            try:
                parent_table, parent_confidence = self.recognizer.recognize(crop, context=local_context)
                _validate_table(parent_table)
            except OcrBackendError as exc:
                recognition_error = exc

            proposals: tuple[SubtableProposal, ...] = ()
            if depth < self.max_depth:
                charge(crop)
                proposals = self.splitter.propose(crop, context=local_context)
            split_boxes = _accepted_boxes(proposals, crop.width, crop.height)
            if len(split_boxes) >= 2:
                reasons.append("TABLE_SPLIT_UNCALIBRATED")
                reasons.append("TABLE_CONTEXT_REVIEW_REQUIRED")
                nodes.append(TableTreeNode(
                    node_id=node_id, parent_id=ancestor_id, bbox_pixels=node_box,
                    depth=depth, status="split",
                    children_ids=tuple(f"{node_id}.{n}" for n in range(1, len(split_boxes) + 1)),
                    confidence=parent_confidence, reason="split proposed by model",
                    table=parent_table, evidence_ref=f"table_recognition:{node_id}",
                ))
                for n, (box, proposal) in enumerate(split_boxes, 1):
                    x0, y0, x1, y1 = box
                    child = crop.crop(box)
                    try:
                        visit(
                            child, node_id=f"{node_id}.{n}", ancestor_id=node_id,
                            left=left + x0, top=top + y0, depth=depth + 1,
                        )
                    finally:
                        child.close()
                return
            if proposals and len(proposals) >= 2:
                reasons.append("TABLE_SPLIT_GEOMETRY_CONFLICT")
            if recognition_error is not None:
                raise recognition_error
            assert parent_table is not None
            if parent_table.metadata.get("header_uncertain"):
                reasons.append("TABLE_HEADER_REVIEW_REQUIRED")
            nodes.append(TableTreeNode(
                node_id=node_id, parent_id=ancestor_id, bbox_pixels=node_box,
                depth=depth, status="leaf", confidence=parent_confidence,
                reason="no supported subdivision", table=parent_table,
                evidence_ref=f"table_recognition:{node_id}",
            ))
            leaves.append(TableLeaf(
                node_id=node_id, bbox_pixels=node_box, table=parent_table,
                confidence=parent_confidence,
            ))

        visit(image, node_id=parent_id, ancestor_id=None, left=0, top=0, depth=0)
        if self.require_boundary_review:
            reasons.append("TABLE_BOUNDARY_UNVERIFIED")
        tree = TableTree(
            tree_id=tree_id, root_id=parent_id, nodes=tuple(nodes),
            leaf_ids=tuple(leaf.node_id for leaf in leaves),
            review_required=bool(reasons),
            review_reasons=tuple(dict.fromkeys(reasons)),
            review_status="pending" if reasons else "not_required",
            model_calls=model_calls,
            processed_pixels=processed_pixels,
            duration_ms=round((perf_counter() - start) * 1000, 3),
        )
        return TableAnalysisResult(tree=tree, leaves=tuple(leaves))


def _validate_table(table: OcrTable) -> None:
    if not table.columns or any(len(row) != len(table.columns) for row in table.rows):
        raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", "Table result has inconsistent columns and rows.")


def _accepted_boxes(
    proposals: tuple[SubtableProposal, ...], width: int, height: int,
) -> list[tuple[tuple[int, int, int, int], SubtableProposal]]:
    if len(proposals) < 2:
        return []
    boxes: list[tuple[tuple[int, int, int, int], SubtableProposal]] = []
    for proposal in proposals:
        try:
            box = _box(proposal.bbox_pixels, width, height)
        except OcrBackendError:
            return []
        if (box[2] - box[0]) * (box[3] - box[1]) >= width * height * 0.98:
            return []
        for previous, _ in boxes:
            intersection = (
                max(0, min(box[2], previous[2]) - max(box[0], previous[0]))
                * max(0, min(box[3], previous[3]) - max(box[1], previous[1]))
            )
            minimum = min(
                (box[2] - box[0]) * (box[3] - box[1]),
                (previous[2] - previous[0]) * (previous[3] - previous[1]),
            )
            if intersection / minimum > 0.05:
                return []
        boxes.append((box, proposal))
    return sorted(boxes, key=lambda item: (item[0][1], item[0][0]))
