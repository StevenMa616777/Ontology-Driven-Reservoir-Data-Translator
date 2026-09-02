# DESIGN.md implementation completeness

Status date: 2026-09-02

This file is the task-by-task and layer-by-layer implementation matrix. For the
project narrative and runtime flow, see [`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md).
For the frozen acceptance scope and closure decision, see
[`docs/POC_CLOSURE.md`](docs/POC_CLOSURE.md).

## PoC closure evidence

The bundled Demo now passes one reproducible real-provider acceptance chain:

- `deepseek-v4-flash` mapped all three source blocks into 71 accepted semantic
  mappings with no unresolved or review-required outcomes.
- Canonical construction and L1-L4 validation passed.
- Deterministic Eclipse output contains `SWOF`, `PVDO`, `PVDG`, `PVTW`,
  `DENSITY`, `ROCK`, `WCONPROD`, `WCONINJE`, and `TSTEP`.
- OPM Python Parser 2025.10 parsed both the bundled output Golden File and the
  generated INCLUDE in a minimal host-deck context; their normalized keyword
  semantics are equal.
- The full automated suite passes 129 tests, and a rebuilt
  wheel passes installed import, CLI, quarter-unit, and OPM comparison checks.

The `.inc` comparison is output-Golden validation. It does not substitute for
the concept/path annotation Gold dataset that is still being collected, so no
semantic extraction precision, recall, or F1 is claimed.

## Task acceptance

| Task | Status | Evidence |
|---|---|---|
| 1 | Complete | YAML ontology, Convention, immutable Registry, structured Definition Validator and CLI |
| 2 | Complete | Strict platform-independent Pydantic Canonical models and JSON Schema generation |
| 3 | Complete | Deterministic Pint unit normalization with explicit time policy |
| 4 | Complete | Structured SemanticMapping and deterministic CanonicalBuilder |
| 5 | Complete | L1 schema, L2 ontology-instance, L3 domain and delegated L4 export validation |
| 6 | Complete | RawDocument/RawBlock and TXT/JSON/CSV/XLSX parsing without domain mapping |
| 7 | Complete | Alias, keyword and external Source Mapping candidate retrieval |
| 8 | Complete | Provider abstraction, structured output, supplied-concept/path/unit enforcement, UNMAPPED/AMBIGUOUS |
| 9 | Complete | Client A CSV, Client B JSON and Client C TXT produce equivalent business Canonical payloads |
| 10 | Complete for demo scope | Deterministic Eclipse intermediate mapping, rendering and export validation |
| 11 | Complete for demo scope | Deterministic CMG demo control mapping using the same Canonical model |
| 12 | Complete | Six FastAPI endpoints plus full traced translation response |

## Layer audit

| Layer | PoC status | Remaining boundary |
|---|---|---|
| Company Ontology | Complete for v0.1 concepts and conventions | Not a complete reservoir ontology; long-term Role/time model remains future work |
| Source Mapping | Complete for the three Task 9 clients | No governance UI, mapping approval workflow or large customer catalog |
| Ingestion | Complete for TXT, JSON, CSV and XLSX | No PDF/DOCX/OCR, streaming large workbooks or password-protected files |
| Ontology Retrieval | Complete for deterministic lexical/source mappings | No embedding index, retrieval evaluation corpus or context-budget strategy |
| Semantic Agent | DeepSeek V4 Flash provider and complete bundled-Demo run pass | OpenAI/local providers and a representative annotated semantic Gold corpus remain unimplemented |
| Human Review | Safety stop, confidence thresholds and explicit session-only low-confidence approval implemented | No persistent review queue, mapping edit workflow or reviewer audit history |
| Entity Resolution | Partial | Builder groups exact selectors only; normalized aliases and uncertain merge review are not implemented |
| Unit Normalization | Complete for v0.1 vocabulary | No broader unit catalog or source-unit ontology governance |
| Canonical Model/Builder | Complete for v0.1 schema | Not full PVT/SCAL/schedule coverage; time-varying well roles are not modeled |
| Validation L1-L3 | Complete for v0.1 rules | No simulator-independent full engineering rule library or external reference-condition reconciliation |
| Eclipse Mapper/L4 | Demo output passes OPM Python Parser 2025.10 and its output Golden | A real host deck plus actual Flow/commercial simulator execution is still required |
| CMG Mapper/L4 | Demo control fragment complete | CMG product/version grammar is not frozen; non-well domains are intentionally not rendered |
| API | Complete for six Task 12 endpoints | No authentication, authorization, rate limits, job queue, object storage or API version migration policy |
| Trace/Replay | In-response translation ID and stage trace implemented | No durable append-only event store, replay endpoint or operational observability backend |
| Evaluation | Cross-source equivalence, 129-test regression suite, synthetic smoke, and full real DeepSeek Demo/OPM/Golden chain pass | No semantic Gold accuracy metrics, production metrics store, drift dashboard, or representative benchmark suite |
| UI | Complete for the local PoC workbench: upload/paste, mapping, review, Canonical, validation, export and trace | No authentication, durable review/replay, artifact-bundle storage or production deployment |

## Production-readiness conclusion

Tasks 1-12 and the v0.1 automated Definition of Done are implemented at PoC level.
The system is not yet a production translator or a verified commercial-simulator
deck generator. The next release should add the collected semantic Gold labels,
exercise this INCLUDE inside a real host deck with Flow, freeze and validate one
CMG product/version, then implement durable human review and replay.
