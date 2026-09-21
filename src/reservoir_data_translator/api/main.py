"""FastAPI exposure for each stage and the complete translation pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict
import asyncio
import json
import os
from io import BytesIO
from threading import Lock
from pathlib import Path
from typing import Iterable
import unicodedata
from uuid import UUID, uuid4
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

# PDFium (used by pdfplumber's image renderer) must not run concurrently.
_ocr_render_lock = Lock()

from reservoir_data_translator.canonical import (
    CanonicalBuildError,
    ReservoirSimulationModel,
)
from reservoir_data_translator.ingestion import (
    IngestionError,
    OcrReviewRequired,
    OcrReviewSession,
    PaddleOcrBackend,
    PdfParser,
    RawDocument,
)
from reservoir_data_translator.mappers import (
    CMGDemoMapper,
    EclipseDemoMapper,
    PlatformMapper,
    PlatformMappingError,
    PlatformMappingRegistry,
)
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.ocr_lab import (
    CropLabService,
    create_ocr_lab_router,
)
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
    OcrReviewDecisionRequest,
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
from reservoir_data_translator.ingestion.ocr.artifacts import artifact_root, progress_callback, publish


PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_ROOT = Path(__file__).resolve().parent.parent / "ui"
DEFAULT_TRACE_ROOT = PROJECT_ROOT / "artifacts" / "deepseek_traces"
UI_VERSION = "ocr-lab-v4"


class NoStoreStaticFiles(StaticFiles):
    """Serve development UI assets without leaving stale scripts in the browser."""

    async def get_response(self, path: str, scope: dict):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["X-Reservoir-UI-Version"] = UI_VERSION
        return response


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
    pdf_parser: PdfParser | None = None,
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
        pdf_parser=pdf_parser or _default_pdf_parser(),
    )


def _default_pdf_parser() -> PdfParser:
    backend_name = os.getenv("RESERVOIR_OCR_BACKEND", "paddleocr").strip().casefold()
    if backend_name in {"", "none", "disabled", "off"}:
        return PdfParser()
    if backend_name not in {"paddle", "paddleocr", "ppstructurev3"}:
        raise IngestionError(
            "PDF_OCR_BACKEND_UNSUPPORTED",
            f"Unsupported OCR backend {backend_name!r}.",
        )
    languages = tuple(
        language.strip()
        for language in os.getenv("RESERVOIR_OCR_LANGUAGES", "ch,en").split(",")
        if language.strip()
    )
    minimum_confidence = float(
        os.getenv("RESERVOIR_OCR_MIN_CONFIDENCE", "0.70")
    )
    backend = PaddleOcrBackend(
        lang=os.getenv("RESERVOIR_OCR_LANG") or languages[0],
        device=os.getenv("RESERVOIR_OCR_DEVICE", "gpu:0").strip() or "gpu:0",
        paddlex_config=os.getenv("RESERVOIR_OCR_PADDLEX_CONFIG") or None,
        minimum_confidence=minimum_confidence,
        enable_mkldnn=(
            os.getenv("RESERVOIR_OCR_ENABLE_MKLDNN", "false")
            .strip()
            .casefold()
            in {"1", "true", "yes", "on"}
        ),
        cpu_threads=(
            int(os.environ["RESERVOIR_OCR_CPU_THREADS"])
            if os.getenv("RESERVOIR_OCR_CPU_THREADS")
            else None
        ),
    )
    return PdfParser(
        ocr_backend=backend,
        ocr_render_dpi=int(os.getenv("RESERVOIR_OCR_RENDER_DPI", "300")),
        ocr_max_pixels_per_page=int(
            os.getenv("RESERVOIR_OCR_MAX_PIXELS_PER_PAGE", "40000000")
        ),
        ocr_languages=languages,
        reject_low_confidence_ocr=(
            os.getenv("RESERVOIR_OCR_REJECT_LOW_CONFIDENCE", "true")
            .strip()
            .casefold()
            in {"1", "true", "yes", "on"}
        ),
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
            for detail in call.get("error_details") or []:
                location = detail.get("location") or detail.get("path") or ["<root>"]
                if isinstance(location, list):
                    location = ".".join(str(part) for part in location)
                lines.append(
                    f"Error detail: {_readable_text(location)} "
                    f"{_readable_text(detail.get('type'))} "
                    f"{_readable_text(detail.get('message'))}"
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
    pdf_parser: PdfParser | None = None,
    ocr_lab_service: CropLabService | None = None,
) -> FastAPI:
    if registry is None and mappers is None and source_mappings is None:
        services = _default_services(provider, pdf_parser)
    elif registry is not None:
        services = PipelineServices(
            registry,
            provider=provider,
            mappers=mappers or (),
            source_mappings=source_mappings or (),
            pdf_parser=pdf_parser or _default_pdf_parser(),
        )
    else:
        raise ValueError("registry is required when explicit services are supplied")

    api = FastAPI(
        title="Reservoir Data Translator",
        version="0.1.0",
        description="Ontology-driven staged reservoir data translation PoC.",
    )
    api.state.services = services
    # One running translation per process protects the shared model instance.
    translation_lock = asyncio.Lock()
    crop_lab = ocr_lab_service or CropLabService()
    jobs: dict[str, dict] = {}
    review_sessions: dict[str, tuple[TranslateRequest, OcrReviewSession]] = {}
    background_tasks: set[asyncio.Task] = set()
    api.state.translation_jobs = jobs
    api.state.ocr_lab_service = crop_lab
    api.mount("/ui", NoStoreStaticFiles(directory=UI_ROOT), name="ui")
    api.include_router(
        create_ocr_lab_router(crop_lab, execution_lock=translation_lock)
    )

    @api.get("/", include_in_schema=False)
    def workbench() -> FileResponse:
        return FileResponse(
            UI_ROOT / "index.html",
            headers={"Cache-Control": "no-store, max-age=0", "X-Reservoir-UI-Version": UI_VERSION},
        )

    @api.get("/ontology", include_in_schema=False)
    def ontology_explorer() -> FileResponse:
        return FileResponse(
            UI_ROOT / "ontology.html",
            headers={"Cache-Control": "no-store, max-age=0", "X-Reservoir-UI-Version": UI_VERSION},
        )

    @api.get("/ocr-lab", include_in_schema=False)
    def ocr_lab() -> FileResponse:
        return FileResponse(
            UI_ROOT / "ocr-lab.html",
            headers={"Cache-Control": "no-store, max-age=0", "X-Reservoir-UI-Version": UI_VERSION},
        )

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
    async def ingest(request: SourceInput) -> RawDocument:
        try:
            async with translation_lock:
                return await asyncio.to_thread(service().ingest, request)
        except IngestionError as exc:
            raise _ingestion_http_error(exc) from exc

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

    async def execute_translation(
        request: TranslateRequest, translation_id: str,
        *, source_override: RawDocument | None = None,
        review_summary: dict | None = None,
    ) -> TranslateResult:
        services = service()
        trace: list[TranslationTraceEvent] = []
        try:
            publish(stage="ingest")
            source = (source_override if source_override is not None else
                      await asyncio.to_thread(services.ingest, request.source, review_ocr=True))
            restricted_ocr = any(
                block.extraction_evidence is not None
                and "SOURCE_TEXT_EXTRACTION_RESTRICTED"
                in block.extraction_evidence.quality_flags
                for block in source.blocks
            )
            trace.append(
                TranslationTraceEvent(
                    stage="ingest",
                    status="success",
                    detail=(
                        "PDF text extraction was restricted; pages were rendered "
                        "and processed through OCR automatically."
                        if restricted_ocr else
                        "OCR review decisions applied before semantic mapping."
                        if review_summary else None
                    ),
                )
            )
        except OcrReviewRequired:
            raise
        except IngestionError as exc:
            raise _ingestion_http_error(exc) from exc

        if review_summary is not None:
            trace.append(TranslationTraceEvent(
                stage="ocr_review", status="success",
                detail=(f"{review_summary['included_count']} included, "
                        f"{review_summary['excluded_count']} excluded OCR region(s)."),
            ))

        deepseek_calls: list[DeepSeekCallTrace] = []
        semantic_status = "failed"
        deepseek_trace_summary: DeepSeekTraceSummary | None = None
        try:
            publish(stage="semantic_map")
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
            if deepseek_trace_summary is not None:
                publish(deepseek_trace=deepseek_trace_summary.model_dump(mode="json"))

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
            status="partial" if review_summary and review_summary["excluded_count"] else "success",
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

    async def run_translation(
        request: TranslateRequest, task_id: str, state: dict,
        *, source_override: RawDocument | None = None,
        review_summary: dict | None = None,
    ) -> TranslateResult:
        loop = asyncio.get_running_loop()

        def progress(event: dict) -> None:
            loop.call_soon_threadsafe(state.update, event)

        async with translation_lock:
            state.update(status="running", stage="ingest")
            token = progress_callback.set(progress)
            try:
                result = await execute_translation(
                    request, task_id, source_override=source_override,
                    review_summary=review_summary,
                )
                # Flush page events queued by the ingestion worker before attaching.
                await asyncio.sleep(0)
                return result.model_copy(update={
                    "ocr_intermediate": state.get("ocr_intermediate"),
                    "ocr_review": review_summary,
                })
            except HTTPException as exc:
                # Deliver the semantic-finally event before exposing the failure.
                await asyncio.sleep(0)
                if state.get("deepseek_trace") and isinstance(exc.detail, dict):
                    exc.detail["deepseek_trace"] = state["deepseek_trace"]
                raise
            finally:
                progress_callback.reset(token)

    @api.post("/translate", response_model=TranslateResult)
    async def translate(request: TranslateRequest) -> TranslateResult:
        state: dict = {}
        try:
            return await run_translation(request, str(uuid4()), state)
        except OcrReviewRequired as exc:
            error = _http_error(409, "OCR_REVIEW_REQUIRES_JOB",
                                "OCR 区域需要人工审查；请通过转换任务接口提交以暂停和继续。")
            error.detail["ocr_intermediate"] = exc.session.artifact
            raise error from exc
        except HTTPException as exc:
            if state.get("ocr_intermediate") and isinstance(exc.detail, dict):
                exc.detail["ocr_intermediate"] = state["ocr_intermediate"]
            raise

    @api.post("/translation-jobs", status_code=202)
    async def submit_translation(request: TranslateRequest) -> dict:
        service()  # Configuration failures should be reported before accepting.
        pending = sum(job["status"] in {"queued", "running"} for job in jobs.values())
        if pending >= 8:
            raise _http_error(429, "TRANSLATION_QUEUE_FULL", "翻译任务队列已满，请等待当前任务完成。")
        for key in list(jobs):
            if len(jobs) < 100:
                break
            if jobs[key]["status"] in {"completed", "failed", "stopped"}:
                del jobs[key]
        task_id = str(uuid4())
        state = {"task_id": task_id, "status": "queued", "stage": "queued", "ocr_intermediate": None}
        jobs[task_id] = state

        async def work() -> None:
            try:
                result = await run_translation(request, task_id, state)
                state.update(status="completed", stage="complete", result=result.model_dump(mode="json"))
            except OcrReviewRequired as exc:
                # The OCR worker has finished; release the run lock while a person reviews.
                await asyncio.sleep(0)
                session = exc.session
                review_sessions[task_id] = (request, session)
                source_pdf = Path(session.artifact["local_path"]).with_suffix(".clean-source.pdf")
                state.update(
                    status="ocr_review_required", stage="ocr_review",
                    ocr_intermediate=session.artifact,
                    ocr_review={"items": [
                        {**asdict(issue), "source_image_url": (
                            f"/translation-jobs/{task_id}/ocr-review/{issue.issue_id}/source"
                            if source_pdf.is_file() else None
                        )} for issue in session.issues
                    ]},
                )
            except HTTPException as exc:
                state.update(status="failed", error=exc.detail)
            except asyncio.CancelledError:
                state.update(status="failed", error={"code": "TRANSLATION_INTERRUPTED", "message": "服务停止，翻译任务已中断。"})
                raise
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Translation task failed: %s", task_id)
                state.update(status="failed", error={"code": "TRANSLATION_FAILED", "message": "翻译任务执行失败，请检查服务日志。"})

        task = asyncio.create_task(work())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return {"task_id": task_id, "status_url": f"/translation-jobs/{task_id}"}

    @api.get("/translation-jobs/{task_id}")
    async def translation_status(task_id: UUID) -> dict:
        state = jobs.get(str(task_id))
        if state is None:
            raise _http_error(404, "TRANSLATION_JOB_NOT_FOUND", "任务不存在或服务已重启；已保存的 OCR 文件仍在本地目录中。")
        return state

    def save_ocr_review_decisions(
        task_id: str, session: OcrReviewSession, action: str,
        decisions: dict[str, str],
    ) -> None:
        source_path = Path(session.artifact["local_path"])
        audit_path = source_path.with_suffix(".review.json")
        payload = {
            "task_id": task_id, "artifact_id": session.artifact["artifact_id"],
            "action": action, "decisions": decisions,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "issues": [asdict(issue) for issue in session.issues],
        }
        temporary = audit_path.with_name(audit_path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(audit_path)

    @api.post("/translation-jobs/{task_id}/ocr-review")
    async def decide_ocr_review(task_id: UUID, decision: OcrReviewDecisionRequest) -> dict:
        key = str(task_id)
        state = jobs.get(key)
        pending = review_sessions.get(key)
        if state is None or pending is None or state["status"] != "ocr_review_required":
            raise _http_error(409, "OCR_REVIEW_NOT_PENDING", "此任务当前没有待处理的 OCR 审查。")
        request, session = pending
        expected = {issue.issue_id for issue in session.issues}
        if not set(decision.decisions).issubset(expected):
            raise _http_error(422, "OCR_REVIEW_ITEM_UNKNOWN", "审查决定包含不属于此任务的区域。")
        if decision.action == "continue":
            if set(decision.decisions) != expected:
                raise _http_error(422, "OCR_REVIEW_INCOMPLETE", "请为每个 OCR 问题区域选择带入或排除。")
        try:
            save_ocr_review_decisions(key, session, decision.action, decision.decisions)
        except OSError as exc:
            raise _http_error(500, "OCR_REVIEW_SAVE_FAILED", "无法保存 OCR 审查记录。") from exc
        session.artifact["review_url"] = f"/ocr-intermediates/{session.artifact['artifact_id']}/review"
        if decision.action == "stop":
            review_sessions.pop(key, None)
            state.update(status="stopped", stage="ocr_review", ocr_review={
                **state["ocr_review"], "action": "stop", "decisions": decision.decisions,
            })
            return {"task_id": key, "status": "stopped"}

        summary = {
            "action": "continue", "decisions": decision.decisions,
            "included_count": sum(value == "include" for value in decision.decisions.values()),
            "excluded_count": sum(value == "exclude" for value in decision.decisions.values()),
            "excluded_regions": [asdict(issue) for issue in session.issues
                                 if decision.decisions[issue.issue_id] == "exclude"],
        }
        state.update(status="queued", stage="ocr_review_resuming", ocr_review={
            **state["ocr_review"], "action": "continue", "decisions": decision.decisions,
        })

        async def resume() -> None:
            try:
                parser = service().pdf_parser
                assert parser is not None
                source = await asyncio.to_thread(parser.finalize_ocr_review, session, decision.decisions)
                result = await run_translation(
                    request, key, state, source_override=source, review_summary=summary,
                )
                state.update(status="completed", stage="complete", result=result.model_dump(mode="json"))
            except HTTPException as exc:
                state.update(status="failed", error=exc.detail)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("OCR review continuation failed: %s", key)
                state.update(status="failed", error={
                    "code": "OCR_REVIEW_CONTINUATION_FAILED",
                    "message": "OCR 审查后续处理失败，请检查服务日志。",
                })
            finally:
                review_sessions.pop(key, None)

        task = asyncio.create_task(resume())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return {"task_id": key, "status_url": f"/translation-jobs/{key}"}

    @api.get("/translation-jobs/{task_id}/ocr-review/{issue_id}/source")
    def ocr_review_source(task_id: UUID, issue_id: str) -> Response:
        import pdfplumber

        pending = review_sessions.get(str(task_id))
        issue = next((item for item in pending[1].issues if item.issue_id == issue_id), None) if pending else None
        if issue is None:
            raise _http_error(404, "OCR_REVIEW_ITEM_NOT_FOUND", "OCR 审查区域不存在。")
        source = Path(pending[1].artifact["local_path"]).with_suffix(".clean-source.pdf")
        if not source.is_file():
            raise _http_error(404, "OCR_REVIEW_SOURCE_NOT_FOUND", "源文件区域图像不可用。")
        with _ocr_render_lock, pdfplumber.open(source) as pdf:
            page = pdf.pages[issue.page_index]
            # Use the OCR render DPI recorded with the artifact. This is still a
            # reconstruction from the clean source PDF, so OCR Lab provenance
            # marks it as non-identical to the original in-memory model input.
            resolution = int(pending[1].artifact.get("render_dpi") or 144)
            image = page.to_image(resolution=resolution).original
            scale_x, scale_y = image.width / page.width, image.height / page.height
            x0, top, x1, bottom = issue.bbox
            padding_points = 4.0
            crop_box = (
                max(0, int((x0 - padding_points) * scale_x)),
                max(0, int((top - padding_points) * scale_y)),
                min(image.width, int((x1 + padding_points) * scale_x)),
                min(image.height, int((bottom + padding_points) * scale_y)),
            )
            crop = image.crop(crop_box)
            crop_x0, crop_top, crop_x1, crop_bottom = crop_box
            geometry = {
                "coordinate_space": "pdf_top_left_points",
                "render_dpi": resolution,
                "page_width_points": float(page.width),
                "page_height_points": float(page.height),
                "region_bbox_points": [x0, top, x1, bottom],
                "crop_bbox_points": [
                    crop_x0 / scale_x,
                    crop_top / scale_y,
                    crop_x1 / scale_x,
                    crop_bottom / scale_y,
                ],
                "region_bbox_pixels": [
                    max(0.0, x0 * scale_x - crop_x0),
                    max(0.0, top * scale_y - crop_top),
                    min(float(crop.width), x1 * scale_x - crop_x0),
                    min(float(crop.height), bottom * scale_y - crop_top),
                ],
                "image_width_pixels": crop.width,
                "image_height_pixels": crop.height,
                "padding_points": padding_points,
            }
            buffer = BytesIO()
            crop.save(buffer, format="PNG")
        return Response(
            buffer.getvalue(),
            media_type="image/png",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "X-Reservoir-OCR-Geometry": json.dumps(geometry, separators=(",", ":")),
            },
        )

    def ocr_intermediate(artifact_id: UUID, kind: str, download: bool = False) -> Response:
        if kind not in {"pdf", "raw", "review", "source-regions", "clean", "clean-source"}:
            raise _http_error(404, "OCR_ARTIFACT_NOT_FOUND", "OCR 文件不存在。")
        root = (services.pdf_parser.ocr_artifact_dir if services and services.pdf_parser else None) or artifact_root()
        root = Path(root).resolve()
        for manifest in root.glob("*.manifest.json"):
            try:
                metadata = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if metadata.get("artifact_id") != str(artifact_id):
                continue
            # Resolve only known local files, never client-provided filesystem paths.
            stem = manifest.name.removesuffix(".manifest.json")
            suffix = {"pdf": ".pdf", "raw": ".raw.json", "review": ".review.json", "source-regions": ".source-regions.pdf",
                      "clean": ".clean.pdf", "clean-source": ".clean-source.pdf"}[kind]
            path = (root / (stem + suffix)).resolve()
            if path.parent != root or not path.is_file():
                break
            if kind in {"raw", "review"}:
                # Format legacy compact artifacts too, without rewriting evidence.
                payload = json.loads(path.read_text(encoding="utf-8"))
                return Response(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    media_type="application/json",
                    headers={"Cache-Control": "no-store", "Content-Disposition":
                             f"attachment; filename*=utf-8''{quote(path.name)}"},
                )
            return FileResponse(path, media_type="application/pdf", filename=path.name, content_disposition_type="attachment" if download else "inline", headers={"Cache-Control": "no-store"})
        raise _http_error(404, "OCR_ARTIFACT_NOT_FOUND", "OCR 文件尚未生成或不存在。")

    @api.get("/ocr-intermediates/{artifact_id}/comparison")
    def ocr_comparison(artifact_id: UUID) -> dict:
        import pdfplumber

        result = ocr_intermediate(artifact_id, "pdf")
        source = ocr_intermediate(artifact_id, "source-regions")
        result_path = Path(result.path)
        comparison_path = result_path.with_name(result_path.name.removesuffix(".pdf") + ".comparison.json")
        interactive = comparison_path.is_file()
        if interactive:
            try:
                comparison_pages = json.loads(comparison_path.read_text(encoding="utf-8"))["pages"]
                clean_result = ocr_intermediate(artifact_id, "clean")
                clean_source = ocr_intermediate(artifact_id, "clean-source")
                result, source = clean_result, clean_source
            except (OSError, ValueError, KeyError, HTTPException):
                interactive = False
        with pdfplumber.open(result.path) as pdf, pdfplumber.open(source.path) as original:
            if len(pdf.pages) != len(original.pages) or (interactive and len(pdf.pages) != len(comparison_pages)):
                raise _http_error(409, "OCR_COMPARISON_PAGE_MISMATCH", "两份 PDF 页数不一致。")
            return {"interactive": interactive, "pages": [
                {"index": i, "width": page.width, "height": page.height,
                 "source_url": f"/ocr-intermediates/{artifact_id}/{'clean-source' if interactive else 'source-regions'}/pages/{i}",
                 "result_url": f"/ocr-intermediates/{artifact_id}/{'clean' if interactive else 'pdf'}/pages/{i}",
                 "regions": comparison_pages[i - 1]["regions"] if interactive else [],
                 "source_page": comparison_pages[i - 1]["source_page"] if interactive else i}
                for i, page in enumerate(pdf.pages, 1)]}

    @api.get("/ocr-intermediates/{artifact_id}/{kind}/pages/{page_number}")
    def ocr_comparison_page(artifact_id: UUID, kind: str, page_number: int) -> Response:
        import pdfplumber

        if kind not in {"pdf", "source-regions", "clean", "clean-source"}:
            raise _http_error(404, "OCR_ARTIFACT_NOT_FOUND", "OCR 文件不存在。")
        artifact = ocr_intermediate(artifact_id, kind)
        with _ocr_render_lock, pdfplumber.open(artifact.path) as pdf:
            if not 1 <= page_number <= len(pdf.pages):
                raise _http_error(404, "OCR_ARTIFACT_NOT_FOUND", "OCR 页面不存在。")
            page = pdf.pages[page_number - 1]
            dpi = min(144, 72 * (6_000_000 / (page.width * page.height)) ** 0.5)
            rendered = page.to_image(resolution=dpi)
            try:
                stream = BytesIO()
                rendered.original.save(stream, format="PNG")
                return Response(stream.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})
            finally:
                rendered.original.close()
                rendered.annotated.close()

    api.get("/ocr-intermediates/{artifact_id}/{kind}")(ocr_intermediate)
    return api


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _ingestion_http_error(error: IngestionError) -> HTTPException:
    detail: dict[str, object] = {
        "code": error.code,
        "message": str(error),
        "stage": error.stage or "ingest",
        "stage_label": error.stage_label or "文件解析",
    }
    if error.resolution:
        detail["resolution"] = error.resolution
    if error.details:
        detail["context"] = error.details
    return HTTPException(status_code=422, detail=detail)


app = create_app()
