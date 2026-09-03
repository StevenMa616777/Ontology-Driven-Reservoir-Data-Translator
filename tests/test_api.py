import base64
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import httpx
from httpx import ASGITransport, AsyncClient
import pytest
from openpyxl import Workbook

from reservoir_data_translator.api import create_app
from reservoir_data_translator.canonical import ReservoirSimulationModel
from reservoir_data_translator.mappers import (
    CMGDemoMapper,
    EclipseDemoMapper,
    PlatformMappingRegistry,
)
from reservoir_data_translator.ontology import OntologyRegistry


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
