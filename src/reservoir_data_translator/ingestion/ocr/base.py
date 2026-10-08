"""Backend-neutral OCR contract."""

from __future__ import annotations

from typing import Any, Protocol

from .models import OcrPageResult


class OcrBackendError(RuntimeError):
    """An OCR engine could not safely return a normalized page result."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class OcrBackend(Protocol):
    """Recognize one already-rendered PDF page at a time."""

    def analyze_page(
        self,
        image: Any,
        *,
        page_number: int,
        languages: tuple[str, ...],
    ) -> OcrPageResult:
        """Return normalized text, table, and figure regions."""
