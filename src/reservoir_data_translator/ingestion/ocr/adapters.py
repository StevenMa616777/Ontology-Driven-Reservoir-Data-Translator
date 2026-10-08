"""Adapters that expose existing OCR implementations through crop contracts."""

from __future__ import annotations

from typing import Any

from .components import CropRunContext
from .models import OcrPageResult
from .paddle_backend import PaddleOcrBackend


class PaddleCompositeCropEngine:
    """Keep the current PP-StructureV3 path as an explicit composite baseline."""

    def __init__(self, backend: PaddleOcrBackend) -> None:
        self.backend = backend

    def analyze_crop(
        self,
        image: Any,
        *,
        context: CropRunContext,
    ) -> OcrPageResult:
        return self.backend.analyze_page(
            image,
            page_number=1,
            languages=context.languages,
        )
