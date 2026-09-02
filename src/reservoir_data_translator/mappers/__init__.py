"""Deterministic target-platform mapping and rendering."""

from .base import (
    PlatformMapper,
    PlatformMapperRegistry,
    PlatformMappingError,
)
from .cmg import CMGDemoMapper, CMGMapper
from .eclipse import EclipseDemoMapper, EclipseMapper
from .models import (
    PlatformBlock,
    PlatformExportResult,
    PlatformIntermediateModel,
    PlatformRecord,
    PlatformToken,
)
from .registry import (
    PlatformMappingDefinition,
    PlatformMappingEntry,
    PlatformMappingRegistry,
)

__all__ = [
    "CMGDemoMapper",
    "CMGMapper",
    "EclipseDemoMapper",
    "EclipseMapper",
    "PlatformBlock",
    "PlatformExportResult",
    "PlatformIntermediateModel",
    "PlatformMapper",
    "PlatformMapperRegistry",
    "PlatformMappingDefinition",
    "PlatformMappingEntry",
    "PlatformMappingError",
    "PlatformMappingRegistry",
    "PlatformRecord",
    "PlatformToken",
]
