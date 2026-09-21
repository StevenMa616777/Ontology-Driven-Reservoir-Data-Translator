"""Raw TXT, JSON, CSV, and XLSX document ingestion."""

from __future__ import annotations

from pathlib import Path

from .base import DocumentParser, IngestionError
from .csv_parser import CsvParser
from .excel_parser import ExcelParser, XlsxParser
from .json_parser import JsonParser
from .models import (
    BlockType,
    BoundingBox,
    CharacterSpan,
    ExtractionEvidence,
    RawBlock,
    RawDocument,
    SourceRegion,
)
from .ocr import (
    CompositeOcrEngine,
    CropPipelineResult,
    CropPipelineSelection,
    CropRecognitionPipeline,
    CropRunContext,
    EngineDescriptor,
    LayoutEngine,
    OcrEngineRegistry,
    OcrBackend,
    OcrBackendError,
    OcrPageResult,
    OcrRegion,
    OcrTable,
    PaddleOcrBackend,
    PaddleCompositeCropEngine,
    TableEngine,
    TextDetectionEngine,
    TextRecognitionEngine,
)
from .pdf_parser import PDFParser, PdfParser, OcrReviewRequired, OcrReviewSession
from .text_parser import TextParser, TxtParser


_PARSERS: dict[str, type[DocumentParser]] = {
    suffix: parser
    for parser in (TextParser, JsonParser, CsvParser, ExcelParser, PdfParser)
    for suffix in parser.suffixes
}


def parse_document(
    path: str | Path,
    *,
    source_id: str | None = None,
) -> RawDocument:
    """Select a parser by file suffix and return a raw document."""

    source_path = Path(path)
    parser_type = _PARSERS.get(source_path.suffix.casefold())
    if parser_type is None:
        raise IngestionError(
            "UNSUPPORTED_SOURCE_TYPE",
            f"No parser is configured for source suffix {source_path.suffix!r}",
            path=source_path,
        )
    return parser_type().parse(source_path, source_id=source_id)


__all__ = [
    "BlockType",
    "BoundingBox",
    "CharacterSpan",
    "CompositeOcrEngine",
    "CropPipelineResult",
    "CropPipelineSelection",
    "CropRecognitionPipeline",
    "CropRunContext",
    "CsvParser",
    "DocumentParser",
    "ExcelParser",
    "ExtractionEvidence",
    "EngineDescriptor",
    "IngestionError",
    "JsonParser",
    "LayoutEngine",
    "OcrBackend",
    "OcrBackendError",
    "OcrPageResult",
    "OcrEngineRegistry",
    "OcrRegion",
    "OcrReviewRequired",
    "OcrReviewSession",
    "OcrTable",
    "PDFParser",
    "PaddleOcrBackend",
    "PaddleCompositeCropEngine",
    "PdfParser",
    "RawBlock",
    "RawDocument",
    "SourceRegion",
    "TableEngine",
    "TextDetectionEngine",
    "TextRecognitionEngine",
    "TextParser",
    "TxtParser",
    "XlsxParser",
    "parse_document",
]
