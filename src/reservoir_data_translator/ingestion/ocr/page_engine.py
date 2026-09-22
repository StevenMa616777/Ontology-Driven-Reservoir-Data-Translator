"""Project-owned page OCR orchestration over independently registered engines."""

from __future__ import annotations

from dataclasses import asdict, replace
from html import escape
import math
from time import perf_counter
from typing import Any

from .base import OcrBackendError
from .components import (
    CropPipelineSelection,
    CropRunContext,
    LayoutElement,
    LayoutResult,
    OcrEngineRegistry,
)
from .models import (
    OcrPageResult,
    OcrRegion,
    PixelBoundingBox,
    TableAnalysisResult,
    TableTree,
)


_TABLE_LABELS = {"table"}
_FIGURE_LABELS = {"figure", "image", "picture", "chart", "diagram"}
_SKIP_LABELS = {"footer", "page_footer", "page_header", "page_number"}


def _box(box: PixelBoundingBox, width: int, height: int) -> tuple[int, int, int, int]:
    if len(box) != 4 or not all(math.isfinite(v) for v in box):
        raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", "An OCR component returned an invalid box.")
    x0, y0, x1, y1 = box
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 <= x0 or y1 <= y0:
        raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", "An OCR component returned an out-of-page box.")
    return math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)


def _global_box(box: PixelBoundingBox, left: int, top: int) -> PixelBoundingBox:
    return (box[0] + left, box[1] + top, box[2] + left, box[3] + top)


def _html_table(region: OcrRegion) -> str:
    if region.table is None:
        return ""
    rows = [region.table.columns, *region.table.rows]
    return "<table>" + "".join(
        "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    ) + "</table>"


class OcrEngine:
    """Run layout first, then route original-image crops to text or table engines."""

    def __init__(
        self,
        registry: OcrEngineRegistry,
        selection: CropPipelineSelection,
        *,
        engine_id: str = "reservoir-ocr-engine",
        minimum_confidence: float = 0.70,
    ) -> None:
        if selection.composite_engine or not selection.layout_engine:
            raise ValueError("Project OCREngine requires a layout component, not a composite engine")
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        self.registry = registry
        self.selection = selection
        self.engine_id = engine_id
        self.minimum_confidence = minimum_confidence

    def analyze_page(
        self,
        image: Any,
        *,
        page_number: int,
        languages: tuple[str, ...],
    ) -> OcrPageResult:
        from .artifacts import active_artifact

        if image.width < 1 or image.height < 1:
            raise OcrBackendError("PDF_OCR_INPUT_INVALID", "Page image has no pixels.")
        evidence: list[dict[str, Any]] = []
        timings: dict[str, float] = {}
        regions: list[OcrRegion] = []
        trees: list[TableTree] = []
        context = CropRunContext(
            run_id=f"page-{page_number}", crop_kind="mixed", languages=languages,
            parameters={"raw_evidence": evidence, "page_number": page_number},
        )
        writer = active_artifact.get()
        selected_models: dict[str, str] = {}
        failure: dict[str, str] | None = None
        stage = "registry"
        started = perf_counter()
        try:
            selected_models = self._selected_models()
            stage = "layout"
            layout_engine = self.registry.get("layout", self.selection.layout_engine)
            layout: LayoutResult = layout_engine.detect(image, context=context)
            timings["layout"] = round((perf_counter() - started) * 1000, 3)
            if not isinstance(layout, LayoutResult):
                raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", "Layout component returned no LayoutResult.")
            elements = sorted(
                layout.elements,
                key=lambda item: (item.bbox_pixels[1], item.bbox_pixels[0], item.element_id),
            )
            ids: set[str] = set()
            for order, element in enumerate(elements, 1):
                if element.element_id in ids:
                    raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", "Layout element IDs must be unique.")
                ids.add(element.element_id)
                left, top, right, bottom = _box(element.bbox_pixels, image.width, image.height)
                label = element.element_type.strip().casefold().replace("-", "_")
                if label in _SKIP_LABELS:
                    continue
                crop = image.crop((left, top, right, bottom))
                crop_context = CropRunContext(
                    run_id=context.run_id,
                    crop_kind="table" if label in _TABLE_LABELS else "text",
                    languages=languages,
                    parameters={
                        "raw_evidence": evidence, "page_number": page_number,
                        "element_id": element.element_id,
                        "page_bbox_pixels": (left, top, right, bottom),
                    },
                )
                try:
                    if label in _TABLE_LABELS:
                        stage = f"table:{element.element_id}"
                        self._analyze_table(
                            crop, element, order, crop_context, left, top,
                            regions, trees, timings,
                        )
                    elif label in _FIGURE_LABELS:
                        regions.append(OcrRegion(
                            region_id=element.element_id, region_type="figure",
                            layout_label=label, bbox_pixels=element.bbox_pixels,
                            reading_order=order, confidence=element.confidence,
                            source_region_index=order,
                        ))
                    else:
                        stage = f"text:{element.element_id}"
                        self._analyze_text(
                            crop, element, order, crop_context, left, top,
                            regions, timings,
                        )
                finally:
                    crop.close()
            timings["total"] = round((perf_counter() - started) * 1000, 3)
            return OcrPageResult(
                page_number=page_number, image_width=image.width, image_height=image.height,
                regions=tuple(regions), engine=self.engine_id, engine_version="1.0",
                model_version="; ".join(f"{key}={value}" for key, value in selected_models.items()),
                table_trees=tuple(trees), stage_timings_ms=timings,
            )
        except Exception as exc:
            failure = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            timings.setdefault("total", round((perf_counter() - started) * 1000, 3))
            # Record the pre-normalization component outputs even if a later stage fails.
            if writer is not None and page_number not in writer.pages:
                payload = {
                    "engine": self.engine_id,
                    "contract_version": "1.0",
                    "minimum_confidence": self.minimum_confidence,
                    "selected_components": asdict(self.selection),
                    "selected_models": selected_models,
                    "component_raw_results": evidence,
                    "table_trees": [asdict(tree) for tree in trees],
                    "stage_timings_ms": timings,
                    "failure": failure,
                    "parsing_res_list": [
                        {
                            "block_id": region.region_id,
                            "block_order": region.reading_order,
                            "block_label": region.layout_label,
                            "block_bbox": list(region.bbox_pixels),
                            "block_content": _html_table(region) if region.table else region.text or "",
                        }
                        for region in regions
                    ],
                }
                writer.record(page_number, payload, image, source_format="component_raw_results")

    def _selected_models(self) -> dict[str, str]:
        selected = (
            ("layout", self.selection.layout_engine),
            ("text_detection", self.selection.text_detection_engine),
            ("text_recognition", self.selection.text_recognition_engine),
            ("table", self.selection.table_engine),
        )
        models: dict[str, str] = {}
        for kind, engine_id in selected:
            if not engine_id:
                continue
            engine = self.registry.get(kind, engine_id)
            if kind == "table" and hasattr(engine, "recognizer") and hasattr(engine, "splitter"):
                models["table_recognizer"] = str(getattr(engine.recognizer, "model_name", None)
                    or engine.recognizer.__class__.__name__)
                models["subtable_proposal"] = str(getattr(engine.splitter, "model_name", None)
                    or engine.splitter.__class__.__name__)
            else:
                models[kind] = str(getattr(engine, "model_name", None) or engine.__class__.__name__)
        return models

    def _analyze_text(
        self, crop: Any, element: LayoutElement, order: int,
        context: CropRunContext, left: int, top: int,
        regions: list[OcrRegion], timings: dict[str, float],
    ) -> None:
        del left, top
        if not self.selection.text_detection_engine or not self.selection.text_recognition_engine:
            raise OcrBackendError("PDF_OCR_ENGINE_INVALID", "Text detection and recognition must be selected.")
        local_layout = LayoutResult((LayoutElement(
            element.element_id, element.element_type, (0, 0, crop.width, crop.height),
            element.confidence,
        ),))
        started = perf_counter()
        detector = self.registry.get("text_detection", self.selection.text_detection_engine)
        detections = detector.detect(crop, layout=local_layout, context=context)
        timings["text_detection"] = timings.get("text_detection", 0) + round(
            (perf_counter() - started) * 1000, 3
        )
        for line in detections.lines:
            _box(line.bbox_pixels, crop.width, crop.height)
        started = perf_counter()
        recognizer = self.registry.get("text_recognition", self.selection.text_recognition_engine)
        recognized = recognizer.recognize(crop, detections=detections, context=context)
        timings["text_recognition"] = timings.get("text_recognition", 0) + round(
            (perf_counter() - started) * 1000, 3
        )
        line_ids = {line.line_id for line in detections.lines}
        span_ids = [span.line_id for span in recognized.spans]
        if len(span_ids) != len(set(span_ids)) or set(span_ids) - line_ids:
            raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", "Text recognition does not match detected line IDs.")
        text = "\n".join(span.text.strip() for span in recognized.spans if span.text.strip())
        if not text:
            return
        scores = [span.confidence for span in recognized.spans if span.confidence is not None]
        confidence = min(scores) if scores else None
        quality_flags = (
            ("LOW_TEXT_CONFIDENCE",)
            if confidence is not None and confidence < self.minimum_confidence else ()
        )
        regions.append(OcrRegion(
            region_id=element.element_id, region_type="text",
            layout_label=element.element_type, bbox_pixels=element.bbox_pixels,
            reading_order=order, text=text, confidence=confidence,
            quality_flags=quality_flags, source_region_index=order, raw_content=text,
        ))

    def _analyze_table(
        self, crop: Any, element: LayoutElement, order: int,
        context: CropRunContext, left: int, top: int,
        regions: list[OcrRegion], trees: list[TableTree], timings: dict[str, float],
    ) -> None:
        if not self.selection.table_engine:
            raise OcrBackendError("PDF_OCR_ENGINE_INVALID", "A table engine must be selected.")
        started = perf_counter()
        engine = self.registry.get("table", self.selection.table_engine)
        local_layout = LayoutResult((LayoutElement(
            element.element_id, "table", (0, 0, crop.width, crop.height),
            element.confidence,
        ),))
        from .components import TextRecognitionResult
        result = engine.parse(
            crop, layout=local_layout, recognized_text=TextRecognitionResult(),
            context=context,
        )
        timings["table"] = timings.get("table", 0) + round((perf_counter() - started) * 1000, 3)
        if not isinstance(result, TableAnalysisResult):
            raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", "Table component returned no TableAnalysisResult.")
        tree = replace(result.tree, nodes=tuple(
            replace(node, bbox_pixels=_global_box(node.bbox_pixels, left, top))
            for node in result.tree.nodes
        ))
        trees.append(tree)
        for leaf in result.leaves:
            _box(leaf.bbox_pixels, crop.width, crop.height)
            flags = list(leaf.quality_flags)
            if leaf.confidence is not None and leaf.confidence < self.minimum_confidence:
                flags.append("TABLE_CELL_LOW_CONFIDENCE")
            if tree.review_required:
                flags.append("TABLE_SPLIT_REVIEW_REQUIRED")
            regions.append(OcrRegion(
                region_id=leaf.node_id, region_type="table", layout_label="table",
                bbox_pixels=_global_box(leaf.bbox_pixels, left, top),
                reading_order=order,
                table=leaf.table, confidence=leaf.confidence,
                quality_flags=tuple(dict.fromkeys(flags)),
                parent_region_id=element.element_id, table_tree_id=tree.tree_id,
            ))
