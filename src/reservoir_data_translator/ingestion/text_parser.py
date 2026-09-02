"""Plain-text ingestion without reservoir-domain interpretation."""

from __future__ import annotations

from pathlib import Path
import re

from .base import DocumentParser, IngestionError
from .models import RawBlock, RawDocument


class TextParser(DocumentParser):
    """Split UTF-8 text into non-empty paragraph blocks with line provenance."""

    source_type = "txt"
    suffixes = (".txt",)

    def parse(
        self,
        path: str | Path,
        *,
        source_id: str | None = None,
    ) -> RawDocument:
        source_path = self._source_path(path)
        try:
            text = source_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise IngestionError(
                "SOURCE_DECODE_ERROR",
                f"Text source must be UTF-8: {source_path.name}",
                path=source_path,
            ) from exc

        blocks: list[RawBlock] = []
        lines = text.splitlines()
        paragraph_start: int | None = None
        paragraph_lines: list[str] = []

        def flush(end_line: int) -> None:
            nonlocal paragraph_start, paragraph_lines
            if paragraph_start is None:
                return
            content = "\n".join(paragraph_lines)
            block_number = len(blocks) + 1
            location = (
                f"line {paragraph_start}"
                if paragraph_start == end_line
                else f"lines {paragraph_start}-{end_line}"
            )
            blocks.append(
                RawBlock(
                    block_id=f"block_{block_number:04d}",
                    block_type="text",
                    content=content,
                    source_location=location,
                )
            )
            paragraph_start = None
            paragraph_lines = []

        for line_number, line in enumerate(lines, start=1):
            if re.fullmatch(r"\s*", line):
                flush(line_number - 1)
                continue
            if paragraph_start is None:
                paragraph_start = line_number
            paragraph_lines.append(line)
        flush(len(lines))

        return RawDocument(
            source_id=self._source_id(source_path, source_id),
            source_type=self.source_type,
            file_name=source_path.name,
            blocks=blocks,
        )


TxtParser = TextParser
