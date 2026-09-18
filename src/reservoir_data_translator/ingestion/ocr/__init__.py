"""Optional OCR backends and normalized document-layout results."""

from .base import OcrBackend, OcrBackendError
from .models import OcrPageResult, OcrRegion, OcrTable, PixelBoundingBox
from .paddle_backend import PaddleOcrBackend

__all__ = [
    "OcrBackend",
    "OcrBackendError",
    "OcrPageResult",
    "OcrRegion",
    "OcrTable",
    "PaddleOcrBackend",
    "PixelBoundingBox",
]
