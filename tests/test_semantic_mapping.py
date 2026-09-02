import pytest
from pydantic import ValidationError

from reservoir_data_translator.canonical import Provenance
from reservoir_data_translator.semantic import SemanticMapping


def test_semantic_mapping_preserves_structured_evidence() -> None:
    provenance = Provenance(
        source_id="demo",
        source_block_id="block-1",
        raw_text="定液量 500 方/天",
        extraction_method="manual_fixture",
    )
    mapping = SemanticMapping(
        source_text="定液量 500 方/天",
        source_block_id="block-1",
        ontology_concept="well.control.liquid_rate",
        canonical_path="wells[A15].controls[liquid_rate].target",
        value=500,
        source_unit="m3/day",
        canonical_unit="m3/day",
        confidence=0.98,
        provenance=provenance,
    )

    assert mapping.provenance.raw_text == "定液量 500 方/天"
    assert mapping.model_dump()["ontology_concept"] == "well.control.liquid_rate"


def test_semantic_mapping_rejects_mismatched_source_block_evidence() -> None:
    with pytest.raises(ValidationError, match="source_block_id must match"):
        SemanticMapping(
            source_block_id="block-1",
            ontology_concept="rock.reference_pressure",
            canonical_path="rock.reference_pressure",
            value=200,
            source_unit="bar",
            confidence=0.9,
            provenance=Provenance(
                source_id="demo",
                source_block_id="block-2",
                extraction_method="manual_fixture",
            ),
        )


def test_mapping_batch_applies_human_review_thresholds() -> None:
    def mapping(confidence: float) -> SemanticMapping:
        return SemanticMapping(
            source_block_id="block-1",
            ontology_concept="schedule.duration",
            canonical_path="schedule.duration",
            value=5,
            source_unit="year",
            canonical_unit="day",
            confidence=confidence,
            provenance=Provenance(
                source_id="demo",
                source_block_id="block-1",
                extraction_method="fixture",
            ),
        )

    from reservoir_data_translator.semantic import SemanticMappingBatch

    batch = SemanticMappingBatch(
        source_id="demo",
        mappings=[mapping(0.99), mapping(0.90), mapping(0.70)],
    )

    assert batch.review_required
    assert [item.confidence for item in batch.auto_accepted] == [0.99]
    assert [item.confidence for item in batch.accepted_with_warning] == [0.90]
