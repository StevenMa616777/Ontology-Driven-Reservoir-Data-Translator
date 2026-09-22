"""Explicit, lazily loaded OCR component registrations."""

from __future__ import annotations

from importlib.util import find_spec

from .components import CropPipelineSelection, EngineDescriptor, OcrEngineRegistry
from .paddle_components import (
    PaddleLayoutEngine,
    PaddleSubtableProposalEngine,
    PaddleTableRecognizer,
    PaddleTextDetectionEngine,
    PaddleTextRecognitionEngine,
)
from .table_pipeline import RecursiveTableEngine


DEFAULT_SELECTION = CropPipelineSelection(
    layout_engine="paddle-layout",
    text_detection_engine="paddle-text-detection",
    text_recognition_engine="paddle-text-recognition",
    table_engine="paddle-table-recursive",
)


def register_paddle_components(
    registry: OcrEngineRegistry,
    *,
    device: str = "gpu:0",
    layout_model: str = "PP-DocLayout_plus-L",
    text_detection_model: str = "PP-OCRv5_server_det",
    text_recognition_model: str = "PP-OCRv5_server_rec",
    subtable_model: str = "PP-DocLayout_plus-L",
) -> None:
    available = find_spec("paddleocr") is not None
    reason = None if available else "PaddleOCR optional dependencies are not installed"
    registrations = (
        (
            "paddle-layout", "Paddle layout detection", "layout",
            lambda: PaddleLayoutEngine(model_name=layout_model, device=device),
        ),
        (
            "paddle-text-detection", "Paddle text detection", "text_detection",
            lambda: PaddleTextDetectionEngine(model_name=text_detection_model, device=device),
        ),
        (
            "paddle-text-recognition", "Paddle text recognition", "text_recognition",
            lambda: PaddleTextRecognitionEngine(model_name=text_recognition_model, device=device),
        ),
        (
            "paddle-table-recursive", "Paddle table recognition with reviewable subtable proposals", "table",
            lambda: RecursiveTableEngine(
                PaddleTableRecognizer(device=device),
                PaddleSubtableProposalEngine(model_name=subtable_model, device=device),
            ),
        ),
    )
    for engine_id, display_name, kind, factory in registrations:
        registry.register(
            EngineDescriptor(
                engine_id=engine_id, display_name=display_name, kind=kind,
                available=available, unavailable_reason=reason,
                description=(
                    "子表检测权重尚未针对逻辑子表校准；候选拆分必须人工复核。"
                    if kind == "table" else None
                ),
            ),
            factory,
        )
