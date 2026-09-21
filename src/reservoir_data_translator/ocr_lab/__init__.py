"""Crop-level OCR experimentation workbench."""

from .api import create_ocr_lab_router
from .service import CropLabError, CropLabService, build_default_engine_registry

__all__ = [
    "CropLabError",
    "CropLabService",
    "build_default_engine_registry",
    "create_ocr_lab_router",
]
