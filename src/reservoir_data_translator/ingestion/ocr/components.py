"""Replaceable crop-recognition component contracts and orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Literal, Protocol

from .models import OcrPageResult, PixelBoundingBox


EngineKind = Literal[
    "composite",
    "layout",
    "text_detection",
    "text_recognition",
    "table",
]
CropKind = Literal["text", "table", "mixed", "unknown"]


@dataclass(frozen=True)
class LayoutElement:
    element_id: str
    element_type: str
    bbox_pixels: PixelBoundingBox
    confidence: float | None = None


@dataclass(frozen=True)
class LayoutResult:
    elements: tuple[LayoutElement, ...] = ()


@dataclass(frozen=True)
class TextLine:
    line_id: str
    bbox_pixels: PixelBoundingBox
    confidence: float | None = None


@dataclass(frozen=True)
class TextDetectionResult:
    lines: tuple[TextLine, ...] = ()


@dataclass(frozen=True)
class RecognizedText:
    line_id: str
    text: str
    bbox_pixels: PixelBoundingBox | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class TextRecognitionResult:
    spans: tuple[RecognizedText, ...] = ()


@dataclass(frozen=True)
class TableCell:
    row: int
    column: int
    text: str
    bbox_pixels: PixelBoundingBox | None = None
    rowspan: int = 1
    colspan: int = 1
    confidence: float | None = None


@dataclass(frozen=True)
class TableResult:
    row_count: int
    column_count: int
    cells: tuple[TableCell, ...] = ()


@dataclass(frozen=True)
class CropRunContext:
    run_id: str
    crop_kind: CropKind
    languages: tuple[str, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)


class LayoutEngine(Protocol):
    def detect(self, image: Any, *, context: CropRunContext) -> LayoutResult: ...


class TextDetectionEngine(Protocol):
    def detect(
        self,
        image: Any,
        *,
        layout: LayoutResult,
        context: CropRunContext,
    ) -> TextDetectionResult: ...


class TextRecognitionEngine(Protocol):
    def recognize(
        self,
        image: Any,
        *,
        detections: TextDetectionResult,
        context: CropRunContext,
    ) -> TextRecognitionResult: ...


class TableEngine(Protocol):
    def parse(
        self,
        image: Any,
        *,
        layout: LayoutResult,
        recognized_text: TextRecognitionResult,
        context: CropRunContext,
    ) -> TableResult: ...


class CompositeOcrEngine(Protocol):
    def analyze_crop(self, image: Any, *, context: CropRunContext) -> OcrPageResult: ...


@dataclass(frozen=True)
class EngineDescriptor:
    engine_id: str
    display_name: str
    kind: EngineKind
    available: bool = True
    unavailable_reason: str | None = None
    description: str | None = None
    parameter_schema: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class _Registration:
    descriptor: EngineDescriptor
    factory: Callable[[], Any]
    instance: Any | None = None


class OcrEngineRegistry:
    """Explicit registry with lazy, reusable engine instances and no default."""

    def __init__(self) -> None:
        self._registrations: dict[tuple[EngineKind, str], _Registration] = {}

    def register(
        self,
        descriptor: EngineDescriptor,
        factory: Callable[[], Any],
    ) -> None:
        key = (descriptor.kind, descriptor.engine_id)
        if key in self._registrations:
            raise ValueError(f"OCR engine {descriptor.engine_id!r} is already registered")
        self._registrations[key] = _Registration(descriptor, factory)

    def descriptors(self) -> tuple[EngineDescriptor, ...]:
        return tuple(
            registration.descriptor
            for _, registration in sorted(self._registrations.items())
        )

    def get(self, kind: EngineKind, engine_id: str) -> Any:
        registration = self._registrations.get((kind, engine_id))
        if registration is None:
            raise KeyError(f"No {kind} OCR engine is registered as {engine_id!r}")
        if not registration.descriptor.available:
            reason = registration.descriptor.unavailable_reason or "engine unavailable"
            raise RuntimeError(f"OCR engine {engine_id!r} is unavailable: {reason}")
        if registration.instance is None:
            registration.instance = registration.factory()
        return registration.instance


@dataclass(frozen=True)
class CropPipelineSelection:
    composite_engine: str | None = None
    layout_engine: str | None = None
    text_detection_engine: str | None = None
    text_recognition_engine: str | None = None
    table_engine: str | None = None


@dataclass(frozen=True)
class CropStageResult:
    stage: EngineKind
    engine_id: str
    elapsed_ms: float
    output: Any


@dataclass(frozen=True)
class CropPipelineResult:
    stages: tuple[CropStageResult, ...]
    page_result: OcrPageResult | None = None


class CropRecognitionPipeline:
    """Coordinate either one composite baseline or independently selected ports."""

    def __init__(self, registry: OcrEngineRegistry) -> None:
        self.registry = registry

    @staticmethod
    def _timed(stage: EngineKind, engine_id: str, callback: Callable[[], Any]) -> CropStageResult:
        started = perf_counter()
        output = callback()
        return CropStageResult(
            stage=stage,
            engine_id=engine_id,
            elapsed_ms=round((perf_counter() - started) * 1000, 3),
            output=output,
        )

    def run(
        self,
        image: Any,
        *,
        selection: CropPipelineSelection,
        context: CropRunContext,
    ) -> CropPipelineResult:
        component_ids = (
            selection.layout_engine,
            selection.text_detection_engine,
            selection.text_recognition_engine,
            selection.table_engine,
        )
        if selection.composite_engine:
            if any(component_ids):
                raise ValueError("A composite OCR engine cannot be combined with component engines")
            engine: CompositeOcrEngine = self.registry.get(
                "composite", selection.composite_engine
            )
            stage = self._timed(
                "composite",
                selection.composite_engine,
                lambda: engine.analyze_crop(image, context=context),
            )
            return CropPipelineResult(stages=(stage,), page_result=stage.output)

        if not any(component_ids):
            raise ValueError("Select a composite engine or at least one component engine")

        stages: list[CropStageResult] = []
        layout = LayoutResult()
        detections = TextDetectionResult()
        recognized = TextRecognitionResult()

        if selection.layout_engine:
            engine: LayoutEngine = self.registry.get("layout", selection.layout_engine)
            stage = self._timed(
                "layout",
                selection.layout_engine,
                lambda: engine.detect(image, context=context),
            )
            layout = stage.output
            stages.append(stage)

        if selection.text_detection_engine:
            engine: TextDetectionEngine = self.registry.get(
                "text_detection", selection.text_detection_engine
            )
            stage = self._timed(
                "text_detection",
                selection.text_detection_engine,
                lambda: engine.detect(image, layout=layout, context=context),
            )
            detections = stage.output
            stages.append(stage)

        if selection.text_recognition_engine:
            engine: TextRecognitionEngine = self.registry.get(
                "text_recognition", selection.text_recognition_engine
            )
            stage = self._timed(
                "text_recognition",
                selection.text_recognition_engine,
                lambda: engine.recognize(image, detections=detections, context=context),
            )
            recognized = stage.output
            stages.append(stage)

        if selection.table_engine:
            engine: TableEngine = self.registry.get("table", selection.table_engine)
            stage = self._timed(
                "table",
                selection.table_engine,
                lambda: engine.parse(
                    image,
                    layout=layout,
                    recognized_text=recognized,
                    context=context,
                ),
            )
            stages.append(stage)

        return CropPipelineResult(stages=tuple(stages))
