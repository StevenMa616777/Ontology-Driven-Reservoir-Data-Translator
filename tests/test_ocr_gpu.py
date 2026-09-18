import sys
import base64
from io import BytesIO
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from reservoir_data_translator.api import create_app
from reservoir_data_translator.ingestion import PdfParser
from reservoir_data_translator.ingestion.ocr.base import OcrBackendError
from reservoir_data_translator.ingestion.ocr.paddle_backend import PaddleOcrBackend


@pytest.mark.parametrize(
    "cuda,count,device,code",
    [
        (False, 0, "gpu:0", "PDF_OCR_GPU_RUNTIME_UNAVAILABLE"),
        (True, 0, "gpu:0", "PDF_OCR_GPU_UNAVAILABLE"),
        (True, 1, "gpu:1", "PDF_OCR_GPU_UNAVAILABLE"),
    ],
)
def test_gpu_failure_stops_before_model_loading(monkeypatch, cuda, count, device, code):
    def unexpected_factory(**options):
        pytest.fail("Models must not load when CUDA is unavailable")

    monkeypatch.setitem(sys.modules, "paddle", SimpleNamespace(
        is_compiled_with_cuda=lambda: cuda,
        device=SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: count)),
    ))
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PPStructureV3=unexpected_factory))
    with pytest.raises(OcrBackendError) as error:
        PaddleOcrBackend(device=device)._get_pipeline()
    assert error.value.code == code


def test_default_gpu_is_checked_and_pipeline_reused(monkeypatch):
    selected = []
    options = []
    pipeline = object()

    def factory(**kwargs):
        options.append(kwargs)
        return pipeline

    monkeypatch.setitem(sys.modules, "paddle", SimpleNamespace(
        is_compiled_with_cuda=lambda: True,
        device=SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: 1)),
        set_device=selected.append,
    ))
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PPStructureV3=factory))
    backend = PaddleOcrBackend()
    assert selected == []  # Native-text documents do not initialize CUDA.
    assert backend._get_pipeline() is pipeline
    assert backend._get_pipeline() is pipeline
    assert selected == ["gpu:0"]
    assert len(options) == 1
    assert options[0]["device"] == "gpu:0"


def test_explicit_cpu_does_not_require_cuda(monkeypatch):
    options = []
    monkeypatch.setitem(sys.modules, "paddle", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(
        PPStructureV3=lambda **kwargs: options.append(kwargs),
    ))
    PaddleOcrBackend(device="cpu")._get_pipeline()
    assert options[0]["device"] == "cpu"


@pytest.mark.asyncio
async def test_gpu_runtime_failure_is_exposed_to_web_client(monkeypatch, registry, tmp_path):
    monkeypatch.setitem(sys.modules, "paddle", SimpleNamespace(is_compiled_with_cuda=lambda: False))
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(100, 100))
    pdf.drawImage(ImageReader(Image.new("RGB", (100, 100), "white")), 0, 0, 100, 100)
    pdf.save()
    parser = PdfParser(ocr_backend=PaddleOcrBackend(), ocr_render_dpi=72,
                       ocr_artifact_dir=tmp_path / "ocr")
    app = create_app(registry=registry, pdf_parser=parser)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/ingest", json={
            "file_name": "scan.pdf",
            "content_encoding": "base64",
            "content": base64.b64encode(buffer.getvalue()).decode("ascii"),
        })
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "PDF_OCR_GPU_RUNTIME_UNAVAILABLE"
    assert detail["stage_label"] == "OCR 识别"
    assert "CPU" in detail["message"]
