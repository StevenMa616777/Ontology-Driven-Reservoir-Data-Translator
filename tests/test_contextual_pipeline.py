"""Regression tests across retrieval, semantic IR, safety gates and demo export."""

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from reservoir_data_translator.api import create_app
from reservoir_data_translator.canonical import CanonicalBuilder
from reservoir_data_translator.canonical.mapping_contract import (
    get_canonical_mapping_contract,
    normalize_semantic_identity,
)
from reservoir_data_translator.ingestion import RawBlock, RawDocument
from reservoir_data_translator.mappers import EclipseDemoMapper, PlatformMappingRegistry
from reservoir_data_translator.ontology import SemanticContext
from reservoir_data_translator.semantic import (
    OntologyRetriever, SemanticMappingAgent, SemanticModelProvider,
    SemanticAgentContractError, SourceMappingRegistry,
)
from reservoir_data_translator.semantic.source_mapping import SourceMappingDefinition
from test_canonical_builder import _demo_mappings


class FixedIRProvider(SemanticModelProvider):
    def __init__(self, mappings):
        self.mappings = mappings
        self.prompts = []

    async def structured_generate(self, prompt, response_model):
        self.prompts.append(prompt)
        return {"mappings": self.mappings}


def _document(text):
    return RawDocument(source_id="context-test", source_type="txt", file_name="context.txt", blocks=[
        RawBlock(block_id="block_0001", block_type="text", content=text),
    ])


def _pressure(phase="oil", **overrides):
    return {
        "status": "MAPPED", "source_block_id": "block_0001",
        "ontology_concept": "physical.pressure",
        "context": {"scope": "pvt", "phase": phase, "role": "coordinate"},
        "selectors": {"point_index": 0}, "value": 100,
        "source_unit": "bar", "canonical_unit": "bar", "confidence": 0.99,
        **overrides,
    }


def _pvt(phase):
    return {"status": "MAPPED", "source_block_id": "block_0001",
            "ontology_concept": "fluid.pvt",
            "context": {"scope": "pvt", "phase": phase, "role": "model"},
            "value": {"model_type": "table"}, "confidence": 0.99}


def test_retrieval_keeps_context_keys_and_narrows_only_known_phases(registry):
    retriever = OntologyRetriever(registry, default_top_k=40)
    unknown = [c for c in retriever.retrieve("PVT pressure 100 bar") if c.concept_id == "physical.pressure"]
    assert {c.context.phase for c in unknown} == {"oil", "water", "gas"}
    assert len({c.semantic_key for c in unknown}) == 3
    assert not any(c.context_supported for c in unknown)
    gas = [c for c in retriever.retrieve("Gas PVT pressure 100 bar") if c.concept_id == "physical.pressure"]
    assert [c.context.phase for c in gas] == ["gas"]
    assert gas[0].context_supported
    mixed = retriever.retrieve("Gas PVT pressure 100 bar; oil density 850 kg/m3; A15 LiquidRate 500 m3/day; duration 5 years")
    assert {c.context.scope for c in mixed if c.context} >= {"pvt", "fluid_properties", "well_controls", "schedule"}
    assert {c.context.phase for c in mixed if c.concept_id == "physical.pressure" and c.context.scope == "pvt"} == {"gas"}
    kr = [c for c in retriever.retrieve("Sw Krw Kro Pcow oil water relative permeability table") if c.concept_id == "physical.relative_permeability"]
    assert {c.context.phase for c in kr} == {"oil", "water"}


@pytest.mark.asyncio
async def test_two_phases_share_one_pressure_concept_and_resolve_without_llm_paths(registry):
    provider = FixedIRProvider([_pvt("oil"), _pressure("oil"), _pvt("gas"), _pressure("gas", value=200)])
    batch = await SemanticMappingAgent(registry, provider).map_document(
        _document("Oil PVT table pressure 100 bar; Gas PVT table pressure 200 bar")
    )
    assert not batch.review_required
    pressures = [m for m in batch.mapped if m.ontology_concept == "physical.pressure"]
    assert {m.canonical_path for m in pressures} == {"fluids.oil.pvt.points[0].pressure", "fluids.gas.pvt.points[0].pressure"}
    model = CanonicalBuilder(registry).build(batch.mapped)
    assert model.fluids.oil.pvt.points[0].pressure.value == 100
    assert model.fluids.gas.pvt.points[0].pressure.value == 200
    prompt = json.loads(provider.prompts[0].split("INPUT:\n")[1])
    assert "canonical_schema" not in prompt
    assert all("canonical_path_template" not in c for c in prompt["ontology_candidates"])


@pytest.mark.asyncio
@pytest.mark.parametrize("draft", [
    _pressure(),
    {"status": "AMBIGUOUS", "source_block_id": "block_0001", "confidence": 0.5,
     "candidate_contexts": [
         {"concept_id": "physical.pressure", "context": {"scope": "pvt", "phase": phase, "role": "coordinate"}}
         for phase in ("oil", "gas")
     ]},
])
async def test_unknown_phase_cannot_escape_review_even_if_provider_guesses(registry, draft):
    provider = FixedIRProvider([draft])
    app = create_app(registry=registry, provider=provider)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/translate", json={
            "source": "PVT pressure 100 bar", "target_platform": "eclipse",
        })
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "review_required"
    assert result["canonical_model"] is None
    assert result["target"] is None
    assert result["semantic_mapping"]["mappings"][0]["status"] == "AMBIGUOUS"


@pytest.mark.asyncio
@pytest.mark.parametrize("draft, code", [
    (_pressure("gas"), "CONTEXT_OUTSIDE_CANDIDATES"),
    (_pressure("oil", canonical_path="fluids.gas.pvt.points[0].pressure"), "CANONICAL_PATH_OUTSIDE_CONTRACT"),
    (_pressure("oil", context=None), "SEMANTIC_CONTEXT_INVALID"),
])
async def test_provider_cannot_invent_or_cross_context(registry, draft, code):
    with pytest.raises(SemanticAgentContractError) as error:
        await SemanticMappingAgent(registry, FixedIRProvider([draft]), contract_retries=0).map_document(
            _document("Oil PVT pressure 100 bar")
        )
    assert error.value.code == code


@pytest.mark.asyncio
async def test_table_parent_from_another_instance_does_not_satisfy_children(registry):
    parent = {"status": "MAPPED", "source_block_id": "block_0001",
              "ontology_concept": "scal.relative_permeability",
              "context": {"scope": "relative_permeability", "role": "model", "phase_system": "oil_water"},
              "selectors": {"table_id": "A"}, "value": {"phase_system": ["oil", "water"]}, "confidence": 0.99}
    child = {"status": "MAPPED", "source_block_id": "block_0001",
             "ontology_concept": "physical.water_saturation",
             "context": {"scope": "relative_permeability", "phase": "water", "role": "coordinate", "phase_system": "oil_water"},
             "selectors": {"table_id": "B", "point_index": 0}, "value": 0.2,
             "source_unit": "fraction", "canonical_unit": "fraction", "confidence": 0.99}
    with pytest.raises(SemanticAgentContractError) as error:
        await SemanticMappingAgent(registry, FixedIRProvider([parent, child]), contract_retries=0).map_document(
            _document("Oil water relative permeability tables A and B; B Sw 0.2 fraction")
        )
    assert error.value.code == "REQUIRED_STRUCTURAL_MAPPING_MISSING"
    assert "[B]" in str(error.value)


def test_source_mapping_legacy_terms_migrate_to_contextual_identity(registry):
    source = SourceMappingRegistry(SourceMappingDefinition.model_validate({
        "mapping_version": "0.1.0", "source_system": "test",
        "entries": [{"source_term": "VISC_G_OLD", "concept_id": "fluid.gas.pvt.viscosity"}],
    }), registry)
    candidate = OntologyRetriever(registry, source_mappings=[source]).retrieve("VISC_G_OLD")[0]
    assert candidate.concept_id == "physical.viscosity"
    assert candidate.context == SemanticContext(scope="pvt", phase="gas", role="property")
    assert candidate.context_supported


@pytest.mark.asyncio
async def test_contextual_ir_runs_entire_demo_pipeline_with_identical_export(registry):
    # A deterministic provider fixture isolates orchestration/IR correctness
    # from LLM extraction quality and does not make external calls.
    legacy = _demo_mappings()
    drafts, source_lines = [], []
    for mapping in legacy:
        concept_id, context = normalize_semantic_identity(mapping.ontology_concept)
        contract = get_canonical_mapping_contract(concept_id, context)
        binding = next(b for b in registry.scopes.bindings if (b.concept_id, b.context) == (concept_id, context))
        drafts.append({
            "status": "MAPPED", "source_block_id": "block_0001",
            "ontology_concept": concept_id, "context": context.model_dump(exclude_none=True),
            "selectors": contract.extract_selectors(mapping.canonical_path), "value": mapping.value,
            "source_unit": mapping.source_unit, "canonical_unit": mapping.canonical_unit,
            "confidence": mapping.confidence,
        })
        source_lines.append(f"{binding.aliases[0]} {json.dumps(drafts[-1]['selectors'])}: {json.dumps(mapping.value)} {mapping.source_unit or ''}")
    mapper = EclipseDemoMapper(PlatformMappingRegistry.load(Path(__file__).parents[1] / "mappings/eclipse.yaml", registry))
    expected = mapper.export(CanonicalBuilder(registry).build(legacy)).content
    app = create_app(registry=registry, provider=FixedIRProvider(drafts), mappers=[mapper])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/translate", json={"source": "\n".join(source_lines), "target_platform": "eclipse"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "success", result
    assert result["validation"]["valid"]
    assert result["target"]["content"] == expected
    assert all(m["context"] for m in result["semantic_mapping"]["mappings"])


def test_phase_evidence_is_not_borrowed_from_another_scope(registry):
    candidates = OntologyRetriever(registry, default_top_k=40).retrieve(
        "Oil density 850 kg/m3; PVT pressure 100 bar"
    )
    pressures = [candidate for candidate in candidates if candidate.concept_id == "physical.pressure"
                 and candidate.context.scope == "pvt"]
    assert {candidate.context.phase for candidate in pressures} == {"oil", "water", "gas"}
    assert not any(candidate.context_supported for candidate in pressures)


def test_short_source_term_does_not_match_inside_an_unrelated_word(registry):
    source = SourceMappingRegistry(SourceMappingDefinition.model_validate({
        "mapping_version": "0.2.0", "source_system": "test",
        "entries": [{"source_term": "Po", "concept_id": "physical.pressure",
                     "context": {"scope": "pvt", "phase": "oil", "role": "coordinate"}}],
    }), registry)
    assert source.search("unsupported coefficient") == []
    assert source.search("Po 100 bar")[0].context.phase == "oil"


def test_generic_relative_permeability_does_not_imply_oil_water_system(registry):
    candidates = OntologyRetriever(registry, default_top_k=40).retrieve("relative permeability table")
    assert not any(candidate.context_supported for candidate in candidates
                   if candidate.context and candidate.context.phase_system == "oil_water")
