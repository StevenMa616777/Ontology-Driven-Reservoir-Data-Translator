"""FastAPI exposure for each stage and the complete translation pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from fastapi import FastAPI, HTTPException

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
    DeepSeekProvider,
    SemanticAgentContractError,
    SemanticMappingBatch,
    SemanticModelProvider,
    SemanticProviderError,
    SourceMappingRegistry,
)
from reservoir_data_translator.validation import ValidationResult

from .models import (
    CanonicalBuildRequest,
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

        try:
            semantic = await services.semantic_map(
                source,
                source_system=request.source_system,
            )
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
        )

    return api


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


app = create_app()
