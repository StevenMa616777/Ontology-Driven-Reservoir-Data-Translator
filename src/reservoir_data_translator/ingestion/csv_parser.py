"""CSV ingestion as one source table."""

from __future__ import annotations

import csv
from pathlib import Path

from .base import DocumentParser, IngestionError
from .models import RawBlock, RawDocument


class CsvParser(DocumentParser):
    """Preserve CSV cells as strings; semantic typing belongs downstream."""

    source_type = "csv"
    suffixes = (".csv",)

    def parse(
        self,
        path: str | Path,
        *,
        source_id: str | None = None,
    ) -> RawDocument:
        source_path = self._source_path(path)
        try:
            handle = source_path.open("r", encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise IngestionError(
                "SOURCE_READ_ERROR",
                f"Could not read CSV source: {source_path.name}",
                path=source_path,
            ) from exc

        try:
            with handle:
                reader = csv.reader(handle)
                records = list(reader)
        except UnicodeDecodeError as exc:
            raise IngestionError(
                "SOURCE_DECODE_ERROR",
                f"CSV source must be UTF-8: {source_path.name}",
                path=source_path,
            ) from exc
        except csv.Error as exc:
            raise IngestionError(
                "MALFORMED_CSV",
                f"Could not parse CSV source {source_path.name}: {exc}",
                path=source_path,
            ) from exc

        blocks: list[RawBlock] = []
        if records:
            columns = records[0]
            rows = records[1:]
            mismatched = [
                index
                for index, row in enumerate(rows, start=2)
                if len(row) != len(columns)
            ]
            if mismatched:
                raise IngestionError(
                    "INCONSISTENT_TABLE_WIDTH",
                    (
                        "CSV row width differs from the header at line(s): "
                        + ", ".join(str(line) for line in mismatched)
                    ),
                    path=source_path,
                )
            blocks.append(
                RawBlock(
                    block_id="block_0001",
                    block_type="table",
                    content={"columns": columns, "rows": rows},
                    source_location=f"rows 1-{len(records)}",
                )
            )

        return RawDocument(
            source_id=self._source_id(source_path, source_id),
            source_type=self.source_type,
            file_name=source_path.name,
            blocks=blocks,
        )
