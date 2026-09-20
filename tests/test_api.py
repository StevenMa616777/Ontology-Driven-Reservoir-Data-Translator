import base64
import asyncio
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import httpx
from httpx import ASGITransport, AsyncClient
import pytest
from PIL import Image
from openpyxl import Workbook
from reportlab.lib.pagesizes import letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from reservoir_data_translator.api import create_app
from reservoir_data_translator.api.main import _default_pdf_parser
from reservoir_data_translator.canonical import ReservoirSimulationModel
from reservoir_data_translator.mappers import (
    CMGDemoMapper,
    EclipseDemoMapper,
    PlatformMappingRegistry,
)
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.ingestion import OcrPageResult, OcrRegion, PaddleOcrBackend, PdfParser


def test_default_pdf_parser_uses_lazy_paddle_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RESERVOIR_OCR_BACKEND", raising=False)
    monkeypatch.delenv("RESERVOIR_OCR_DEVICE", raising=False)
    parser = _default_pdf_parser()
    assert isinstance(parser.ocr_backend, PaddleOcrBackend)
    assert parser.ocr_backend.device == "gpu:0"


def test_default_pdf_parser_respects_explicit_cpu_device(monkeypatch) -> None:
    monkeypatch.setenv("RESERVOIR_OCR_BACKEND", "paddleocr")
    monkeypatch.setenv("RESERVOIR_OCR_DEVICE", "cpu")
    assert _default_pdf_parser().ocr_backend.device == "cpu"


@pytest.mark.asyncio
async def test_ontology_graph_exposes_runtime_concepts_and_typed_edges(
    registry: OntologyRegistry,
) -> None:
    app = create_app(registry=registry)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    response = await client.get("/api/ontology/graph")

    assert response.status_code == 200
    graph = response.json()
    assert graph["ontology"]["version"] == registry.metadata.version
    assert len(graph["nodes"]) == len(registry)
    viscosity = next(node for node in graph["nodes"] if node["id"] == "fluid.oil.pvt.viscosity")
    assert viscosity["canonical_unit"] == "cP"
    assert viscosity["relationships"]["dependent_on"] == ["fluid.oil.pvt.pressure"]
    assert any(
        edge["source"] == "fluid.oil.pvt"
        and edge["target"] == "fluid.oil.pvt.viscosity"
        and edge["type"] == "parent"
        for edge in graph["edges"]
    )
    assert any(
        edge["source"] == "fluid.oil.pvt.viscosity"
        and edge["target"] == "fluid.oil.pvt.pressure"
        and edge["type"] == "dependent_on"
        for edge in graph["edges"]
    )
    pressure = next(node for node in graph["nodes"] if node["id"] == "fluid.oil.pvt.pressure")
    assert {"source": "fluid.oil.pvt.viscosity", "type": "dependent_on"} in pressure["incoming_relationships"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ontology_concept_endpoint_returns_detail_and_404(
    registry: OntologyRegistry,
) -> None:
    app = create_app(registry=registry)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    response = await client.get("/api/ontology/concepts/fluid.oil.density")
    missing = await client.get("/api/ontology/concepts/not.real")

    assert response.status_code == 200
    assert response.json()["source_file"].endswith("fluid.yaml")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "ONTOLOGY_CONCEPT_NOT_FOUND"
    await client.aclose()
from reservoir_data_translator.semantic import (
    DeepSeekProvider,
    SemanticModelProvider,
    SemanticProviderError,
    SourceMappingRegistry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class APIWellProvider(SemanticModelProvider):
    calls = 0

    async def structured_generate(self, prompt: str, response_model: type[Any]) -> Any:
        self.calls += 1
        return {
            "mappings": [
                {
                    "status": "MAPPED",
                    "source_text": "A15",
                    "source_block_id": "block_0001",
                    "ontology_concept": "well",
                    "canonical_path": "wells[A15].id",
                    "value": "A15",
                    "confidence": 0.99,
                },
                {
                    "status": "MAPPED",
                    "source_text": "producer",
                    "source_block_id": "block_0001",
                    "ontology_concept": "well.producer",
                    "canonical_path": "wells[A15].well_type",
                    "value": "producer",
                    "confidence": 0.98,
                },
                {
                    "status": "MAPPED",
                    "source_text": "500 m3/day",
                    "source_block_id": "block_0001",
                    "ontology_concept": "well.control.liquid_rate",
                    "canonical_path": "wells[A15].controls[liquid_rate].target",
                    "value": 500,
                    "source_unit": "m3/day",
                    "canonical_unit": "m3/day",
                    "confidence": 0.98,
                },
                {
                    "status": "MAPPED",
                    "source_text": "80 bar",
                    "source_block_id": "block_0001",
                    "ontology_concept": "well.constraint.minimum_bhp",
                    "canonical_path": (
                        "wells[A15].controls[liquid_rate]."
                        "constraints[minimum_bhp].value"
                    ),
                    "value": 80,
                    "source_unit": "bar",
                    "canonical_unit": "bar",
                    "confidence": 0.97,
                },
            ]
        }


class LowConfidenceWellProvider(APIWellProvider):
    async def structured_generate(self, prompt: str, response_model: type[Any]) -> Any:
        response = await super().structured_generate(prompt, response_model)
        response["mappings"][0]["confidence"] = 0.70
        return response


class FailingSemanticProvider(SemanticModelProvider):
    async def structured_generate(self, prompt: str, response_model: type[Any]) -> Any:
        raise SemanticProviderError(
            "DEEPSEEK_TIMEOUT",
            "DeepSeek request timed out.",
        )


def _configured_client(
    registry: OntologyRegistry,
    provider: SemanticModelProvider | None = None,
    pdf_parser: PdfParser | None = None,
):
    mappers = [
        EclipseDemoMapper(
            PlatformMappingRegistry.load(
                PROJECT_ROOT / "mappings" / "eclipse.yaml",
                registry,
            )
        ),
        CMGDemoMapper(
            PlatformMappingRegistry.load(
                PROJECT_ROOT / "mappings" / "cmg.yaml",
                registry,
            )
        ),
    ]
    source_mappings = [
        SourceMappingRegistry.load(path, registry)
        for path in sorted((PROJECT_ROOT / "mappings").glob("customer_*.yaml"))
    ]
    app = create_app(
        registry=registry,
        provider=provider,
        mappers=mappers,
        source_mappings=source_mappings,
        pdf_parser=pdf_parser,
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_stage_endpoints_cover_ingest_semantic_build_validate_and_export(
    registry: OntologyRegistry,
) -> None:
    client = _configured_client(registry, APIWellProvider())
    source_text = (
        "A15井采用定液生产制度，\n"
        "日产液控制在500方，\n"
        "井底流压不得低于80 bar。"
    )

    ingest = await client.post(
        "/ingest",
        json={
            "file_name": "client_c.txt",
            "content": source_text,
            "source_id": "client-c",
        },
    )
    assert ingest.status_code == 200
    document = ingest.json()
    assert document["blocks"][0]["block_type"] == "text"

    semantic = await client.post(
        "/semantic-map",
        json={"document": document, "source_system": "client_c"},
    )
    assert semantic.status_code == 200
    batch = semantic.json()
    assert len(batch["mappings"]) == 4

    build = await client.post(
        "/canonical/build",
        json={"mappings": batch["mappings"]},
    )
    assert build.status_code == 200
    canonical = build.json()
    assert canonical["wells"][0]["id"] == "A15"

    validation = await client.post(
        "/validate",
        json={"canonical_model": canonical, "target_platform": "eclipse"},
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is True

    export = await client.post(
        "/export/eclipse",
        json={"canonical_model": canonical},
    )
    assert export.status_code == 200
    assert export.json()["export_validation"]["valid"] is True
    assert "WCONPROD" in export.json()["target"]["content"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ingest_accepts_base64_xlsx(
    registry: OntologyRegistry,
) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["Well_ID", "LiquidRate"])
    worksheet.append(["A15", 500])
    buffer = BytesIO()
    workbook.save(buffer)
    client = _configured_client(registry, APIWellProvider())

    response = await client.post(
        "/ingest",
        json={
            "file_name": "client.xlsx",
            "content_encoding": "base64",
            "content": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "xlsx"
    assert payload["file_name"] == "client.xlsx"
    assert payload["blocks"][0]["content"]["rows"] == [["A15", 500]]
    await client.aclose()


@pytest.mark.asyncio
async def test_translate_returns_complete_trace_and_target(
    registry: OntologyRegistry,
) -> None:
    client = _configured_client(registry, APIWellProvider())

    response = await client.post(
        "/translate",
        json={
            "source": "A15井采用定液生产制度，日产液控制在500方，井底流压不得低于80 bar。",
            "source_system": "client_c",
            "target_platform": "cmg",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["translation_id"]
    assert payload["canonical_model"]["wells"][0]["id"] == "A15"
    assert payload["validation"]["valid"] is True
    assert payload["export_validation"]["valid"] is True
    assert "*OPERATE 'A15' *MAX *STL 500" in payload["target"]["content"]
    assert [event["stage"] for event in payload["trace"]] == [
        "ingest",
        "semantic_map",
        "canonical_build",
        "validation",
        "export_validation",
        "render",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_ingest_accepts_base64_native_text_pdf(
    registry: OntologyRegistry,
) -> None:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, "Minimum BHP 80 bar")
    pdf.save()
    client = _configured_client(registry, APIWellProvider())

    response = await client.post(
        "/ingest",
        json={
            "file_name": "client.pdf",
            "content_encoding": "base64",
            "content": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "pdf"
    assert payload["blocks"][0]["block_type"] == "text"
    assert payload["blocks"][0]["source_region"]["page"] == 1
    assert payload["blocks"][0]["source_region"]["bbox"]["coordinate_system"] == (
        "pdf_top_left_points"
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_ingest_accepts_base64_scanned_pdf_with_configured_ocr(
    registry: OntologyRegistry,
) -> None:
    class Backend:
        def analyze_page(
            self,
            image,
            *,
            page_number: int,
            languages: tuple[str, ...],
        ) -> OcrPageResult:
            width, height = image.size
            return OcrPageResult(
                page_number=page_number,
                image_width=width,
                image_height=height,
                engine="api-fixture-ocr",
                regions=(
                    OcrRegion(
                        region_id="body",
                        region_type="text",
                        layout_label="text",
                        bbox_pixels=(10, 10, width - 10, height - 10),
                        reading_order=1,
                        text="Minimum BHP 80 bar",
                        confidence=0.97,
                    ),
                ),
            )

    page_image = BytesIO()
    Image.new("RGB", (100, 100), "white").save(page_image, format="PNG")
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.drawImage(
        ImageReader(BytesIO(page_image.getvalue())),
        0,
        0,
        width=letter[0],
        height=letter[1],
    )
    pdf.save()
    client = _configured_client(
        registry,
        APIWellProvider(),
        pdf_parser=PdfParser(ocr_backend=Backend(), ocr_render_dpi=72),
    )

    response = await client.post(
        "/ingest",
        json={
            "file_name": "scan.pdf",
            "content_encoding": "base64",
            "content": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocks"][0]["content"] == "Minimum BHP 80 bar"
    assert payload["blocks"][0]["extraction_evidence"]["engine"] == (
        "api-fixture-ocr"
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_ocr_review_job_stops_or_continues_only_after_region_decisions(
    registry: OntologyRegistry, tmp_path,
) -> None:
    from reservoir_data_translator.ingestion.ocr.artifacts import active_artifact

    class Backend:
        def analyze_page(self, image, *, page_number: int, languages: tuple[str, ...]) -> OcrPageResult:
            width, height = image.size
            boxes = [(10, 10, 200, 50), (10, 60, width - 10, 120)]
            contents = ["unreliable text", "A15井采用定液生产制度，日产液控制在500方，井底流压不得低于80 bar。"]
            writer = active_artifact.get()
            writer.record(page_number, {"parsing_res_list": [
                {"block_id": index, "block_label": "text", "block_bbox": box,
                 "block_content": contents[index]}
                for index, box in enumerate(boxes)
            ]}, image)
            return OcrPageResult(
                page_number=page_number, image_width=width, image_height=height,
                engine="review-fixture", regions=tuple(
                    OcrRegion(region_id=str(index), region_type="text", layout_label="text",
                              bbox_pixels=box, reading_order=index + 1,
                              text=contents[index], raw_content=contents[index],
                              confidence=0.4 if index == 0 else 0.98,
                              quality_flags=("LOW_TEXT_CONFIDENCE",) if index == 0 else (),
                              source_region_index=index + 1)
                    for index, box in enumerate(boxes)
                ),
            )

    page_image = BytesIO()
    Image.new("RGB", (100, 100), "white").save(page_image, format="PNG")
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.drawImage(ImageReader(BytesIO(page_image.getvalue())), 0, 0,
                  width=letter[0], height=letter[1])
    pdf.save()
    request = {"source": {"file_name": "review.pdf", "content_encoding": "base64",
                          "content": base64.b64encode(buffer.getvalue()).decode("ascii")},
               "source_system": "client_c", "target_platform": "cmg"}
    provider = APIWellProvider()
    provider.calls = 0
    client = _configured_client(registry, provider, pdf_parser=PdfParser(
        ocr_backend=Backend(), ocr_render_dpi=72, ocr_artifact_dir=tmp_path,
    ))

    async def start_review():
        submitted = await client.post("/translation-jobs", json=request)
        assert submitted.status_code == 202
        url = submitted.json()["status_url"]
        for _ in range(150):
            job = (await client.get(url)).json()
            if job["status"] == "ocr_review_required":
                return url, job
            await asyncio.sleep(0.01)
        pytest.fail(f"OCR review was not reached: {job}")

    first_url, first = await start_review()
    assert provider.calls == 0
    item = first["ocr_review"]["items"][0]
    assert (item["page"], item["number"], item["label"], item["block_id"]) == (1, "R1", "text", "0")
    assert item["raw_content"] == "unreliable text"
    assert item["recognized_content"] == "unreliable text"
    assert item["source_image_url"]
    assert (await client.get(item["source_image_url"])).headers["content-type"] == "image/png"
    incomplete = await client.post(first_url + "/ocr-review", json={"action": "continue"})
    assert incomplete.status_code == 422
    stopped = await client.post(first_url + "/ocr-review", json={"action": "stop"})
    assert stopped.status_code == 200
    assert (await client.get(first_url)).json()["status"] == "stopped"
    assert provider.calls == 0

    second_url, second = await start_review()
    issue_id = second["ocr_review"]["items"][0]["issue_id"]
    continued = await client.post(second_url + "/ocr-review", json={
        "action": "continue", "decisions": {issue_id: "exclude"},
    })
    assert continued.status_code == 200
    for _ in range(150):
        resumed = (await client.get(second_url)).json()
        if resumed["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.01)
    assert resumed["status"] == "completed", resumed.get("error")
    assert resumed["result"]["status"] == "partial"
    assert resumed["result"]["ocr_review"]["excluded_count"] == 1
    saved_review = await client.get(resumed["ocr_intermediate"]["review_url"])
    assert saved_review.status_code == 200
    assert saved_review.json()["decisions"] == {issue_id: "exclude"}
    assert all("unreliable text" not in str(block["content"])
               for block in resumed["result"]["source"]["blocks"])
    assert provider.calls > 0
    audits = [json.loads(path.read_text(encoding="utf-8"))
              for path in tmp_path.glob("*.review.json")]
    assert {audit["action"] for audit in audits} == {"stop", "continue"}
    assert next(audit for audit in audits if audit["action"] == "continue")["decisions"] == {
        issue_id: "exclude",
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_ingest_automatically_ocrs_restricted_pdf(
    registry: OntologyRegistry,
) -> None:
    class Backend:
        def analyze_page(
            self,
            image,
            *,
            page_number: int,
            languages: tuple[str, ...],
        ) -> OcrPageResult:
            width, height = image.size
            return OcrPageResult(
                page_number=page_number,
                image_width=width,
                image_height=height,
                engine="restricted-fixture-ocr",
                regions=(
                    OcrRegion(
                        region_id="body",
                        region_type="text",
                        layout_label="text",
                        bbox_pixels=(10, 10, width - 10, height - 10),
                        reading_order=1,
                        text="Minimum BHP 80 bar",
                        confidence=0.97,
                    ),
                ),
            )

    encryption = StandardEncryption(
        "",
        ownerPassword="fixture-owner",
        canPrint=1,
        canModify=0,
        canCopy=0,
        canAnnotate=0,
    )
    page_image = BytesIO()
    Image.new("RGB", (100, 100), "white").save(page_image, format="PNG")
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, encrypt=encryption)
    pdf.drawImage(ImageReader(BytesIO(page_image.getvalue())), 72, 72, 240, 320)
    pdf.save()
    client = _configured_client(
        registry,
        APIWellProvider(),
        pdf_parser=PdfParser(ocr_backend=Backend(), ocr_render_dpi=72),
    )

    response = await client.post(
        "/ingest",
        json={
            "file_name": "restricted.pdf",
            "content_encoding": "base64",
            "content": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    )

    assert response.status_code == 200
    block = response.json()["blocks"][0]
    assert block["content"] == "Minimum BHP 80 bar"
    assert "SOURCE_TEXT_EXTRACTION_RESTRICTED" in (
        block["extraction_evidence"]["quality_flags"]
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_translate_persists_and_exposes_deepseek_call_trace(
    registry: OntologyRegistry,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_TRACE_DIR", str(tmp_path / "deepseek-traces"))

    async def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        assert "A15井采用定液生产制度" in request_payload["input"]
        mappings = await APIWellProvider().structured_generate("", object)
        return httpx.Response(
            200,
            json={
                "id": "resp-api-trace",
                "status": "completed",
                "model": "deepseek-v4-flash",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    "以下是结果：\n"
                                    + json.dumps(mappings, ensure_ascii=False)
                                    + "\n处理完成。"
                                ),
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 40,
                    "total_tokens": 160,
                },
            },
        )

    provider = DeepSeekProvider(
        "test-api-key",
        transport=httpx.MockTransport(handler),
    )
    client = _configured_client(registry, provider)
    response = await client.post(
        "/translate",
        json={
            "source": "A15井采用定液生产制度，日产液控制在500方，井底流压不得低于80 bar。",
            "source_system": "client_c",
            "target_platform": "cmg",
        },
    )

    assert response.status_code == 200
    summary = response.json()["deepseek_trace"]
    assert summary["api_requests"] == 1
    assert summary["retry_requests"] == 0
    assert summary["local_corrections"] == 1
    assert summary["avoided_network_retries"] == 1
    assert summary["input_tokens"] == 120
    assert summary["output_tokens"] == 40
    assert summary["total_tokens"] == 160
    assert summary["duration_ms"] >= 0
    assert summary["trace_url"].startswith("/deepseek-traces/")
    assert summary["readable_log_url"].endswith("/readable")
    trace_response = await client.get(summary["trace_url"])
    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["translation_id"] == response.json()["translation_id"]
    assert trace["calls"][0]["source_block_id"] == "block_0001"
    assert trace["calls"][0]["call_reason"] == "initial"
    assert trace["calls"][0]["outcome"] == "accepted_after_local_correction"
    assert trace["calls"][0]["local_correction"] == "json_extracted_from_wrapper"
    assert trace["calls"][0]["avoided_network_retry"] is True
    assert trace["calls"][0]["request_payload"]["input"]
    assert trace["calls"][0]["response_payload"]["output"]
    assert "Authorization" not in trace["calls"][0]["request_payload"]
    assert (tmp_path / "deepseek-traces" / f'{trace["translation_id"]}.json').is_file()
    readable_response = await client.get(summary["readable_log_url"])
    assert readable_response.status_code == 200
    assert readable_response.headers["content-type"].startswith("text/plain")
    assert "Call 1" in readable_response.text
    assert "Block: block_0001" in readable_response.text
    assert "Request input:" in readable_response.text
    assert "\\" not in readable_response.text
    assert (
        tmp_path / "deepseek-traces" / f'{trace["translation_id"]}.readable.log'
    ).is_file()
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/translate", "/translation-jobs"])
@pytest.mark.parametrize("failure_stage", ["semantic", "canonical"])
async def test_failed_translation_exposes_saved_trace(registry, monkeypatch, tmp_path, endpoint, failure_stage):
    from reservoir_data_translator.canonical import CanonicalBuilder, CanonicalBuildError

    monkeypatch.setenv("DEEPSEEK_TRACE_DIR", str(tmp_path / "traces"))

    async def handler(request):
        mappings = await APIWellProvider().structured_generate("", object)
        if failure_stage == "semantic":
            for mapping in mappings["mappings"]:
                if mapping.get("source_unit"):
                    mapping["value"] = {"value": mapping["value"], "unit": mapping["source_unit"]}
        return httpx.Response(200, json={"id": "failure-trace", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": json.dumps(mappings)}]}]})

    if failure_stage == "canonical":
        def fail_build(*args, **kwargs):
            raise CanonicalBuildError("CANONICAL_MODEL_INVALID", "Missing pressure")
        monkeypatch.setattr(CanonicalBuilder, "build", fail_build)

    provider = DeepSeekProvider("test-key", transport=httpx.MockTransport(handler))
    client = _configured_client(registry, provider)
    response = await client.post(endpoint, json={
        "source": "A15井采用定液生产制度，日产液控制在500方，井底流压不得低于80 bar。",
        "source_system": "client_c", "target_platform": "cmg"})
    if endpoint == "/translate":
        assert response.status_code >= 400
        error = response.json()["detail"]
    else:
        assert response.status_code == 202
        for _ in range(100):
            job = (await client.get(response.json()["status_url"])).json()
            if job["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        assert job["status"] == "failed"
        error = job["error"]
        assert job["deepseek_trace"] == error["deepseek_trace"]
    summary = error["deepseek_trace"]
    trace_response = await client.get(summary["trace_url"])
    assert trace_response.status_code == 200
    calls = trace_response.json()["calls"]
    assert summary["api_requests"] == (2 if failure_stage == "semantic" else 1)
    if failure_stage == "semantic":
        assert error["code"] == "SEMANTIC_PHYSICAL_VALUE_INVALID"
        assert calls[-1]["call_reason"] == "contract_retry"
        assert calls[-1]["error_code"] == error["code"]
    else:
        assert error["code"] == "CANONICAL_MODEL_INVALID"
    assert (await client.get(summary["readable_log_url"])).status_code == 200
    await client.aclose()


@pytest.mark.asyncio
async def test_translate_stops_for_unmapped_content_without_fabricating_target(
    registry: OntologyRegistry,
) -> None:
    provider = APIWellProvider()
    client = _configured_client(registry, provider)

    response = await client.post(
        "/translate",
        json={"source": "XYZ_COEFF = 12.5", "target_platform": "eclipse"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "review_required"
    assert payload["semantic_mapping"]["mappings"][0]["status"] == "UNMAPPED"
    assert payload["canonical_model"] is None
    assert payload["target"] is None
    assert provider.calls == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_translate_stops_for_low_confidence_mapping(
    registry: OntologyRegistry,
) -> None:
    client = _configured_client(registry, LowConfidenceWellProvider())

    response = await client.post(
        "/translate",
        json={
            "source": "A15井采用定液生产制度，日产液控制在500方，井底流压不得低于80 bar。",
            "source_system": "client_c",
            "target_platform": "eclipse",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "review_required"
    assert payload["canonical_model"] is None
    assert payload["trace"][-1]["stage"] == "review"
    await client.aclose()


@pytest.mark.asyncio
async def test_semantic_endpoint_reports_unconfigured_provider(
    registry: OntologyRegistry,
) -> None:
    client = _configured_client(registry)
    document = {
        "source_id": "demo",
        "source_type": "txt",
        "file_name": "demo.txt",
        "blocks": [
            {
                "block_id": "block_0001",
                "block_type": "text",
                "content": "simulation duration 5 year",
                "source_location": "line 1",
            }
        ],
    }

    response = await client.post("/semantic-map", json={"document": document})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SEMANTIC_PROVIDER_NOT_CONFIGURED"
    await client.aclose()


@pytest.mark.asyncio
async def test_semantic_endpoint_returns_safe_provider_error(
    registry: OntologyRegistry,
) -> None:
    client = _configured_client(registry, FailingSemanticProvider())
    document = {
        "source_id": "demo",
        "source_type": "txt",
        "file_name": "demo.txt",
        "blocks": [
            {
                "block_id": "block_0001",
                "block_type": "text",
                "content": "simulation duration 5 year",
                "source_location": "line 1",
            }
        ],
    }

    response = await client.post("/semantic-map", json={"document": document})

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "DEEPSEEK_TIMEOUT",
        "message": "DeepSeek request timed out.",
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_openapi_exposes_all_six_required_routes(
    registry: OntologyRegistry,
    canonical_demo: ReservoirSimulationModel,
) -> None:
    client = _configured_client(registry, APIWellProvider())
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert {
        "/ingest",
        "/semantic-map",
        "/canonical/build",
        "/validate",
        "/export/{platform}",
        "/translate",
    } <= set(paths)
    await client.aclose()
