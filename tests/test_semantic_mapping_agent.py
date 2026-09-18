import json
from typing import Any

import pytest

from reservoir_data_translator.canonical import CanonicalBuilder
from reservoir_data_translator.ingestion import RawBlock, RawDocument, parse_document
from reservoir_data_translator.ingestion.models import BoundingBox, SourceRegion, SourceRegionPart
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


class SequenceProvider(SemanticModelProvider):
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    async def structured_generate(self, prompt: str, response_model: type[Any]) -> Any:
        self.prompts.append(prompt)
        return next(self.responses)


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
async def test_prompt_exposes_other_blocks_as_structure_without_their_content(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="text",
        content="Simulation duration is 5 years",
        source_location="page 1",
    )
    other = RawBlock(
        block_id="block_0002",
        block_type="text",
        content="SECRET_OTHER_BLOCK_FACT oil PVT pressure 100 bar",
        source_location="page 2",
    )
    document = RawDocument(
        source_id="scope-test",
        source_type="pdf",
        file_name="scope.pdf",
        blocks=[block, other],
    )
    provider = FakeProvider(
        {
            "mappings": [
                {
                    "status": "MAPPED",
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

    await SemanticMappingAgent(registry, provider).map_block(document, block)

    prompt = provider.calls[0][0]
    payload = json.loads(prompt.split("INPUT:\n", 1)[1])
    assert "document_context" not in payload
    assert payload["mapping_scope"]["source_block_id"] == "block_0001"
    assert payload["document_structure"] == [
        {
            "block_id": "block_0001",
            "block_type": "text",
            "source_location": "page 1",
            "source_region": None,
        },
        {
            "block_id": "block_0002",
            "block_type": "text",
            "source_location": "page 2",
            "source_region": None,
        },
    ]
    assert "SECRET_OTHER_BLOCK_FACT" not in prompt
    assert "Every returned source_block_id must" in prompt

    other.source_region = SourceRegion(
        region_id="other", parent_region_id="page2", page=2,
        bbox=BoundingBox(x0=0, top=0, x1=100, bottom=20), reading_order=1,
        extraction_method="pdf_ocr_text:page",
        parts=[SourceRegionPart(region_id="original", bbox=BoundingBox(x0=0, top=0, x1=100, bottom=20),
                                extraction_method="pdf_ocr_text:region", text="SECRET_OTHER_BLOCK_FACT")],
    )
    await SemanticMappingAgent(registry, provider).map_block(document, block)
    assert "SECRET_OTHER_BLOCK_FACT" not in provider.calls[-1][0]
    payload = json.loads(provider.calls[-1][0].split("INPUT:\n", 1)[1])
    assert "parts" not in payload["document_structure"][1]["source_region"]


def _duration_response(value):
    return {"mappings": [{
        "status": "MAPPED", "source_block_id": "block_0001",
        "ontology_concept": "schedule.duration", "canonical_path": "schedule.duration",
        "value": value, "source_unit": "year", "canonical_unit": "day", "confidence": 0.99,
    }]}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_value", [
    {"value": 5, "unit": "year"}, "5", True, False, None, [5],
    float("nan"), float("inf"), float("-inf"), 10 ** 400,
])
async def test_physical_value_contract_retries_invalid_magnitude(registry, bad_value):
    block = RawBlock(block_id="block_0001", block_type="text", content="Simulation duration is 5 years")
    provider = SequenceProvider([_duration_response(bad_value), _duration_response(5)])
    batch = await SemanticMappingAgent(registry, provider).map_document(_document(block))
    assert batch.mapped[0].value == 5
    assert len(provider.prompts) == 2
    assert "SEMANTIC_PHYSICAL_VALUE_INVALID" in provider.prompts[1]
    assert "schedule.duration" in provider.prompts[1].split("CORRECTION REQUIRED:")[1]


@pytest.mark.asyncio
async def test_physical_value_contract_stops_when_retries_exhausted(registry):
    block = RawBlock(block_id="block_0001", block_type="text", content="Simulation duration is 5 years")
    bad = _duration_response({"value": 5, "unit": "year"})
    provider = SequenceProvider([bad, bad])
    with pytest.raises(SemanticAgentContractError) as error:
        await SemanticMappingAgent(registry, provider).map_document(_document(block))
    assert error.value.code == "SEMANTIC_PHYSICAL_VALUE_INVALID"
    assert error.value.source_block_id == "block_0001"
    assert len(provider.prompts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, 5, 5.25, -5])
async def test_physical_value_contract_accepts_finite_numbers_without_retry(registry, value):
    block = RawBlock(block_id="block_0001", block_type="text", content="Simulation duration is 5 years")
    provider = SequenceProvider([_duration_response(value)])
    batch = await SemanticMappingAgent(registry, provider).map_document(_document(block))
    assert batch.mapped[0].value == value
    assert len(provider.prompts) == 1


@pytest.mark.asyncio
async def test_source_block_mismatch_retry_reasserts_raw_block_scope(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="text",
        content="Simulation duration is 5 years",
    )
    mapping = {
        "status": "MAPPED",
        "ontology_concept": "schedule.duration",
        "canonical_path": "schedule.duration",
        "value": 5,
        "source_unit": "year",
        "canonical_unit": "day",
        "confidence": 0.99,
    }
    provider = SequenceProvider(
        [
            {"mappings": [{**mapping, "source_block_id": "block_0002"}]},
            {"mappings": [{**mapping, "source_block_id": "block_0001"}]},
        ]
    )

    result = await SemanticMappingAgent(registry, provider).map_document(
        _document(block)
    )

    assert result.mapped[0].source_block_id == "block_0001"
    assert len(provider.prompts) == 2
    assert "SOURCE_BLOCK_MISMATCH" in provider.prompts[1]
    assert "using facts only from INPUT.raw_block" in provider.prompts[1]
    assert "Do not map document_structure" in provider.prompts[1]


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
async def test_agent_retries_structural_value_contract(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="text",
        content="Oil PVT pressure 100 bar, Bo 1.2 rm3/sm3, viscosity 2.5 cP",
    )
    base_mapping = {
        "status": "MAPPED",
        "source_block_id": "block_0001",
        "ontology_concept": "fluid.oil.pvt",
        "canonical_path": "fluids.oil.pvt",
        "confidence": 0.95,
    }
    provider = SequenceProvider(
        [
            {"mappings": [{**base_mapping, "value": None}]},
            {
                "mappings": [
                    {**base_mapping, "value": {"model_type": "table"}}
                ]
            },
        ]
    )

    result = await SemanticMappingAgent(registry, provider).map_document(
        _document(block)
    )

    assert result.mapped[0].value == {"model_type": "table"}
    assert len(provider.prompts) == 2
    assert "STRUCTURAL_VALUE_OUTSIDE_CONTRACT" in provider.prompts[1]


@pytest.mark.asyncio
async def test_agent_retries_missing_pvt_parent_mapping(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="text",
        content="Oil PVT pressure 100 bar",
    )
    point = {
        "status": "MAPPED",
        "source_block_id": "block_0001",
        "ontology_concept": "fluid.oil.pvt.pressure",
        "canonical_path": "fluids.oil.pvt.points[0].pressure",
        "value": 100,
        "source_unit": "bar",
        "canonical_unit": "bar",
        "confidence": 0.98,
    }
    table = {
        "status": "MAPPED",
        "source_block_id": "block_0001",
        "ontology_concept": "fluid.oil.pvt",
        "canonical_path": "fluids.oil.pvt",
        "value": {"model_type": "table"},
        "canonical_unit": None,
        "confidence": 0.98,
    }
    provider = SequenceProvider(
        [
            {"mappings": [point]},
            {"mappings": [table, point]},
        ]
    )

    result = await SemanticMappingAgent(registry, provider).map_document(
        _document(block)
    )

    assert [mapping.ontology_concept for mapping in result.mapped] == [
        "fluid.oil.pvt",
        "fluid.oil.pvt.pressure",
    ]
    assert len(provider.prompts) == 2
    assert "REQUIRED_STRUCTURAL_MAPPING_MISSING" in provider.prompts[1]
    assert "Structural parent mappings are mandatory" in provider.prompts[0]
    prompt_payload = json.loads(provider.prompts[0].split("INPUT:\n", 1)[1])
    prompt_candidates = prompt_payload["ontology_candidates"]
    oil_parent = next(
        candidate
        for candidate in prompt_candidates
        if candidate["concept_id"] == "fluid.oil.pvt"
    )
    oil_pressure = next(
        candidate
        for candidate in prompt_candidates
        if candidate["concept_id"] == "fluid.oil.pvt.pressure"
    )
    assert prompt_payload["required_structural_parents"] == [
        "fluid.oil.pvt",
        "fluid.water.pvt",
        "fluid.gas.pvt",
        "scal.relative_permeability",
    ]
    assert all(
        "required_parent_concept" not in candidate
        and "required_for_descendants" not in candidate
        for candidate in prompt_candidates
    )
    assert prompt_candidates.index(oil_parent) < prompt_candidates.index(oil_pressure)


@pytest.mark.asyncio
async def test_agent_preserves_but_does_not_semantically_map_figure_blocks(
    registry: OntologyRegistry,
) -> None:
    figure = RawBlock(
        block_id="block_0001",
        block_type="figure",
        content={"figure_index": 1},
        source_location="page 1, bbox (10, 20, 100, 80)",
    )
    provider = FakeProvider("must not be called")

    batch = await SemanticMappingAgent(registry, provider).map_document(_document(figure))

    assert batch.mappings == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_agent_retries_control_that_conflicts_with_well_type(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="text",
        content="C1 注水井定注入量 800 方/天，不是定液量控制",
    )
    well_type = {
        "status": "MAPPED",
        "source_block_id": "block_0001",
        "ontology_concept": "well.water_injector",
        "canonical_path": "wells[C1].well_type",
        "value": "water_injector",
        "canonical_unit": None,
        "confidence": 0.99,
    }
    rate = {
        "status": "MAPPED",
        "source_block_id": "block_0001",
        "canonical_path": "wells[C1].controls[liquid_rate].target",
        "value": 800,
        "source_unit": "m3/day",
        "canonical_unit": "m3/day",
        "confidence": 0.95,
    }
    provider = SequenceProvider(
        [
            {
                "mappings": [
                    well_type,
                    {**rate, "ontology_concept": "well.control.liquid_rate"},
                ]
            },
            {
                "mappings": [
                    well_type,
                    {
                        **rate,
                        "ontology_concept": "well.control.water_injection_rate",
                        "canonical_path": (
                            "wells[C1].controls[water_injection_rate].target"
                        ),
                    },
                ]
            },
        ]
    )

    result = await SemanticMappingAgent(registry, provider).map_document(
        _document(block)
    )

    assert result.mapped[1].ontology_concept == "well.control.water_injection_rate"
    assert len(provider.prompts) == 2
    assert "ONTOLOGY_RELATIONSHIP_CONFLICT" in provider.prompts[1]


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
