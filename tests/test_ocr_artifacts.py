import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
import json

from httpx import ASGITransport, AsyncClient
import pdfplumber
from PIL import Image
import pytest
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from reservoir_data_translator.api import create_app
from reservoir_data_translator.ingestion import IngestionError, PaddleOcrBackend, PdfParser
from reservoir_data_translator.ingestion.ocr.artifacts import OcrArtifactWriter, active_artifact, matching_table
from reservoir_data_translator.semantic import SemanticModelProvider, SemanticProviderError


def raw_page(text="油藏压力 100 bar", *, score=0.99):
    return {
        "width": 400, "height": 500,
        "parsing_res_list": [{"block_label": "text", "block_id": 0, "block_order": 0, "block_bbox": [30, 30, 360, 60], "block_content": text}],
        "overall_ocr_res": {"rec_boxes": [[30, 30, 360, 60]], "rec_scores": [score]},
    }


def scan_bytes(pages=1):
    stream = BytesIO()
    pdf = Canvas(stream, pagesize=(400, 500))
    image = Image.new("RGB", (400, 500), "white")
    for _ in range(pages):
        pdf.drawImage(ImageReader(image), 0, 0, 400, 500)
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


def backend_for(payload, *, fail_page=None):
    class Pipeline:
        calls = 0

        def predict(self, image, **options):
            self.calls += 1
            if self.calls == fail_page:
                raise RuntimeError("simulated second page failure")
            return [payload]

    return PaddleOcrBackend(pipeline_factory=lambda **options: Pipeline())


def test_raw_pdf_preserves_errors_chinese_table_and_image(tmp_path):
    payload = raw_page("未经修正的 OCR: →000^0  4.2e-51/bar")
    payload["parsing_res_list"].extend([
        {"block_label": "table", "block_bbox": [30, 90, 370, 220], "block_content": '<table><tr><td colspan="2">原始表头</td></tr><tr><td>压力(bar)</td><td>粘度(cP)</td></tr><tr><td>00</td><td>→006\'0</td></tr></table>'},
        {"block_label": "image", "block_bbox": [30, 260, 200, 380], "block_content": ""},
    ])
    writer = OcrArtifactWriter("临时.pdf", 1, 72, tmp_path)
    writer.record(1, payload, Image.new("RGB", (400, 500), "#b8dcd3"))
    reference = writer.finish()
    assert reference["status"] == "complete"
    raw_text = writer.raw_path.read_text(encoding="utf-8")
    assert raw_text.startswith('{\n  "pages": [\n')
    assert '\n        "parsing_res_list": [' in raw_text
    assert json.loads(raw_text)["pages"][0]["result"] == payload
    assert json.loads(writer.raw_path.read_text(encoding="utf-8"))["pages"][0]["result"] == payload
    with pdfplumber.open(writer.pdf_path) as pdf:
        assert len(pdf.pages) == 1
        text = pdf.pages[0].extract_text()
        assert "未经修正" in text
        assert "→000^0" in text
        assert "原始表头" in text
        assert "→006'0" in text
        assert len(pdf.pages[0].images) == 1
        assert "table" in text
        assert "image" in text
    with pdfplumber.open(writer.source_pdf_path) as pdf:
        assert "text" in pdf.pages[0].extract_text()
        assert "table" in pdf.pages[0].extract_text()
        assert "image" in pdf.pages[0].extract_text()


def test_names_are_unique_even_with_concurrent_writers(tmp_path):
    def create(_):
        writer = OcrArtifactWriter("same.pdf", 1, 72, tmp_path)
        writer.record(1, raw_page(), Image.new("RGB", (400, 500)))
        writer.finish()
        return writer.pdf_path.name

    with ThreadPoolExecutor(max_workers=3) as pool:
        names = list(pool.map(create, range(3)))
    base = f"same_{datetime.now().astimezone():%Y-%m-%d}"
    assert set(names) == {base + ".pdf", base + "(2).pdf", base + "(3).pdf"}


@pytest.mark.parametrize("dpi", [72, 144, 300])
def test_source_and_result_have_matching_pixel_bbox_overlays(tmp_path, dpi):
    writer = OcrArtifactWriter("regions.pdf", 1, dpi, tmp_path)
    with Image.new("RGB", (400, 500), "white") as image:
        writer.record(1, raw_page(), image)
    reference = writer.finish()
    assert reference["source_regions_preview_url"].endswith("/source-regions")
    rectangles = []
    for path in (writer.pdf_path, writer.source_pdf_path):
        with pdfplumber.open(path) as pdf:
            page = pdf.pages[0]
            assert "R1" in page.extract_text()
            assert "text" in page.extract_text()
            red = [r for r in page.rects if r["stroke"] and tuple(r["stroking_color"]) == (1, 0, 0)]
            assert len(red) == 1
            box = red[0]
            coordinates = [box["x0"], box["top"], box["x1"], box["bottom"]]
            assert coordinates == pytest.approx([v * 72 / dpi for v in [30, 30, 360, 60]], abs=0.001)
            rectangles.append(coordinates)
            assert len(page.images) == (1 if path == writer.source_pdf_path else 0)
    assert rectangles[0] == rectangles[1]
    comparison = json.loads(writer.comparison_path.read_text(encoding="utf-8"))
    assert len(comparison["pages"]) == 1
    recorded = comparison["pages"][0]
    assert recorded["source_page"] == 1
    assert (recorded["width"], recorded["height"]) == pytest.approx((400 * 72 / dpi, 500 * 72 / dpi))
    assert recorded["regions"][0]["number"] == "R1"
    assert recorded["regions"][0]["type"] == "text"
    assert recorded["regions"][0]["bbox"] == pytest.approx([v * 72 / dpi for v in (30, 30, 360, 60)])
    for path in (writer.clean_pdf_path, writer.clean_source_path):
        with pdfplumber.open(path) as pdf:
            page = pdf.pages[0]
            assert "R1" not in (page.extract_text() or "")
            assert not [r for r in page.rects if r["stroke"] and r["stroking_color"] == (1, 0, 0)]
            assert len(page.images) == (1 if path == writer.clean_source_path else 0)


def test_existing_pdf_without_manifest_is_not_overwritten(tmp_path):
    base = f"existing_{datetime.now().astimezone():%Y-%m-%d}"
    original = tmp_path / (base + ".pdf")
    original.write_bytes(b"existing file")
    writer = OcrArtifactWriter("existing.pdf", 1, 72, tmp_path)
    writer.record(1, raw_page(), Image.new("RGB", (400, 500)))
    writer.finish()
    assert original.read_bytes() == b"existing file"
    assert writer.pdf_path.name == base + "(2).pdf"
    assert len(list(tmp_path.glob("*.manifest.json"))) == 1


def test_paddle_raw_wrapper_is_kept_before_normalization(tmp_path):
    payload = {"res": raw_page(), "model_metadata": {"untouched": True}}
    writer = OcrArtifactWriter("wrapped.pdf", 1, 72, tmp_path)
    token = active_artifact.set(writer)
    try:
        result = backend_for(payload).analyze_page(Image.new("RGB", (400, 500)), page_number=1, languages=("ch",))
        writer.finish()
    finally:
        active_artifact.reset(token)
    assert result.regions[0].text == "油藏压力 100 bar"
    saved = json.loads(writer.raw_path.read_text(encoding="utf-8"))
    assert saved["pages"][0]["result"] == payload


@pytest.mark.asyncio
async def test_synchronous_api_preserves_artifact_on_low_confidence(registry, tmp_path):
    parser = PdfParser(ocr_backend=backend_for(raw_page(score=0.1)), ocr_render_dpi=72, ocr_artifact_dir=tmp_path)
    app = create_app(registry=registry, pdf_parser=parser)
    body = {"source": {"file_name": "low.pdf", "content": base64.b64encode(scan_bytes()).decode(), "content_encoding": "base64"}, "target_platform": "eclipse"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/translate", json=body)
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "PDF_OCR_LOW_CONFIDENCE"
        artifact = detail["ocr_intermediate"]
        assert artifact["status"] == "complete"
        assert (await client.get(artifact["preview_url"])).status_code == 200


def test_all_ocr_pages_saved_before_low_confidence_gate(tmp_path):
    source = tmp_path / "low.pdf"
    source.write_bytes(scan_bytes(2))
    root = tmp_path / "output"
    with pytest.raises(IngestionError) as error:
        PdfParser(ocr_backend=backend_for(raw_page(score=0.2)), ocr_render_dpi=72, ocr_artifact_dir=root).parse(source)
    assert error.value.code == "PDF_OCR_LOW_CONFIDENCE"
    manifest = json.loads(next(root.glob("*.manifest.json")).read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["completed_pages"] == [1, 2]
    with pdfplumber.open(next(root.glob("*.pdf"))) as pdf:
        assert len(pdf.pages) == 2


def test_partial_model_failure_retains_completed_pages(tmp_path):
    source = tmp_path / "partial.pdf"
    source.write_bytes(scan_bytes(2))
    root = tmp_path / "output"
    with pytest.raises(IngestionError):
        PdfParser(ocr_backend=backend_for(raw_page(), fail_page=2), ocr_render_dpi=72, ocr_artifact_dir=root).parse(source)
    manifest = json.loads(next(root.glob("*.manifest.json")).read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert manifest["completed_pages"] == [1]
    with pdfplumber.open(next(root.glob("*.pdf"))) as pdf:
        assert len(pdf.pages) == 1
    assert len(json.loads(next(root.glob("*.raw.json")).read_text(encoding="utf-8"))["pages"]) == 1


def test_artifact_save_failure_has_own_stage(tmp_path):
    root = tmp_path / "not-a-directory"
    root.write_text("occupied")
    with pytest.raises(IngestionError) as error:
        OcrArtifactWriter("scan.pdf", 1, 72, root)
    assert error.value.code == "PDF_OCR_ARTIFACT_SAVE_FAILED"
    assert error.value.stage == "ocr_artifact"


def test_native_pdf_does_not_create_artifacts(tmp_path):
    source = tmp_path / "native.pdf"
    pdf = Canvas(str(source))
    pdf.drawString(50, 700, "Reservoir pressure 100 bar")
    pdf.save()
    root = tmp_path / "output"
    PdfParser(ocr_backend=backend_for(raw_page()), ocr_artifact_dir=root).parse(source)
    assert not root.exists()


def test_table_association_uses_region_html_and_geometry():
    a = {"table_bbox": [10, 50, 350, 150], "pred_html": "wrong?"}
    b = {"table_bbox": [10, 200, 350, 300], "pred_html": "other"}
    region = {"block_label": "table", "block_bbox": a["table_bbox"], "block_content": "<table><tr><td>Pressure</td></tr><tr><td>100</td></tr></table>"}
    assert matching_table([b, a], region) is a
    assert matching_table([{}, {}], region) == {}
    payload = {"parsing_res_list": [region], "table_res_list": [b, a]}
    normalized = PaddleOcrBackend()._regions(payload)
    assert normalized[0].table.columns == ["Pressure"]
    assert normalized[0].table.rows == [["100"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_semantic", [False, True])
async def test_preview_available_while_semantic_running_and_after_failure(registry, tmp_path, monkeypatch, fail_semantic):
    monkeypatch.setenv("DEEPSEEK_TRACE_DIR", str(tmp_path / "traces"))
    entered, release = asyncio.Event(), asyncio.Event()

    class Provider(SemanticModelProvider):
        async def structured_generate(self, prompt, response_model):
            entered.set()
            await release.wait()
            if fail_semantic:
                raise SemanticProviderError("TEST_SEMANTIC_FAILED", "simulated failure")
            return {"mappings": [{"status": "UNMAPPED", "source_block_id": "block_0001", "source_text": "reservoir pressure", "candidate_concepts": [], "confidence": 0}]}

    root = tmp_path / "output"
    parser = PdfParser(ocr_backend=backend_for(raw_page("Oil density 850 kg/m3")), ocr_render_dpi=72, ocr_artifact_dir=root)
    app = create_app(registry=registry, provider=Provider(), pdf_parser=parser)
    body = {"source": {"file_name": "原始名称.pdf", "content": base64.b64encode(scan_bytes()).decode(), "content_encoding": "base64"}, "target_platform": "eclipse"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        submitted = await client.post("/translation-jobs", json=body)
        assert submitted.status_code == 202
        url = submitted.json()["status_url"]
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            state = (await client.get(url)).json()
            assert state["status"] == "running"
            assert "result" not in state
            artifact = state["ocr_intermediate"]
            assert artifact["file_name"].startswith("原始名称_")
            preview = await client.get(artifact["preview_url"])
            assert preview.status_code == 200
            assert preview.content.startswith(b"%PDF")
            assert preview.headers["content-disposition"].startswith("inline")
            download = await client.get(artifact["download_url"])
            assert download.headers["content-disposition"].startswith("attachment")
            source_preview = await client.get(artifact["source_regions_preview_url"])
            assert source_preview.status_code == 200
            assert source_preview.content.startswith(b"%PDF")
            assert source_preview.headers["content-disposition"].startswith("inline")
            source_download = await client.get(artifact["source_regions_download_url"])
            assert source_download.status_code == 200
            assert source_download.headers["content-disposition"].startswith("attachment")
            comparison = await client.get(artifact["comparison_url"])
            assert comparison.status_code == 200
            pages = comparison.json()["pages"]
            assert comparison.json()["interactive"] is True
            assert len(pages) == 1
            assert pages[0]["source_page"] == 1
            assert pages[0]["regions"][0] == {"number": "R1", "type": "text",
                                              "bbox": [30, 30, 360, 60]}
            assert "/clean-source/pages/" in pages[0]["source_url"]
            assert "/clean/pages/" in pages[0]["result_url"]
            for key in ("source_url", "result_url"):
                image = await client.get(pages[0][key])
                assert image.status_code == 200
                assert image.headers["content-type"] == "image/png"
                with Image.open(BytesIO(image.content)) as rendered:
                    assert rendered.size == (800, 1000)
            assert (await client.get(pages[0]["result_url"].removesuffix("1") + "0")).status_code == 404
            assert (await client.get(pages[0]["result_url"].removesuffix("1") + "2")).status_code == 404
            next(root.glob("*.comparison.json")).unlink()
            legacy = (await client.get(artifact["comparison_url"])).json()
            assert legacy["interactive"] is False
            assert "/source-regions/pages/" in legacy["pages"][0]["source_url"]
            raw = await client.get(artifact["raw_url"])
            assert raw.text.startswith('{\n  "pages": [\n')
            assert raw.headers["content-disposition"].startswith("attachment")
            assert raw.json()["pages"][0]["result"]["parsing_res_list"][0]["block_content"] == "Oil density 850 kg/m3"
            # Older compact files download prettified without changing disk data.
            raw_path = next(root.glob("*.raw.json"))
            compact = json.dumps(raw.json(), ensure_ascii=False)
            raw_path.write_text(compact, encoding="utf-8")
            legacy_download = await client.get(artifact["raw_url"])
            assert legacy_download.text.startswith('{\n  "pages": [\n')
            assert legacy_download.json() == raw.json()
            assert raw_path.read_text(encoding="utf-8") == compact
        finally:
            release.set()
        for _ in range(100):
            state = (await client.get(url)).json()
            if state["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert state["status"] == ("failed" if fail_semantic else "completed")
        assert (await client.get(artifact["preview_url"])).status_code == 200
        if not fail_semantic:
            assert state["result"]["ocr_intermediate"] == artifact
    # Files remain addressable after creating a fresh app (no in-memory registry).
    fresh = create_app(registry=registry, pdf_parser=parser)
    async with AsyncClient(transport=ASGITransport(app=fresh), base_url="http://test") as client:
        assert (await client.get(artifact["preview_url"])).status_code == 200
        assert (await client.get(artifact["preview_url"].replace("/pdf", "/unknown"))).status_code == 404
