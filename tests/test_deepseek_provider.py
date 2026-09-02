import json

import httpx
import pytest

from reservoir_data_translator.semantic import (
    DeepSeekCallTrace,
    DeepSeekProvider,
    SemanticModelResponse,
    SemanticProviderError,
)


def _success_response() -> dict[str, object]:
    return {
        "id": "resp-test-123",
        "object": "response",
        "status": "completed",
        "model": "deepseek-v4-flash",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "mappings": [
                                    {
                                        "status": "MAPPED",
                                        "source_text": "Simulation duration = 5 years",
                                        "source_block_id": "block_0001",
                                        "ontology_concept": "schedule.duration",
                                        "canonical_path": "schedule.duration",
                                        "value": 5,
                                        "source_unit": "year",
                                        "canonical_unit": "day",
                                        "confidence": 0.99,
                                    }
                                ]
                            }
                        ),
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        },
    }


@pytest.mark.asyncio
async def test_deepseek_provider_uses_responses_json_schema_and_validates_output() -> None:
    traces: list[DeepSeekCallTrace] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.deepseek.com/responses"
        assert request.headers["Authorization"] == "Bearer test-api-key"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["reasoning"] == {"effort": "none"}
        assert payload["temperature"] == 0
        output_format = payload["text"]["format"]
        assert output_format["type"] == "json_schema"
        assert output_format["name"] == "SemanticModelResponse"
        assert output_format["schema"]["title"] == "SemanticModelResponse"
        return httpx.Response(200, json=_success_response())

    provider = DeepSeekProvider(
        "test-api-key",
        transport=httpx.MockTransport(handler),
        trace_sink=traces.append,
    )

    result = await provider.structured_generate("return JSON", SemanticModelResponse)

    assert isinstance(result, SemanticModelResponse)
    assert result.mappings[0].ontology_concept == "schedule.duration"
    assert provider.provider_name == "deepseek:deepseek-v4-flash"
    assert traces == [
        DeepSeekCallTrace(
            request_id="resp-test-123",
            requested_model="deepseek-v4-flash",
            response_model="deepseek-v4-flash",
            status="completed",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        )
    ]


@pytest.mark.asyncio
async def test_deepseek_provider_retries_transient_status() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(200, json=_success_response())

    provider = DeepSeekProvider(
        "test-api-key",
        max_retries=1,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.structured_generate("return JSON", SemanticModelResponse)

    assert result.mappings[0].status == "MAPPED"
    assert calls == 2


@pytest.mark.asyncio
async def test_deepseek_provider_errors_do_not_echo_remote_response_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "do-not-expose-remote-details"}},
        )

    provider = DeepSeekProvider(
        "test-api-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SemanticProviderError) as error:
        await provider.structured_generate("return JSON", SemanticModelResponse)

    assert error.value.code == "DEEPSEEK_API_ERROR"
    assert "HTTP 401" in str(error.value)
    assert "do-not-expose" not in str(error.value)


def test_deepseek_provider_loads_environment_before_key_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    key_file = tmp_path / "api_key"
    key_file.write_text("file-key\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    provider = DeepSeekProvider.from_environment(api_key_file=key_file)

    assert provider.model == "deepseek-v4-flash"
    assert provider._api_key == "environment-key"


def test_deepseek_provider_rejects_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY_FILE", raising=False)

    with pytest.raises(SemanticProviderError) as error:
        DeepSeekProvider.from_environment()

    assert error.value.code == "DEEPSEEK_CREDENTIAL_UNAVAILABLE"
