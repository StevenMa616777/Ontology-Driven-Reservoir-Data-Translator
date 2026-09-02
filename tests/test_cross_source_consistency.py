from pathlib import Path
from typing import Any

import pytest

from reservoir_data_translator.canonical import CanonicalBuilder
from reservoir_data_translator.ingestion import parse_document
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.semantic import (
    OntologyRetriever,
    SemanticMappingAgent,
    SemanticModelProvider,
    SourceMappingRegistry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
MAPPINGS = PROJECT_ROOT / "mappings"


class EquivalentWellProvider(SemanticModelProvider):
    async def structured_generate(self, prompt: str, response_model: type[Any]) -> Any:
        block_id = "block_0001"
        return {
            "mappings": [
                {
                    "status": "MAPPED",
                    "source_text": "A15",
                    "source_block_id": block_id,
                    "ontology_concept": "well",
                    "canonical_path": "wells[A15].id",
                    "value": "A15",
                    "confidence": 0.99,
                },
                {
                    "status": "MAPPED",
                    "source_text": "producer",
                    "source_block_id": block_id,
                    "ontology_concept": "well.producer",
                    "canonical_path": "wells[A15].well_type",
                    "value": "producer",
                    "confidence": 0.98,
                },
                {
                    "status": "MAPPED",
                    "source_text": "liquid rate 500 m3/day",
                    "source_block_id": block_id,
                    "ontology_concept": "well.control.liquid_rate",
                    "canonical_path": "wells[A15].controls[liquid_rate].target",
                    "value": 500,
                    "source_unit": "m3/day",
                    "canonical_unit": "m3/day",
                    "confidence": 0.98,
                },
                {
                    "status": "MAPPED",
                    "source_text": "minimum BHP 80 bar",
                    "source_block_id": block_id,
                    "ontology_concept": "well.constraint.minimum_bhp",
                    "canonical_path": (
                        "wells[A15].controls[liquid_rate]."
                        "constraints[minimum_bhp].value"
                    ),
                    "value": 80,
                    "source_unit": "bar",
                    "canonical_unit": "bar",
                    "confidence": 0.97,
                },
            ]
        }


def _business_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _business_payload(item)
            for key, item in value.items()
            if key not in {"provenance", "confidence"}
        }
    if isinstance(value, list):
        return [_business_payload(item) for item in value]
    return value


@pytest.mark.asyncio
async def test_three_heterogeneous_sources_build_equivalent_canonical_models(
    registry: OntologyRegistry,
) -> None:
    canonical_payloads: list[dict[str, Any]] = []
    expected_concepts = {
        "well",
        "well.producer",
        "well.control.liquid_rate",
        "well.constraint.minimum_bhp",
    }

    for client, suffix in (("a", "csv"), ("b", "json"), ("c", "txt")):
        document = parse_document(
            FIXTURES / f"client_{client}.{suffix}",
            source_id=f"client-{client}",
        )
        source_mapping = SourceMappingRegistry.load(
            MAPPINGS / f"customer_{client}.yaml",
            registry,
        )
        retriever = OntologyRetriever(
            registry,
            source_mappings=[source_mapping],
        )
        batch = await SemanticMappingAgent(
            registry,
            EquivalentWellProvider(),
            retriever=retriever,
        ).map_document(document)

        assert batch.unresolved == []
        assert {mapping.ontology_concept for mapping in batch.mapped} == expected_concepts
        canonical = CanonicalBuilder(registry).build(batch.mapped)
        canonical_payloads.append(_business_payload(canonical.model_dump()))

    assert canonical_payloads[0] == canonical_payloads[1] == canonical_payloads[2]
    well = canonical_payloads[0]["wells"][0]
    assert well == {
        "id": "A15",
        "well_type": "producer",
        "controls": [
            {
                "control_type": "liquid_rate",
                "target": {"value": 500.0, "unit": "m3/day"},
                "constraints": [
                    {
                        "constraint_type": "minimum_bhp",
                        "value": {"value": 80.0, "unit": "bar"},
                    }
                ],
            }
        ],
    }


def test_customer_terms_stay_outside_ontology_aliases(
    registry: OntologyRegistry,
) -> None:
    assert registry.search_by_alias("LRAT") == []
    source_mapping = SourceMappingRegistry.load(
        MAPPINGS / "customer_b.yaml",
        registry,
    )
    matches = source_mapping.search("control_mode=LRAT")
    assert {match.concept_id for match in matches} == {
        "well.control.liquid_rate"
    }
