"""DeepSeek Responses API adapter for strict semantic structured output."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any

import httpx
from pydantic import ValidationError

from .provider import ResponseModel, SemanticModelProvider, SemanticProviderError


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
_SCHEMA_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True, slots=True)
class DeepSeekCallTrace:
    """Non-secret call evidence suitable for smoke-test artifacts."""

    request_id: str | None
    requested_model: str
    response_model: str | None
    status: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class DeepSeekProvider(SemanticModelProvider):
    """Generate Pydantic-compatible data through DeepSeek's Responses API.

    The adapter sends the supplied Pydantic JSON Schema to DeepSeek, then
    independently parses and validates the returned JSON before it reaches the
    semantic agent. Prompts, source content, and credentials are never included
    in provider error messages or call traces.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_DEEPSEEK_MODEL,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        timeout_seconds: float = 120.0,
        max_output_tokens: int = 32_768,
        reasoning_effort: str = "none",
        max_retries: int = 1,
        max_output_retries: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
        trace_sink: Callable[[DeepSeekCallTrace], None] | None = None,
    ) -> None:
        key = _validated_api_key(api_key)
        if not model.strip():
            raise ValueError("model must be a non-empty string")
        if not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        if reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("reasoning_effort is not supported by the Responses API")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if max_output_retries < 0:
            raise ValueError("max_output_retries must be non-negative")

        self._api_key = key
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.max_retries = max_retries
        self.max_output_retries = max_output_retries
        self._transport = transport
        self._trace_sink = trace_sink

    @property
    def provider_name(self) -> str:
        return f"deepseek:{self.model}"

    @classmethod
    def from_environment(
        cls,
        *,
        api_key_file: str | Path | None = None,
        **overrides: Any,
    ) -> "DeepSeekProvider":
        """Load environment-first configuration without exposing the key."""

        key = os.getenv("DEEPSEEK_API_KEY")
        configured_file = os.getenv("DEEPSEEK_API_KEY_FILE")
        key_path = Path(configured_file) if configured_file else api_key_file
        if not key and key_path is not None:
            try:
                key = Path(key_path).read_text(encoding="utf-8")
            except OSError as exc:
                raise SemanticProviderError(
                    "DEEPSEEK_CREDENTIAL_UNAVAILABLE",
                    f"DeepSeek API key file could not be read: {Path(key_path)}",
                ) from exc
        if not key:
            raise SemanticProviderError(
                "DEEPSEEK_CREDENTIAL_UNAVAILABLE",
                "Set DEEPSEEK_API_KEY or DEEPSEEK_API_KEY_FILE.",
            )

        configuration: dict[str, Any] = {
            "model": os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
            "base_url": os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
        }
        if timeout := os.getenv("DEEPSEEK_TIMEOUT_SECONDS"):
            try:
                configuration["timeout_seconds"] = float(timeout)
            except ValueError as exc:
                raise SemanticProviderError(
                    "DEEPSEEK_CONFIGURATION_INVALID",
                    "DEEPSEEK_TIMEOUT_SECONDS must be numeric.",
                ) from exc
        configuration.update(overrides)
        try:
            return cls(key, **configuration)
        except ValueError as exc:
            raise SemanticProviderError(
                "DEEPSEEK_CONFIGURATION_INVALID",
                f"Invalid DeepSeek provider configuration: {exc}",
            ) from exc

    async def structured_generate(
        self,
        prompt: str,
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        if not prompt.strip():
            raise ValueError("prompt must be a non-empty string")

        schema_name = _SCHEMA_NAME_PATTERN.sub("_", response_model.__name__)[:128]
        request_body: dict[str, Any] = {
            "model": self.model,
            "instructions": (
                "Return exactly one JSON value conforming to the supplied JSON "
                "Schema. Do not add Markdown or explanatory prose."
            ),
            "input": prompt,
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": response_model.model_json_schema(),
                }
            },
        }
        if self.reasoning_effort == "none":
            request_body["temperature"] = 0

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "reservoir-data-translator/0.1",
        }
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            for attempt in range(self.max_output_retries + 1):
                response = await self._post_with_retry(client, headers, request_body)
                payload = _response_json(response)
                self._emit_trace(payload)
                try:
                    return self._validated_output(payload, response_model)
                except SemanticProviderError as exc:
                    retryable = exc.code in {
                        "DEEPSEEK_INVALID_JSON",
                        "DEEPSEEK_MISSING_OUTPUT",
                        "DEEPSEEK_SCHEMA_MISMATCH",
                    }
                    if not retryable or attempt >= self.max_output_retries:
                        raise
                    await asyncio.sleep(0.25 * (attempt + 1))
        raise AssertionError("DeepSeek output retry loop exhausted")

    @staticmethod
    def _validated_output(
        payload: Mapping[str, Any],
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        status = payload.get("status")
        if status != "completed":
            raise SemanticProviderError(
                "DEEPSEEK_RESPONSE_INCOMPLETE",
                f"DeepSeek response ended with status {status!r}.",
            )
        output_text = _extract_output_text(payload)
        try:
            structured = _decode_structured_json(output_text)
        except json.JSONDecodeError as exc:
            raise SemanticProviderError(
                "DEEPSEEK_INVALID_JSON",
                "DeepSeek returned output that was not valid JSON.",
            ) from exc
        try:
            result = response_model.model_validate(structured)
        except ValidationError as exc:
            raise SemanticProviderError(
                "DEEPSEEK_SCHEMA_MISMATCH",
                "DeepSeek JSON did not satisfy the requested response model.",
            ) from exc

        return result

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
    ) -> httpx.Response:
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                response = await client.post(
                    f"{self.base_url}/responses",
                    headers=headers,
                    json=request_body,
                )
            except httpx.TimeoutException as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                raise SemanticProviderError(
                    "DEEPSEEK_TIMEOUT",
                    "DeepSeek request timed out.",
                ) from exc
            except httpx.RequestError as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                raise SemanticProviderError(
                    "DEEPSEEK_CONNECTION_ERROR",
                    "DeepSeek request could not be completed.",
                ) from exc

            if response.is_success:
                return response
            if response.status_code in {408, 409, 429} or response.status_code >= 500:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
            raise SemanticProviderError(
                "DEEPSEEK_API_ERROR",
                f"DeepSeek API returned HTTP {response.status_code}.",
            )
        raise AssertionError("DeepSeek retry loop exhausted without a result")

    def _emit_trace(self, payload: Mapping[str, Any]) -> None:
        if self._trace_sink is None:
            return
        usage = payload.get("usage")
        if not isinstance(usage, Mapping):
            usage = {}
        self._trace_sink(
            DeepSeekCallTrace(
                request_id=_optional_string(payload.get("id")),
                requested_model=self.model,
                response_model=_optional_string(payload.get("model")),
                status=str(payload.get("status", "unknown")),
                input_tokens=_optional_int(usage.get("input_tokens")),
                output_tokens=_optional_int(usage.get("output_tokens")),
                total_tokens=_optional_int(usage.get("total_tokens")),
            )
        )


def _validated_api_key(api_key: str) -> str:
    key = api_key.strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError("api_key must be a non-empty token without whitespace")
    return key


def _response_json(response: httpx.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SemanticProviderError(
            "DEEPSEEK_INVALID_RESPONSE",
            "DeepSeek returned a non-JSON API response.",
        ) from exc
    if not isinstance(payload, Mapping):
        raise SemanticProviderError(
            "DEEPSEEK_INVALID_RESPONSE",
            "DeepSeek returned an invalid API response object.",
        )
    return payload


def _extract_output_text(payload: Mapping[str, Any]) -> str:
    output = payload.get("output")
    if not isinstance(output, list):
        raise SemanticProviderError(
            "DEEPSEEK_MISSING_OUTPUT",
            "DeepSeek response did not contain output items.",
        )
    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if (
                isinstance(part, Mapping)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            ):
                text_parts.append(part["text"])
    output_text = "".join(text_parts).strip()
    if not output_text:
        raise SemanticProviderError(
            "DEEPSEEK_MISSING_OUTPUT",
            "DeepSeek response did not contain structured output text.",
        )
    return output_text


def _decode_structured_json(output_text: str) -> Any:
    """Decode raw JSON or one conventional Markdown JSON code fence."""

    try:
        return json.loads(output_text)
    except json.JSONDecodeError as original_error:
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```",
            output_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced is None:
            raise original_error
        return json.loads(fenced.group(1))


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
