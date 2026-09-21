import base64
from io import BytesIO

from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest
from pydantic import ValidationError

from reservoir_data_translator.api import create_app
from reservoir_data_translator.ingestion.ocr import (
    CropPipelineSelection,
    CropRecognitionPipeline,
    CropRunContext,
    EngineDescriptor,
    LayoutElement,
    LayoutResult,
    OcrEngineRegistry,
    OcrPageResult,
    OcrRegion,
    RecognizedText,
    TextDetectionResult,
    TextLine,
    TextRecognitionResult,
)
from reservoir_data_translator.ocr_lab import CropLabError, CropLabService
from reservoir_data_translator.ocr_lab.models import CropRunRequest


class FakeComposite:
    def analyze_crop(self, image, *, context):
        return OcrPageResult(
            page_number=1,
            image_width=image.width,
            image_height=image.height,
            regions=(OcrRegion(
                region_id="text-1",
                region_type="text",
                layout_label="text",
                bbox_pixels=(0, 0, image.width, image.height),
                reading_order=1,
                text="pressure 100 bar",
                confidence=0.91,
            ),),
            engine="fake-composite",
        )


class FakeLayout:
    def detect(self, image, *, context):
        return LayoutResult((LayoutElement("e1", "text", (0, 0, image.width, image.height), 0.9),))


class FakeTextDetector:
    def detect(self, image, *, layout, context):
        assert layout.elements[0].element_id == "e1"
        return TextDetectionResult((TextLine("l1", (0, 0, image.width, image.height), 0.8),))


class FakeTextRecognizer:
    def recognize(self, image, *, detections, context):
        assert detections.lines[0].line_id == "l1"
        return TextRecognitionResult((RecognizedText("l1", "100 bar", confidence=0.95),))


def fake_registry() -> OcrEngineRegistry:
    registry = OcrEngineRegistry()
    registry.register(EngineDescriptor("fake-composite", "Fake composite", "composite"), FakeComposite)
    return registry


def image_payload(width=80, height=40) -> str:
    stream = BytesIO()
    Image.new("RGB", (width, height), "white").save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def test_component_pipeline_runs_explicit_replaceable_ports_in_order() -> None:
    registry = OcrEngineRegistry()
    registry.register(EngineDescriptor("layout", "Layout", "layout"), FakeLayout)
    registry.register(EngineDescriptor("detect", "Detector", "text_detection"), FakeTextDetector)
    registry.register(EngineDescriptor("recognize", "Recognizer", "text_recognition"), FakeTextRecognizer)
    pipeline = CropRecognitionPipeline(registry)

    result = pipeline.run(
        Image.new("RGB", (20, 10), "white"),
        selection=CropPipelineSelection(
            layout_engine="layout",
            text_detection_engine="detect",
            text_recognition_engine="recognize",
        ),
        context=CropRunContext("run", "text", ("en",)),
    )

    assert [stage.stage for stage in result.stages] == [
        "layout", "text_detection", "text_recognition"
    ]
    assert result.stages[-1].output.spans[0].text == "100 bar"


def test_crop_lab_requires_explicit_engine_selection(tmp_path) -> None:
    service = CropLabService(fake_registry(), artifact_root=tmp_path)
    request = CropRunRequest.model_validate({
        "file_name": "crop.png",
        "media_type": "image/png",
        "content_base64": image_payload(),
        "pipeline": {},
    })

    with pytest.raises(CropLabError, match="Select a composite engine"):
        service.run(request)


@pytest.mark.parametrize("scale", [0.25, 0.75, 3.5])
def test_crop_lab_scale_contract_rejects_out_of_range_or_half_steps(scale) -> None:
    with pytest.raises(ValidationError):
        CropRunRequest.model_validate({
            "content_base64": image_payload(),
            "media_type": "image/png",
            "preprocessing": {"scale": scale},
            "pipeline": {"composite_engine": "fake-composite"},
        })


@pytest.mark.parametrize("scale", [0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
def test_crop_lab_scale_contract_accepts_half_steps(scale) -> None:
    request = CropRunRequest.model_validate({
        "content_base64": image_payload(),
        "media_type": "image/png",
        "preprocessing": {"scale": scale},
        "pipeline": {"composite_engine": "fake-composite"},
    })
    assert request.preprocessing.scale == scale


def test_crop_lab_rejects_processed_image_over_pixel_budget(tmp_path, monkeypatch) -> None:
    from reservoir_data_translator.ocr_lab import service as service_module

    monkeypatch.setattr(service_module, "MAX_PROCESSED_CROP_PIXELS", 1_000)
    request = CropRunRequest.model_validate({
        "content_base64": image_payload(20, 20),
        "media_type": "image/png",
        "preprocessing": {"scale": 3},
        "pipeline": {"composite_engine": "fake-composite"},
    })

    with pytest.raises(CropLabError) as error:
        CropLabService(fake_registry(), artifact_root=tmp_path).run(request)

    assert error.value.code == "OCR_LAB_PROCESSED_PIXEL_LIMIT_EXCEEDED"
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_crop_lab_api_crops_source_and_runs_selected_engine(registry, tmp_path) -> None:
    service = CropLabService(fake_registry(), artifact_root=tmp_path)
    app = create_app(registry=registry, ocr_lab_service=service)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    engines = await client.get("/api/ocr-lab/engines")
    assert engines.status_code == 200
    assert engines.json()["default_engine"] is None

    response = await client.post("/api/ocr-lab/runs", json={
        "file_name": "problem-crop.png",
        "media_type": "image/png",
        "content_base64": image_payload(100, 60),
        "crop_kind": "text",
        "bbox": {"x0": 10, "y0": 5, "x1": 70, "y1": 35},
        "preprocessing": {"scale": 2, "contrast": 1, "sharpen": 0,
                          "grayscale": False, "threshold": None, "padding": 2},
        "pipeline": {"composite_engine": "fake-composite"},
        "provenance": {"source_kind": "manual_recrop"},
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["input"]["effective_bbox"] == {"x0": 8, "y0": 3, "x1": 72, "y1": 37}
    assert payload["input"]["crop_width"] == 64
    assert payload["input"]["processed_width"] == 128
    assert payload["result"]["page_result"]["regions"][0]["text"] == "pressure 100 bar"
    artifact = await client.get(payload["artifacts"]["crop_url"])
    assert artifact.status_code == 200
    assert artifact.headers["content-type"].startswith("image/png")
    await client.aclose()


@pytest.mark.asyncio
async def test_ocr_lab_page_is_third_top_level_workbench(registry) -> None:
    app = create_app(registry=registry, ocr_lab_service=CropLabService(fake_registry()))
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    page = await client.get("/ocr-lab")
    script = await client.get("/ui/ocr-lab.js")
    workbench_script = await client.get("/ui/app.js")

    assert page.status_code == 200
    assert 'class="is-active" href="/ocr-lab"' in page.text
    assert "Translation Workbench" in page.text
    assert "Ontology Explorer" in page.text
    assert 'id="crop-file"' in page.text
    assert 'id="pre-scale" type="range" min="0.5" max="3" step="0.5" value="1"' in page.text
    assert "请选择，不自动使用 PaddleOCR" in page.text
    assert script.status_code == 200
    assert 'window.addEventListener("paste"' in script.text
    assert 'elements.stage.addEventListener("pointerdown"' in script.text
    assert 'elements.scale.addEventListener("input", schedulePreviewScale)' in script.text
    assert "96 / dpi" in script.text
    assert 'response.headers.get("X-Reservoir-OCR-Geometry")' in script.text
    assert "hasLowConfidenceFlag(item.flags)" in workbench_script.text
    assert "LOW(?:_[A-Z0-9]+)*_CONFIDENCE" in workbench_script.text
    assert "reviewFingerprint" in workbench_script.text
    assert "送到 OCR Lab 测试" in workbench_script.text
    assert "ocr-lab-v4" in (await client.get("/")).text
    await client.aclose()
