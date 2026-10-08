"""Persist pre-normalization OCR evidence and a visible, uncorrected PDF view."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from html import escape
from importlib import metadata
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from threading import Lock
from uuid import uuid4

from ..base import IngestionError


progress_callback: ContextVar[Callable[[dict], None] | None] = ContextVar("ocr_progress", default=None)
active_artifact: ContextVar[OcrArtifactWriter | None] = ContextVar("ocr_artifact", default=None)
_font_lock = Lock()


def preview_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    with _font_lock:
        path = Path(os.getenv("RESERVOIR_OCR_PDF_FONT") or Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts" / "simsun.ttc")
        if path.is_file():
            if "OCRPreview" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("OCRPreview", str(path)))
            return "OCRPreview"
        if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"


def publish(**event: Any) -> None:
    callback = progress_callback.get()
    if callback is not None:
        callback(event)


def artifact_root() -> Path:
    return Path(os.getenv("RESERVOIR_OCR_ARTIFACT_DIR") or Path(__file__).resolve().parents[4] / "tmp" / "ocr_intermediates").resolve()


def json_default(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Unsupported OCR JSON value: {type(value).__name__}")


def matching_table(tables: list, region: Mapping) -> Mapping:
    """Never associate independent model lists by their incidental positions."""
    box = region.get("block_bbox")
    matches = []
    for table in tables:
        if not isinstance(table, Mapping):
            continue
        candidate_box = table.get("table_bbox", table.get("bbox"))
        if candidate_box is not None and box is not None:
            if len(candidate_box) == 4 and all(abs(float(a) - float(b)) < 2 for a, b in zip(candidate_box, box)):
                matches.append(table)
                continue
        if table.get("block_id") is not None and table.get("block_id") == region.get("block_id"):
            matches.append(table)
    if len(matches) == 1:
        return matches[0]
    # A single table has an unambiguous association even without geometry.
    if len(tables) == 1 and isinstance(tables[0], Mapping):
        return tables[0]
    return {}


class OcrArtifactWriter:
    def __init__(self, source_name: str, total_pages: int, dpi: int, root: Path | None = None) -> None:
        self.root = root or artifact_root()
        self.total_pages = total_pages
        self.dpi = dpi
        self.pages: list[int] = []
        self.finished = False
        self.canvas = None
        self.source_canvas = None
        self.clean_canvas = None
        self.clean_source_canvas = None
        self.comparison_pages: list[dict] = []
        self.raw = None
        self.artifact_id = str(uuid4())
        self.created_at = datetime.now().astimezone().isoformat()
        self.source_name = source_name
        self.render_error: str | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(source_name).stem).strip(" .")[:120] or "document"
            base = f"{stem}_{datetime.now().astimezone():%Y-%m-%d}"
            number = 1
            while True:
                self.name = base + (f"({number})" if number > 1 else "")
                self.manifest = self.root / f"{self.name}.manifest.json"
                if any((self.root / (self.name + suffix)).exists() for suffix in
                       (".pdf", ".raw.json", ".pdf.tmp", ".raw.json.tmp", ".clean.pdf", ".clean-source.pdf", ".comparison.json")):
                    number += 1
                    continue
                try:
                    with self.manifest.open("x", encoding="utf-8") as stream:
                        json.dump({"artifact_id": self.artifact_id, "status": "writing"}, stream)
                    break
                except FileExistsError:
                    number += 1
            self.pdf_path = self.root / f"{self.name}.pdf"
            self.source_pdf_path = self.root / f"{self.name}.source-regions.pdf"
            self.clean_pdf_path = self.root / f"{self.name}.clean.pdf"
            self.clean_source_path = self.root / f"{self.name}.clean-source.pdf"
            self.comparison_path = self.root / f"{self.name}.comparison.json"
            self.raw_path = self.root / f"{self.name}.raw.json"
            self.raw = self.raw_path.with_suffix(".json.tmp").open("w", encoding="utf-8")
            self.raw.write('{\n  "pages": [\n')
            self._write_manifest("writing")
        except Exception as exc:
            raise self.failure(exc) from exc

    @staticmethod
    def failure(exc: Exception) -> IngestionError:
        return IngestionError("PDF_OCR_ARTIFACT_SAVE_FAILED", f"Could not save OCR intermediate: {exc}", stage="ocr_artifact", stage_label="保存 OCR 中间结果", resolution="请检查保存目录的写入权限、磁盘空间和 PDF 生成依赖。")

    def record(self, page: int, payload: Mapping, image: Any, *, source_format: str = "paddle_raw") -> None:
        if page in self.pages:
            return
        try:
            serialized = json.dumps({"page": page, "source_format": source_format, "result": payload}, ensure_ascii=False, default=json_default, indent=2)
            if self.pages:
                self.raw.write(",\n")
            self.raw.write("\n".join("    " + line for line in serialized.splitlines()))
            self.raw.flush()
            self.pages.append(page)
            comparison_page = self._render(payload, image)
            self.comparison_pages.append({"source_page": page, **comparison_page})
            self._write_manifest("writing")
        except Exception as exc:
            self.render_error = str(exc)
            raise self.failure(exc) from exc

    def _render(self, payload: Mapping, image: Any) -> dict:
        if "parsing_res_list" not in payload and isinstance(payload.get("res"), Mapping):
            payload = payload["res"]
        from reportlab.pdfgen.canvas import Canvas
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph, Table, TableStyle
        from reportlab.lib.utils import ImageReader
        from .paddle_backend import _HtmlTableParser

        scale = 72 / self.dpi
        width, height = image.width * scale, image.height * scale
        if self.canvas is None:
            self.canvas = Canvas(str(self.pdf_path.with_suffix(".pdf.tmp")), pagesize=(width, height))
            self.canvas.setTitle(self.source_name)
            self.source_canvas = Canvas(str(self.source_pdf_path.with_suffix(".pdf.tmp")), pagesize=(width, height))
            self.source_canvas.setTitle(f"{self.source_name} - OCR source regions")
            self.clean_canvas = Canvas(str(self.clean_pdf_path.with_suffix(".pdf.tmp")), pagesize=(width, height))
            self.clean_source_canvas = Canvas(str(self.clean_source_path.with_suffix(".pdf.tmp")), pagesize=(width, height))
        pdf = self.canvas
        pdf.setPageSize((width, height))
        self.source_canvas.setPageSize((width, height))
        self.clean_canvas.setPageSize((width, height))
        self.clean_source_canvas.setPageSize((width, height))
        self.source_canvas.drawImage(ImageReader(image), 0, 0, width, height)
        self.clean_source_canvas.drawImage(ImageReader(image), 0, 0, width, height)
        style = ParagraphStyle("ocr", fontName=preview_font(), fontSize=10, leading=12, wordWrap="CJK", spaceAfter=0, spaceBefore=0)
        tables = payload.get("table_res_list") or []
        boxes = []
        regions = []
        for index, region in enumerate(payload.get("parsing_res_list", []), 1):
            box = region.get("block_bbox")
            if box is None or len(box) != 4:
                continue
            x0, top, x1, bottom = [float(v) for v in box]
            if not all(math.isfinite(v) for v in (x0, top, x1, bottom)):
                continue
            x0, top = max(0, x0), max(0, top)
            x1, bottom = min(image.width, x1), min(image.height, bottom)
            x, y, w, h = x0 * scale, height - bottom * scale, (x1-x0)*scale, (bottom-top)*scale
            if w <= 0 or h <= 0:
                continue
            boxes.append((index, str(region.get("block_label") or "unknown"), x, y, w, h))
            regions.append({"number": f"R{index}", "type": str(region.get("block_label") or "unknown"),
                            "bbox": [x, top * scale, x + w, bottom * scale]})
            label = str(region.get("block_label", "text")).lower()
            content = str(region.get("block_content") or "")
            if label in {"image", "figure", "chart", "diagram", "picture", "seal"}:
                crop = image.crop((max(0, x0), max(0, top), min(image.width, x1), min(image.height, bottom)))
                try:
                    for target in (pdf, self.clean_canvas):
                        target.drawImage(ImageReader(crop), x, y, w, h)
                finally:
                    crop.close()
                continue
            if label == "table":
                # Prefer HTML already attached to this exact region.
                if "<tr" not in content.lower():
                    content = str(matching_table(list(tables), region).get("pred_html") or "")
                parser = _HtmlTableParser()
                parser.feed(content)
                if not parser.rows:
                    raise ValueError("A table region has no unambiguously associated HTML")
                cells: dict[tuple[int, int], str] = {}
                occupied: set[tuple[int, int]] = set()
                spans = []
                for row_index, row in enumerate(parser.rows):
                    col = 0
                    for text, rowspan, colspan, _ in row:
                        while (row_index, col) in occupied:
                            col += 1
                        cells[row_index, col] = text
                        for rr in range(row_index, row_index + rowspan):
                            for cc in range(col, col + colspan):
                                occupied.add((rr, cc))
                        if rowspan > 1 or colspan > 1:
                            spans.append(("SPAN", (col, row_index), (col+colspan-1, row_index+rowspan-1)))
                        col += colspan
                nr = max(r for r, c in occupied) + 1
                nc = max(c for r, c in occupied) + 1
                data = [[Paragraph(escape(cells.get((r, c), "")).replace("\n", "<br/>"), style) for c in range(nc)] for r in range(nr)]
                flowable = Table(data, colWidths=[w/nc]*nc)
                flowable.setStyle(TableStyle([("GRID", (0,0), (-1,-1), 0.35, "#555555"), ("VALIGN", (0,0), (-1,-1), "TOP"), ("LEFTPADDING", (0,0), (-1,-1), 2), ("RIGHTPADDING", (0,0), (-1,-1), 2), *spans]))
            else:
                if not content:
                    continue
                flowable = Paragraph(escape(content).replace("\n", "<br/>"), style)
            fw, fh = flowable.wrap(w, 100000)
            factor = min(1, w/max(fw, 1), h/max(fh, 1))
            for target in (pdf, self.clean_canvas):
                target.saveState()
                target.translate(x, y+h-fh*factor)
                target.scale(factor, factor)
                flowable.drawOn(target, 0, 0)
                target.restoreState()
        # Overlay after all content so images and tables cannot hide the outlines.
        for target in (pdf, self.source_canvas):
            target.saveState()
            target.setStrokeColorRGB(1, 0, 0)
            target.setFillColorRGB(1, 0, 0)
            target.setLineWidth(0.8)
            target.setFont("Helvetica", 7)
            for index, label, x, y, w, h in boxes:
                target.rect(x, y, w, h, stroke=1, fill=0)
                tag = f"R{index} · {label}"
                target.setFont(preview_font(), 7)
                tag_width = target.stringWidth(tag, preview_font(), 7)
                tag_x = min(x + 2, max(0, width - tag_width - 4))
                tag_y = min(height - 8, y + h + 2)
                target.setFillColorRGB(1, 1, 1)
                target.rect(tag_x - 1, tag_y - 1, tag_width + 2, 9, stroke=0, fill=1)
                target.setFillColorRGB(1, 0, 0)
                target.drawString(tag_x, tag_y, tag)
            target.restoreState()
            target.showPage()
        self.clean_canvas.showPage()
        self.clean_source_canvas.showPage()
        return {"width": width, "height": height, "regions": regions}

    def _write_manifest(self, status: str, error: str | None = None) -> dict:
        reference = {"artifact_id": self.artifact_id, "file_name": self.pdf_path.name, "local_path": str(self.pdf_path), "status": status, "completed_pages": list(self.pages), "total_pages": self.total_pages, "created_at": self.created_at, "source_file_name": self.source_name, "preview_url": f"/ocr-intermediates/{self.artifact_id}/pdf", "download_url": f"/ocr-intermediates/{self.artifact_id}/pdf?download=true", "raw_url": f"/ocr-intermediates/{self.artifact_id}/raw", "error": error}
        reference["render_dpi"] = self.dpi
        reference["source_regions_preview_url"] = f"/ocr-intermediates/{self.artifact_id}/source-regions"
        reference["source_regions_download_url"] = reference["source_regions_preview_url"] + "?download=true"
        reference["source_regions_local_path"] = str(self.source_pdf_path)
        reference["comparison_url"] = f"/ocr-intermediates/{self.artifact_id}/comparison"
        for package in ("paddleocr", "paddlex"):
            try:
                reference[package + "_version"] = metadata.version(package)
            except metadata.PackageNotFoundError:
                reference[package + "_version"] = None
        temp = self.manifest.with_suffix(".json.tmp")
        temp.write_text(json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.manifest)
        return reference

    def finish(self, error: str | None = None) -> dict:
        if self.finished:
            return json.loads(self.manifest.read_text(encoding="utf-8"))
        self.finished = True
        try:
            if self.raw:
                self.raw.write("\n  ]\n}\n")
                self.raw.close()
                self.raw_path.with_suffix(".json.tmp").replace(self.raw_path)
            if self.canvas and not self.render_error:
                self.canvas.save()
                self.source_canvas.save()
                self.clean_canvas.save()
                self.clean_source_canvas.save()
                import pdfplumber
                for path in (self.pdf_path, self.source_pdf_path, self.clean_pdf_path, self.clean_source_path):
                    with pdfplumber.open(path.with_suffix(".pdf.tmp")) as check:
                        if len(check.pages) != len(self.pages):
                            raise ValueError("OCR preview page count mismatch")
                    path.with_suffix(".pdf.tmp").replace(path)
                temp = self.comparison_path.with_suffix(".json.tmp")
                temp.write_text(json.dumps({"pages": self.comparison_pages}, ensure_ascii=False), encoding="utf-8")
                temp.replace(self.comparison_path)
            status = "failed" if self.render_error or not self.pages else "partial" if error or len(self.pages) != self.total_pages else "complete"
            reference = self._write_manifest(status, self.render_error or error)
            publish(stage="ocr_artifact", ocr_intermediate=reference)
            return reference
        except Exception as exc:
            try:
                reference = self._write_manifest("failed", str(exc))
                publish(stage="ocr_artifact", ocr_intermediate=reference)
            except OSError:
                pass
            raise self.failure(exc) from exc
