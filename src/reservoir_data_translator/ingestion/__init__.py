"""Raw TXT, JSON, CSV, and XLSX document ingestion."""

from __future__ import annotations

from pathlib import Path

from .base import DocumentParser, IngestionError
from .csv_parser import CsvParser
from .excel_parser import ExcelParser, XlsxParser
from .json_parser import JsonParser
from .models import BlockType, RawBlock, RawDocument
from .text_parser import TextParser, TxtParser


_PARSERS: dict[str, type[DocumentParser]] = {
    suffix: parser
    for parser in (TextParser, JsonParser, CsvParser, ExcelParser)
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
    "CsvParser",
    "DocumentParser",
    "ExcelParser",
    "IngestionError",
    "JsonParser",
    "RawBlock",
    "RawDocument",
    "TextParser",
    "TxtParser",
    "XlsxParser",
    "parse_document",
]
