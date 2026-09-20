"""Native-text PDF ingestion with page-bounded, layout-aware chunking."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import json
import math
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
    ExtractionEvidence,
    RawBlock,
    RawDocument,
    SourceRegion,
    SourceRegionPart,
)
from .ocr import OcrBackend, OcrBackendError, OcrPageResult, OcrRegion

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
    extraction_evidence: ExtractionEvidence | None = None
    parts: tuple[SourceRegionPart, ...] = ()
    section_title: str | None = None
    original_region_id: str | None = None


@dataclass(frozen=True)
class _PageAnalysis:
    page: Any
    page_number: int
    native_blocks: tuple[_PendingBlock, ...]
    has_native_text: bool
    image_count: int
    image_coverage: float

    @property
    def requires_ocr(self) -> bool:
        return not self.has_native_text and self.image_count > 0


@dataclass(frozen=True)
class OcrReviewIssue:
    issue_id: str
    page: int
    page_index: int
    number: str
    block_id: str
    label: str
    region_type: str
    bbox: tuple[float, float, float, float]
    confidence: float | None
    flags: tuple[str, ...]
    raw_content: str
    recognized_content: str


@dataclass(frozen=True)
class OcrReviewSession:
    issues: tuple[OcrReviewIssue, ...]
    pages: tuple[tuple[OcrPageResult, float, float], ...]
    path: Path
    source_id: str
    source_quality_flags: tuple[str, ...]
    artifact: dict[str, Any]


class OcrReviewRequired(Exception):
    def __init__(self, session: OcrReviewSession) -> None:
        self.session = session
        super().__init__(f"{len(session.issues)} OCR region(s) need review")


class PdfParser(DocumentParser):
    """Parse native PDFs or whole-document scans through an optional OCR backend."""

    source_type = "pdf"
    suffixes = (".pdf",)

    def __init__(
        self,
        *,
        max_chunk_tokens: int = 1_024,
        max_pages: int = 500,
        token_counter: Callable[[str], int] | None = None,
        ocr_backend: OcrBackend | None = None,
        ocr_render_dpi: int = 300,
        ocr_max_pixels_per_page: int = 40_000_000,
        ocr_languages: Sequence[str] = ("ch", "en"),
        reject_low_confidence_ocr: bool = True,
        ocr_artifact_dir: Path | None = None,
    ) -> None:
        if max_chunk_tokens < 1:
            raise ValueError("max_chunk_tokens must be at least 1")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if ocr_render_dpi < 72:
            raise ValueError("ocr_render_dpi must be at least 72")
        if ocr_max_pixels_per_page < 1:
            raise ValueError("ocr_max_pixels_per_page must be at least 1")
        normalized_languages = tuple(
            language.strip() for language in ocr_languages if language.strip()
        )
        if not normalized_languages:
            raise ValueError("ocr_languages must contain at least one language")
        self.max_chunk_tokens = max_chunk_tokens
        self.max_pages = max_pages
        self._token_counter = token_counter or self._estimated_token_count
        self.ocr_backend = ocr_backend
        self.ocr_render_dpi = ocr_render_dpi
        self.ocr_max_pixels_per_page = ocr_max_pixels_per_page
        self.ocr_languages = normalized_languages
        self.reject_low_confidence_ocr = reject_low_confidence_ocr
        self.ocr_artifact_dir = ocr_artifact_dir

    def parse(
        self,
        path: str | Path,
        *,
        source_id: str | None = None,
        review_ocr: bool = False,
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
                text_extraction_allowed = bool(pdf.doc.is_extractable)

                analyses: list[_PageAnalysis] = []
                for page_number, page in enumerate(pdf.pages, start=1):
                    if text_extraction_allowed:
                        page_blocks, has_native_text = self._parse_page(
                            page,
                            page_number,
                            path=source_path,
                        )
                    else:
                        # Do not ask pdfminer to extract protected text. The
                        # page remains renderable and is routed directly to OCR.
                        page_blocks, has_native_text = [], False
                    page_area = max(float(page.width) * float(page.height), 1.0)
                    image_area = sum(
                        self._area(
                            self._bbox_tuple(
                                (
                                    image["x0"],
                                    image["top"],
                                    image["x1"],
                                    image["bottom"],
                                )
                            )
                        )
                        for image in page.images
                    )
                    analyses.append(
                        _PageAnalysis(
                            page=page,
                            page_number=page_number,
                            native_blocks=tuple(page_blocks),
                            has_native_text=has_native_text,
                            image_count=len(page.images),
                            image_coverage=min(image_area / page_area, 1.0),
                        )
                    )

                if not text_extraction_allowed:
                    pending = self._parse_restricted_document(
                        analyses,
                        path=source_path,
                        source_id=self._source_id(source_path, source_id),
                        review_ocr=review_ocr,
                    )
                else:
                    pending = self._parse_document_mode(
                        analyses, path=source_path,
                        source_id=self._source_id(source_path, source_id),
                        review_ocr=review_ocr,
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
                "PDF_RENDER_FAILED",
                "The restricted PDF could not be opened for page rendering.",
                path=source_path,
                stage="pdf_render",
                stage_label="PDF 页面渲染",
                resolution="请检查文件密码、损坏状态或页面渲染权限。",
                details={"restriction": "copy_and_text_extraction"},
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
    ) -> tuple[list[_PendingBlock], bool]:
        table_blocks, table_boxes = self._table_blocks(page, page_number, path=path)
        figure_blocks, figure_boxes = self._figure_blocks(page, page_number)
        text_blocks, has_native_text = self._text_blocks(
            page,
            page_number,
            exclusions=(*table_boxes, *figure_boxes),
        )
        return self._structure_and_chunk(
            table_blocks + figure_blocks + text_blocks, path=path
        ), has_native_text

    def _parse_restricted_document(
        self,
        analyses: Sequence[_PageAnalysis],
        *,
        path: Path,
        source_id: str,
        review_ocr: bool,
    ) -> list[_PendingBlock]:
        context = {
            "restriction": "copy_and_text_extraction",
            "page_count": len(analyses),
            "pages_with_images": sum(analysis.image_count > 0 for analysis in analyses),
            "image_coverage_by_page": {
                str(analysis.page_number): round(analysis.image_coverage, 4)
                for analysis in analyses
            },
        }
        if self.ocr_backend is None:
            raise IngestionError(
                "PDF_OCR_REQUIRED",
                "PDF text extraction is restricted, so OCR is required, but no OCR backend is enabled.",
                path=path,
                stage="ocr_configuration",
                stage_label="OCR 配置",
                resolution="请安装 OCR 依赖并设置 RESERVOIR_OCR_BACKEND=paddleocr。",
                details=context,
            )
        return self._parse_scanned_document(
            analyses,
            path=path,
            source_quality_flags=("SOURCE_TEXT_EXTRACTION_RESTRICTED",),
            source_id=source_id,
            review_ocr=review_ocr,
        )

    def _parse_document_mode(
        self,
        analyses: Sequence[_PageAnalysis],
        *,
        path: Path,
        source_id: str,
        review_ocr: bool,
    ) -> list[_PendingBlock]:
        ocr_pages = [analysis.page_number for analysis in analyses if analysis.requires_ocr]
        native_pages = [
            analysis.page_number
            for analysis in analyses
            if analysis.has_native_text
            or any(block.block_type == "table" for block in analysis.native_blocks)
        ]

        if ocr_pages and native_pages:
            raise IngestionError(
                "PDF_HYBRID_UNSUPPORTED",
                (
                    "PDF mixes native-content page(s) "
                    f"{self._page_list(native_pages)} with OCR-required page(s) "
                    f"{self._page_list(ocr_pages)}; hybrid PDF ingestion is not supported."
                ),
                path=path,
            )
        if ocr_pages:
            if self.ocr_backend is None:
                raise IngestionError(
                    "PDF_OCR_REQUIRED",
                    f"PDF page(s) require OCR: {self._page_list(ocr_pages)}.",
                    path=path,
                    stage="ocr_configuration",
                    stage_label="OCR 配置",
                    resolution=(
                        "请安装 OCR 依赖并设置 RESERVOIR_OCR_BACKEND=paddleocr。"
                    ),
                    details={
                        "ocr_pages": ocr_pages,
                        "image_coverage_by_page": {
                            str(analysis.page_number): round(
                                analysis.image_coverage,
                                4,
                            )
                            for analysis in analyses
                            if analysis.requires_ocr
                        },
                    },
                )
            return self._parse_scanned_document(
                analyses, path=path, source_id=source_id, review_ocr=review_ocr,
            )
        unclassified_pages = [
            analysis.page_number
            for analysis in analyses
            if not analysis.has_native_text
            and analysis.image_count == 0
            and not analysis.native_blocks
        ]
        if unclassified_pages and not native_pages:
            raise IngestionError(
                "PDF_CAPABILITY_UNDETERMINED",
                (
                    "PDF has no native text or raster image evidence on page(s) "
                    f"{self._page_list(unclassified_pages)}."
                ),
                path=path,
            )
        return [block for analysis in analyses for block in analysis.native_blocks]

    def _parse_scanned_document(
        self,
        analyses: Sequence[_PageAnalysis],
        *,
        path: Path,
        source_quality_flags: Sequence[str] = (),
        source_id: str,
        review_ocr: bool,
    ) -> list[_PendingBlock]:
        assert self.ocr_backend is not None
        from .ocr.artifacts import OcrArtifactWriter, active_artifact, publish
        writer = OcrArtifactWriter(path.name, len(analyses), self.ocr_render_dpi, self.ocr_artifact_dir)
        token = active_artifact.set(writer)
        results = []
        try:
            for analysis in analyses:
                publish(stage="ocr", page=analysis.page_number, total_pages=len(analyses))
                page_result = self._ocr_page(analysis, path=path)
                results.append((analysis, page_result))
            artifact = writer.finish()
        except Exception as exc:
            writer.finish(str(exc))
            raise
        finally:
            active_artifact.reset(token)
        page_data = tuple(
            (page_result, float(analysis.page.width), float(analysis.page.height))
            for analysis, page_result in results
        )
        if review_ocr and self.reject_low_confidence_ocr:
            issues = self._ocr_review_issues(page_data, path=path)
            if issues:
                raise OcrReviewRequired(OcrReviewSession(
                    issues=issues, pages=page_data, path=path, source_id=source_id,
                    source_quality_flags=tuple(source_quality_flags), artifact=artifact,
                ))
        pending: list[_PendingBlock] = []
        for analysis, page_result in results:
            pending.extend(
                self._ocr_blocks(
                    page_result,
                    page_width=float(analysis.page.width),
                    page_height=float(analysis.page.height),
                    path=path,
                    source_quality_flags=source_quality_flags,
                )
            )
        if not pending:
            raise IngestionError(
                "PDF_NO_EXTRACTABLE_CONTENT",
                "OCR found no supported text, table, or figure content.",
                path=path,
            )
        return pending

    def _ocr_page(
        self,
        analysis: _PageAnalysis,
        *,
        path: Path,
    ) -> OcrPageResult:
        width_pixels = math.ceil(float(analysis.page.width) * self.ocr_render_dpi / 72)
        height_pixels = math.ceil(float(analysis.page.height) * self.ocr_render_dpi / 72)
        estimated_pixels = width_pixels * height_pixels
        if estimated_pixels > self.ocr_max_pixels_per_page:
            raise IngestionError(
                "PDF_PIXEL_LIMIT_EXCEEDED",
                (
                    f"PDF page {analysis.page_number} would render to "
                    f"{estimated_pixels} pixels; limit is {self.ocr_max_pixels_per_page}."
                ),
                path=path,
            )
        try:
            page_image = analysis.page.to_image(
                resolution=self.ocr_render_dpi,
                antialias=True,
            ).original
        except Exception as exc:
            raise IngestionError(
                "PDF_RENDER_FAILED",
                f"Could not render PDF page {analysis.page_number}: {exc}",
                path=path,
                stage="pdf_render",
                stage_label="PDF 页面渲染",
                resolution="请检查文件密码、损坏状态或页面渲染权限。",
                details={"page": analysis.page_number},
            ) from exc
        actual_pixels = int(page_image.width) * int(page_image.height)
        if actual_pixels > self.ocr_max_pixels_per_page:
            raise IngestionError(
                "PDF_PIXEL_LIMIT_EXCEEDED",
                (
                    f"Rendered PDF page {analysis.page_number} contains "
                    f"{actual_pixels} pixels; limit is {self.ocr_max_pixels_per_page}."
                ),
                path=path,
            )
        try:
            assert self.ocr_backend is not None
            result = self.ocr_backend.analyze_page(
                page_image,
                page_number=analysis.page_number,
                languages=self.ocr_languages,
            )
            # Other backends can still expose a preview from their public contract.
            from .ocr.artifacts import active_artifact
            writer = active_artifact.get()
            if writer is not None and analysis.page_number not in writer.pages:
                from html import escape
                regions = []
                for region in result.regions:
                    content = region.text or ""
                    if region.table:
                        rows = [region.table.columns, *region.table.rows]
                        content = "<table>" + "".join("<tr>" + "".join("<td>" + escape(str(cell)) + "</td>" for cell in row) + "</tr>" for row in rows) + "</table>"
                    regions.append({"block_label": region.region_type, "block_bbox": list(region.bbox_pixels), "block_content": content})
                writer.record(analysis.page_number, {"parsing_res_list": regions, "engine": result.engine, "engine_version": result.engine_version}, page_image, source_format="normalized_backend_contract")
        except OcrBackendError as exc:
            raise IngestionError(
                exc.code,
                f"OCR failed on PDF page {analysis.page_number}: {exc}",
                path=path,
                stage="ocr_inference",
                stage_label="OCR 识别",
                resolution="请检查 OCR 依赖、模型文件、设备配置和输入页面质量。",
                details={"page": analysis.page_number},
            ) from exc
        except Exception as exc:
            if isinstance(exc, IngestionError):
                raise
            raise IngestionError(
                "PDF_OCR_FAILED",
                f"OCR failed on PDF page {analysis.page_number}: {exc}",
                path=path,
                stage="ocr_inference",
                stage_label="OCR 识别",
                resolution="请检查 OCR 模型和运行设备配置。",
                details={"page": analysis.page_number},
            ) from exc
        finally:
            page_image.close()
        if result.page_number != analysis.page_number:
            raise IngestionError(
                "PDF_OCR_OUTPUT_INVALID",
                (
                    f"OCR returned page {result.page_number} for requested page "
                    f"{analysis.page_number}."
                ),
                path=path,
            )
        if result.image_width < 1 or result.image_height < 1:
            raise IngestionError(
                "PDF_OCR_OUTPUT_INVALID",
                f"OCR returned invalid dimensions for page {analysis.page_number}.",
                path=path,
            )
        return result

    def _ocr_blocks(
        self,
        result: OcrPageResult,
        *,
        page_width: float,
        page_height: float,
        path: Path,
        source_quality_flags: Sequence[str] = (),
        review_decisions: Mapping[str, str] | None = None,
    ) -> list[_PendingBlock]:
        pending: list[_PendingBlock] = []
        figure_index = 0
        for region_index, region in enumerate(result.regions, start=1):
            display_region = f"R{region.source_region_index or region_index}"
            bbox = self._ocr_bbox_to_pdf(
                region,
                image_width=result.image_width,
                image_height=result.image_height,
                page_width=page_width,
                page_height=page_height,
                path=path,
                page=result.page_number,
            )
            confidence = (
                self._normalized_ocr_confidence(
                    region.confidence,
                    path=path,
                    page=result.page_number,
                    display_region=display_region,
                )
                if region.confidence is not None
                else None
            )
            review_flags = self._ocr_review_flags(region, confidence)
            issue_id = self._ocr_issue_id(result.page_number, region, region_index)
            decision = review_decisions.get(issue_id) if review_decisions is not None else None
            if review_flags and decision == "exclude":
                continue
            if self.reject_low_confidence_ocr and review_flags and decision != "include":
                raise IngestionError(
                    "PDF_OCR_LOW_CONFIDENCE",
                    (
                        f"OCR confidence requires review on page {result.page_number}, "
                        f"region {display_region}: {', '.join(review_flags)}."
                    ),
                    path=path,
                )
            quality_flags = [*source_quality_flags, *review_flags]
            method = f"pdf_ocr_{region.region_type}"
            evidence = ExtractionEvidence(
                method=method,
                engine=result.engine,
                engine_version=result.engine_version,
                model_version=result.model_version,
                languages=list(self.ocr_languages),
                confidence=confidence,
                layout_label=region.layout_label,
                render_dpi=self.ocr_render_dpi,
                preprocessing=list(result.preprocessing),
                quality_flags=quality_flags,
            )
            parent = (
                f"page_{result.page_number:04d}_ocr_"
                f"{region.region_type}_{region_index:03d}"
            )
            sort_prefix = (float(region.reading_order), bbox[0])

            if region.region_type == "text":
                text = (region.text or "").strip()
                if not text:
                    continue
                for part_index, chunk in enumerate([_ChunkRange(0, len(text), "region")], start=1):
                    pending.append(
                        _PendingBlock(
                            block_type="text",
                            content=text[chunk.start : chunk.end],
                            page=result.page_number,
                            bbox=bbox,
                            region_id=f"{parent}_chunk_{part_index:03d}",
                            parent_region_id=parent,
                            extraction_method=f"{method}:{chunk.split_method}",
                            source_span=(chunk.start, chunk.end),
                            row_span=None,
                            sort_key=(*sort_prefix, 0, part_index),
                            extraction_evidence=evidence,
                            original_region_id=region.region_id,
                        )
                    )
                continue

            if region.region_type == "table":
                if region.table is None:
                    raise IngestionError(
                        "PDF_OCR_OUTPUT_INVALID",
                        f"OCR table region {display_region} has no table structure.",
                        path=path,
                    )
                if not region.table.columns or any(
                    len(row) != len(region.table.columns)
                    for row in region.table.rows
                ):
                    raise IngestionError(
                        "PDF_OCR_OUTPUT_INVALID",
                        (
                            f"OCR table region {display_region} does not satisfy "
                            "the columns/rows contract."
                        ),
                        path=path,
                    )
                metadata = {
                    **dict(region.table.metadata),
                    "ocr_region_id": region.region_id,
                }
                rows = [list(row) for row in region.table.rows]
                groups = [(1, len(rows), rows)] if rows else []
                if not groups:
                    pending.append(
                        _PendingBlock(
                            block_type="table",
                            content={
                                "columns": list(region.table.columns),
                                "rows": [],
                                **metadata,
                            },
                            page=result.page_number,
                            bbox=bbox,
                            region_id=f"{parent}_part_001",
                            parent_region_id=parent,
                            extraction_method=method,
                            source_span=None,
                            row_span=None,
                            sort_key=(*sort_prefix, 1, 1),
                            extraction_evidence=evidence,
                            original_region_id=region.region_id,
                        )
                    )
                    continue
                for part_index, (row_start, row_end, rows) in enumerate(
                    groups,
                    start=1,
                ):
                    pending.append(
                        _PendingBlock(
                            block_type="table",
                            content={
                                "columns": list(region.table.columns),
                                "rows": rows,
                                **metadata,
                            },
                            page=result.page_number,
                            bbox=bbox,
                            region_id=f"{parent}_part_{part_index:03d}",
                            parent_region_id=parent,
                            extraction_method=method,
                            source_span=None,
                            row_span=(row_start, row_end),
                            sort_key=(*sort_prefix, 1, part_index),
                            extraction_evidence=evidence,
                            original_region_id=region.region_id,
                        )
                    )
                continue

            figure_index += 1
            pending.append(
                _PendingBlock(
                    block_type="figure",
                    content={
                        "figure_index": figure_index,
                        "layout_label": region.layout_label,
                        "ocr_region_id": region.region_id,
                        "source": "scanned_page_region",
                    },
                    page=result.page_number,
                    bbox=bbox,
                    region_id=parent,
                    parent_region_id=f"page_{result.page_number:04d}",
                    extraction_method=method,
                    source_span=None,
                    row_span=None,
                    sort_key=(*sort_prefix, 2, figure_index),
                    extraction_evidence=evidence,
                    original_region_id=region.region_id,
                )
            )
        return self._structure_and_chunk(pending, path=path)

    @staticmethod
    def _ocr_issue_id(page: int, region: OcrRegion, index: int) -> str:
        return f"p{page:04d}-r{region.source_region_index or index:04d}"

    @staticmethod
    def _ocr_review_flags(region: OcrRegion, confidence: float | None) -> list[str]:
        is_formula = region.layout_label.casefold() == "formula"
        flags = [flag for flag in region.quality_flags
                 if not (is_formula and flag in {"LOW_TEXT_CONFIDENCE", "OCR_CONFIDENCE_UNAVAILABLE"})]
        if not is_formula and region.region_type in {"text", "table"} and confidence is None:
            flags.append("OCR_CONFIDENCE_UNAVAILABLE")
        return list(dict.fromkeys(flags))

    def _ocr_review_issues(
        self,
        pages: tuple[tuple[OcrPageResult, float, float], ...],
        *,
        path: Path,
    ) -> tuple[OcrReviewIssue, ...]:
        issues: list[OcrReviewIssue] = []
        for page_index, (result, width, height) in enumerate(pages):
            for index, region in enumerate(result.regions, 1):
                number = f"R{region.source_region_index or index}"
                confidence = (self._normalized_ocr_confidence(
                    region.confidence, path=path, page=result.page_number,
                    display_region=number,
                ) if region.confidence is not None else None)
                flags = self._ocr_review_flags(region, confidence)
                if not flags:
                    continue
                bbox = self._ocr_bbox_to_pdf(
                    region, image_width=result.image_width, image_height=result.image_height,
                    page_width=width, page_height=height, path=path, page=result.page_number,
                )
                content = (region.text if region.region_type == "text" else
                           json.dumps({"columns": region.table.columns, "rows": region.table.rows},
                                      ensure_ascii=False, indent=2) if region.table else "")
                issues.append(OcrReviewIssue(
                    issue_id=self._ocr_issue_id(result.page_number, region, index),
                    page=result.page_number, page_index=page_index, number=number,
                    block_id=region.region_id, label=region.layout_label,
                    region_type=region.region_type, bbox=bbox, confidence=confidence,
                    flags=tuple(flags), raw_content=region.raw_content or content or "",
                    recognized_content=content or "",
                ))
        return tuple(issues)

    def finalize_ocr_review(
        self,
        session: OcrReviewSession,
        decisions: Mapping[str, str],
    ) -> RawDocument:
        expected = {issue.issue_id for issue in session.issues}
        if set(decisions) != expected or any(value not in {"include", "exclude"} for value in decisions.values()):
            raise ValueError("Every OCR issue needs exactly one include/exclude decision")
        pending: list[_PendingBlock] = []
        for result, width, height in session.pages:
            pending.extend(self._ocr_blocks(
                result, page_width=width, page_height=height, path=session.path,
                source_quality_flags=session.source_quality_flags,
                review_decisions=decisions,
            ))
        return RawDocument(
            source_id=session.source_id, source_type=self.source_type,
            file_name=session.path.name, blocks=self._materialize_blocks(pending),
        )

    @staticmethod
    def _source_part(region: _PendingBlock, *, role: str = "evidence") -> SourceRegionPart:
        evidence = region.extraction_evidence
        return SourceRegionPart(
            region_id=region.original_region_id or region.region_id,
            bbox=BoundingBox(x0=region.bbox[0], top=region.bbox[1], x1=region.bbox[2], bottom=region.bbox[3]),
            extraction_method=region.extraction_method,
            text=region.content if region.block_type == "text" else None,
            confidence=evidence.confidence if evidence else None,
            quality_flags=list(evidence.quality_flags) if evidence else [],
            role=role,
        )

    @staticmethod
    def _combined_evidence(regions: Sequence[_PendingBlock]) -> ExtractionEvidence | None:
        evidence = [r.extraction_evidence for r in regions if r.extraction_evidence is not None]
        if not evidence:
            return None
        scores = [e.confidence for e in evidence if e.confidence is not None]
        return evidence[0].model_copy(update={
            "confidence": min(scores) if scores else None,
            "quality_flags": sorted({flag for e in evidence for flag in e.quality_flags}),
        })

    @staticmethod
    def _heading(region: _PendingBlock) -> bool:
        text = str(region.content).strip()
        label = region.extraction_evidence.layout_label if region.extraction_evidence else None
        return label in {"doc_title", "paragraph_title", "title"} or (
            len(text) <= 40 and text.endswith((":", "：")) and "\n" not in text
        )

    def _structure_and_chunk(self, regions: Sequence[_PendingBlock], *, path: Path) -> list[_PendingBlock]:
        """Shared page organizer/chunker; extraction adapters supply unsplit regions."""
        if not regions:
            return []
        text_regions = [r for r in regions if r.block_type == "text"]
        # Correct single-column OCR order, but retain extractor order when boxes
        # explicitly demonstrate simultaneous side-by-side text columns.
        multicolumn = any(
            max(a.bbox[1], b.bbox[1]) < min(a.bbox[3], b.bbox[3])
            and (a.bbox[2] <= b.bbox[0] or b.bbox[2] <= a.bbox[0])
            for i, a in enumerate(text_regions) for b in text_regions[i + 1:]
        )
        ordered = sorted(regions, key=lambda r: r.sort_key if multicolumn else (r.bbox[1], r.bbox[0], r.sort_key[2], r.sort_key[3]))
        owned: set[str] = set()
        associated: dict[str, list[_PendingBlock]] = {}

        def overlaps(a: _PendingBlock, b: _PendingBlock) -> bool:
            overlap = max(0.0, min(a.bbox[2], b.bbox[2]) - max(a.bbox[0], b.bbox[0]))
            return overlap / max(a.bbox[2] - a.bbox[0], 1.0) >= 0.5

        for table in (r for r in ordered if r.block_type == "table"):
            candidates = sorted((r for r in text_regions if r.region_id not in owned
                and r.bbox[3] <= table.bbox[1] and overlaps(r, table)), key=lambda r: r.bbox[3])
            if not candidates or table.bbox[1] - candidates[-1].bbox[3] > 24:
                continue
            caption = candidates[-1]
            label = caption.extraction_evidence.layout_label if caption.extraction_evidence else None
            if label not in {"table_title", "figure_title", "figure_table_title"} and not re.match(r"^(?:表\s*\d|table\b|pvt\b|pvt分析)", str(caption.content), re.I):
                continue
            context = [caption]
            if len(candidates) > 1:
                previous = candidates[-2]
                if caption.bbox[1] - previous.bbox[3] <= 18:
                    context.insert(0, previous)
            associated[table.region_id] = context
            owned.update(r.region_id for r in context)

        output: list[_PendingBlock] = []
        body: list[_PendingBlock] = []
        section: _PendingBlock | None = None
        group = 0
        page = ordered[0].page

        def flush() -> None:
            nonlocal group
            if not body:
                return
            group += 1
            positioned: list[_TextRegion] = []
            offset = 0
            for region in body:
                if positioned:
                    offset += 2
                text = str(region.content)
                positioned.append(_TextRegion(text, region.bbox, offset, offset + len(text)))
                offset += len(text)
            text = "\n\n".join(r.text for r in positioned)
            parent = f"page_{page:04d}_text_body" + (f"_{group:03d}" if group > 1 else "")
            title = str(section.content) if section else ""
            method = body[0].extraction_method.split(":", 1)[0]
            for index, chunk in enumerate(self._split_text(text, context=title), 1):
                contributors = [(r, p) for r, p in zip(body, positioned, strict=True)
                                if p.end > chunk.start and p.start < chunk.end]
                parts = []
                for original, p in contributors:
                    start, end = max(chunk.start, p.start) - p.start, min(chunk.end, p.end) - p.start
                    parts.append(self._source_part(original).model_copy(update={
                        "text": p.text[start:end], "source_span": CharacterSpan(start=start, end=end),
                    }))
                if section and all(r.region_id != section.region_id for r, _ in contributors):
                    parts.insert(0, self._source_part(section, role="context"))
                bbox = self._bbox_for_span(positioned, chunk.start, chunk.end)
                selected = [r for r, _ in contributors] + ([section] if section else [])
                output.append(_PendingBlock(
                    block_type="text", content=text[chunk.start:chunk.end], page=page,
                    bbox=bbox, region_id=f"{parent}_chunk_{index:03d}", parent_region_id=parent,
                    extraction_method=f"{method}:{chunk.split_method}", source_span=(chunk.start, chunk.end),
                    row_span=None, sort_key=(float(len(output)), bbox[0], 0, index),
                    extraction_evidence=self._combined_evidence(selected), parts=tuple(parts),
                    section_title=title or None,
                ))
            body.clear()

        for region in ordered:
            if region.region_id in owned:
                continue
            if region.block_type == "text":
                if multicolumn and body and (body[-1].bbox[2] <= region.bbox[0] or region.bbox[2] <= body[-1].bbox[0]):
                    flush()
                    section = None
                if self._heading(region):
                    flush()
                    section = region
                body.append(region)
                continue
            flush()
            if region.block_type != "table":
                output.append(replace(region, sort_key=(float(len(output)), region.bbox[0], 2, 1), parts=(self._source_part(region),)))
                continue
            context = associated.get(region.region_id, [])
            metadata = {k: v for k, v in region.content.items() if k not in {"columns", "rows"}}
            if context:
                metadata["caption"] = str(context[-1].content)
                if len(context) > 1:
                    metadata["context"] = [str(r.content) for r in context[:-1]]
            if section:
                metadata["section_title"] = str(section.content)
            evidence_context = list(context)
            if section and all(r.region_id != section.region_id for r in context):
                evidence_context.append(section)
            parts = (self._source_part(region), *(self._source_part(r, role="context") for r in evidence_context))
            evidence = self._combined_evidence([region, *evidence_context])
            groups = self._split_table_rows(region.content["columns"], region.content["rows"], path=path, metadata=metadata)
            if not groups:
                groups = [(0, 0, [])]
            for index, (start, end, rows) in enumerate(groups, 1):
                output.append(replace(region, content={"columns": region.content["columns"], "rows": rows, **metadata},
                    region_id=f"{region.parent_region_id}_part_{index:03d}", row_span=(start, end) if rows else None,
                    sort_key=(float(len(output)), region.bbox[0], 1, index), parts=parts, extraction_evidence=evidence))
        flush()
        return output

    @staticmethod
    def _ocr_bbox_to_pdf(
        region: OcrRegion,
        *,
        image_width: int,
        image_height: int,
        page_width: float,
        page_height: float,
        path: Path,
        page: int,
    ) -> tuple[float, float, float, float]:
        x0, top, x1, bottom = region.bbox_pixels
        if x1 < x0 or bottom < top:
            raise IngestionError(
                "PDF_OCR_OUTPUT_INVALID",
                f"OCR returned an inverted bounding box on page {page}.",
                path=path,
            )
        if x0 < 0 or top < 0 or x1 > image_width or bottom > image_height:
            raise IngestionError(
                "PDF_OCR_OUTPUT_INVALID",
                f"OCR returned an out-of-page bounding box on page {page}.",
                path=path,
            )
        return (
            x0 / image_width * page_width,
            top / image_height * page_height,
            x1 / image_width * page_width,
            bottom / image_height * page_height,
        )

    @staticmethod
    def _page_list(pages: Sequence[int]) -> str:
        return ", ".join(str(page) for page in pages)

    @staticmethod
    def _normalized_ocr_confidence(
        value: float,
        *,
        path: Path,
        page: int,
        display_region: str,
    ) -> float:
        confidence = float(value)
        if not math.isfinite(confidence):
            raise IngestionError(
                "PDF_OCR_OUTPUT_INVALID",
                (
                    f"OCR returned a non-finite confidence on page {page}, "
                    f"region {display_region}."
                ),
                path=path,
            )
        return min(max(confidence, 0.0), 1.0)

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
            metadata: dict[str, Any] = {}
            parent = f"page_{page_number:04d}_table_{table_index:03d}"
            row_groups = [(1, len(data_rows), data_rows)]
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

        parent = f"page_{page_number:04d}_text_body"
        pending: list[_PendingBlock] = []
        for chunk_index, region in enumerate(regions, start=1):
            bbox = region.bbox
            pending.append(
                _PendingBlock(
                    block_type="text",
                    content=region.text,
                    page=page_number,
                    bbox=bbox,
                    region_id=f"{parent}_line_{chunk_index:03d}",
                    parent_region_id=parent,
                    extraction_method="pdf_native_text:line",
                    source_span=None,
                    row_span=None,
                    sort_key=(bbox[1], bbox[0], 0, chunk_index),
                )
            )
        return pending, True

    def _text_tokens(self, text: str, context: str = "") -> int:
        return self._token_counter((context + "\n" if context else "") + text)

    def _split_text(self, text: str, context: str = "") -> list[_ChunkRange]:
        if self._text_tokens(text, context) <= self.max_chunk_tokens:
            return [_ChunkRange(0, len(text), "page")]
        return self._split_range(text, 0, len(text), level=0, context=context)

    def _split_range(
        self,
        text: str,
        start: int,
        end: int,
        *,
        level: int,
        context: str = "",
    ) -> list[_ChunkRange]:
        if self._text_tokens(text[start:end], context) <= self.max_chunk_tokens:
            method = ("paragraph", "sentence")[min(level, 1)]
            return [_ChunkRange(start, end, method)]

        boundaries = (_PARAGRAPH_BOUNDARY, _SENTENCE_BOUNDARY)
        methods = ("paragraph", "sentence")
        if level >= len(boundaries):
            return self._hard_token_split(text, start, end, context=context)

        split_points = [
            match.end()
            for match in boundaries[level].finditer(text, start, end)
            if start < match.end() < end
        ]
        if not split_points:
            return self._split_range(text, start, end, level=level + 1, context=context)

        units = list(zip([start, *split_points], [*split_points, end], strict=True))
        result: list[_ChunkRange] = []
        group_start: int | None = None
        group_end: int | None = None
        for unit_start, unit_end in units:
            if self._text_tokens(text[unit_start:unit_end], context) > self.max_chunk_tokens:
                if group_start is not None and group_end is not None:
                    result.append(_ChunkRange(group_start, group_end, methods[level]))
                result.extend(
                    self._split_range(text, unit_start, unit_end, level=level + 1, context=context)
                )
                group_start = None
                group_end = None
                continue
            if group_start is None:
                group_start, group_end = unit_start, unit_end
                continue
            assert group_end is not None
            if self._text_tokens(text[group_start:unit_end], context) <= self.max_chunk_tokens:
                group_end = unit_end
            else:
                result.append(_ChunkRange(group_start, group_end, methods[level]))
                group_start, group_end = unit_start, unit_end
        if group_start is not None and group_end is not None:
            result.append(_ChunkRange(group_start, group_end, methods[level]))
        return result

    def _hard_token_split(self, text: str, start: int, end: int, *, context: str = "") -> list[_ChunkRange]:
        chunks: list[_ChunkRange] = []
        cursor = start
        while cursor < end:
            low, high = cursor + 1, end
            best = cursor
            while low <= high:
                middle = (low + high) // 2
                if self._text_tokens(text[cursor:middle], context) <= self.max_chunk_tokens:
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            if best == cursor:
                raise IngestionError("PDF_CHUNK_BUDGET_TOO_SMALL", "Section context and one source character exceed the chunk budget.")
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
                    section_title=item.section_title,
                    source_location=location,
                    extraction_evidence=item.extraction_evidence,
                    source_region=SourceRegion(
                        region_id=item.region_id,
                        parent_region_id=item.parent_region_id,
                        page=item.page,
                        bbox=bbox,
                        parts=list(item.parts),
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
