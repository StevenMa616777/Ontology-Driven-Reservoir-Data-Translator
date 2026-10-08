"""Lazy Paddle module adapters for the project-owned OCR engine."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .base import OcrBackendError
from .components import (
    CropRunContext,
    LayoutElement,
    LayoutResult,
    RecognizedText,
    TextDetectionResult,
    TextLine,
    TextRecognitionResult,
)
from .models import OcrTable
from .paddle_backend import PaddleOcrBackend
from .table_pipeline import SubtableProposal


def _payload(result: Any) -> Mapping[str, Any]:
    return PaddleOcrBackend._result_payload(result)


def _capture(context: CropRunContext, stage: str, payload: Mapping[str, Any]) -> None:
    collector = context.parameters.get("raw_evidence")
    if isinstance(collector, list):
        collector.append({
            "stage": stage,
            "element_id": context.parameters.get("element_id"),
            "table_node_id": context.parameters.get("table_node_id"),
            "result": payload,
        })


def _clamp_box(raw: Any, width: int, height: int) -> tuple[float, float, float, float] | None:
    bbox = PaddleOcrBackend._bbox(raw)
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    clipped = (max(0.0, x0), max(0.0, y0), min(float(width), x1), min(float(height), y1))
    return clipped if clipped[2] > clipped[0] and clipped[3] > clipped[1] else None


def _one_result(model: Any, image: Any, *, stage: str) -> Mapping[str, Any]:
    import numpy as np

    try:
        results = list(model.predict(np.asarray(image.convert("RGB"))))
    except Exception as exc:
        raise OcrBackendError("PDF_OCR_FAILED", f"Paddle {stage} inference failed: {exc}") from exc
    if len(results) != 1:
        raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", f"Paddle {stage} returned {len(results)} results.")
    return _payload(results[0])


class _LazyPaddleModel:
    model_class: str

    def __init__(self, *, model_name: str | None = None, device: str = "gpu:0", factory: Any = None) -> None:
        self.model_name = model_name
        self.device = device
        self.factory = factory
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is None:
            if self.factory is None:
                if self.device.startswith("gpu"):
                    # Reuse the existing explicit GPU validation before loading weights.
                    PaddleOcrBackend(device=self.device)._validate_gpu_runtime()
                try:
                    import paddleocr
                    factory = getattr(paddleocr, self.model_class)
                except (ImportError, AttributeError) as exc:
                    raise OcrBackendError("PDF_OCR_BACKEND_UNAVAILABLE", f"Paddle {self.model_class} is unavailable.") from exc
            else:
                factory = self.factory
            options: dict[str, Any] = {"device": self.device, "enable_mkldnn": False}
            if self.model_name:
                options["model_name"] = self.model_name
            if self.model_class == "TableRecognitionPipelineV2":
                options.update(
                    use_layout_detection=False,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_ocr_model=True,
                )
            try:
                self._model = factory(**options)
            except Exception as exc:
                raise OcrBackendError("PDF_OCR_MODEL_UNAVAILABLE", f"Could not initialize {self.model_class}: {exc}") from exc
        return self._model


class PaddleLayoutEngine(_LazyPaddleModel):
    model_class = "LayoutDetection"

    def detect(self, image: Any, *, context: CropRunContext) -> LayoutResult:
        payload = _one_result(self._get_model(), image, stage="layout")
        _capture(context, "layout", payload)
        boxes = payload.get("boxes")
        if not isinstance(boxes, Sequence):
            raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", "Paddle layout has no boxes list.")
        elements = []
        for index, raw in enumerate(boxes, 1):
            if not isinstance(raw, Mapping):
                continue
            bbox = _clamp_box(raw.get("coordinate"), image.width, image.height)
            if bbox is None:
                raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", "Paddle layout box is invalid.")
            elements.append(LayoutElement(
                element_id=f"p{context.parameters.get('page_number', 1):04d}-e{index:04d}",
                element_type=str(raw.get("label") or "unknown"),
                bbox_pixels=bbox,
                confidence=float(raw["score"]) if raw.get("score") is not None else None,
            ))
        return LayoutResult(tuple(elements))


class PaddleTextDetectionEngine(_LazyPaddleModel):
    model_class = "TextDetection"

    def detect(self, image: Any, *, layout: LayoutResult, context: CropRunContext) -> TextDetectionResult:
        del layout
        payload = _one_result(self._get_model(), image, stage="text_detection")
        _capture(context, "text_detection", payload)
        polygons = payload.get("dt_polys")
        scores = payload.get("dt_scores")
        polygons = [] if polygons is None else polygons
        scores = [] if scores is None else scores
        if hasattr(polygons, "tolist"):
            polygons = polygons.tolist()
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        lines = []
        for index, polygon in enumerate(polygons, 1):
            bbox = _clamp_box(polygon, image.width, image.height)
            if bbox is None:
                continue
            lines.append(TextLine(
                line_id=f"{context.parameters.get('element_id', context.run_id)}-l{index:04d}",
                bbox_pixels=bbox,
                confidence=float(scores[index - 1]) if index <= len(scores) else None,
            ))
        return TextDetectionResult(tuple(sorted(lines, key=lambda line: (line.bbox_pixels[1], line.bbox_pixels[0]))))


class PaddleTextRecognitionEngine(_LazyPaddleModel):
    model_class = "TextRecognition"

    def recognize(
        self, image: Any, *, detections: TextDetectionResult, context: CropRunContext,
    ) -> TextRecognitionResult:
        output = []
        for line in detections.lines:
            x0, y0, x1, y1 = line.bbox_pixels
            box = (max(0, int(x0)), max(0, int(y0)), min(image.width, int(x1 + 1)), min(image.height, int(y1 + 1)))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            crop = image.crop(box)
            try:
                payload = _one_result(self._get_model(), crop, stage="text_recognition")
            finally:
                crop.close()
            _capture(context, "text_recognition", payload)
            output.append(RecognizedText(
                line_id=line.line_id, text=str(payload.get("rec_text") or ""),
                bbox_pixels=line.bbox_pixels,
                confidence=float(payload["rec_score"]) if payload.get("rec_score") is not None else None,
            ))
        return TextRecognitionResult(tuple(output))


class PaddleTableRecognizer(_LazyPaddleModel):
    model_class = "TableRecognitionPipelineV2"

    def recognize(self, image: Any, *, context: CropRunContext) -> tuple[OcrTable, float | None]:
        payload = _one_result(self._get_model(), image, stage="table_recognition")
        _capture(context, "table_recognition", payload)
        tables = payload.get("table_res_list")
        if not isinstance(tables, Sequence) or len(tables) != 1 or not isinstance(tables[0], Mapping):
            raise OcrBackendError("PDF_OCR_OUTPUT_INVALID", "Paddle table crop did not produce exactly one table.")
        table, confidence = PaddleOcrBackend._table(PaddleOcrBackend(device="cpu"), tables[0], {})
        if not table.metadata.get("header_detected"):
            table = OcrTable(
                columns=[f"column_{index}" for index in range(1, len(table.columns) + 1)],
                rows=[list(table.columns), *table.rows],
                metadata={**dict(table.metadata), "header_uncertain": True},
            )
        return table, confidence


class PaddleSubtableProposalEngine(_LazyPaddleModel):
    """Independent table-box proposal model; every subdivision requires review.

    The shipped layout weights detect table boxes, not domain-specific logical
    subtables. The dedicated model port can be replaced after labeled evaluation.
    """

    model_class = "LayoutDetection"

    def propose(self, image: Any, *, context: CropRunContext) -> tuple[SubtableProposal, ...]:
        payload = _one_result(self._get_model(), image, stage="subtable_proposal")
        _capture(context, "subtable_proposal", payload)
        boxes = payload.get("boxes") or []
        proposals = []
        for raw in boxes:
            if not isinstance(raw, Mapping) or str(raw.get("label") or "").casefold() != "table":
                continue
            bbox = _clamp_box(raw.get("coordinate"), image.width, image.height)
            if bbox is None:
                continue
            proposals.append(SubtableProposal(
                bbox_pixels=bbox,
                confidence=float(raw["score"]) if raw.get("score") is not None else None,
                reason="Paddle table-box proposal",
            ))
        return tuple(proposals)
