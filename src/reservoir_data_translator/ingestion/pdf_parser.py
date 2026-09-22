"""Native-text PDF ingestion with page-bounded, layout-aware chunking."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

try:  # Keep non-PDF ingestion importable when the optional runtime is incomplete.
    import pdfplumber
    from pdfminer.pdfdocument import (
        PDFPasswordIncorrect,
        PDFSyntaxError,
        PDFTextExtractionNotAllowed,
    )
    from pdfplumber.utils.exceptions import PdfminerException
except ImportError:  # pragma: no cover - exercised by an isolated import test.
    pdfplumber = None  # type: ignore[assignment]

    class _UnavailablePdfError(Exception):
        pass

    PDFPasswordIncorrect = _UnavailablePdfError  # type: ignore[misc,assignment]
    PDFSyntaxError = _UnavailablePdfError  # type: ignore[misc,assignment]
    PDFTextExtractionNotAllowed = _UnavailablePdfError  # type: ignore[misc,assignment]
    PdfminerException = _UnavailablePdfError  # type: ignore[misc,assignment]

from .base import DocumentParser, IngestionError
from .models import (
    BoundingBox,
    CharacterSpan,
    RawBlock,
    RawDocument,
    SourceRegion,
)

# 认为一个换行后接着至少一个换行为段落边界 两个换行之间可以有空格或制表符
_PARAGRAPH_BOUNDARY = re.compile(r"\n[ \t]*\n+")

# 以中英文的 [.!?。！？；;] 结尾后面可包含制表符或空格 作为分句依据
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？；;])(?:[ \t]+|\n+)")

# 一个汉字、1到4个ASCII字符、一个标点符号 粗略算成一个Token
_TOKEN_ESTIMATE = re.compile(
    r"[\u3400-\u9fff]|[^\x00-\x7f\s]|[A-Za-z0-9_]{1,4}|[^\w\s]",
    re.UNICODE,
)


@dataclass(frozen=True)
class _TextRegion:
    text: str
    bbox: tuple[float, float, float, float]
    start: int = 0
    end: int = 0


@dataclass(frozen=True)
class _ChunkRange:
    start: int
    end: int
    split_method: str


@dataclass(frozen=True)
class _PendingBlock:
    block_type: str
    content: Any
    page: int
    bbox: tuple[float, float, float, float]
    region_id: str
    parent_region_id: str
    extraction_method: str
    source_span: tuple[int, int] | None
    row_span: tuple[int, int] | None
    sort_key: tuple[float, float, int, int]


class PdfParser(DocumentParser):
    """Parse machine-generated PDFs; OCR and chart interpretation are out of scope."""

    source_type = "pdf"
    suffixes = (".pdf",)

    def __init__(
        self,
        *,
        max_chunk_tokens: int = 1_024,
        max_pages: int = 500,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        if max_chunk_tokens < 1:
            raise ValueError("max_chunk_tokens must be at least 1")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self.max_chunk_tokens = max_chunk_tokens
        self.max_pages = max_pages
        self._token_counter = token_counter or self._estimated_token_count

    def parse(
        self,
        path: str | Path,
        *,
        source_id: str | None = None,
    ) -> RawDocument:
        source_path = self._source_path(path)
        # TODO: 延迟失败设计 Codex考虑了不同的 parser分服务包装给用户的情况
        #  但实际上这里完全可以直接在 import阶段检查
        #  所有的 parser本来就会被一起打包成工具
        if pdfplumber is None:
            raise IngestionError(
                "PDF_DEPENDENCY_UNAVAILABLE",
                "PDF ingestion requires the pdfplumber package.",
                path=source_path,
            )

        try:
            with pdfplumber.open(
                source_path,
                laparams={"all_texts": True, "boxes_flow": 0.5},
            ) as pdf:
                if len(pdf.pages) > self.max_pages:
                    raise IngestionError(
                        "PDF_PAGE_LIMIT_EXCEEDED",
                        f"PDF has {len(pdf.pages)} pages; limit is {self.max_pages}.",
                        path=source_path,
                    )
                # TODO: 这里发现 PDF的文本层无法提取似乎可以开始 OCR了
                if not pdf.doc.is_extractable:
                    raise IngestionError(
                        "PDF_TEXT_EXTRACTION_NOT_ALLOWED",
                        "PDF security settings do not allow text extraction.",
                        path=source_path,
                    )

                pending: list[_PendingBlock] = []
                # 记录只包含图片的页码 如果无法解析出原生text 或者 图片主导由于未实现 OCR,报错
                image_only_pages: list[int] = []
                for page_number, page in enumerate(pdf.pages, start=1):
                    # 解析返回 page 中拆解出的 blocks、是否能解析出 text、是否图片主导
                    page_blocks, has_native_text, image_dominates = self._parse_page(
                        page,
                        page_number,
                        path=source_path,
                    )
                    pending.extend(page_blocks)
                    if not has_native_text and image_dominates:
                        image_only_pages.append(page_number)

                if image_only_pages:
                    pages = ", ".join(str(page) for page in image_only_pages)
                    raise IngestionError(
                        "PDF_OCR_REQUIRED",
                        f"PDF page(s) require OCR and are outside this parser: {pages}.",
                        path=source_path,
                    )
        except IngestionError:
            raise
        except PDFPasswordIncorrect as exc:
            raise IngestionError(
                "PDF_ENCRYPTED",
                "Encrypted PDF requires a password and is not supported.",
                path=source_path,
            ) from exc
        except PDFTextExtractionNotAllowed as exc:
            raise IngestionError(
                "PDF_TEXT_EXTRACTION_NOT_ALLOWED",
                "PDF security settings do not allow text extraction.",
                path=source_path,
            ) from exc
        except (PDFSyntaxError, PdfminerException, OSError, ValueError) as exc:
            raise IngestionError(
                "PDF_INVALID",
                f"Could not parse PDF source {source_path.name}: {exc}",
                path=source_path,
            ) from exc

        if not pending:
            raise IngestionError(
                "PDF_NO_EXTRACTABLE_CONTENT",
                "PDF contains no supported native text, table, or figure content.",
                path=source_path,
            )

        blocks = self._materialize_blocks(pending)
        return RawDocument(
            source_id=self._source_id(source_path, source_id),
            source_type=self.source_type,
            file_name=source_path.name,
            blocks=blocks,
        )

    def _parse_page(
        self,
        page: Any,
        page_number: int,
        *,
        path: Path,
    ) -> tuple[list[_PendingBlock], bool, bool]:
        table_blocks, table_boxes = self._table_blocks(page, page_number, path=path)
        figure_blocks, figure_boxes = self._figure_blocks(page, page_number)
        text_blocks, has_native_text = self._text_blocks(
            page,
            page_number,
            exclusions=(*table_boxes, *figure_boxes),
        )
        page_area = max(float(page.width) * float(page.height), 1.0)
        image_area = sum(self._area(box) for box in figure_boxes)
        image_dominates = image_area / page_area >= 0.8
        return table_blocks + figure_blocks + text_blocks, has_native_text, image_dominates

    def _table_blocks(
        self,
        page: Any,
        page_number: int,
        *,
        path: Path,
    ) -> tuple[list[_PendingBlock], list[tuple[float, float, float, float]]]:
        pending: list[_PendingBlock] = []
        boxes: list[tuple[float, float, float, float]] = []
        for table_index, table in enumerate(page.find_tables(), start=1):
            extracted = table.extract() or []
            rows = [list(row) for row in extracted if any(cell not in (None, "") for cell in row)]
            if not rows:
                continue
            columns = rows[0]
            data_rows = rows[1:]
            bbox = self._bbox_tuple(table.bbox)
            boxes.append(bbox)
            if not data_rows:
                continue
            context, context_boxes = self._table_context(page, bbox)
            boxes.extend(context_boxes)
            metadata: dict[str, Any] = {}
            if context:
                metadata["caption"] = context[-1]
                if len(context) > 1:
                    metadata["context"] = context[:-1]
            parent = f"page_{page_number:04d}_table_{table_index:03d}"
            row_groups = self._split_table_rows(
                columns,
                data_rows,
                path=path,
                metadata=metadata,
            )
            for part_index, (row_start, row_end, group) in enumerate(row_groups, start=1):
                content = {"columns": columns, "rows": group, **metadata}
                region_id = f"{parent}_part_{part_index:03d}"
                pending.append(
                    _PendingBlock(
                        block_type="table",
                        content=content,
                        page=page_number,
                        bbox=bbox,
                        region_id=region_id,
                        parent_region_id=parent,
                        extraction_method="pdf_native_table",
                        source_span=None,
                        row_span=(row_start, row_end),
                        sort_key=(bbox[1], bbox[0], 1, part_index),
                    )
                )
        return pending, boxes

    def _table_context(
        self,
        page: Any,
        table_bbox: tuple[float, float, float, float],
    ) -> tuple[list[str], list[tuple[float, float, float, float]]]:
        """Own a nearby caption/heading and its immediately preceding context."""

        lines = page.extract_text_lines(layout=False, return_chars=False) or []
        candidates: list[tuple[str, tuple[float, float, float, float]]] = []
        for line in lines:
            text = str(line.get("text", "")).strip()
            if not text:
                continue
            bbox = self._bbox_tuple(
                (line["x0"], line["top"], line["x1"], line["bottom"])
            )
            horizontal_overlap = max(
                0.0,
                min(bbox[2], table_bbox[2]) - max(bbox[0], table_bbox[0]),
            )
            line_width = max(bbox[2] - bbox[0], 1.0)
            if bbox[3] <= table_bbox[1] and horizontal_overlap / line_width >= 0.5:
                candidates.append((text, bbox))

        if not candidates or table_bbox[1] - candidates[-1][1][3] > 24.0:
            return [], []

        caption_index = len(candidates) - 1
        selected = [candidates[caption_index]]
        if caption_index > 0:
            previous = candidates[caption_index - 1]
            gap = selected[0][1][1] - previous[1][3]
            if gap <= 18.0:
                selected.insert(0, previous)
        return [item[0] for item in selected], [item[1] for item in selected]

    def _split_table_rows(
        self,
        columns: list[Any],
        rows: list[list[Any]],
        *,
        path: Path,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[tuple[int, int, list[list[Any]]]]:
        header_text = self._table_text(columns, [], metadata=metadata)
        if self._token_counter(header_text) > self.max_chunk_tokens:
            raise IngestionError(
                "PDF_TABLE_HEADER_TOO_LARGE",
                "A PDF table header exceeds the configured chunk budget.",
                path=path,
            )
        if not rows:
            return []

        groups: list[tuple[int, int, list[list[Any]]]] = []
        current: list[list[Any]] = []
        start = 1
        for row_number, row in enumerate(rows, start=1):
            if self._token_counter(
                self._table_text(columns, [row], metadata=metadata)
            ) > self.max_chunk_tokens:
                raise IngestionError(
                    "PDF_TABLE_ROW_TOO_LARGE",
                    f"PDF table row {row_number} exceeds the configured chunk budget.",
                    path=path,
                )
            candidate = [*current, row]
            if current and self._token_counter(
                self._table_text(columns, candidate, metadata=metadata)
            ) > self.max_chunk_tokens:
                groups.append((start, row_number - 1, current))
                current = [row]
                start = row_number
            else:
                current = candidate
        if current:
            groups.append((start, start + len(current) - 1, current))
        return groups

    @staticmethod
    def _table_text(
        columns: list[Any],
        rows: list[list[Any]],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        return json.dumps(
            {"columns": columns, "rows": rows, **(metadata or {})},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )

    def _figure_blocks(
        self,
        page: Any,
        page_number: int,
    ) -> tuple[list[_PendingBlock], list[tuple[float, float, float, float]]]:
        pending: list[_PendingBlock] = []
        boxes: list[tuple[float, float, float, float]] = []
        for figure_index, image in enumerate(page.images, start=1):
            bbox = self._bbox_tuple(
                (image["x0"], image["top"], image["x1"], image["bottom"])
            )
            boxes.append(bbox)
            region_id = f"page_{page_number:04d}_figure_{figure_index:03d}"
            source_size = image.get("srcsize")
            source_width = source_size[0] if isinstance(source_size, tuple) else None
            source_height = source_size[1] if isinstance(source_size, tuple) else None
            content = {
                "figure_index": figure_index,
                "object_name": image.get("name"),
                "width": bbox[2] - bbox[0],
                "height": bbox[3] - bbox[1],
                "source_width": source_width,
                "source_height": source_height,
            }
            pending.append(
                _PendingBlock(
                    block_type="figure",
                    content=content,
                    page=page_number,
                    bbox=bbox,
                    region_id=region_id,
                    parent_region_id=f"page_{page_number:04d}",
                    extraction_method="pdf_embedded_image",
                    source_span=None,
                    row_span=None,
                    sort_key=(bbox[1], bbox[0], 2, figure_index),
                )
            )
        return pending, boxes

    def _text_blocks(
        self,
        page: Any,
        page_number: int,
        *,
        exclusions: Sequence[tuple[float, float, float, float]],
    ) -> tuple[list[_PendingBlock], bool]:
        regions: list[_TextRegion] = []
        # Line-level regions allow caption ownership/exclusion to remain exact;
        # coarse text boxes can contain both a caption and unrelated paragraphs.
        objects = page.extract_text_lines(layout=False, return_chars=False) or []

        for item in objects:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            bbox = self._bbox_tuple(
                (item["x0"], item["top"], item["x1"], item["bottom"])
            )
            if any(self._overlap_ratio(bbox, excluded) >= 0.5 for excluded in exclusions):
                continue
            regions.append(_TextRegion(text=text, bbox=bbox))

        if not regions:
            return [], False

        # pdfminer supplies textboxhorizontal objects in its layout reading order.
        # Preserve that order instead of flattening the page by raw y/x coordinates.
        body_parts: list[str] = []
        positioned: list[_TextRegion] = []
        offset = 0
        for region in regions:
            if body_parts:
                body_parts.append("\n\n")
                offset += 2
            start = offset
            body_parts.append(region.text)
            offset += len(region.text)
            positioned.append(
                _TextRegion(
                    text=region.text,
                    bbox=region.bbox,
                    start=start,
                    end=offset,
                )
            )
        body_text = "".join(body_parts)
        parent = f"page_{page_number:04d}_text_body"
        pending: list[_PendingBlock] = []
        for chunk_index, chunk in enumerate(self._split_text(body_text), start=1):
            bbox = self._bbox_for_span(positioned, chunk.start, chunk.end)
            pending.append(
                _PendingBlock(
                    block_type="text",
                    content=body_text[chunk.start : chunk.end],
                    page=page_number,
                    bbox=bbox,
                    region_id=f"{parent}_chunk_{chunk_index:03d}",
                    parent_region_id=parent,
                    extraction_method=f"pdf_native_text:{chunk.split_method}",
                    source_span=(chunk.start, chunk.end),
                    row_span=None,
                    sort_key=(bbox[1], bbox[0], 0, chunk_index),
                )
            )
        return pending, True

    def _split_text(self, text: str) -> list[_ChunkRange]:
        if self._token_counter(text) <= self.max_chunk_tokens:
            return [_ChunkRange(0, len(text), "page")]
        return self._split_range(text, 0, len(text), level=0)

    def _split_range(
        self,
        text: str,
        start: int,
        end: int,
        *,
        level: int,
    ) -> list[_ChunkRange]:
        if self._token_counter(text[start:end]) <= self.max_chunk_tokens:
            method = ("paragraph", "sentence")[min(level, 1)]
            return [_ChunkRange(start, end, method)]

        boundaries = (_PARAGRAPH_BOUNDARY, _SENTENCE_BOUNDARY)
        methods = ("paragraph", "sentence")
        if level >= len(boundaries):
            return self._hard_token_split(text, start, end)

        split_points = [
            match.end()
            for match in boundaries[level].finditer(text, start, end)
            if start < match.end() < end
        ]
        if not split_points:
            return self._split_range(text, start, end, level=level + 1)

        units = list(zip([start, *split_points], [*split_points, end], strict=True))
        result: list[_ChunkRange] = []
        group_start: int | None = None
        group_end: int | None = None
        for unit_start, unit_end in units:
            if self._token_counter(text[unit_start:unit_end]) > self.max_chunk_tokens:
                if group_start is not None and group_end is not None:
                    result.append(_ChunkRange(group_start, group_end, methods[level]))
                result.extend(
                    self._split_range(text, unit_start, unit_end, level=level + 1)
                )
                group_start = None
                group_end = None
                continue
            if group_start is None:
                group_start, group_end = unit_start, unit_end
                continue
            assert group_end is not None
            if self._token_counter(text[group_start:unit_end]) <= self.max_chunk_tokens:
                group_end = unit_end
            else:
                result.append(_ChunkRange(group_start, group_end, methods[level]))
                group_start, group_end = unit_start, unit_end
        if group_start is not None and group_end is not None:
            result.append(_ChunkRange(group_start, group_end, methods[level]))
        return result

    def _hard_token_split(self, text: str, start: int, end: int) -> list[_ChunkRange]:
        chunks: list[_ChunkRange] = []
        cursor = start
        while cursor < end:
            low, high = cursor + 1, end
            best = cursor
            while low <= high:
                middle = (low + high) // 2
                if self._token_counter(text[cursor:middle]) <= self.max_chunk_tokens:
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            if best == cursor:
                best = cursor + 1
            if best < end:
                boundary = self._nearby_whitespace(text, cursor, best)
                if boundary is not None:
                    best = boundary
            chunks.append(_ChunkRange(cursor, best, "token"))
            cursor = best
        return chunks

    @staticmethod
    def _nearby_whitespace(text: str, start: int, proposed: int) -> int | None:
        search_start = max(start + 1, proposed - max((proposed - start) // 5, 1))
        for index in range(proposed, search_start - 1, -1):
            if text[index - 1].isspace():
                return index
        return None

    # TODO: 这个方法拓展下去可能有助于实现 .PDF->.doc 的准确映射
    #  就像一个把 PDF 转换为可编辑的文件的工具
    def _materialize_blocks(self, pending: list[_PendingBlock]) -> list[RawBlock]:
        blocks: list[RawBlock] = []
        ordered = sorted(pending, key=lambda block: (block.page, *block.sort_key))
        page_orders: dict[int, int] = {}
        for block_number, item in enumerate(ordered, start=1):
            page_orders[item.page] = page_orders.get(item.page, 0) + 1
            reading_order = page_orders[item.page]
            bbox = BoundingBox(
                x0=item.bbox[0],
                top=item.bbox[1],
                x1=item.bbox[2],
                bottom=item.bbox[3],
            )
            span = (
                CharacterSpan(start=item.source_span[0], end=item.source_span[1])
                if item.source_span is not None
                else None
            )
            location = self._source_location(
                item.page,
                item.bbox,
                item.source_span,
                item.row_span,
            )
            blocks.append(
                RawBlock(
                    block_id=f"block_{block_number:04d}",
                    block_type=item.block_type,
                    content=item.content,
                    source_location=location,
                    source_region=SourceRegion(
                        region_id=item.region_id,
                        parent_region_id=item.parent_region_id,
                        page=item.page,
                        bbox=bbox,
                        reading_order=reading_order,
                        extraction_method=item.extraction_method,
                        source_span=span,
                        row_start=item.row_span[0] if item.row_span is not None else None,
                        row_end=item.row_span[1] if item.row_span is not None else None,
                    ),
                )
            )
        return blocks

    @staticmethod
    def _source_location(
        page: int,
        bbox: tuple[float, float, float, float],
        span: tuple[int, int] | None,
        row_span: tuple[int, int] | None,
    ) -> str:
        coordinates = ", ".join(f"{value:.2f}" for value in bbox)
        if span is not None:
            suffix = f", chars {span[0]}-{span[1]}"
        elif row_span is not None:
            suffix = f", data rows {row_span[0]}-{row_span[1]}"
        else:
            suffix = ""
        return f"page {page}, bbox ({coordinates}){suffix}"

    @staticmethod
    def _bbox_for_span(
        regions: Sequence[_TextRegion],
        start: int,
        end: int,
    ) -> tuple[float, float, float, float]:
        boxes = [
            region.bbox
            for region in regions
            if region.start < end and region.end > start
        ]
        if not boxes:
            raise AssertionError("text chunk must overlap at least one source region")
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )

    @staticmethod
    def _bbox_tuple(values: Sequence[Any]) -> tuple[float, float, float, float]:
        if len(values) != 4:
            raise ValueError("PDF bounding box must contain four coordinates")
        x0, top, x1, bottom = (float(value) for value in values)
        return (x0, top, x1, bottom)

    @staticmethod
    def _area(box: tuple[float, float, float, float]) -> float:
        return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)

    @classmethod
    def _overlap_ratio(
        cls,
        first: tuple[float, float, float, float],
        second: tuple[float, float, float, float],
    ) -> float:
        intersection = (
            max(min(first[2], second[2]) - max(first[0], second[0]), 0.0)
            * max(min(first[3], second[3]) - max(first[1], second[1]), 0.0)
        )
        return intersection / max(cls._area(first), 1.0)

    @staticmethod
    def _estimated_token_count(text: str) -> int:
        """Conservative, deterministic estimate; provider preflight remains authoritative."""

        return len(_TOKEN_ESTIMATE.findall(text))


PDFParser = PdfParser
