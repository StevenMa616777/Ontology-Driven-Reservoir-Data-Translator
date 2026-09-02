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
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert ".mapping-row" in stylesheet.text
    assert ".validation-grid" in stylesheet.text
    assert ".file-summary:hover" in stylesheet.text
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
