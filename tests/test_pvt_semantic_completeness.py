import pytest

from reservoir_data_translator.ingestion import RawBlock, RawDocument
from reservoir_data_translator.canonical import CanonicalBuilder
from reservoir_data_translator.semantic import SemanticMappingAgent, SemanticAgentContractError, SemanticModelProvider


class Responses(SemanticModelProvider):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts = []
        self.failures = []

    async def structured_generate(self, prompt, response_model):
        self.prompts.append(prompt)
        return next(self.responses)

    def record_contract_failure(self, code, message):
        self.failures.append(code)


def _parent(phase="water", model_type="constant"):
    return dict(status="MAPPED", source_block_id="block_0001", confidence=0.99,
                ontology_concept=f"fluid.{phase}.pvt", canonical_path=f"fluids.{phase}.pvt",
                value={"model_type": model_type})


def _point(field, value, phase="water", index=0):
    unit = "bar" if field == "pressure" else "rm3/sm3"
    return dict(status="MAPPED", source_block_id="block_0001", confidence=0.99,
                ontology_concept=f"fluid.{phase}.pvt.{field}",
                canonical_path=f"fluids.{phase}.pvt.points[{index}].{field}",
                value=value, source_unit=unit, canonical_unit=unit)


def _document():
    return RawDocument(source_id="pvt", source_type="pdf", file_name="pvt.pdf", blocks=[RawBlock(
        block_id="block_0001", block_type="text",
        content="Water PVT Bw formation volume factor 1.02 rm3/sm3 at reference pressure 300 bar. "
                "Oil PVT Bo 1.02 rm3/sm3 at 300 bar. Gas PVT Bg 1.02 rm3/sm3 at 300 bar.")])


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["oil", "water", "gas"])
@pytest.mark.parametrize("model_type", ["table", "constant"])
async def test_missing_pressure_retries_then_builds(registry, phase, model_type):
    parent = _parent(phase, model_type)
    factor = _point("formation_volume_factor", 1.02, phase)
    pressure = _point("pressure", 300, phase)
    provider = Responses([{"mappings": [parent, factor]}, {"mappings": [parent, factor, pressure]}])
    batch = await SemanticMappingAgent(registry, provider).map_document(_document())
    assert provider.failures == ["SEMANTIC_PVT_POINT_INCOMPLETE"]
    correction = provider.prompts[1].split("CORRECTION REQUIRED:")[1]
    assert f"fluids.{phase}.pvt.points[0].pressure" in correction
    assert "Do not invent pressure" in correction
    model = CanonicalBuilder(registry).build(batch.mapped)
    assert getattr(model.fluids, phase).pvt.points[0].pressure.value == 300


@pytest.mark.asyncio
@pytest.mark.parametrize("other_phase, other_index", [("water", 1), ("oil", 0)])
async def test_other_point_or_phase_pressure_does_not_fill_gap(registry, other_phase, other_index):
    mappings = [_parent(), _point("formation_volume_factor", 1.02),
                _point("pressure", 300, other_phase, other_index)]
    if other_phase != "water":
        mappings.append(_parent(other_phase))
    provider = Responses([{"mappings": mappings}, {"mappings": mappings}])
    with pytest.raises(SemanticAgentContractError) as error:
        await SemanticMappingAgent(registry, provider).map_document(_document())
    assert error.value.code == "SEMANTIC_PVT_POINT_INCOMPLETE"
    assert error.value.source_block_id == "block_0001"
    assert "fluids.water.pvt.points[0].pressure" in str(error.value)
    assert len(provider.prompts) == 2


@pytest.mark.asyncio
async def test_pressure_only_point_does_not_require_optional_properties(registry):
    provider = Responses([{"mappings": [_parent(), _point("pressure", 300)]}])
    batch = await SemanticMappingAgent(registry, provider).map_document(_document())
    assert len(provider.prompts) == 1
    assert CanonicalBuilder(registry).build(batch.mapped).fluids.water.pvt.points[0].pressure.value == 300
