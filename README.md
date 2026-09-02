# Reservoir Data Translator

Ontology-driven reservoir simulation data translation PoC.

The internal architecture keeps semantic understanding separate from deterministic
canonical-model construction and simulator export. Tasks 1-12 provide the Python
foundation, externalized Company Ontology, platform-independent Pydantic canonical
models, deterministic unit normalization and construction, four-level validation,
format-neutral ingestion, deterministic ontology retrieval, a guarded semantic
model abstraction, cross-source consistency fixtures, deterministic Eclipse/CMG
demo mappers, and a staged FastAPI pipeline.

## Current scope (Tasks 1-12)

- YAML ontology manifest, machine-readable Convention, and domain concept files
- immutable in-memory ontology concepts
- deterministic alias lookup
- concept relationship validation
- structured `ERROR` / `WARNING` / `INFO` convention validation
- controlled value type, dimension/unit, constraint, and relationship vocabularies
- table topology, inverse relationship, source-pollution, and lifecycle checks
- reusable `PhysicalValue` and `Provenance` models
- canonical rock, fluid/PVT, SCAL, well/control, and schedule models
- strict JSON input/output with unknown-field rejection
- deterministic JSON Schema generation for the six design-level model groups
- Pint-backed pressure, rate, viscosity, density, time, and compressibility conversion
- explicit 30-day month and 365-day year policy for PoC schedule normalization
- structured `SemanticMapping` results with source-block provenance
- deterministic `CanonicalBuilder` with concept/path/unit contract enforcement
- stable collection grouping for PVT points, SCAL tables, wells, controls, and constraints
- L1 schema, L2 ontology-instance, L3 domain, and target-delegated L4 export validation
- structured path-addressable `ValidationResult` errors and warnings
- format-neutral `RawDocument` / `RawBlock` models with source locations
- TXT paragraph, JSON structure, CSV table, and XLSX worksheet parsers
- deterministic alias-first and keyword-fallback `OntologyRetriever`
- provider-neutral async `SemanticModelProvider` structured-output contract
- DeepSeek Responses API provider with Pydantic JSON Schema output and local revalidation
- environment-first DeepSeek credentials with an ignored local key-file fallback
- non-secret DeepSeek call traces for live smoke-test evidence
- `MAPPED`, `UNMAPPED`, and `AMBIGUOUS` semantic outcomes
- enforcement that provider concepts, canonical paths, and canonical units come
  from the retrieved ontology/canonical contracts
- parser-owned provenance retained independently of provider-proposed excerpts
- external customer Source Mapping registries that do not pollute ontology aliases
- three heterogeneous CSV/JSON/TXT datasets with Canonical equivalence testing
- external Eclipse and CMG Platform Output Mapping registries
- deterministic `PlatformMapper` mapping/rendering split and L4 export validation
- METRIC ECLIPSE/OPM demo INCLUDE generation
- explicitly labelled CMG IMEX-style demo well-control fragment generation
- FastAPI endpoints for ingest, semantic mapping, Canonical build, validation,
  export, and full translation
- translation IDs and an in-response stage trace
- unit tests

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev]'
.venv/bin/ontology-validate ontology
.venv/bin/python -m pytest
.venv/bin/uvicorn reservoir_data_translator.api.main:app --reload
```

## Basic usage

```python
from reservoir_data_translator.ontology import OntologyRegistry

registry = OntologyRegistry.load("ontology")
matches = registry.search_by_alias("日产液")
assert matches[0].concept_id == "well.control.liquid_rate"
assert registry.validation.valid
```

Ingestion identifies only file-level structure; it does not decide what a field
means:

```python
from reservoir_data_translator.ingestion import parse_document

raw_document = parse_document("client_data.xlsx", source_id="client-a")
assert raw_document.source_type == "xlsx"
assert raw_document.blocks[0].block_type == "table"
```

Candidate retrieval is deterministic and can be inspected before any model call:

```python
from reservoir_data_translator.semantic import OntologyRetriever

retriever = OntologyRetriever(registry)
candidates = retriever.retrieve(raw_document.blocks[0], top_k=8)
```

`SemanticMappingAgent` requires an application-supplied implementation of the
abstract `SemanticModelProvider`. The provider receives the Pydantic
`SemanticModelResponse` type and must return structured data. The agent rejects
free text, unsupplied concepts, invented canonical paths, and incorrect canonical
units:

```python
from reservoir_data_translator.semantic import SemanticMappingAgent

agent = SemanticMappingAgent(registry, provider, retriever=retriever)
mapping_batch = await agent.map_document(raw_document)
semantic_mappings = mapping_batch.mapped
unresolved = mapping_batch.unresolved
```

The bundled hosted provider uses DeepSeek's Responses API with
`deepseek-v4-flash` by default. Credentials are resolved from
`DEEPSEEK_API_KEY` first, then `DEEPSEEK_API_KEY_FILE`, then the project-local
ignored file `LLM/DeepSeek/api_key` when the default FastAPI app is created:

```python
from reservoir_data_translator.semantic import DeepSeekProvider

provider = DeepSeekProvider.from_environment(
    api_key_file="LLM/DeepSeek/api_key",
)
```

Optional runtime configuration:

```text
RESERVOIR_SEMANTIC_PROVIDER=deepseek  # use "disabled" for deterministic-only mode
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT_SECONDS=120
```

Run the real synthetic semantic smoke test without printing the credential:

```bash
.venv/bin/python scripts/smoke_deepseek.py \
  --output artifacts/deepseek_semantic_smoke.json
```

Customer-only terms such as `LRAT`, `PROD`, and `pressure_floor` live in
`mappings/customer_*.yaml`. Pass the selected `source_system` through the API or
construct `OntologyRetriever` with the corresponding `SourceMappingRegistry`.

Canonical models use standard Pydantic v2 serialization and validation APIs:

```python
from reservoir_data_translator.canonical import (
    PhysicalValue,
    ReservoirSimulationModel,
    generate_json_schemas,
    write_json_schemas,
)

pressure = PhysicalValue(value=200, unit="bar")
schema = ReservoirSimulationModel.model_json_schema()
schemas = generate_json_schemas()
write_json_schemas("canonical/schemas")
```

Unit conversion is deterministic and accepts only the v0.1 vocabulary:

```python
from reservoir_data_translator.semantic import UnitNormalizer

normalizer = UnitNormalizer()
assert normalizer.normalize(5, "year", "day") == 1825
assert normalizer.normalize(1, "g/cm3", "kg/m3") == 1000
```

`CanonicalBuilder` accepts manually or agent-produced `MAPPED` outcomes.
It checks the ontology concept, canonical path, and ontology-owned canonical unit
before constructing the Pydantic model. It never invents a missing value.

```python
from reservoir_data_translator.canonical import CanonicalBuilder

builder = CanonicalBuilder(registry)
canonical = builder.build(semantic_mappings)
```

Canonical and target export readiness are deliberately separate:

```python
from reservoir_data_translator.validation import ValidationEngine

result = ValidationEngine(registry).validate(canonical)
assert result.valid
```

The two demo mappers consume the same Canonical model and never call an LLM:

```python
from reservoir_data_translator.mappers import (
    EclipseDemoMapper,
    PlatformMappingRegistry,
)

mapping = PlatformMappingRegistry.load("mappings/eclipse.yaml", registry)
eclipse = EclipseDemoMapper(mapping)
export = eclipse.export(canonical)
```

L4 export checks remain target-specific. The Eclipse artifact is an INCLUDE that
requires a compatible host deck. The CMG artifact is deliberately labelled as an
unverified-version IMEX-style demo control fragment; it is not represented as a
standalone production dataset.

## API

The six required endpoints are:

```text
POST /ingest
POST /semantic-map
POST /canonical/build
POST /validate
POST /export/{platform}
POST /translate
```

Text sources are sent as UTF-8 JSON strings. Binary XLSX content uses base64:

```json
{
  "file_name": "client.xlsx",
  "content_encoding": "base64",
  "content": "..."
}
```

The default application loads `ontology/` and `mappings/` from the project or
from `RESERVOIR_ONTOLOGY_PATH` / `RESERVOIR_MAPPING_PATH`. It configures DeepSeek
when a supported credential source is present. Otherwise semantic endpoints
return `503 SEMANTIC_PROVIDER_NOT_CONFIGURED`; deterministic endpoints remain
usable.

## Deliberate PoC boundaries

- Only DeepSeek is bundled; OpenAI and local providers are still application extensions.
- Live-provider evaluation currently has one synthetic smoke case, not a representative
  extraction accuracy, drift, latency, or cost benchmark suite.
- No persistent human-review queue or durable translation replay store exists.
- Entity resolution remains exact selector grouping; normalized/agent-assisted
  entity merging is not implemented.
- Eclipse output still requires host-deck context and an OPM/commercial parser
  compatibility smoke test for the chosen deployment version.
- CMG product/version grammar has not been frozen or validated with CMG software.
- The Phase 5 upload/review UI is not implemented.

The human-readable modeling rules are in
[`ONTOLOGY_CONVENTIONS.md`](ONTOLOGY_CONVENTIONS.md). Source/platform terminology is
kept outside the canonical ontology under [`mappings/`](mappings/README.md).
