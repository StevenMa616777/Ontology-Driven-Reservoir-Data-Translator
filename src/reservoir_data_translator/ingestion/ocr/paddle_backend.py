"""PaddleOCR PP-StructureV3 adapter with lazy optional imports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from html.parser import HTMLParser
from importlib import metadata
import logging
from statistics import fmean
from typing import Any

from .base import OcrBackendError
from .models import OcrPageResult, OcrRegion, OcrTable, PixelBoundingBox


_TEXT_LABELS = {
    "abstract",
    "caption",
    "content",
    "doc_title",
    "figure_title",
    "figure_table_title",
    "footnote",
    "list",
    "paragraph_title",
    "reference",
    "references",
    "table_title",
    "text",
    "title",
}
_TABLE_LABELS = {"table"}
_FIGURE_LABELS = {"chart", "diagram", "figure", "image", "picture"}
_IGNORED_LABELS = {"footer", "header", "page_footer", "page_header", "page_number"}


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int, bool]]] = []
        self._row: list[tuple[str, int, int, bool]] | None = None
        self._cell_parts: list[str] | None = None
        self._rowspan = 1
        self._colspan = 1
        self._header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered == "tr":
            self._row = []
        elif lowered in {"td", "th"} and self._row is not None:
            values = {key.casefold(): value for key, value in attrs}
            self._rowspan = self._positive_int(values.get("rowspan"))
            self._colspan = self._positive_int(values.get("colspan"))
            self._header = lowered == "th"
            self._cell_parts = []
        elif lowered == "br" and self._cell_parts is not None:
            self._cell_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in {"td", "th"} and self._row is not None and self._cell_parts is not None:
            value = " ".join("".join(self._cell_parts).split())
            self._row.append((value, self._rowspan, self._colspan, self._header))
            self._cell_parts = None
        elif lowered == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    @staticmethod
    def _positive_int(value: str | None) -> int:
        try:
            parsed = int(value or "1")
        except ValueError:
            return 1
        return max(parsed, 1)


class PaddleOcrBackend:
    """Run PP-StructureV3 and normalize its structured JSON result."""

    def __init__(
        self,
        *,
        lang: str | None = None,
        device: str | None = "gpu:0",
        paddlex_config: str | None = None,
        minimum_confidence: float = 0.70,
        enable_mkldnn: bool = False,
        cpu_threads: int | None = None,
        pipeline_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if cpu_threads is not None and cpu_threads < 1:
            raise ValueError("cpu_threads must be at least 1")
        self.lang = lang
        self.device = device.strip() if device and device.strip() else "gpu:0"
        self.paddlex_config = paddlex_config
        self.minimum_confidence = minimum_confidence
        self.enable_mkldnn = enable_mkldnn
        self.cpu_threads = cpu_threads
        self._pipeline_factory = pipeline_factory
        self._pipeline: Any | None = None

    def analyze_page(
        self,
        image: Any,
        *,
        page_number: int,
        languages: tuple[str, ...],
    ) -> OcrPageResult:
        pipeline = self._get_pipeline()
        try:
            import numpy as np

            image_array = np.asarray(image.convert("RGB"))
            results = list(
                pipeline.predict(
                    image_array,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=True,
                    use_formula_recognition=False,
                    use_chart_recognition=False,
                    use_seal_recognition=False,
                )
            )
        except OcrBackendError:
            raise
        except Exception as exc:
            raise OcrBackendError(
                "PDF_OCR_FAILED",
                f"PaddleOCR page inference failed: {exc}",
            ) from exc

        if len(results) != 1:
            raise OcrBackendError(
                "PDF_OCR_OUTPUT_INVALID",
                f"PaddleOCR returned {len(results)} page results for one page image.",
            )
        raw_payload = self._result_payload(results[0], unwrap=False)
        # Capture before normalization, table association, or confidence rejection.
        from .artifacts import active_artifact
        writer = active_artifact.get()
        if writer is not None:
            writer.record(page_number, raw_payload, image)
        nested = raw_payload.get("res")
        payload = nested if "parsing_res_list" not in raw_payload and isinstance(nested, Mapping) else raw_payload
        width = self._positive_dimension(payload.get("width"), image.width)
        height = self._positive_dimension(payload.get("height"), image.height)
        regions = self._regions(payload)
        return OcrPageResult(
            page_number=page_number,
            image_width=width,
            image_height=height,
            regions=tuple(regions),
            engine="paddleocr-ppstructurev3",
            engine_version=self._distribution_version("paddleocr"),
            model_version=self._model_version(payload),
            preprocessing=(
                "textline_orientation_classification",
            ),
        )

    def _get_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        factory = self._pipeline_factory
        if factory is None:
            if self.device.startswith("gpu"):
                self._validate_gpu_runtime()
            try:
                from paddleocr import PPStructureV3
            except ImportError as exc:
                raise OcrBackendError(
                    "PDF_OCR_BACKEND_UNAVAILABLE",
                    "Install the 'ocr' optional dependencies to enable PaddleOCR.",
                ) from exc
            factory = PPStructureV3
        options: dict[str, Any] = {
            # Geometry-changing page preprocessing stays disabled until its
            # inverse transform can be preserved in SourceRegion coordinates.
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
            "use_formula_recognition": False,
            "use_chart_recognition": False,
            "use_seal_recognition": False,
            "use_table_recognition": True,
            "enable_mkldnn": self.enable_mkldnn,
        }
        if self.lang:
            options["lang"] = self.lang
        if self.device:
            options["device"] = self.device
        if self.paddlex_config:
            options["paddlex_config"] = self.paddlex_config
        if self.cpu_threads is not None:
            options["cpu_threads"] = self.cpu_threads
        try:
            self._pipeline = factory(**options)
        except Exception as exc:
            raise OcrBackendError(
                "PDF_OCR_MODEL_UNAVAILABLE",
                f"Could not initialize PaddleOCR PP-StructureV3: {exc}",
            ) from exc
        return self._pipeline

    def _validate_gpu_runtime(self) -> None:
        """Fail before loading models when the requested CUDA device is unusable."""
        try:
            import paddle
        except Exception as exc:
            raise OcrBackendError(
                "PDF_OCR_GPU_RUNTIME_UNAVAILABLE",
                f"OCR 无法加载 GPU 运行时：{exc}。"
                "请在运行后端的 Python 环境安装 paddlepaddle-gpu 并检查 CUDA 依赖。",
            ) from exc
        if not paddle.is_compiled_with_cuda():
            raise OcrBackendError(
                "PDF_OCR_GPU_RUNTIME_UNAVAILABLE",
                "OCR 请求使用 GPU，但当前 PaddlePaddle 是 CPU 版本。"
                "请卸载 paddlepaddle 并安装 paddlepaddle-gpu，然后重启后端。",
            )
        try:
            suffix = self.device.partition(":")[2]
            indices = [int(value) for value in suffix.split(",")] if suffix else [0]
            if self.device != "gpu" and not self.device.startswith("gpu:"):
                raise ValueError("expected gpu or gpu:<index>")
            count = paddle.device.cuda.device_count()
            if any(index < 0 or index >= count for index in indices):
                raise RuntimeError(
                    f"设备 {self.device} 不可用，Paddle 检测到 {count} 个 CUDA GPU。"
                )
            paddle.set_device(f"gpu:{indices[0]}")
        except Exception as exc:
            raise OcrBackendError(
                "PDF_OCR_GPU_UNAVAILABLE",
                f"OCR 无法使用指定 GPU：{exc}。请检查 NVIDIA 驱动和设备编号。",
            ) from exc
        logging.getLogger(__name__).info("OCR CUDA device ready: %s", self.device)

    def _regions(self, payload: Mapping[str, Any]) -> list[OcrRegion]:
        parsing = payload.get("parsing_res_list")
        if not isinstance(parsing, Sequence) or isinstance(parsing, (str, bytes)):
            raise OcrBackendError(
                "PDF_OCR_OUTPUT_INVALID",
                "PaddleOCR output does not contain parsing_res_list.",
            )
        table_results = payload.get("table_res_list")
        tables = (
            list(table_results)
            if isinstance(table_results, Sequence) and not isinstance(table_results, (str, bytes))
            else []
        )
        regions: list[OcrRegion] = []
        overall_ocr = payload.get("overall_ocr_res")
        for fallback_order, raw_region in enumerate(parsing, start=1):
            if not isinstance(raw_region, Mapping):
                continue
            label = self._normalized_label(raw_region.get("block_label"))
            if label in _IGNORED_LABELS:
                continue
            bbox = self._bbox(raw_region.get("block_bbox"))
            if bbox is None:
                raise OcrBackendError(
                    "PDF_OCR_OUTPUT_INVALID",
                    f"PaddleOCR region R{fallback_order} has no valid bounding box.",
                )
            order = self._positive_dimension(raw_region.get("block_order"), fallback_order)
            raw_id = raw_region.get("block_id")
            region_id = str(raw_id) if raw_id is not None else str(fallback_order - 1)
            confidence = self._region_confidence(overall_ocr, bbox)
            quality_flags: list[str] = []

            if label in _TABLE_LABELS:
                from .artifacts import matching_table
                raw_table = matching_table(tables, raw_region)
                # Region-owned HTML stays tied to its own geometry.
                if "<tr" in str(raw_region.get("block_content") or "").lower():
                    raw_table = {**raw_table, "pred_html": raw_region["block_content"]}
                table, table_confidence = self._table(raw_table, raw_region)
                confidence = table_confidence if table_confidence is not None else confidence
                if confidence is not None and confidence < self.minimum_confidence:
                    quality_flags.append("TABLE_CELL_LOW_CONFIDENCE")
                regions.append(
                    OcrRegion(
                        region_id=region_id,
                        region_type="table",
                        layout_label=label,
                        bbox_pixels=bbox,
                        reading_order=order,
                        table=table,
                        confidence=confidence,
                        quality_flags=tuple(quality_flags),
                        source_region_index=fallback_order,
                        raw_content=str(raw_region.get("block_content") or ""),
                    )
                )
                continue

            content = str(raw_region.get("block_content") or "").strip()
            if label in _FIGURE_LABELS or (not content and label not in _TEXT_LABELS):
                regions.append(
                    OcrRegion(
                        region_id=region_id,
                        region_type="figure",
                        layout_label=label or "figure",
                        bbox_pixels=bbox,
                        reading_order=order,
                        confidence=confidence,
                        source_region_index=fallback_order,
                        raw_content=content,
                    )
                )
                continue
            if not content:
                continue
            if label != "formula" and confidence is not None and confidence < self.minimum_confidence:
                quality_flags.append("LOW_TEXT_CONFIDENCE")
            regions.append(
                OcrRegion(
                    region_id=region_id,
                    region_type="text",
                    layout_label=label or "text",
                    bbox_pixels=bbox,
                    reading_order=order,
                    text=content,
                    confidence=confidence,
                    quality_flags=tuple(quality_flags),
                    source_region_index=fallback_order,
                    raw_content=content,
                )
            )
        return regions

    def _table(
        self,
        raw_table: Any,
        raw_region: Mapping[str, Any],
    ) -> tuple[OcrTable, float | None]:
        table_mapping = raw_table if isinstance(raw_table, Mapping) else {}
        html = table_mapping.get("pred_html")
        if not isinstance(html, str) or "<tr" not in html.casefold():
            candidate = raw_region.get("block_content")
            html = candidate if isinstance(candidate, str) else ""
        if "<tr" not in html.casefold():
            raise OcrBackendError(
                "PDF_OCR_OUTPUT_INVALID",
                "PaddleOCR table output does not contain structured HTML rows.",
            )
        parser = _HtmlTableParser()
        parser.feed(html)
        grid, header_detected, merged_cells = self._expand_table(parser.rows)
        if not grid or not any(any(cell for cell in row) for row in grid):
            raise OcrBackendError(
                "PDF_OCR_OUTPUT_INVALID",
                "PaddleOCR table output contains no usable cells.",
            )
        width = max(len(row) for row in grid)
        normalized = [row + [""] * (width - len(row)) for row in grid]
        if header_detected or len(normalized) > 1:
            columns = normalized[0]
            rows = normalized[1:]
        else:
            columns = [f"column_{index}" for index in range(1, width + 1)]
            rows = normalized
        metadata: dict[str, Any] = {"header_detected": header_detected}
        if merged_cells:
            metadata["merged_cells"] = merged_cells
        confidence = self._mean_score(
            (table_mapping.get("table_ocr_pred") or {}).get("rec_scores")
            if isinstance(table_mapping.get("table_ocr_pred"), Mapping)
            else None
        )
        return OcrTable(columns=columns, rows=rows, metadata=metadata), confidence

    @staticmethod
    def _expand_table(
        source_rows: list[list[tuple[str, int, int, bool]]],
    ) -> tuple[list[list[str]], bool, list[dict[str, int]]]:
        grid: list[list[str | None]] = []
        merged: list[dict[str, int]] = []
        header_detected = bool(source_rows and any(cell[3] for cell in source_rows[0]))
        for row_index, source_row in enumerate(source_rows):
            while len(grid) <= row_index:
                grid.append([])
            column = 0
            for value, rowspan, colspan, _ in source_row:
                while column < len(grid[row_index]) and grid[row_index][column] is not None:
                    column += 1
                for target_row in range(row_index, row_index + rowspan):
                    while len(grid) <= target_row:
                        grid.append([])
                    while len(grid[target_row]) < column + colspan:
                        grid[target_row].append(None)
                    for target_column in range(column, column + colspan):
                        grid[target_row][target_column] = (
                            value
                            if target_row == row_index and target_column == column
                            else ""
                        )
                if rowspan > 1 or colspan > 1:
                    merged.append(
                        {
                            "row": row_index + 1,
                            "column": column + 1,
                            "rowspan": rowspan,
                            "colspan": colspan,
                        }
                    )
                column += colspan
        return [[cell or "" for cell in row] for row in grid], header_detected, merged

    @staticmethod
    def _result_payload(result: Any, *, unwrap: bool = True) -> Mapping[str, Any]:
        raw = getattr(result, "json", result)
        if callable(raw):
            raw = raw()
        if not isinstance(raw, Mapping):
            raise OcrBackendError(
                "PDF_OCR_OUTPUT_INVALID",
                "PaddleOCR result does not expose a JSON mapping.",
            )
        nested = raw.get("res")
        if unwrap and "parsing_res_list" not in raw and isinstance(nested, Mapping):
            raw = nested
        return raw

    @classmethod
    def _region_confidence(
        cls,
        overall_ocr: Any,
        bbox: PixelBoundingBox,
    ) -> float | None:
        if not isinstance(overall_ocr, Mapping):
            return None
        boxes = overall_ocr.get("rec_boxes")
        if boxes is None:
            boxes = overall_ocr.get("rec_polys")
        scores = overall_ocr.get("rec_scores")
        if hasattr(boxes, "tolist"):
            boxes = boxes.tolist()
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        if not isinstance(boxes, Sequence) or not isinstance(scores, Sequence):
            return None
        contained: list[float] = []
        for raw_box, raw_score in zip(boxes, scores, strict=False):
            word_bbox = cls._bbox(raw_box)
            if word_bbox is None:
                continue
            center_x = (word_bbox[0] + word_bbox[2]) / 2
            center_y = (word_bbox[1] + word_bbox[3]) / 2
            if bbox[0] <= center_x <= bbox[2] and bbox[1] <= center_y <= bbox[3]:
                try:
                    contained.append(float(raw_score))
                except (TypeError, ValueError):
                    continue
        return fmean(contained) if contained else None

    @staticmethod
    def _bbox(value: Any) -> PixelBoundingBox | None:
        if hasattr(value, "tolist"):
            value = value.tolist()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return None
        if len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
            x0, top, x1, bottom = (float(item) for item in value)
        else:
            points: list[Sequence[Any]] = []
            for item in value:
                if isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) >= 2:
                    points.append(item)
            if not points:
                return None
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            x0, top, x1, bottom = min(xs), min(ys), max(xs), max(ys)
        if x1 < x0 or bottom < top:
            return None
        return (x0, top, x1, bottom)

    @staticmethod
    def _mean_score(scores: Any) -> float | None:
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        if not isinstance(scores, Sequence) or isinstance(scores, (str, bytes)):
            return None
        numeric: list[float] = []
        for score in scores:
            try:
                numeric.append(float(score))
            except (TypeError, ValueError):
                continue
        return fmean(numeric) if numeric else None

    @staticmethod
    def _normalized_label(value: Any) -> str:
        return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")

    @staticmethod
    def _positive_dimension(value: Any, fallback: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return fallback
        return parsed if parsed > 0 else fallback

    @staticmethod
    def _distribution_version(name: str) -> str | None:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return None

    @staticmethod
    def _model_version(payload: Mapping[str, Any]) -> str | None:
        settings = payload.get("model_settings")
        if not isinstance(settings, Mapping):
            return None
        version = settings.get("pipeline_name") or settings.get("model_name")
        return str(version) if version else None
