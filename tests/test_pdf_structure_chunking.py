from pathlib import Path

from reportlab.pdfgen import canvas

from reservoir_data_translator.ingestion import OcrPageResult, OcrRegion, OcrTable, PaddleOcrBackend, PdfParser


def _text(identifier, text, top, *, label="text", confidence=0.98, flags=(), left=72, right=300):
    return OcrRegion(identifier, "text", label, (left, top, right, top + 12), top,
                     text=text, confidence=confidence, quality_flags=flags)


def _blocks(parser, regions):
    result = OcrPageResult(1, 612, 792, tuple(regions), "fake")
    return parser._materialize_blocks(parser._ocr_blocks(
        result, page_width=612, page_height=792, path=Path("scan.pdf")))


def test_native_and_ocr_share_heading_body_chunking(tmp_path):
    path = tmp_path / "native.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(612, 792))
    lines = ["Production:", "Liquid rate is 500 m3/day.", "Duration is 5 years."]
    for index, text in enumerate(lines):
        pdf.drawString(72, 740 - index * 24, text)
    pdf.save()
    parser = PdfParser()
    native = parser.parse(path).blocks
    # Deliberately shuffled single-column regions must recover geometric order.
    ocr = _blocks(parser, [_text("duration", lines[2], 90),
                           _text("title", lines[0], 42, label="paragraph_title"),
                           _text("rate", lines[1], 66)])
    assert len(native) == len(ocr) == 1
    assert native[0].content == ocr[0].content == "\n\n".join(lines)
    assert native[0].section_title == ocr[0].section_title == "Production:"
    assert [part.region_id for part in ocr[0].source_region.parts] == ["title", "rate", "duration"]


def test_ocr_table_owns_caption_and_context_not_standalone_blocks():
    table = OcrRegion("table", "table", "table", (72, 100, 350, 170), 100,
                      table=OcrTable(["Pressure", "Rs"], [["100", "20"], ["150", "28"]]), confidence=0.96)
    blocks = _blocks(PdfParser(), [_text("core", "Core X-12, waterflood", 60),
                                  _text("caption", "Table 3-2 Relative permeability", 80, label="table_title"),
                                  table, _text("after", "Unrelated paragraph.", 190)])
    assert [block.block_type for block in blocks] == ["table", "text"]
    assert blocks[0].content["caption"] == "Table 3-2 Relative permeability"
    assert blocks[0].content["context"] == ["Core X-12, waterflood"]
    assert blocks[0].content["rows"] == [["100", "20"], ["150", "28"]]
    assert [part.role for part in blocks[0].source_region.parts] == ["evidence", "context", "context"]
    assert blocks[1].content == "Unrelated paragraph."


def test_chunk_budget_repeats_title_preserves_exact_primary_spans_and_quality():
    parser = PdfParser(max_chunk_tokens=6, token_counter=lambda text: len(text.split()),
                       reject_low_confidence_ocr=False)
    body = " ".join(f"value{i}" for i in range(16))
    blocks = _blocks(parser, [_text("title", "Production:", 40, label="paragraph_title"),
                              _text("body", body, 70, confidence=0.61, flags=("LOW_TEXT_CONFIDENCE",))])
    assert len(blocks) > 1
    assert "".join(block.content for block in blocks) == "Production:\n\n" + body
    previous_end = 0
    for block in blocks:
        assert len(block.searchable_text().split()) <= 6
        assert block.section_title == "Production:"
        span = block.source_region.source_span
        assert span.start == previous_end
        previous_end = span.end
        for part in block.source_region.parts:
            if part.region_id == "body":
                assert part.text == body[part.source_span.start:part.source_span.end]
                assert block.extraction_evidence.confidence == 0.61
                assert "LOW_TEXT_CONFIDENCE" in block.extraction_evidence.quality_flags
    assert blocks[-1].source_region.bbox.top == 70
    assert blocks[-1].source_region.parts[0].role == "context"


def test_explicit_columns_are_not_merged_into_one_body():
    blocks = _blocks(PdfParser(), [_text("left", "Left column.", 40, left=20, right=200),
                                  _text("right", "Right column.", 40, left=350, right=580)])
    assert [block.content for block in blocks] == ["Left column.", "Right column."]


def test_ocr_table_row_groups_repeat_columns_and_owned_context():
    parser = PdfParser(max_chunk_tokens=1, token_counter=lambda text: text.count("100") + text.count("150"))
    table = OcrRegion("table", "table", "table", (72, 100, 350, 170), 100,
                      table=OcrTable(["Pressure", "Rs"], [["100", "20"], ["150", "28"]]), confidence=0.96)
    blocks = _blocks(parser, [_text("caption", "Table 1 PVT", 80, label="table_title"), table])
    assert len(blocks) == 2
    assert [block.source_region.row_start for block in blocks] == [1, 2]
    assert [block.source_region.row_end for block in blocks] == [1, 2]
    for block in blocks:
        assert block.content["columns"] == ["Pressure", "Rs"]
        assert block.content["caption"] == "Table 1 PVT"
        assert len(block.content["rows"]) == 1
        assert block.source_region.parts[0].region_id == "table"


def test_paddle_adapter_preserves_paragraph_boundaries():
    regions = PaddleOcrBackend()._regions({"parsing_res_list": [{
        "block_id": "body", "block_label": "text", "block_bbox": [0, 0, 100, 100],
        "block_content": "  Paragraph one.\n\nParagraph two.  ",
    }]})
    assert regions[0].text == "Paragraph one.\n\nParagraph two."
