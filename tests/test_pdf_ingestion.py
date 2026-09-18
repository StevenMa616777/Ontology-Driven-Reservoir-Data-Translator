import base64
from io import BytesIO
import json

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from reservoir_data_translator.ingestion import IngestionError, PdfParser, parse_document


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
    assert list(grouped) == ["page_0001_text_body"]

    ordered = sorted(
        text_blocks,
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
