from reservoir_data_translator.ingestion import RawBlock
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.semantic import OntologyRetriever


def test_retriever_ranks_alias_matches_before_keywords(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="key_value",
        content={"key": "pressure_floor", "value": 80},
    )

    candidates = OntologyRetriever(registry).retrieve(block, top_k=5)

    assert candidates[0].concept_id == "well.constraint.minimum_bhp"
    assert candidates[0].match_type == "alias"
    assert candidates[0].score == 0.9


def test_retriever_handles_table_columns_without_mapping_them(
    registry: OntologyRegistry,
) -> None:
    block = RawBlock(
        block_id="block_0001",
        block_type="table",
        content={
            "columns": ["Well_ID", "LiquidRate", "MinBHP"],
            "rows": [["A15", "500", "80"]],
        },
    )

    candidate_ids = {
        candidate.concept_id
        for candidate in OntologyRetriever(registry).retrieve(block, top_k=8)
    }

    assert "well" in candidate_ids
    assert "well.control.liquid_rate" in candidate_ids
    assert "well.constraint.minimum_bhp" in candidate_ids


def test_retriever_uses_deterministic_keyword_fallback(
    registry: OntologyRegistry,
) -> None:
    candidates = OntologyRetriever(registry).retrieve(
        "total elapsed runtime",
        top_k=3,
    )

    assert candidates[0].concept_id == "schedule.duration"
    assert candidates[0].match_type == "keyword"
    assert set(candidates[0].matched_terms) == {"elapsed", "total"}


def test_retriever_returns_no_zero_score_candidates(
    registry: OntologyRegistry,
) -> None:
    assert OntologyRetriever(registry).retrieve("XYZ_COEFF") == []


def test_retriever_recalls_demo_injection_and_schedule_phrases(
    registry: OntologyRegistry,
) -> None:
    source = (
        "C1 注水井定注入量 800 方/天，模拟总时长 5 年，按季度出报"
    )

    concept_ids = {
        candidate.concept_id
        for candidate in OntologyRetriever(registry).retrieve(source, top_k=20)
    }

    assert "well.control.water_injection_rate" in concept_ids
    assert "schedule.duration" in concept_ids
    assert "schedule.report_interval" in concept_ids
