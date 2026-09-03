"""FastAPI exposure for each stage and the complete translation pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Iterable
import unicodedata
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from reservoir_data_translator.canonical import (
    CanonicalBuildError,
    ReservoirSimulationModel,
)
from reservoir_data_translator.ingestion import IngestionError, RawDocument
from reservoir_data_translator.mappers import (
    CMGDemoMapper,
    EclipseDemoMapper,
    PlatformMapper,
    PlatformMappingError,
    PlatformMappingRegistry,
)
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.semantic import (
    DeepSeekCallTrace,
    DeepSeekProvider,
    SemanticAgentContractError,
    SemanticMappingBatch,
    SemanticModelProvider,
    SemanticProviderError,
    SourceMappingRegistry,
    capture_deepseek_traces,
)
from reservoir_data_translator.validation import ValidationResult

from .models import (
    CanonicalBuildRequest,
    DeepSeekTraceSummary,
    ExportRequest,
    ExportResponse,
    SemanticMapRequest,
    SourceInput,
    TargetArtifact,
    TranslateRequest,
    TranslateResult,
    TranslationTraceEvent,
    ValidateRequest,
)
from .service import (
    PipelineServices,
    SemanticProviderNotConfigured,
    UnknownSourceSystemError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_ROOT = Path(__file__).resolve().parent.parent / "ui"
DEFAULT_TRACE_ROOT = PROJECT_ROOT / "artifacts" / "deepseek_traces"


def _configured_path(environment_name: str, default_name: str) -> Path | None:
    configured = os.getenv(environment_name)
    candidates = [Path(configured)] if configured else []
    candidates.extend((Path.cwd() / default_name, PROJECT_ROOT / default_name))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _default_services(
    provider: SemanticModelProvider | None = None,
) -> PipelineServices | None:
    ontology_path = _configured_path("RESERVOIR_ONTOLOGY_PATH", "ontology")
    if ontology_path is None:
        return None
    registry = OntologyRegistry.load(ontology_path)
    mapping_path = _configured_path("RESERVOIR_MAPPING_PATH", "mappings")
    mappers: list[PlatformMapper] = []
    source_mappings: list[SourceMappingRegistry] = []
    if mapping_path is not None:
        eclipse_path = mapping_path / "eclipse.yaml"
        cmg_path = mapping_path / "cmg.yaml"
        if eclipse_path.is_file():
            mappers.append(
                EclipseDemoMapper(PlatformMappingRegistry.load(eclipse_path, registry))
            )
        if cmg_path.is_file():
            mappers.append(CMGDemoMapper(PlatformMappingRegistry.load(cmg_path, registry)))
        for customer_path in sorted(mapping_path.glob("customer_*.yaml")):
            source_mappings.append(
                SourceMappingRegistry.load(customer_path, registry)
            )
    return PipelineServices(
        registry,
        provider=provider or _default_semantic_provider(),
        mappers=mappers,
        source_mappings=source_mappings,
    )


def _default_semantic_provider() -> SemanticModelProvider | None:
    provider_name = os.getenv("RESERVOIR_SEMANTIC_PROVIDER", "").strip().casefold()
    if provider_name in {"none", "disabled", "off"}:
        return None
    if provider_name not in {"", "deepseek"}:
        raise SemanticProviderError(
            "SEMANTIC_PROVIDER_UNSUPPORTED",
            f"Unsupported semantic provider {provider_name!r}.",
        )

    key_file = _configured_path("DEEPSEEK_API_KEY_FILE", "LLM/DeepSeek/api_key")
    if not os.getenv("DEEPSEEK_API_KEY") and key_file is None:
        if provider_name == "deepseek":
            raise SemanticProviderError(
                "DEEPSEEK_CREDENTIAL_UNAVAILABLE",
                "Set DEEPSEEK_API_KEY or DEEPSEEK_API_KEY_FILE.",
            )
        return None
    return DeepSeekProvider.from_environment(api_key_file=key_file)


def _trace_root() -> Path:
    configured = os.getenv("DEEPSEEK_TRACE_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_TRACE_ROOT


def _persist_deepseek_trace(
    translation_id: str,
    source: RawDocument,
    calls: list[DeepSeekCallTrace],
    *,
    semantic_status: str,
) -> DeepSeekTraceSummary | None:
    if not calls:
        return None
    trace_root = _trace_root()
    trace_root.mkdir(parents=True, exist_ok=True)
    trace_path = trace_root / f"{translation_id}.json"
    readable_log_path = trace_root / f"{translation_id}.readable.log"
    payload = {
        "translation_id": translation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "semantic_status": semantic_status,
        "source": {
            "source_id": source.source_id,
            "file_name": source.file_name,
            "source_type": source.source_type,
            "block_count": len(source.blocks),
        },
        "summary": {
            "api_requests": len(calls),
            "retry_requests": sum(call.call_reason != "initial" for call in calls),
            "local_corrections": sum(call.local_correction is not None for call in calls),
            "avoided_network_retries": sum(
                call.avoided_network_retry
                and call.outcome == "accepted_after_local_correction"
                for call in calls
            ),
            "input_tokens": sum(call.input_tokens or 0 for call in calls),
            "output_tokens": sum(call.output_tokens or 0 for call in calls),
            "total_tokens": sum(call.total_tokens or 0 for call in calls),
            "duration_ms": round(sum(call.duration_ms for call in calls), 3),
        },
        "calls": [call.model_dump(mode="json") for call in calls],
    }
    temporary_path = trace_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(trace_path)
    temporary_log_path = readable_log_path.with_suffix(".log.tmp")
    temporary_log_path.write_text(
        _readable_deepseek_log(payload),
        encoding="utf-8",
    )
    temporary_log_path.replace(readable_log_path)
    summary = payload["summary"]
    return DeepSeekTraceSummary(
        **summary,
        trace_url=f"/deepseek-traces/{translation_id}",
        readable_log_url=f"/deepseek-traces/{translation_id}/readable",
    )


def _readable_deepseek_log(payload: dict) -> str:
    """Render an observation-only log without JSON escaping or control characters."""

    lines = [
        f"Translation: {_readable_text(payload.get('translation_id'))}",
        f"Created: {_readable_text(payload.get('created_at'))}",
        f"Semantic status: {_readable_text(payload.get('semantic_status'))}",
        "",
    ]
    for index, call in enumerate(payload.get("calls", []), start=1):
        request = call.get("request_payload") or {}
        lines.extend(
            [
                f"Call {index}",
                f"Block: {_readable_text(call.get('source_block_id'))}",
                f"Reason: {_readable_text(call.get('call_reason'))}",
                f"Attempts: output {call.get('logical_attempt')} / network {call.get('transport_attempt')}",
                f"Outcome: {_readable_text(call.get('outcome'))}",
                f"Tokens: input {call.get('input_tokens')} / output {call.get('output_tokens')} / total {call.get('total_tokens')}",
                f"Request instructions: {_readable_text(request.get('instructions'))}",
                f"Request input: {_readable_text(request.get('input'))}",
                f"Response output: {_readable_text(_response_output_text(call.get('response_payload')))}",
            ]
        )
        if call.get("local_correction"):
            lines.append(f"Local correction: {_readable_text(call.get('local_correction'))}")
        if call.get("error_code"):
            lines.append(
                f"Error: {_readable_text(call.get('error_code'))} "
                f"{_readable_text(call.get('error_message'))}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _response_output_text(response_payload: object) -> str:
    if not isinstance(response_payload, dict):
        return ""
    parts: list[str] = []
    for item in response_payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return " ".join(parts)


def _readable_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\\r\\n", " ").replace("\\n", " ").replace("\\r", " ").replace("\\t", " ")
    cleaned: list[str] = []
    for character in text:
        if character == "\\" or character.isspace():
            cleaned.append(" ")
            continue
        category = unicodedata.category(character)
        if category[0] in {"L", "N", "P"} or character in "%°±×÷=<>^":
            cleaned.append(character)
    return " ".join("".join(cleaned).split())


def create_app(
    *,
    registry: OntologyRegistry | None = None,
    provider: SemanticModelProvider | None = None,
    mappers: Iterable[PlatformMapper] | None = None,
    source_mappings: Iterable[SourceMappingRegistry] | None = None,
) -> FastAPI:
    if registry is None and mappers is None and source_mappings is None:
        services = _default_services(provider)
    elif registry is not None:
        services = PipelineServices(
            registry,
            provider=provider,
            mappers=mappers or (),
            source_mappings=source_mappings or (),
        )
    else:
        raise ValueError("registry is required when explicit services are supplied")

    api = FastAPI(
        title="Reservoir Data Translator",
        version="0.1.0",
        description="Ontology-driven staged reservoir data translation PoC.",
    )
    api.state.services = services
    api.mount("/ui", StaticFiles(directory=UI_ROOT), name="ui")

    @api.get("/", include_in_schema=False)
    def workbench() -> FileResponse:
        return FileResponse(UI_ROOT / "index.html")

    @api.get("/ontology", include_in_schema=False)
    def ontology_explorer() -> FileResponse:
        return FileResponse(UI_ROOT / "ontology.html")

    def service() -> PipelineServices:
        configured = api.state.services
        if configured is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "PIPELINE_NOT_CONFIGURED",
                    "message": (
                        "Set RESERVOIR_ONTOLOGY_PATH/RESERVOIR_MAPPING_PATH or "
                        "construct the app with an OntologyRegistry."
                    ),
                },
            )
        return configured

    def ontology_graph_payload() -> dict:
        registry = service().registry
        concepts = registry.list_concepts()
        incoming: dict[str, list[dict[str, str]]] = {
            concept.concept_id: [] for concept in concepts
        }
        edges: list[dict[str, str]] = []
        for concept in concepts:
            if concept.parent is not None:
                edges.append(
                    {
                        "id": f"hierarchy:{concept.parent}:{concept.concept_id}",
                        "source": concept.parent,
                        "target": concept.concept_id,
                        "type": "parent",
                    }
                )
                incoming[concept.concept_id].append(
                    {"source": concept.parent, "type": "parent"}
                )
            for relation, targets in concept.relationships.items():
                for target in targets:
                    edges.append(
                        {
                            "id": f"{relation}:{concept.concept_id}:{target}",
                            "source": concept.concept_id,
                            "target": target,
                            "type": relation,
                        }
                    )
                    incoming[target].append(
                        {"source": concept.concept_id, "type": relation}
                    )

        nodes = []
        for concept in concepts:
            domain = (
                concept.concept_id.split(".", 1)[0]
                if concept.parent is not None
                else concept.concept_id
            )
            nodes.append(
                {
                    "id": concept.concept_id,
                    "label": concept.name,
                    "parent": concept.parent,
                    "domain": domain,
                    "description": concept.description,
                    "value_type": concept.value_type,
                    "dimension": concept.dimension,
                    "canonical_unit": concept.canonical_unit,
                    "aliases": list(concept.aliases),
                    "constraints": dict(concept.constraints),
                    "relationships": {
                        relation: list(targets)
                        for relation, targets in concept.relationships.items()
                    },
                    "incoming_relationships": incoming[concept.concept_id],
                    "source_file": concept.source_file,
                    "status": concept.status,
                    "replaced_by": concept.replaced_by,
                }
            )

        relationship_types = {
            name: {
                "description": rule.description,
                "inverse": rule.inverse,
            }
            for name, rule in registry.convention.relationships.items()
        }
        relationship_types["parent"] = {
            "description": "Concept hierarchy from parent to child.",
            "inverse": None,
        }
        return {
            "ontology": {
                "name": registry.metadata.name,
                "version": registry.metadata.version,
                "namespace": registry.metadata.namespace,
                "domain": registry.metadata.domain,
            },
            "nodes": nodes,
            "edges": edges,
            "relationship_types": relationship_types,
        }

    @api.get("/api/ontology/graph")
    def ontology_graph() -> dict:
        return ontology_graph_payload()

    @api.get("/api/ontology/concepts/{concept_id:path}")
    def ontology_concept(concept_id: str) -> dict:
        payload = ontology_graph_payload()
        for node in payload["nodes"]:
            if node["id"] == concept_id:
                return node
        raise _http_error(404, "ONTOLOGY_CONCEPT_NOT_FOUND", f"Unknown concept {concept_id!r}.")

    @api.post("/ingest", response_model=RawDocument)
    def ingest(request: SourceInput) -> RawDocument:
        try:
            return service().ingest(request)
        except IngestionError as exc:
            raise _http_error(422, exc.code, str(exc)) from exc

    @api.post("/semantic-map", response_model=SemanticMappingBatch)
    async def semantic_map(request: SemanticMapRequest) -> SemanticMappingBatch:
        try:
            return await service().semantic_map(
                request.document,
                source_system=request.source_system,
            )
        except SemanticProviderNotConfigured as exc:
            raise _http_error(503, "SEMANTIC_PROVIDER_NOT_CONFIGURED", str(exc)) from exc
        except SemanticProviderError as exc:
            raise _http_error(502, exc.code, str(exc)) from exc
        except UnknownSourceSystemError as exc:
            raise _http_error(422, "SOURCE_MAPPING_NOT_CONFIGURED", str(exc)) from exc
        except SemanticAgentContractError as exc:
            raise _http_error(502, exc.code, str(exc)) from exc

    @api.post("/canonical/build")
    def canonical_build(request: CanonicalBuildRequest) -> ReservoirSimulationModel:
        try:
            return service().build_canonical(
                request.mappings,
                schema_version=request.schema_version,
            )
        except CanonicalBuildError as exc:
            raise _http_error(422, exc.code, str(exc)) from exc

    @api.post("/validate", response_model=ValidationResult)
    def validate(request: ValidateRequest) -> ValidationResult:
        return service().validation.validate(
            request.canonical_model,
            target_platform=request.target_platform,
        )

    @api.post("/export/{platform}", response_model=ExportResponse)
    def export(platform: str, request: ExportRequest) -> ExportResponse:
        services = service()
        try:
            mapper = services.mapper_registry.get(platform)
        except KeyError as exc:
            raise _http_error(404, "PLATFORM_MAPPER_NOT_CONFIGURED", str(exc)) from exc
        canonical_validation = services.validation.validate(request.canonical_model)
        if not canonical_validation.valid:
            return ExportResponse(
                validation=canonical_validation,
            )
        export_validation = mapper.validate_export(request.canonical_model)
        if not export_validation.valid:
            return ExportResponse(
                validation=canonical_validation,
                export_validation=export_validation,
            )
        result = mapper.export(request.canonical_model)
        return ExportResponse(
            validation=canonical_validation,
            export_validation=result.validation,
            target=TargetArtifact(
                platform=result.platform,
                content=result.content,
                mapped_model=result.mapped_model,
            ),
        )

    @api.get("/deepseek-traces/{translation_id}")
    def deepseek_trace(translation_id: UUID) -> dict:
        trace_path = _trace_root() / f"{translation_id}.json"
        if not trace_path.is_file():
            raise _http_error(
                404,
                "DEEPSEEK_TRACE_NOT_FOUND",
                f"No DeepSeek trace exists for translation {translation_id}.",
            )
        try:
            payload = json.loads(trace_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _http_error(
                500,
                "DEEPSEEK_TRACE_UNAVAILABLE",
                "The stored DeepSeek trace could not be read.",
            ) from exc
        if not isinstance(payload, dict):
            raise _http_error(
                500,
                "DEEPSEEK_TRACE_INVALID",
                "The stored DeepSeek trace is not a JSON object.",
            )
        return payload

    @api.get("/deepseek-traces/{translation_id}/readable", response_class=PlainTextResponse)
    def readable_deepseek_trace(translation_id: UUID) -> PlainTextResponse:
        log_path = _trace_root() / f"{translation_id}.readable.log"
        if not log_path.is_file():
            raise _http_error(
                404,
                "DEEPSEEK_READABLE_LOG_NOT_FOUND",
                f"No readable DeepSeek log exists for translation {translation_id}.",
            )
        try:
            content = log_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise _http_error(
                500,
                "DEEPSEEK_READABLE_LOG_UNAVAILABLE",
                "The stored readable DeepSeek log could not be read.",
            ) from exc
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")

    @api.post("/translate", response_model=TranslateResult)
    async def translate(request: TranslateRequest) -> TranslateResult:
        services = service()
        translation_id = str(uuid4())
        trace: list[TranslationTraceEvent] = []
        try:
            source = services.ingest(request.source)
            trace.append(TranslationTraceEvent(stage="ingest", status="success"))
        except IngestionError as exc:
            raise _http_error(422, exc.code, str(exc)) from exc

        deepseek_calls: list[DeepSeekCallTrace] = []
        semantic_status = "failed"
        deepseek_trace_summary: DeepSeekTraceSummary | None = None
        try:
            with capture_deepseek_traces() as deepseek_calls:
                semantic = await services.semantic_map(
                    source,
                    source_system=request.source_system,
                )
            semantic_status = "success"
            trace.append(
                TranslationTraceEvent(stage="semantic_map", status="success")
            )
        except SemanticProviderNotConfigured as exc:
            raise _http_error(503, "SEMANTIC_PROVIDER_NOT_CONFIGURED", str(exc)) from exc
        except SemanticProviderError as exc:
            raise _http_error(502, exc.code, str(exc)) from exc
        except UnknownSourceSystemError as exc:
            raise _http_error(422, "SOURCE_MAPPING_NOT_CONFIGURED", str(exc)) from exc
        except SemanticAgentContractError as exc:
            raise _http_error(502, exc.code, str(exc)) from exc
        finally:
            deepseek_trace_summary = _persist_deepseek_trace(
                translation_id,
                source,
                deepseek_calls,
                semantic_status=semantic_status,
            )

        if not semantic.mappings or semantic.review_required:
            low_confidence = sum(
                mapping.confidence < 0.80 for mapping in semantic.mapped
            )
            trace.append(
                TranslationTraceEvent(
                    stage="review",
                    status="review_required",
                    detail=(
                        f"{len(semantic.unresolved)} unresolved and "
                        f"{low_confidence} low-confidence mapping outcome(s)."
                    ),
                )
            )
            return TranslateResult(
                translation_id=translation_id,
                status="review_required",
                source=source,
                semantic_mapping=semantic,
                trace=trace,
                deepseek_trace=deepseek_trace_summary,
            )

        try:
            canonical = services.build_canonical(
                semantic.mapped,
                schema_version=request.schema_version,
            )
            trace.append(
                TranslationTraceEvent(stage="canonical_build", status="success")
            )
        except CanonicalBuildError as exc:
            raise _http_error(422, exc.code, str(exc)) from exc

        validation = services.validation.validate(canonical)
        trace.append(
            TranslationTraceEvent(
                stage="validation",
                status="success" if validation.valid else "failed",
            )
        )
        if not validation.valid:
            return TranslateResult(
                translation_id=translation_id,
                status="validation_failed",
                source=source,
                semantic_mapping=semantic,
                canonical_model=canonical,
                validation=validation,
                trace=trace,
                deepseek_trace=deepseek_trace_summary,
            )

        try:
            mapper = services.mapper_registry.get(request.target_platform)
        except KeyError as exc:
            raise _http_error(404, "PLATFORM_MAPPER_NOT_CONFIGURED", str(exc)) from exc
        export_validation = mapper.validate_export(canonical)
        trace.append(
            TranslationTraceEvent(
                stage="export_validation",
                status="success" if export_validation.valid else "failed",
            )
        )
        if not export_validation.valid:
            return TranslateResult(
                translation_id=translation_id,
                status="export_failed",
                source=source,
                semantic_mapping=semantic,
                canonical_model=canonical,
                validation=validation,
                export_validation=export_validation,
                trace=trace,
                deepseek_trace=deepseek_trace_summary,
            )

        try:
            exported = mapper.export(canonical)
        except PlatformMappingError as exc:
            raise _http_error(422, exc.code, str(exc)) from exc
        trace.append(TranslationTraceEvent(stage="render", status="success"))
        return TranslateResult(
            translation_id=translation_id,
            status="success",
            source=source,
            semantic_mapping=semantic,
            canonical_model=canonical,
            validation=validation,
            export_validation=exported.validation,
            target=TargetArtifact(
                platform=exported.platform,
                content=exported.content,
                mapped_model=exported.mapped_model,
            ),
            trace=trace,
            deepseek_trace=deepseek_trace_summary,
        )

    return api


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


app = create_app()
