"""Run the real Demo -> DeepSeek -> Canonical -> Eclipse -> OPM PoC gate.

The matching Eclipse INCLUDE is an output Golden File.  It is intentionally
not treated as a semantic-label Gold dataset; concept/path extraction metrics
remain unavailable until that separate dataset exists.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from reservoir_data_translator.canonical import CanonicalBuilder
from reservoir_data_translator.ingestion import parse_document
from reservoir_data_translator.mappers import EclipseDemoMapper, PlatformMappingRegistry
from reservoir_data_translator.ontology import OntologyRegistry
from reservoir_data_translator.semantic import (
    DeepSeekCallTrace,
    DeepSeekProvider,
    SemanticMappingAgent,
)
from reservoir_data_translator.validation import (
    ExportValidator,
    ValidationEngine,
    compare_eclipse_includes,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "example" / "demo_material_raw.txt"
DEFAULT_GOLDEN = PROJECT_ROOT / "example" / "demo_material_eclipse.inc"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "demo_deepseek_evaluation"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def run(
    *,
    source_path: Path,
    golden_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Execute the acceptance chain and persist a secret-safe artifact bundle."""

    source_path = source_path.resolve()
    golden_path = golden_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    traces: list[DeepSeekCallTrace] = []
    provider = DeepSeekProvider.from_environment(
        api_key_file=PROJECT_ROOT / "LLM" / "DeepSeek" / "api_key",
        trace_sink=traces.append,
    )
    registry = OntologyRegistry.load(PROJECT_ROOT / "ontology")
    document = parse_document(source_path, source_id="demo-material-real-deepseek")

    started = time.perf_counter()
    semantic_batch = await SemanticMappingAgent(registry, provider).map_document(
        document
    )
    if semantic_batch.unresolved:
        raise RuntimeError(
            f"Semantic mapping left {len(semantic_batch.unresolved)} unresolved item(s)."
        )
    if semantic_batch.review_required:
        raise RuntimeError("Semantic mapping contains outcomes below the review gate.")
    _write_json(
        output_dir / "semantic_mapping.json",
        semantic_batch.model_dump(mode="json"),
    )
    _write_json(
        output_dir / "provider_trace.json",
        [asdict(trace) for trace in traces],
    )

    canonical = CanonicalBuilder(registry).build(semantic_batch.mapped)
    _write_json(output_dir / "canonical.json", canonical.model_dump(mode="json"))
    mapping_registry = PlatformMappingRegistry.load(
        PROJECT_ROOT / "mappings" / "eclipse.yaml",
        registry,
    )
    mapper = EclipseDemoMapper(mapping_registry)
    validation = ValidationEngine(
        registry,
        export_validator=ExportValidator([mapper]),
    ).validate(canonical, target_platform="eclipse")
    _write_json(output_dir / "validation.json", validation.model_dump(mode="json"))
    if not validation.valid:
        codes = [issue.code for issue in validation.errors]
        raise RuntimeError(f"Canonical/export validation failed: {codes}")

    export = mapper.export(canonical)
    golden_text = golden_path.read_text(encoding="utf-8")
    comparison = compare_eclipse_includes(golden_text, export.content)
    if not comparison["semantic_equal"]:
        raise RuntimeError(
            "Generated Eclipse INCLUDE differs from the parser-normalized Golden File."
        )

    elapsed_seconds = round(time.perf_counter() - started, 3)
    trace_payload = [asdict(trace) for trace in traces]
    total_tokens = sum(trace.total_tokens or 0 for trace in traces)
    response_models = sorted(
        {trace.response_model for trace in traces if trace.response_model is not None}
    )
    if not traces or response_models != [provider.model]:
        raise RuntimeError("Provider traces do not prove the requested DeepSeek model.")

    summary: dict[str, Any] = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "status": "passed",
        "source": {
            "path": str(source_path),
            "sha256": _sha256(source_path),
            "blocks": len(document.blocks),
        },
        "output_golden": {
            "path": str(golden_path),
            "sha256": _sha256(golden_path),
            "role": "parser-normalized Eclipse output Golden File",
        },
        "semantic_gold": {
            "available": False,
            "metrics_computed": False,
            "reason": "Concept/path annotation Gold data is still being collected.",
        },
        "provider": provider.provider_name,
        "requested_model": provider.model,
        "response_models": response_models,
        "provider_calls": len(traces),
        "total_tokens": total_tokens,
        "semantic_mappings": len(semantic_batch.mapped),
        "unresolved_mappings": len(semantic_batch.unresolved),
        "review_required": semantic_batch.review_required,
        "canonical_validation_valid": validation.valid,
        "export_validation_valid": export.validation.valid,
        "opm_parser": comparison["parser"],
        "opm_parser_version": comparison["parser_version"],
        "opm_golden_valid": comparison["golden_parser_valid"],
        "opm_generated_valid": comparison["generated_parser_valid"],
        "golden_semantic_equal": comparison["semantic_equal"],
        "keywords": comparison["generated"]["keywords"],
        "elapsed_seconds": elapsed_seconds,
        "security": {
            "credential_recorded": False,
            "prompts_recorded": False,
        },
    }

    (output_dir / "generated_eclipse.inc").write_text(export.content, encoding="utf-8")
    _write_json(output_dir / "opm_golden_comparison.json", comparison)
    _write_json(output_dir / "provider_trace.json", trace_payload)
    _write_json(output_dir / "run_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    arguments = parser.parse_args()
    summary = asyncio.run(
        run(
            source_path=arguments.source,
            golden_path=arguments.golden,
            output_dir=arguments.output_dir,
        )
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "requested_model": summary["requested_model"],
                "response_models": summary["response_models"],
                "provider_calls": summary["provider_calls"],
                "total_tokens": summary["total_tokens"],
                "opm_parser_version": summary["opm_parser_version"],
                "golden_semantic_equal": summary["golden_semantic_equal"],
                "output_dir": str(arguments.output_dir.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
