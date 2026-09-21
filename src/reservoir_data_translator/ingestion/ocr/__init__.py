"""Optional OCR backends and normalized document-layout results."""

from .base import OcrBackend, OcrBackendError
from .adapters import PaddleCompositeCropEngine
from .components import (
    CompositeOcrEngine,
    CropPipelineResult,
    CropPipelineSelection,
    CropRecognitionPipeline,
    CropRunContext,
    EngineDescriptor,
    LayoutElement,
    LayoutEngine,
    LayoutResult,
    OcrEngineRegistry,
    RecognizedText,
    TableCell,
    TableEngine,
    TableResult,
    TextDetectionEngine,
    TextDetectionResult,
    TextLine,
    TextRecognitionEngine,
    TextRecognitionResult,
)
from .models import OcrPageResult, OcrRegion, OcrTable, PixelBoundingBox
from .paddle_backend import PaddleOcrBackend

__all__ = [
    "OcrBackend",
    "OcrBackendError",
    "CompositeOcrEngine",
    "CropPipelineResult",
    "CropPipelineSelection",
    "CropRecognitionPipeline",
    "CropRunContext",
    "EngineDescriptor",
    "LayoutElement",
    "LayoutEngine",
    "LayoutResult",
    "OcrEngineRegistry",
    "OcrPageResult",
    "OcrRegion",
    "OcrTable",
    "PaddleOcrBackend",
    "PaddleCompositeCropEngine",
    "PixelBoundingBox",
    "RecognizedText",
    "TableCell",
    "TableEngine",
    "TableResult",
    "TextDetectionEngine",
    "TextDetectionResult",
    "TextLine",
    "TextRecognitionEngine",
    "TextRecognitionResult",
]
