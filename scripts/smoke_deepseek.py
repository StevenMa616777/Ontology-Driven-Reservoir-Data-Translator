"""Run one real, synthetic Semantic Mapping call without exposing credentials."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import time

from reservoir_data_translator.canonical import CanonicalBuilder
from reservoir_data_translator.ingestion import RawBlock, RawDocument
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.semantic import (
    DeepSeekCallTrace,
    DeepSeekProvider,
    SemanticMappingAgent,
)
from reservoir_data_translator.validation import ValidationEngine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def run(output_path: Path | None) -> dict[str, object]:
    traces: list[DeepSeekCallTrace] = []
    provider = DeepSeekProvider.from_environment(
        api_key_file=PROJECT_ROOT / "LLM" / "DeepSeek" / "api_key",
        trace_sink=traces.append,
    )
    registry = OntologyRegistry.load(PROJECT_ROOT / "ontology")
    document = RawDocument(
        source_id="deepseek-smoke-synthetic",
        source_type="txt",
        file_name="deepseek_smoke.txt",
        blocks=[
            RawBlock(
                block_id="block_0001",
                block_type="text",
                content="Simulation duration = 5 years",
                source_location="line 1",
            )
        ],
    )

    started = time.perf_counter()
    batch = await SemanticMappingAgent(registry, provider).map_document(document)
    elapsed_seconds = round(time.perf_counter() - started, 3)
    if len(batch.mapped) != 1 or batch.unresolved:
        raise RuntimeError("Smoke source did not produce exactly one mapped outcome.")

    canonical = CanonicalBuilder(registry).build(batch.mapped)
    validation = ValidationEngine(registry).validate(canonical)
    duration = canonical.schedule.duration
    if duration is None or duration.value != 1825 or duration.unit != "day":
        raise RuntimeError("DeepSeek mapping did not build the expected duration.")
    if not validation.valid:
        raise RuntimeError("Canonical result did not pass L1-L3 validation.")
    if len(traces) != 1:
        raise RuntimeError("Expected exactly one non-secret provider trace.")

    artifact: dict[str, object] = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "status": "passed",
        "elapsed_seconds": elapsed_seconds,
        "provider": provider.provider_name,
        "call": asdict(traces[0]),
        "source_id": document.source_id,
        "semantic_mapping": batch.model_dump(mode="json"),
        "canonical_check": {
            "schedule.duration.value": duration.value,
            "schedule.duration.unit": duration.unit,
            "l1_l3_valid": validation.valid,
        },
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    artifact = asyncio.run(run(arguments.output))
    call = artifact["call"]
    assert isinstance(call, dict)
    print(
        json.dumps(
            {
                "status": artifact["status"],
                "provider": artifact["provider"],
                "response_model": call["response_model"],
                "total_tokens": call["total_tokens"],
                "elapsed_seconds": artifact["elapsed_seconds"],
                "artifact": str(arguments.output) if arguments.output else None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
