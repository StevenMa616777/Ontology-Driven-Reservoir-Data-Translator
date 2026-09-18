import base64
from io import BytesIO
import json

import pytest
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from reservoir_data_translator.ingestion import (
    IngestionError,
    OcrBackendError,
    OcrPageResult,
    OcrRegion,
    OcrTable,
    PaddleOcrBackend,
    PdfParser,
    parse_document,
)


_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _native_pdf(path, *, long_text: str | None = None) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.drawString(72, 740, "Reservoir input summary")
    pdf.drawString(72, 715, long_text or "Minimum bottom-hole pressure is 80 bar.")
    pdf.drawString(72, 690, "Production duration is 5 years.")

    table = Table(
        [["Pressure", "Rs", "Bo"], [100, 20, 1.02], [150, 28, 1.05]],
        colWidths=[80, 60, 60],
        rowHeights=20,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 1, "black"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    table.wrapOn(pdf, 200, 100)
    table.drawOn(pdf, 72, 570)

    pdf.drawImage(ImageReader(BytesIO(_PIXEL_PNG)), 72, 430, width=120, height=80)
    pdf.drawString(72, 410, "Figure 1. Grid overview.")
    pdf.save()


def _captioned_table_pdf(path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.drawString(72, 740, "Core X-12, waterflood")
    pdf.drawString(72, 720, "Table 3-2 Relative permeability")
    table = Table(
        [["Sw", "Krw", "Kro"], [0.2, 0.0, 0.8]],
        colWidths=[80, 80, 80],
        rowHeights=20,
    )
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, "black")]))
    table.wrapOn(pdf, 240, 40)
    table.drawOn(pdf, 72, 660)
    pdf.drawString(72, 620, "Unrelated paragraph remains text.")
    pdf.save()


def _image_only_pdf(path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.drawImage(
        ImageReader(BytesIO(_PIXEL_PNG)),
        0,
        0,
        width=letter[0],
        height=letter[1],
    )
    pdf.save()


def _restricted_image_pdf(path, *, full_page: bool = False) -> None:
    encryption = StandardEncryption(
        "",
        ownerPassword="fixture-owner",
        canPrint=1,
        canModify=0,
        canCopy=0,
        canAnnotate=0,
    )
    pdf = canvas.Canvas(str(path), pagesize=letter, encrypt=encryption)
    width, height = letter if full_page else (240, 320)
    pdf.drawImage(
        ImageReader(BytesIO(_PIXEL_PNG)),
        72,
        72,
        width=width,
        height=height,
    )
    pdf.save()


def _mixed_pdf(path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.drawString(72, 720, "Native reservoir text")
    pdf.showPage()
    pdf.drawImage(
        ImageReader(BytesIO(_PIXEL_PNG)),
        0,
        0,
        width=letter[0],
        height=letter[1],
    )
    pdf.save()


class _FakeOcrBackend:
    def __init__(self, *, low_confidence: bool = False) -> None:
        self.pages: list[int] = []
        self.low_confidence = low_confidence

    def analyze_page(self, image, *, page_number: int, languages: tuple[str, ...]):
        self.pages.append(page_number)
        width, height = image.size
        confidence = 0.4 if self.low_confidence else 0.96
        flags = ("LOW_TEXT_CONFIDENCE",) if self.low_confidence else ()
        return OcrPageResult(
            page_number=page_number,
            image_width=width,
            image_height=height,
            engine="fake-layout-ocr",
            engine_version="1.0",
            model_version="fixture-v1",
            preprocessing=("orientation",),
            regions=(
                OcrRegion(
                    region_id="title",
                    region_type="text",
                    layout_label="text",
                    bbox_pixels=(10, 10, width - 10, 80),
                    reading_order=1,
                    text="Minimum bottom-hole pressure is 80 bar.",
                    confidence=confidence,
                    quality_flags=flags,
                ),
                OcrRegion(
                    region_id="pvt-table",
                    region_type="table",
                    layout_label="table",
                    bbox_pixels=(10, 100, width - 10, 250),
                    reading_order=2,
                    table=OcrTable(
                        columns=["Pressure", "Rs", "Bo"],
                        rows=[["100", "20", "1.02"]],
                        metadata={"header_detected": True},
                    ),
                    confidence=0.94,
                ),
                OcrRegion(
                    region_id="grid-figure",
                    region_type="figure",
                    layout_label="image",
                    bbox_pixels=(10, 270, width - 10, height - 10),
                    reading_order=3,
                    confidence=0.91,
                ),
            ),
        )


class _FailingOcrBackend:
    def analyze_page(self, image, *, page_number: int, languages: tuple[str, ...]):
        raise OcrBackendError("PDF_OCR_TIMEOUT", "fixture timeout")


def test_pdf_parser_extracts_non_overlapping_text_table_and_figure_blocks(tmp_path) -> None:
    path = tmp_path / "native.pdf"
    _native_pdf(path)

    document = parse_document(path, source_id="native-pdf")

    assert document.source_type == "pdf"
    assert document.source_id == "native-pdf"
    assert {block.block_type for block in document.blocks} == {"text", "table", "figure"}

    text = "".join(
        str(block.content) for block in document.blocks if block.block_type == "text"
    )
    assert "Reservoir input summary" in text
    assert "Minimum bottom-hole pressure is 80 bar." in text
    assert "Pressure" not in text

    table = next(block for block in document.blocks if block.block_type == "table")
    assert table.content == {
        "columns": ["Pressure", "Rs", "Bo"],
        "rows": [["100", "20", "1.02"], ["150", "28", "1.05"]],
    }
    assert table.source_region is not None
    assert (table.source_region.row_start, table.source_region.row_end) == (1, 2)
    figure = next(block for block in document.blocks if block.block_type == "figure")
    assert figure.content["figure_index"] == 1

    for block in document.blocks:
        assert block.source_region is not None
        assert block.source_region.page == 1
        assert block.source_region.bbox.coordinate_system == "pdf_top_left_points"
        assert block.source_location.startswith("page 1, bbox")


def test_pdf_table_owns_explicit_caption_and_nearby_context(tmp_path) -> None:
    path = tmp_path / "captioned.pdf"
    _captioned_table_pdf(path)

    document = PdfParser().parse(path)

    table = next(block for block in document.blocks if block.block_type == "table")
    assert table.content["caption"] == "Table 3-2 Relative permeability"
    assert table.content["context"] == ["Core X-12, waterflood"]
    text = "\n".join(
        str(block.content) for block in document.blocks if block.block_type == "text"
    )
    assert "Table 3-2" not in text
    assert "Core X-12" not in text
    assert "Unrelated paragraph remains text." in text


def test_pdf_text_recursively_splits_without_overlapping_primary_content(tmp_path) -> None:
    path = tmp_path / "long.pdf"
    long_sentence = " ".join(f"value{index}" for index in range(30)) + "."
    _native_pdf(path, long_text=long_sentence)
    parser = PdfParser(max_chunk_tokens=5, token_counter=lambda text: len(text.split()))

    document = parser.parse(path)
    text_blocks = [block for block in document.blocks if block.block_type == "text"]

    assert len(text_blocks) > 1
    grouped: dict[str, list] = {}
    for block in text_blocks:
        assert block.source_region is not None
        grouped.setdefault(block.source_region.parent_region_id, []).append(block)
        assert len(str(block.content).split()) <= 5
    # Tables and figures are structural boundaries, not gaps to concatenate over.
    assert len(grouped) == 2

    ordered = sorted(
        grouped["page_0001_text_body"],
        key=lambda block: block.source_region.source_span.start,  # type: ignore[union-attr]
    )
    previous_end = 0
    for block in ordered:
        span = block.source_region.source_span  # type: ignore[union-attr]
        assert span is not None
        assert span.start == previous_end
        assert len(block.content) == span.end - span.start
        previous_end = span.end
    assert "".join(block.content for block in ordered).count(long_sentence) == 1
    for blocks in grouped.values():
        spans = sorted((block.source_region.source_span for block in blocks), key=lambda span: span.start)
        assert spans[0].start == 0
        assert all(left.end == right.start for left, right in zip(spans, spans[1:]))
    assert any(
        block.source_region.extraction_method.endswith(":token")  # type: ignore[union-attr]
        for block in text_blocks
    )


@pytest.mark.parametrize(
    ("text", "expected_method"),
    [
        ("one two.\n\nthree four.\n\nfive six.", "paragraph"),
        ("one two. three four. five six.", "sentence"),
        ("one two three four five six", "token"),
    ],
)
def test_pdf_text_splitter_falls_back_paragraph_sentence_then_token(
    text: str,
    expected_method: str,
) -> None:
    parser = PdfParser(max_chunk_tokens=4, token_counter=lambda value: len(value.split()))

    chunks = parser._split_text(text)

    assert "".join(text[chunk.start : chunk.end] for chunk in chunks) == text
    assert all(len(text[chunk.start : chunk.end].split()) <= 4 for chunk in chunks)
    assert {chunk.split_method for chunk in chunks} == {expected_method}


def test_pdf_table_splitter_keeps_header_context_and_non_overlapping_row_ranges(
    tmp_path,
) -> None:
    path = tmp_path / "table.pdf"

    def row_budget(value: str) -> int:
        return 1 + 2 * len(json.loads(value)["rows"])

    parser = PdfParser(max_chunk_tokens=3, token_counter=row_budget)
    groups = parser._split_table_rows(
        ["Pressure", "Rs", "Bo"],
        [["100", "20", "1.02"], ["150", "28", "1.05"]],
        path=path,
    )

    assert groups == [
        (1, 1, [["100", "20", "1.02"]]),
        (2, 2, [["150", "28", "1.05"]]),
    ]


def test_pdf_parser_rejects_image_dominated_page_as_ocr_required(tmp_path) -> None:
    path = tmp_path / "scan.pdf"
    _image_only_pdf(path)

    with pytest.raises(IngestionError) as error:
        PdfParser().parse(path)

    assert error.value.code == "PDF_OCR_REQUIRED"


def test_pdf_parser_uses_image_presence_not_eighty_percent_coverage(tmp_path) -> None:
    path = tmp_path / "small-scan.pdf"
    _restricted_image_pdf(path)

    with pytest.raises(IngestionError) as error:
        PdfParser().parse(path)

    assert error.value.code == "PDF_OCR_REQUIRED"
    assert error.value.stage == "ocr_configuration"
    assert error.value.details["image_coverage_by_page"]["1"] < 0.8


def test_pdf_parser_routes_restricted_pdf_to_ocr_configuration(tmp_path) -> None:
    path = tmp_path / "restricted.pdf"
    _restricted_image_pdf(path)

    with pytest.raises(IngestionError) as error:
        PdfParser().parse(path)

    assert error.value.code == "PDF_OCR_REQUIRED"
    assert error.value.stage == "ocr_configuration"
    assert error.value.stage_label == "OCR 配置"
    assert error.value.details["restriction"] == "copy_and_text_extraction"
    assert error.value.details["image_coverage_by_page"]["1"] < 0.8
    assert "RESERVOIR_OCR_BACKEND" in (error.value.resolution or "")


def test_pdf_parser_automatically_ocrs_restricted_pdf(
    tmp_path,
) -> None:
    path = tmp_path / "restricted.pdf"
    _restricted_image_pdf(path)
    backend = _FakeOcrBackend()

    document = PdfParser(
        ocr_backend=backend,
        ocr_render_dpi=72,
    ).parse(path)

    assert backend.pages == [1]
    assert document.blocks[0].content == "Minimum bottom-hole pressure is 80 bar."
    assert all(
        block.extraction_evidence is not None
        and "SOURCE_TEXT_EXTRACTION_RESTRICTED"
        in block.extraction_evidence.quality_flags
        for block in document.blocks
    )


def test_pdf_parser_converts_whole_scanned_document_to_existing_block_contract(
    tmp_path,
) -> None:
    path = tmp_path / "scan.pdf"
    _image_only_pdf(path)
    backend = _FakeOcrBackend()

    document = PdfParser(ocr_backend=backend, ocr_render_dpi=72).parse(path)

    assert backend.pages == [1]
    assert [block.block_type for block in document.blocks] == [
        "text",
        "table",
        "figure",
    ]
    text, table, figure = document.blocks
    assert text.content == "Minimum bottom-hole pressure is 80 bar."
    assert table.content == {
        "columns": ["Pressure", "Rs", "Bo"],
        "rows": [["100", "20", "1.02"]],
        "header_detected": True,
        "ocr_region_id": "pvt-table",
    }
    assert figure.content["source"] == "scanned_page_region"
    assert text.extraction_evidence is not None
    assert text.extraction_evidence.engine == "fake-layout-ocr"
    assert text.extraction_evidence.render_dpi == 72
    assert text.source_region is not None
    assert text.source_region.extraction_method == "pdf_ocr_text:page"
    assert 0 <= text.source_region.bbox.x0 < text.source_region.bbox.x1 <= letter[0]


def test_pdf_parser_rejects_native_and_scanned_page_mixture(tmp_path) -> None:
    path = tmp_path / "hybrid.pdf"
    _mixed_pdf(path)
    backend = _FakeOcrBackend()

    with pytest.raises(IngestionError) as error:
        PdfParser(ocr_backend=backend, ocr_render_dpi=72).parse(path)

    assert error.value.code == "PDF_HYBRID_UNSUPPORTED"
    assert backend.pages == []


def test_pdf_parser_can_reject_low_confidence_ocr(tmp_path) -> None:
    path = tmp_path / "scan.pdf"
    _image_only_pdf(path)

    with pytest.raises(IngestionError) as error:
        PdfParser(
            ocr_backend=_FakeOcrBackend(low_confidence=True),
            ocr_render_dpi=72,
            reject_low_confidence_ocr=True,
        ).parse(path)

    assert error.value.code == "PDF_OCR_LOW_CONFIDENCE"


def test_pdf_parser_translates_backend_failure_to_ingestion_error(tmp_path) -> None:
    path = tmp_path / "scan.pdf"
    _image_only_pdf(path)

    with pytest.raises(IngestionError) as error:
        PdfParser(ocr_backend=_FailingOcrBackend(), ocr_render_dpi=72).parse(path)

    assert error.value.code == "PDF_OCR_TIMEOUT"


def test_paddle_backend_normalizes_layout_text_table_and_figure() -> None:
    class Result:
        json = {
            "width": 400,
            "height": 300,
            "model_settings": {"pipeline_name": "PP-StructureV3"},
            "parsing_res_list": [
                {
                    "block_id": 1,
                    "block_label": "text",
                    "block_bbox": [10, 10, 390, 50],
                    "block_order": 1,
                    "block_content": "Reservoir pressure 100 bar",
                },
                {
                    "block_id": 2,
                    "block_label": "table",
                    "block_bbox": [10, 70, 390, 180],
                    "block_order": 2,
                    "block_content": "",
                },
                {
                    "block_id": 3,
                    "block_label": "image",
                    "block_bbox": [10, 200, 390, 290],
                    "block_order": 3,
                    "block_content": "",
                },
            ],
            "overall_ocr_res": {
                "rec_boxes": [[10, 10, 200, 40]],
                "rec_scores": [0.98],
            },
            "table_res_list": [
                {
                    "pred_html": (
                        "<table><tr><th>Pressure</th><th>Bo</th></tr>"
                        "<tr><td>100</td><td>1.02</td></tr></table>"
                    ),
                    "table_ocr_pred": {"rec_scores": [0.96, 0.95]},
                }
            ],
        }

    class Pipeline:
        def predict(self, image, **kwargs):
            assert image.shape == (300, 400, 3)
            return [Result()]

    def factory(**kwargs):
        assert kwargs["use_table_recognition"] is True
        assert kwargs["use_formula_recognition"] is False
        assert kwargs["use_doc_orientation_classify"] is False
        assert kwargs["enable_mkldnn"] is False
        assert kwargs["device"] == "gpu:0"
        return Pipeline()

    backend = PaddleOcrBackend(pipeline_factory=factory)

    result = backend.analyze_page(
        Image.new("RGB", (400, 300), "white"),
        page_number=1,
        languages=("ch", "en"),
    )

    assert [region.region_type for region in result.regions] == [
        "text",
        "table",
        "figure",
    ]
    assert result.regions[0].confidence == pytest.approx(0.98)
    assert result.regions[1].table == OcrTable(
        columns=["Pressure", "Bo"],
        rows=[["100", "1.02"]],
        metadata={"header_detected": True},
    )


def test_pdf_parser_rejects_malformed_pdf_with_stable_error(tmp_path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a PDF")

    with pytest.raises(IngestionError) as error:
        PdfParser().parse(path)

    assert error.value.code == "PDF_INVALID"


def test_pdf_source_region_contract_rejects_invalid_bbox() -> None:
    from pydantic import ValidationError

    from reservoir_data_translator.ingestion import BoundingBox

    with pytest.raises(ValidationError, match="x1"):
        BoundingBox(x0=10, top=5, x1=2, bottom=20)
