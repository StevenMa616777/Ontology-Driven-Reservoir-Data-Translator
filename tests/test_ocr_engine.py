"""Page routing, logical table review, and provenance contract tests."""

from pathlib import Path

import pytest
from PIL import Image

from reservoir_data_translator.ingestion import PdfParser
from reservoir_data_translator.ingestion.ocr.components import (
    CropPipelineSelection, CropRunContext, EngineDescriptor, LayoutElement,
    LayoutResult, OcrEngineRegistry, RecognizedText, TextDetectionResult,
    TextLine, TextRecognitionResult,
)
from reservoir_data_translator.ingestion.ocr.models import OcrTable
from reservoir_data_translator.ingestion.ocr.page_engine import OcrEngine
from reservoir_data_translator.ingestion.ocr.table_pipeline import (
    RecursiveTableEngine, SubtableProposal,
)
from reservoir_data_translator.ingestion.pdf_parser import OcrReviewSession


class _Layout:
    def detect(self, image, *, context):
        return LayoutResult((
            LayoutElement("heading", "table_title", (10, 5, 90, 18), .99),
            LayoutElement("parent", "table", (10, 20, 90, 80), .98),
        ))


class _TextDetector:
    def detect(self, image, *, layout, context):
        return TextDetectionResult((TextLine("line", (0, 0, image.width, image.height), .99),))


class _TextRecognizer:
    def recognize(self, image, *, detections, context):
        return TextRecognitionResult((RecognizedText("line", "PVT 组合表", (0, 0, image.width, image.height), .99),))


class _LowConfidenceTextRecognizer:
    def recognize(self, image, *, detections, context):
        return TextRecognitionResult((RecognizedText("line", "待核查文字", (0, 0, image.width, image.height), .62),))


class _TableRecognizer:
    def recognize(self, image, *, context):
        name = context.parameters["table_node_id"]
        return OcrTable(["phase", "value"], [[name, "1"]]), .95


class _Splitter:
    def propose(self, image, *, context):
        if context.parameters["table_depth"]:
            return ()
        return (
            SubtableProposal((0, 0, 40, 60), None, "left"),
            SubtableProposal((40, 0, 80, 60), None, "right"),
        )


class _ConflictingSplitter:
    def propose(self, image, *, context):
        return (
            SubtableProposal((0, 0, 60, 60), None, "first"),
            SubtableProposal((30, 0, 80, 60), None, "overlap"),
        )


def _engine(*, text_recognizer=None, minimum_confidence=.70):
    registry = OcrEngineRegistry()
    for kind, name, instance in (
        ("layout", "layout", _Layout()),
        ("text_detection", "det", _TextDetector()),
        ("text_recognition", "rec", text_recognizer or _TextRecognizer()),
        ("table", "table", RecursiveTableEngine(_TableRecognizer(), _Splitter())),
    ):
        registry.register(EngineDescriptor(name, name, kind), lambda value=instance: value)
    return OcrEngine(registry, CropPipelineSelection(
        layout_engine="layout", text_detection_engine="det",
        text_recognition_engine="rec", table_engine="table",
    ), minimum_confidence=minimum_confidence)


def test_project_engine_routes_regions_and_preserves_page_geometry():
    with Image.new("RGB", (100, 100), "white") as image:
        result = _engine().analyze_page(image, page_number=1, languages=("en",))
    assert len(result.table_trees) == 1
    tree = result.table_trees[0]
    assert tree.root_id == "parent"
    assert tree.leaf_ids == ("parent.1", "parent.2")
    assert [r.bbox_pixels for r in result.regions if r.region_type == "table"] == [
        (10, 20, 50, 80), (50, 20, 90, 80),
    ]
    assert all("TABLE_SPLIT_REVIEW_REQUIRED" in r.quality_flags
        for r in result.regions if r.region_type == "table")


def test_low_confidence_text_from_component_engine_reaches_ocr_review(tmp_path: Path):
    with Image.new("RGB", (100, 100), "white") as image:
        result = _engine(text_recognizer=_LowConfidenceTextRecognizer()).analyze_page(
            image, page_number=5, languages=("en",))
    text = next(region for region in result.regions if region.region_type == "text")
    assert text.confidence == .62
    assert "LOW_TEXT_CONFIDENCE" in text.quality_flags
    parser = PdfParser(reject_low_confidence_ocr=True)
    issues = parser._ocr_review_issues(((result, 100.0, 100.0),), path=tmp_path / "scan.pdf")
    text_issue = next(issue for issue in issues if issue.issue_type == "region")
    assert text_issue.page == 5
    assert text_issue.flags == ("LOW_TEXT_CONFIDENCE",)
    assert text_issue.recognized_content == "待核查文字"
    assert text_issue.confidence == .62


def test_component_engine_uses_configured_confidence_threshold():
    with Image.new("RGB", (100, 100), "white") as image:
        result = _engine(text_recognizer=_LowConfidenceTextRecognizer(),
            minimum_confidence=.60).analyze_page(image, page_number=1, languages=("en",))
    text = next(region for region in result.regions if region.region_type == "text")
    assert "LOW_TEXT_CONFIDENCE" not in text.quality_flags


def test_review_confirmed_leaf_tables_chunk_separately(tmp_path: Path):
    with Image.new("RGB", (100, 100), "white") as image:
        result = _engine().analyze_page(image, page_number=1, languages=("en",))
    parser = PdfParser(max_chunk_tokens=300, reject_low_confidence_ocr=False)
    pdf = tmp_path / "scan.pdf"
    pdf.touch()
    pages = ((result, 100.0, 100.0),)
    issues = parser._ocr_review_issues(pages, path=pdf)
    assert [issue.issue_type for issue in issues] == ["table_split"]
    issue = issues[0]
    source_id = issue.context_candidates[0]["region_id"]
    session = OcrReviewSession(issues, pages, pdf, "scan", (), {})
    with pytest.raises(ValueError, match="Every table split issue"):
        parser.finalize_ocr_review(session, {})
    document = parser.finalize_ocr_review(session, {}, {issue.issue_id: {
        "action": "accept", "context_assignments": {source_id: ["parent.1"]},
    }})
    tables = [block for block in document.blocks if block.block_type == "table"]
    assert len(tables) == 2
    assert {block.content["ocr_region_id"] for block in tables} == {"parent.1", "parent.2"}
    first = next(block for block in tables if block.content["ocr_region_id"] == "parent.1")
    second = next(block for block in tables if block.content["ocr_region_id"] == "parent.2")
    assert first.content["caption"] == "PVT 组合表"
    assert "caption" not in second.content
    assert first.content["parent_table_region_id"] == "parent"
    assert first.content["table_tree_id"] == "parent:tree"
    assert first.source_region.row_start == 1
    assert second.source_region.row_start == 1


def test_review_can_exclude_uncertain_table_without_mapping(tmp_path: Path):
    with Image.new("RGB", (100, 100), "white") as image:
        result = _engine().analyze_page(image, page_number=1, languages=("en",))
    parser = PdfParser(reject_low_confidence_ocr=False)
    pdf = tmp_path / "scan.pdf"
    pdf.touch()
    pages = ((result, 100.0, 100.0),)
    issue = parser._ocr_review_issues(pages, path=pdf)[0]
    session = OcrReviewSession((issue,), pages, pdf, "scan", (), {})
    document = parser.finalize_ocr_review(session, {}, {issue.issue_id: {
        "action": "exclude", "context_assignments": {
            candidate["region_id"]: [] for candidate in issue.context_candidates
        },
    }})
    assert not any(block.block_type == "table" for block in document.blocks)


def test_conflicting_split_stays_one_leaf_and_requires_review():
    engine = RecursiveTableEngine(_TableRecognizer(), _ConflictingSplitter())
    context = CropRunContext("test", "table", ("en",), {"element_id": "parent"})
    with Image.new("RGB", (80, 60), "white") as image:
        result = engine.parse(image, layout=LayoutResult(),
            recognized_text=TextRecognitionResult(), context=context)
    assert result.tree.leaf_ids == ("parent",)
    assert "TABLE_SPLIT_GEOMETRY_CONFLICT" in result.tree.review_reasons
    assert result.tree.model_calls == 2
    assert result.tree.processed_pixels == 80 * 60 * 2


def test_recursion_call_budget_is_enforced():
    engine = RecursiveTableEngine(_TableRecognizer(), _Splitter(), max_model_calls=1)
    context = CropRunContext("test", "table", ("en",), {"element_id": "parent"})
    with Image.new("RGB", (80, 60), "white") as image:
        with pytest.raises(Exception, match="budget exceeded"):
            engine.parse(image, layout=LayoutResult(),
                recognized_text=TextRecognitionResult(), context=context)
