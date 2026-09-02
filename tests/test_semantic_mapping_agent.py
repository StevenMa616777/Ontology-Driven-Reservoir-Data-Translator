from typing import Any

import pytest

from reservoir_data_translator.canonical import CanonicalBuilder
from reservoir_data_translator.ingestion import RawBlock, RawDocument, parse_document
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.semantic import (
    AmbiguousSemanticMapping,
    SemanticAgentContractError,
    SemanticMapping,
    SemanticMappingAgent,
    SemanticModelProvider,
    UnmappedSemanticMapping,
)


class FakeProvider(SemanticModelProvider):
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, type[Any]]] = []

    async def structured_generate(self, prompt: str, response_model: type[Any]) -> Any:
        self.calls.append((prompt, response_model))
        return self.response


def _document(block: RawBlock) -> RawDocument:
    return RawDocument(
        source_id="client-a",
        source_type="json",
        file_name="client.json",
        blocks=[block],
    )


@pytest.mark.asyncio
async def test_agent_accepts_structured_mapping_from_supplied_candidates(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="key_value",
        content={"key": "pressure_floor", "value": 80},
        source_location="$.pressure_floor",
    )
    provider = FakeProvider(
        {
            "mappings": [
                {
                    "status": "MAPPED",
                    "source_text": "pressure_floor: 80 bar",
                    "source_block_id": "block_0001",
                    "ontology_concept": "well.constraint.minimum_bhp",
                    "canonical_path": (
                        "wells[A15].controls[liquid_rate]."
                        "constraints[minimum_bhp].value"
                    ),
                    "value": 80,
                    "source_unit": "bar",
                    "canonical_unit": "bar",
                    "confidence": 0.98,
                }
            ]
        }
    )

    batch = await SemanticMappingAgent(registry, provider).map_document(_document(block))

    assert len(batch.mappings) == 1
    assert isinstance(batch.mappings[0], SemanticMapping)
    assert batch.mapped[0].ontology_concept == "well.constraint.minimum_bhp"
    assert batch.mapped[0].provenance.source_location == "$.pressure_floor"
    assert batch.mapped[0].provenance.raw_text == '{"key":"pressure_floor","value":80}'
    assert batch.mapped[0].provenance.extraction_method == "semantic_model:FakeProvider"
    prompt, response_model = provider.calls[0]
    assert "well.constraint.minimum_bhp" in prompt
    assert "canonical_schema" in prompt
    assert response_model.__name__ == "SemanticModelResponse"


@pytest.mark.asyncio
async def test_agent_returns_unmapped_without_calling_provider_when_no_candidates(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="key_value",
        content={"key": "XYZ_COEFF", "value": 12.5},
    )
    provider = FakeProvider("must not be called")

    batch = await SemanticMappingAgent(registry, provider).map_document(_document(block))

    assert provider.calls == []
    assert isinstance(batch.mappings[0], UnmappedSemanticMapping)
    assert batch.mappings[0].source_field == "XYZ_COEFF"
    assert batch.mappings[0].candidate_concepts == []
    assert batch.mappings[0].confidence == 0


@pytest.mark.asyncio
async def test_agent_preserves_ambiguous_state_for_supplied_candidates(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="key_value",
        content={"key": "density", "value": 850},
    )
    provider = FakeProvider(
        {
            "mappings": [
                {
                    "status": "AMBIGUOUS",
                    "source_text": "density: 850",
                    "source_field": "density",
                    "source_block_id": "block_0001",
                    "candidate_concepts": [
                        "fluid.oil.density",
                        "fluid.water.density",
                    ],
                    "value": 850,
                    "confidence": 0.5,
                }
            ]
        }
    )

    batch = await SemanticMappingAgent(registry, provider).map_document(_document(block))

    assert isinstance(batch.mappings[0], AmbiguousSemanticMapping)
    assert batch.mappings[0].candidate_concepts == [
        "fluid.oil.density",
        "fluid.water.density",
    ]
    assert batch.mapped == []


@pytest.mark.asyncio
async def test_agent_rejects_concept_not_supplied_by_retriever(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="key_value",
        content={"key": "duration", "value": 5},
    )
    provider = FakeProvider(
        {
            "mappings": [
                {
                    "status": "MAPPED",
                    "source_block_id": "block_0001",
                    "ontology_concept": "rock.compressibility",
                    "canonical_path": "rock.compressibility",
                    "value": 5,
                    "source_unit": "1/bar",
                    "canonical_unit": "1/bar",
                    "confidence": 0.9,
                }
            ]
        }
    )

    with pytest.raises(SemanticAgentContractError) as error:
        await SemanticMappingAgent(registry, provider).map_document(_document(block))

    assert error.value.code == "CONCEPT_OUTSIDE_CANDIDATES"


@pytest.mark.asyncio
async def test_agent_rejects_invented_canonical_path(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="key_value",
        content={"key": "duration", "value": 5},
    )
    provider = FakeProvider(
        {
            "mappings": [
                {
                    "status": "MAPPED",
                    "source_block_id": "block_0001",
                    "ontology_concept": "schedule.duration",
                    "canonical_path": "schedule.made_up_duration",
                    "value": 5,
                    "source_unit": "year",
                    "canonical_unit": "day",
                    "confidence": 0.9,
                }
            ]
        }
    )

    with pytest.raises(SemanticAgentContractError) as error:
        await SemanticMappingAgent(registry, provider).map_document(_document(block))

    assert error.value.code == "CANONICAL_PATH_OUTSIDE_CONTRACT"


@pytest.mark.asyncio
async def test_agent_rejects_free_text_provider_output(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="key_value",
        content={"key": "duration", "value": 5},
    )

    with pytest.raises(SemanticAgentContractError) as error:
        await SemanticMappingAgent(registry, FakeProvider("duration is five")).map_document(
            _document(block)
        )

    assert error.value.code == "INVALID_STRUCTURED_OUTPUT"


@pytest.mark.asyncio
async def test_task_6_to_8_flow_builds_canonical_value(
    registry: OntologyRegistry,
    tmp_path,
) -> None:
    source = tmp_path / "schedule.txt"
    source.write_text("Simulation duration = 5 years", encoding="utf-8")
    document = parse_document(source, source_id="schedule-demo")
    provider = FakeProvider(
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
    )

    batch = await SemanticMappingAgent(registry, provider).map_document(document)
    canonical = CanonicalBuilder(registry).build(batch.mapped)

    assert canonical.schedule.duration is not None
    assert canonical.schedule.duration.value == 1825
    assert canonical.schedule.duration.provenance is not None
    assert canonical.schedule.duration.provenance.source_id == "schedule-demo"
