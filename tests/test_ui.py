from httpx import ASGITransport, AsyncClient
import pytest

from reservoir_data_translator.api import create_app
from reservoir_data_translator.ontology import OntologyRegistry


@pytest.mark.asyncio
async def test_workbench_root_serves_design_pipeline(
    registry: OntologyRegistry,
) -> None:
    app = create_app(registry=registry)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Reservoir Translator Workbench" in response.text
    assert "Source" in response.text
    assert "Semantic" in response.text
    assert "Review" in response.text
    assert "Canonical" in response.text
    assert "Target" in response.text
    assert 'id="source-input"' in response.text
    assert 'id="run-button"' in response.text
    await client.aclose()


@pytest.mark.asyncio
async def test_workbench_assets_expose_real_pipeline_and_review_gate(
    registry: OntologyRegistry,
) -> None:
    app = create_app(registry=registry)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    javascript = await client.get("/ui/app.js")
    stylesheet = await client.get("/ui/styles.css")

    assert javascript.status_code == 200
    assert javascript.headers["content-type"].startswith("text/javascript")
    assert 'postJson("/translate"' in javascript.text
    assert 'mapping.confidence < 0.80' in javascript.text
    assert 'mapping.status !== "MAPPED"' in javascript.text
    assert "function openSelectedFile()" in javascript.text
    assert 'txt: "text/plain;charset=utf-8"' in javascript.text
    assert "URL.createObjectURL(previewFile)" in javascript.text
    assert 'fileSummary.addEventListener("click"' in javascript.text
    assert 'data-toggle="deepseek-trace"' in javascript.text
    assert "renderDeepSeekTraceDetail" in javascript.text
    assert "call.request_payload" in javascript.text
    assert "call.response_payload" in javascript.text
    assert "本地更正通过" in javascript.text
    assert "避免网络重试" in javascript.text
    assert "call.local_correction" in javascript.text
    assert "输出尝试 / 网络尝试" in javascript.text
    assert "readable_log_url" in javascript.text
    assert "查看去除转义与特殊字符的易读日志" in javascript.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert ".readable-text body { zoom: 1.1; }" in stylesheet.text
    assert ".mapping-row" in stylesheet.text
    assert ".validation-grid" in stylesheet.text
    assert ".file-summary:hover" in stylesheet.text
    assert ".deepseek-trace-table" in stylesheet.text
    await client.aclose()


@pytest.mark.asyncio
async def test_workbench_route_is_not_added_to_api_schema(
    registry: OntologyRegistry,
) -> None:
    app = create_app(registry=registry)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/" not in paths
    assert "/ui/{path}" not in paths
    await client.aclose()


@pytest.mark.asyncio
async def test_ontology_explorer_is_a_separate_page_with_interactive_assets(
    registry: OntologyRegistry,
) -> None:
    app = create_app(registry=registry)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    page = await client.get("/ontology")
    javascript = await client.get("/ui/ontology.js")
    stylesheet = await client.get("/ui/ontology.css")
    preferences = await client.get("/ui/ui-preferences.js")

    assert page.status_code == 200
    assert "Ontology Explorer" in page.text
    assert 'id="ontology-graph"' in page.text
    assert 'id="concept-search"' in page.text
    assert 'id="concept-detail"' in page.text
    assert 'href="/"' in page.text
    assert javascript.status_code == 200
    assert 'fetch("/api/ontology/graph")' in javascript.text
    assert "function neighborhood(id)" in javascript.text
    assert "function fitGraph()" in javascript.text
    assert stylesheet.status_code == 200
    assert ".explorer-shell" in stylesheet.text
    assert ".graph-node.is-selected" in stylesheet.text
    assert ".readable-text .detail-description" in stylesheet.text
    assert preferences.status_code == 200
    assert "reservoir-translator-readable-text" in preferences.text
    assert "localStorage.setItem" in preferences.text
    await client.aclose()


@pytest.mark.asyncio
async def test_ontology_explorer_route_is_not_added_to_api_schema(
    registry: OntologyRegistry,
) -> None:
    app = create_app(registry=registry)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/ontology" not in paths
    assert "/api/ontology/graph" in paths
    assert "/api/ontology/concepts/{concept_id}" in paths
    await client.aclose()
