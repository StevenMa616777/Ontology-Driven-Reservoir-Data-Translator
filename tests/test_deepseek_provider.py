import json

import httpx
import pytest

from reservoir_data_translator.semantic import (
    DeepSeekCallTrace,
    DeepSeekProvider,
    SemanticModelResponse,
    SemanticProviderError,
    capture_deepseek_traces,
)
from reservoir_data_translator.semantic.deepseek import _source_block_id


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


def test_source_block_id_prefers_raw_block_over_document_context() -> None:
    prompt = (
        'INPUT:\n{"document_context":[{"block_id":"block_0001"}],'
        '"raw_block":{"block_id":"block_0003","content":"schedule"}}'
    )

    assert _source_block_id(prompt) == "block_0003"


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
    assert len(traces) == 1
    trace = traces[0]
    assert trace.request_id == "resp-test-123"
    assert trace.requested_model == "deepseek-v4-flash"
    assert trace.response_model == "deepseek-v4-flash"
    assert trace.status == "completed"
    assert trace.call_reason == "initial"
    assert trace.logical_attempt == 1
    assert trace.transport_attempt == 1
    assert trace.http_status == 200
    assert trace.input_tokens == 100
    assert trace.output_tokens == 50
    assert trace.total_tokens == 150
    assert trace.request_payload["input"] == "return JSON"
    assert trace.response_payload["output"]
    assert "Authorization" not in trace.request_payload


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

    with capture_deepseek_traces() as traces:
        result = await provider.structured_generate("return JSON", SemanticModelResponse)

    assert result.mappings[0].status == "MAPPED"
    assert calls == 2
    assert [trace.call_reason for trace in traces] == ["initial", "transport_retry"]
    assert [trace.http_status for trace in traces] == [503, 200]


@pytest.mark.asyncio
async def test_deepseek_provider_retries_invalid_structured_json() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = _success_response()
        if calls == 1:
            output = payload["output"]
            assert isinstance(output, list)
            output[0]["content"][0]["text"] = "{invalid"
        return httpx.Response(200, json=payload)

    provider = DeepSeekProvider(
        "test-api-key",
        max_output_retries=1,
        transport=httpx.MockTransport(handler),
    )

    with capture_deepseek_traces() as traces:
        result = await provider.structured_generate("return JSON", SemanticModelResponse)

    assert result.mappings[0].status == "MAPPED"
    assert calls == 2
    assert [trace.call_reason for trace in traces] == ["initial", "output_retry"]


@pytest.mark.asyncio
async def test_deepseek_provider_accepts_one_json_markdown_fence() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = _success_response()
        output = payload["output"]
        assert isinstance(output, list)
        text = output[0]["content"][0]["text"]
        output[0]["content"][0]["text"] = f"```json\n{text}\n```"
        return httpx.Response(200, json=payload)

    provider = DeepSeekProvider(
        "test-api-key",
        transport=httpx.MockTransport(handler),
    )

    with capture_deepseek_traces() as traces:
        result = await provider.structured_generate("return JSON", SemanticModelResponse)

    assert result.mappings[0].status == "MAPPED"
    assert traces[0].outcome == "accepted_after_local_correction"
    assert traces[0].local_correction == "markdown_fence_removed"
    assert traces[0].avoided_network_retry is True


@pytest.mark.asyncio
async def test_deepseek_provider_extracts_json_from_explanatory_wrapper() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = _success_response()
        output = payload["output"]
        assert isinstance(output, list)
        text = output[0]["content"][0]["text"]
        output[0]["content"][0]["text"] = f"Result follows:\n{text}\nEnd of result."
        return httpx.Response(200, json=payload)

    provider = DeepSeekProvider("test-api-key", transport=httpx.MockTransport(handler))

    with capture_deepseek_traces() as traces:
        result = await provider.structured_generate("return JSON", SemanticModelResponse)

    assert result.mappings[0].status == "MAPPED"
    assert calls == 1
    assert traces[0].local_correction == "json_extracted_from_wrapper"
    assert traces[0].avoided_network_retry is True


@pytest.mark.asyncio
async def test_deepseek_provider_removes_trailing_commas_without_changing_strings() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = _success_response()
        output = payload["output"]
        assert isinstance(output, list)
        text = output[0]["content"][0]["text"]
        text = text.replace("Simulation duration = 5 years", "Simulation duration, } = 5 years")
        output[0]["content"][0]["text"] = text.replace("}]}", "},],}")
        return httpx.Response(200, json=payload)

    provider = DeepSeekProvider("test-api-key", transport=httpx.MockTransport(handler))

    with capture_deepseek_traces() as traces:
        result = await provider.structured_generate("return JSON", SemanticModelResponse)

    assert result.mappings[0].status == "MAPPED"
    assert result.mappings[0].source_text == "Simulation duration, } = 5 years"
    assert traces[0].local_correction == "trailing_commas_removed"


@pytest.mark.asyncio
async def test_deepseek_provider_does_not_choose_between_multiple_json_values() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = _success_response()
        if calls == 1:
            output = payload["output"]
            assert isinstance(output, list)
            output[0]["content"][0]["text"] = '{"first": 1}\n{"second": 2}'
        return httpx.Response(200, json=payload)

    provider = DeepSeekProvider(
        "test-api-key",
        max_output_retries=1,
        transport=httpx.MockTransport(handler),
    )

    with capture_deepseek_traces() as traces:
        result = await provider.structured_generate("return JSON", SemanticModelResponse)

    assert result.mappings[0].status == "MAPPED"
    assert calls == 2
    assert traces[0].outcome == "output_invalid"
    assert traces[0].local_correction is None


@pytest.mark.asyncio
async def test_local_correction_is_not_counted_when_schema_validation_fails() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = _success_response()
        if calls == 1:
            output = payload["output"]
            assert isinstance(output, list)
            output[0]["content"][0]["text"] = "Result:\n{\"wrong_field\": []}\nDone."
        return httpx.Response(200, json=payload)

    provider = DeepSeekProvider(
        "test-api-key",
        max_output_retries=1,
        transport=httpx.MockTransport(handler),
    )

    with capture_deepseek_traces() as traces:
        result = await provider.structured_generate("return JSON", SemanticModelResponse)

    assert result.mappings[0].status == "MAPPED"
    assert calls == 2
    assert traces[0].outcome == "output_invalid"
    assert traces[0].error_code == "DEEPSEEK_SCHEMA_MISMATCH"
    assert traces[0].local_correction is None
    assert traces[0].avoided_network_retry is False


@pytest.mark.asyncio
async def test_local_correction_is_not_counted_after_business_contract_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = _success_response()
        output = payload["output"]
        assert isinstance(output, list)
        text = output[0]["content"][0]["text"]
        output[0]["content"][0]["text"] = f"Result:\n{text}\nDone."
        return httpx.Response(200, json=payload)

    provider = DeepSeekProvider("test-api-key", transport=httpx.MockTransport(handler))

    with capture_deepseek_traces() as traces:
        await provider.structured_generate("return JSON", SemanticModelResponse)
        provider.record_contract_failure("TEST_CONTRACT_FAILURE", "rejected downstream")

    assert traces[0].outcome == "contract_invalid"
    assert traces[0].local_correction is None
    assert traces[0].avoided_network_retry is False


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
