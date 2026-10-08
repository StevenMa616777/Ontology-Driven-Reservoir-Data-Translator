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
from .models import (
    OcrPageResult, OcrRegion, OcrTable, PixelBoundingBox,
    TableAnalysisResult, TableLeaf, TableTree, TableTreeNode,
)
from .page_engine import OcrEngine
from .paddle_components import (
    PaddleLayoutEngine, PaddleSubtableProposalEngine, PaddleTableRecognizer,
    PaddleTextDetectionEngine, PaddleTextRecognitionEngine,
)
from .paddle_backend import PaddleOcrBackend
from .registry import DEFAULT_SELECTION, register_paddle_components
from .table_pipeline import RecursiveTableEngine, SubtableProposal, SubtableSplitter

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
    "OcrEngine",
    "TableAnalysisResult",
    "TableLeaf",
    "TableTree",
    "TableTreeNode",
    "RecursiveTableEngine",
    "SubtableProposal",
    "SubtableSplitter",
    "PaddleOcrBackend",
    "PaddleLayoutEngine",
    "PaddleSubtableProposalEngine",
    "PaddleTableRecognizer",
    "PaddleTextDetectionEngine",
    "PaddleTextRecognitionEngine",
    "DEFAULT_SELECTION",
    "register_paddle_components",
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
