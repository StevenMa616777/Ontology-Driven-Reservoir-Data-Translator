"""Application services shared by FastAPI endpoints and pipeline tests."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
import tempfile
from typing import Iterable

from reservoir_data_translator.canonical import CanonicalBuilder, ReservoirSimulationModel
from reservoir_data_translator.ingestion import IngestionError, RawDocument, parse_document
from reservoir_data_translator.mappers import PlatformMapper, PlatformMapperRegistry
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.semantic import (
    OntologyRetriever,
    SemanticMapping,
    SemanticMappingAgent,
    SemanticMappingBatch,
    SemanticModelProvider,
    SourceMappingRegistry,
)
from reservoir_data_translator.validation import ExportValidator, ValidationEngine

from .models import SourceInput


MAX_SOURCE_BYTES = 16 * 1024 * 1024


class SemanticProviderNotConfigured(RuntimeError):
    pass


class UnknownSourceSystemError(ValueError):
    def __init__(self, source_system: str) -> None:
        self.source_system = source_system
        super().__init__(f"No source mapping is configured for {source_system!r}")


class UnconfiguredSemanticModelProvider(SemanticModelProvider):
    async def structured_generate(self, prompt, response_model):
        raise SemanticProviderNotConfigured(
            "No SemanticModelProvider is configured for this API application."
        )


class PipelineServices:
    def __init__(
        self,
        registry: OntologyRegistry,
        *,
        provider: SemanticModelProvider | None = None,
        mappers: Iterable[PlatformMapper] = (),
        source_mappings: Iterable[SourceMappingRegistry] = (),
    ) -> None:
        mapper_list = tuple(mappers)
        self.registry = registry
        self.provider = provider or UnconfiguredSemanticModelProvider()
        self.mapper_registry = PlatformMapperRegistry(mapper_list)
        self.validation = ValidationEngine(
            registry,
            export_validator=ExportValidator(mapper_list),
        )
        self.builder = CanonicalBuilder(registry)
        self._source_mappings = {
            mapping.source_system.casefold(): mapping for mapping in source_mappings
        }

    def ingest(self, source: SourceInput | str) -> RawDocument:
        source_input = (
            SourceInput(content=source, file_name="source.txt")
            if isinstance(source, str)
            else source
        )
        file_name = Path(source_input.file_name).name
        if file_name != source_input.file_name:
            raise IngestionError(
                "INVALID_FILE_NAME",
                "file_name must not contain directory components",
            )
        if source_input.content_encoding == "base64":
            try:
                payload = base64.b64decode(source_input.content, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise IngestionError(
                    "INVALID_BASE64_SOURCE",
                    "Source content is not valid base64.",
                ) from exc
        else:
            payload = source_input.content.encode("utf-8")
        if len(payload) > MAX_SOURCE_BYTES:
            raise IngestionError(
                "SOURCE_TOO_LARGE",
                f"Source exceeds the {MAX_SOURCE_BYTES}-byte PoC limit.",
            )

        suffix = Path(file_name).suffix
        with tempfile.TemporaryDirectory(prefix="reservoir-ingest-") as directory:
            temporary_path = Path(directory) / f"source{suffix}"
            temporary_path.write_bytes(payload)
            document = parse_document(
                temporary_path,
                source_id=source_input.source_id or file_name,
            )
        return document.model_copy(update={"file_name": file_name})

    async def semantic_map(
        self,
        document: RawDocument,
        *,
        source_system: str | None = None,
    ) -> SemanticMappingBatch:
        source_registries: list[SourceMappingRegistry] = []
        if source_system is not None:
            source_mapping = self._source_mappings.get(source_system.casefold())
            if source_mapping is None:
                raise UnknownSourceSystemError(source_system)
            source_registries.append(source_mapping)
        retriever = OntologyRetriever(
            self.registry,
            source_mappings=source_registries,
        )
        return await SemanticMappingAgent(
            self.registry,
            self.provider,
            retriever=retriever,
        ).map_document(document)

    def build_canonical(
        self,
        mappings: list[SemanticMapping],
        *,
        schema_version: str = "0.1.0",
    ) -> ReservoirSimulationModel:
        return self.builder.build(mappings, schema_version=schema_version)
