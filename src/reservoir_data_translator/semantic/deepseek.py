"""DeepSeek Responses API adapter for strict semantic structured output."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from .provider import ResponseModel, SemanticModelProvider, SemanticProviderError


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
_SCHEMA_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")
_RAW_BLOCK_PATTERN = re.compile(
    r'"raw_block"\s*:\s*\{.*?"block_id"\s*:\s*"([^"]+)"',
    flags=re.DOTALL,
)
_SOURCE_BLOCK_PATTERN = re.compile(r'"block_id"\s*:\s*"([^"]+)"')


class DeepSeekCallTrace(BaseModel):
    """One actual DeepSeek HTTP request, including auditable content and usage."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    call_id: str
    source_block_id: str | None
    call_reason: str
    logical_attempt: int
    transport_attempt: int
    started_at: str
    duration_ms: float
    request_id: str | None
    requested_model: str
    response_model: str | None
    status: str
    http_status: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    request_payload: dict[str, Any]
    response_payload: Any = None
    outcome: str
    local_correction: str | None = None
    avoided_network_retry: bool = False
    error_code: str | None = None
    error_message: str | None = None


_TRACE_COLLECTOR: ContextVar[list[DeepSeekCallTrace] | None] = ContextVar(
    "deepseek_trace_collector",
    default=None,
)


@contextmanager
def capture_deepseek_traces():
    """Collect traces for one async request without mixing concurrent requests."""

    traces: list[DeepSeekCallTrace] = []
    token = _TRACE_COLLECTOR.set(traces)
    try:
        yield traces
    finally:
        _TRACE_COLLECTOR.reset(token)


class DeepSeekProvider(SemanticModelProvider):
    """Generate Pydantic-compatible data through DeepSeek's Responses API.

    The adapter sends the supplied Pydantic JSON Schema to DeepSeek, then
    independently parses and validates the returned JSON before it reaches the
    semantic agent. Trace records deliberately include request/response content
    for local auditing, but never include credentials or authorization headers.
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
        source_block_id = _source_block_id(prompt)
        base_reason = "contract_retry" if "CORRECTION REQUIRED:" in prompt else "initial"
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            for output_attempt in range(self.max_output_retries + 1):
                call_reason = base_reason if output_attempt == 0 else "output_retry"
                response = await self._post_with_retry(
                    client,
                    headers,
                    request_body,
                    source_block_id=source_block_id,
                    call_reason=call_reason,
                    logical_attempt=output_attempt + 1,
                )
                payload = _response_json(response)
                try:
                    result, local_correction = self._validated_output(payload, response_model)
                    self._mark_latest_trace(
                        outcome=(
                            "accepted_after_local_correction"
                            if local_correction is not None
                            else "accepted"
                        ),
                        local_correction=local_correction,
                        avoided_network_retry=local_correction is not None,
                    )
                    return result
                except SemanticProviderError as exc:
                    self._mark_latest_trace(
                        outcome="output_invalid",
                        error_code=exc.code,
                        error_message=str(exc),
                    )
                    retryable = exc.code in {
                        "DEEPSEEK_INVALID_JSON",
                        "DEEPSEEK_MISSING_OUTPUT",
                        "DEEPSEEK_SCHEMA_MISMATCH",
                    }
                    if not retryable or output_attempt >= self.max_output_retries:
                        raise
                    await asyncio.sleep(0.25 * (output_attempt + 1))
        raise AssertionError("DeepSeek output retry loop exhausted")

    @staticmethod
    def _validated_output(
        payload: Mapping[str, Any],
        response_model: type[ResponseModel],
    ) -> tuple[ResponseModel, str | None]:
        status = payload.get("status")
        if status != "completed":
            raise SemanticProviderError(
                "DEEPSEEK_RESPONSE_INCOMPLETE",
                f"DeepSeek response ended with status {status!r}.",
            )
        output_text = _extract_output_text(payload)
        try:
            structured, local_correction = _decode_structured_json(output_text)
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

        return result, local_correction

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
        *,
        source_block_id: str | None,
        call_reason: str,
        logical_attempt: int,
    ) -> httpx.Response:
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            started_at = datetime.now(timezone.utc).isoformat()
            started = perf_counter()
            try:
                response = await client.post(
                    f"{self.base_url}/responses",
                    headers=headers,
                    json=request_body,
                )
            except httpx.TimeoutException as exc:
                self._emit_trace(
                    source_block_id=source_block_id,
                    call_reason=(call_reason if attempt == 0 else "transport_retry"),
                    logical_attempt=logical_attempt,
                    transport_attempt=attempt + 1,
                    started_at=started_at,
                    duration_ms=(perf_counter() - started) * 1000,
                    request_payload=request_body,
                    error_code="DEEPSEEK_TIMEOUT",
                    error_message="DeepSeek request timed out.",
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                raise SemanticProviderError(
                    "DEEPSEEK_TIMEOUT",
                    "DeepSeek request timed out.",
                ) from exc
            except httpx.RequestError as exc:
                self._emit_trace(
                    source_block_id=source_block_id,
                    call_reason=(call_reason if attempt == 0 else "transport_retry"),
                    logical_attempt=logical_attempt,
                    transport_attempt=attempt + 1,
                    started_at=started_at,
                    duration_ms=(perf_counter() - started) * 1000,
                    request_payload=request_body,
                    error_code="DEEPSEEK_CONNECTION_ERROR",
                    error_message="DeepSeek request could not be completed.",
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                raise SemanticProviderError(
                    "DEEPSEEK_CONNECTION_ERROR",
                    "DeepSeek request could not be completed.",
                ) from exc

            payload = _trace_response_payload(response)
            self._emit_trace(
                source_block_id=source_block_id,
                call_reason=(call_reason if attempt == 0 else "transport_retry"),
                logical_attempt=logical_attempt,
                transport_attempt=attempt + 1,
                started_at=started_at,
                duration_ms=(perf_counter() - started) * 1000,
                request_payload=request_body,
                response=response,
                response_payload=payload,
                error_code=None if response.is_success else "DEEPSEEK_API_ERROR",
                error_message=None if response.is_success else f"DeepSeek API returned HTTP {response.status_code}.",
            )
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

    def _emit_trace(
        self,
        *,
        source_block_id: str | None,
        call_reason: str,
        logical_attempt: int,
        transport_attempt: int,
        started_at: str,
        duration_ms: float,
        request_payload: Mapping[str, Any],
        response: httpx.Response | None = None,
        response_payload: Any = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        payload = response_payload if isinstance(response_payload, Mapping) else {}
        usage = payload.get("usage")
        if not isinstance(usage, Mapping):
            usage = {}
        trace = DeepSeekCallTrace(
            call_id=str(uuid4()),
            source_block_id=source_block_id,
            call_reason=call_reason,
            logical_attempt=logical_attempt,
            transport_attempt=transport_attempt,
            started_at=started_at,
            duration_ms=round(duration_ms, 3),
            request_id=_optional_string(payload.get("id")),
            requested_model=self.model,
            response_model=_optional_string(payload.get("model")),
            status=str(payload.get("status", error_code or "unknown")),
            http_status=response.status_code if response is not None else None,
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
            total_tokens=_optional_int(usage.get("total_tokens")),
            request_payload=dict(request_payload),
            response_payload=response_payload,
            outcome=(
                "accepted_pending_validation"
                if error_code is None
                else "http_error" if response is not None else "network_error"
            ),
            error_code=error_code,
            error_message=error_message,
        )
        collector = _TRACE_COLLECTOR.get()
        if collector is not None:
            collector.append(trace)
        if self._trace_sink is not None:
            self._trace_sink(trace)

    def _mark_latest_trace(
        self,
        *,
        outcome: str,
        local_correction: str | None = None,
        avoided_network_retry: bool = False,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        collector = _TRACE_COLLECTOR.get()
        if not collector:
            return
        trace = collector[-1]
        trace.outcome = outcome
        trace.local_correction = local_correction
        trace.avoided_network_retry = avoided_network_retry
        trace.error_code = error_code
        trace.error_message = error_message

    def record_contract_failure(self, code: str, message: str) -> None:
        self._mark_latest_trace(
            outcome="contract_invalid",
            error_code=code,
            error_message=message,
        )


def _validated_api_key(api_key: str) -> str:
    key = api_key.strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError("api_key must be a non-empty token without whitespace")
    return key


def _source_block_id(prompt: str) -> str | None:
    match = _RAW_BLOCK_PATTERN.search(prompt) or _SOURCE_BLOCK_PATTERN.search(prompt)
    return match.group(1) if match is not None else None


def _trace_response_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw_text": response.text}


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


def _decode_structured_json(output_text: str) -> tuple[Any, str | None]:
    """Decode JSON with conservative, auditable, syntax-only corrections."""

    try:
        return json.loads(output_text), None
    except json.JSONDecodeError as original_error:
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```",
            output_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced is not None:
            try:
                return json.loads(fenced.group(1)), "markdown_fence_removed"
            except json.JSONDecodeError:
                pass

        repaired = _remove_trailing_commas(output_text)
        if repaired != output_text:
            try:
                return json.loads(repaired), "trailing_commas_removed"
            except json.JSONDecodeError:
                pass

        extracted = _extract_single_json_value(output_text)
        if extracted is not None:
            try:
                return json.loads(extracted), "json_extracted_from_wrapper"
            except json.JSONDecodeError:
                repaired = _remove_trailing_commas(extracted)
                if repaired != extracted:
                    return json.loads(repaired), "wrapper_extracted_and_trailing_commas_removed"

        raise original_error


def _extract_single_json_value(text: str) -> str | None:
    """Return one balanced top-level JSON object/array surrounded only by prose."""

    candidates: list[tuple[int, int]] = []
    for start, character in enumerate(text):
        if character not in "{[":
            continue
        end = _balanced_json_end(text, start)
        if end is not None:
            candidates.append((start, end))
    outermost = [
        span
        for span in candidates
        if not any(
            other_start <= span[0]
            and span[1] <= other_end
            and span != (other_start, other_end)
            for other_start, other_end in candidates
        )
    ]
    unique = list(dict.fromkeys(text[start:end] for start, end in outermost))
    if len(unique) != 1:
        return None
    return unique[0]


def _balanced_json_end(text: str, start: int) -> int | None:
    pairs = {"{": "}", "[": "]"}
    stack: list[str] = []
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in pairs:
            stack.append(pairs[character])
        elif character in "}]":
            if not stack or stack.pop() != character:
                return None
            if not stack:
                return index + 1
    return None


def _remove_trailing_commas(text: str) -> str:
    """Remove commas before object/array closers, never touching JSON strings."""

    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        character = text[index]
        if in_string:
            result.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            result.append(character)
            index += 1
            continue
        if character == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        result.append(character)
        index += 1
    return "".join(result)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
