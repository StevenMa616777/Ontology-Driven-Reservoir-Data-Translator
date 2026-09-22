"""Deterministic ontology candidate retrieval for raw source blocks."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable

from reservoir_data_translator.ingestion import RawBlock
from reservoir_data_translator.ontology import OntologyConcept, OntologyRegistry

from reservoir_data_translator.ontology.context import SemanticContext

from .source_mapping import SourceMappingRegistry


_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "columns",
        "data",
        "for",
        "in",
        "is",
        "key",
        "of",
        "on",
        "or",
        "rows",
        "the",
        "to",
        "value",
        "with",
    }
)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)
    normalized = normalized.casefold()
    return " ".join(re.sub(r"[_\W]+", " ", normalized).split())


def _compact(value: str) -> str:
    return value.replace(" ", "")


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _normalize(value).split()
        if len(token) > 1 and token not in _STOP_WORDS
    )


def _contains(query: str, term: str) -> bool:
    # Short Latin symbols must be words (Bo must not match 'bottom'). Chinese
    # aliases still support embedded phrases without whitespace boundaries.
    if term.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", query) is not None
    return _compact(term) in _compact(query)


def _bounded_evidence(source, text, registry):
    """Collect domain/phase evidence within a row schema or one local clause.

    Do not transfer a phase from density to PVT merely because both appear in
    the document. The existing unknown-phase gate remains authoritative when
    no local association can be established.
    """
    scopes, hints, concepts = set(), {}, set()

    def phases(value):
        normalized = _normalize(value)
        return {phase for phase in ("oil", "water", "gas")
                if any(_contains(normalized, _normalize(alias))
                       for alias in registry.get_concept(f"fluid.{phase}").aliases)}

    def add(scope, found):
        scopes.add(scope)
        hints.setdefault(scope, set()).update(found)

    if isinstance(source, RawBlock) and source.block_type == "table":
        columns = [_normalize(str(c)) for c in source.content["columns"]]
        header = " ".join(columns)
        if ("pressure" in header and any(term in header for term in ("formation volume", "viscosity"))):
            found = set()
            for index, column in enumerate(columns):
                if column in {"fluid", "phase", "fluid phase"}:
                    for row in source.content["rows"]:
                        found.update(phases(str(row[index])))
            add("pvt", found)
    for clause in re.split(r"[;；\n]", text):
        normalized = _normalize(clause)
        density = bool(re.search(r"density|密度|kg\s*/\s*m(?:3|³)|g\s*/\s*cm(?:3|³)", clause, re.I))
        pvt = any(_contains(normalized, term) for term in
                  ("pvt", "formation volume factor", "viscosity", "体积系数", "粘度", "黏度"))
        if density and not pvt:
            add("fluid_properties", phases(clause))
        if pvt and not density:
            add("pvt", phases(clause))
        if _contains(normalized, "岩石") and _contains(normalized, "压缩系数"):
            scopes.add("rock")
            concepts.add("rock.compressibility")
        if re.search(r"(?:每|间隔)\s*\d+\s*个?(?:月|天|年).*?(?:输出|报告|看一次)", clause):
            scopes.add("schedule")
            concepts.add("schedule.report_interval")
    return scopes, hints, concepts


@dataclass(frozen=True, slots=True)
class OntologyCandidate:
    """A ranked ontology concept plus deterministic retrieval evidence."""

    concept: OntologyConcept
    score: float
    match_type: str
    matched_terms: tuple[str, ...]
    context: SemanticContext | None = None
    context_supported: bool = True

    @property
    def semantic_key(self) -> tuple[str, SemanticContext | None]:
        return self.concept_id, self.context

    @property
    def concept_id(self) -> str:
        return self.concept.concept_id

    @property
    def name(self) -> str:
        return self.concept.name

    def as_prompt_dict(self) -> dict[str, object]:
        """Serialize only stable ontology facts needed by a model provider."""

        return {
            "concept_id": self.concept.concept_id,
            "context": self.context.model_dump(exclude_none=True) if self.context else None,
            "context_requires_disambiguation": not self.context_supported,
            "name": self.concept.name,
            "description": self.concept.description,
            "value_type": self.concept.value_type,
            "dimension": self.concept.dimension,
            "canonical_unit": self.concept.canonical_unit,
            "constraints": dict(self.concept.constraints),
            "relationships": {
                relation: list(targets)
                for relation, targets in self.concept.relationships.items()
            },
            "retrieval": {
                "score": self.score,
                "match_type": self.match_type,
                "matched_terms": list(self.matched_terms),
            },
        }


class OntologyRetriever:
    """Rank alias matches first, then deterministic lexical overlap.

    The interface returns scored candidates so an embedding-backed strategy can
    be added later without changing the semantic agent contract.
    """

    def __init__(
        self,
        registry: OntologyRegistry,
        *,
        default_top_k: int = 8,
        source_mappings: Iterable[SourceMappingRegistry] = (),
    ) -> None:
        if default_top_k < 1:
            raise ValueError("default_top_k must be at least 1")
        self._registry = registry
        self.default_top_k = default_top_k
        self._source_mappings = tuple(source_mappings)

    def retrieve(
        self,
        source: RawBlock | str,
        *,
        top_k: int | None = None,
    ) -> list[OntologyCandidate]:
        """Return only positively matched active concepts in stable rank order."""

        limit = self.default_top_k if top_k is None else top_k
        if limit < 1:
            raise ValueError("top_k must be at least 1")
        text = source.searchable_text() if isinstance(source, RawBlock) else source
        if not isinstance(text, str) or not text.strip():
            return []

        normalized_query = _normalize(text)
        compact_query = _compact(normalized_query)
        query_tokens = _tokens(text)
        local_scopes, local_phases, local_concepts = _bounded_evidence(source, text, self._registry)
        explicit_scopes = set(self._registry.scopes.detect(text)) | local_scopes
        scopes = set(explicit_scopes)
        # A mixed paragraph may name a quantity without a domain heading (e.g.
        # a trailing 'duration'). Retain its possible scopes instead of pruning
        # facts solely because another scope was detected first.
        for concept in self._registry.list_concepts():
            terms = (concept.concept_id.rsplit(".", 1)[-1], concept.name, *concept.aliases)
            if any(_contains(normalized_query, _normalize(term)) for term in terms):
                uses = {b.context.scope for b in self._registry.scopes.bindings
                        if b.concept_id == concept.concept_id}
                if not uses.intersection(explicit_scopes):
                    scopes.update(uses)
        ranked_by_key: dict[tuple[str, SemanticContext | None], OntologyCandidate] = {}

        def retain(candidate: OntologyCandidate) -> None:
            current = ranked_by_key.get(candidate.semantic_key)
            if current is None or candidate.score > current.score:
                ranked_by_key[candidate.semantic_key] = candidate

        bindings = self._registry.scopes.bindings
        # Context aliases provide phase evidence independently of quantity names.
        # Unknown phase keeps all choices for review; explicit phase narrows only
        # that scope, so mixed-domain blocks do not steal each other's phase.
        phase_hints: dict[str, set[str]] = {}
        for binding in bindings:
            if binding.context.phase and any(_contains(normalized_query, _normalize(alias))
                                             for alias in binding.aliases):
                phase_hints.setdefault(binding.context.scope, set()).add(binding.context.phase)
        for scope, phases in local_phases.items():
            phase_hints.setdefault(scope, set()).update(phases)
        bare_phases = {
            phase for phase in ("oil", "water", "gas")
            if any(_contains(normalized_query, _normalize(alias))
                   for alias in self._registry.get_concept(f"fluid.{phase}").aliases)
        }
        phase_scopes = {scope for scope in scopes if self._registry.scopes.get_scope(scope).phases}
        # A bare phase is useful in a single-domain header, but must not transfer
        # from e.g. oil density to an unspecified PVT table in the same block.
        if len(phase_scopes) == 1 and not phase_hints and bare_phases:
            scope = next(iter(phase_scopes))
            if scope in {"pvt", "fluid_properties"}:
                phase_hints[scope] = bare_phases
        for source_mapping in self._source_mappings:
            if not source_mapping.applies_to(text):
                continue
            for match in source_mapping.search(text):
                concept = self._registry.get_concept(match.concept_id)
                if concept.status != "active":
                    continue
                contexts = [match.context] if match.context else [
                    b.context for b in bindings if b.concept_id == match.concept_id
                ] or [None]
                for context in contexts:
                    retain(OntologyCandidate(
                        concept, 0.99 if match.exact else 0.98,
                        "source_mapping", (match.source_term,), context,
                    ))

        # A binding is a use of a concept, not another ontology concept. Keep
        # distinct phase/role choices even when their concept IDs are identical.
        entries = [(self._registry.get_concept(b.concept_id), b.context, b.aliases)
                   for b in bindings if (not scopes or b.context.scope in scopes)
                   and (b.context.scope not in {"pvt", "fluid_properties"}
                        or not phase_hints.get(b.context.scope)
                        or b.context.phase in phase_hints[b.context.scope])]
        bound_ids = {b.concept_id for b in bindings}
        entries.extend((c, None, ()) for c in self._registry.list_concepts()
                       if c.concept_id not in bound_ids)
        for concept, context, contextual_aliases in entries:
            if concept.status != "active":
                continue
            supported = (
                context is None or not concept.concept_id.startswith(("physical.", "fluid.pvt"))
                or ((context.scope in explicit_scopes or context.scope == "relative_permeability")
                    and (context.phase is None
                         or context.phase in phase_hints.get(context.scope, set())))
            )
            if context and context.phase_system == "oil_water":
                phase_pair_supported = (
                    {"oil", "water"} <= phase_hints.get(context.scope, set())
                    or any(_contains(normalized_query, _normalize(term))
                           for term in ("oil water", "water oil", "油水", "水驱油"))
                )
                supported = supported and phase_pair_supported
            aliases = (*concept.aliases, *contextual_aliases)
            if concept.concept_id in local_concepts:
                retain(OntologyCandidate(concept, 0.9, "bounded_context", (concept.concept_id,), context, supported))
            alias_matches = []
            for alias in aliases:
                normalized_alias = _normalize(alias)
                if not normalized_alias:
                    continue
                if compact_query == _compact(normalized_alias):
                    alias_matches.append((1.0, alias))
                elif _contains(normalized_query, normalized_alias):
                    alias_matches.append((0.9, alias))
            if alias_matches:
                score = max(score for score, _ in alias_matches)
                terms = tuple(sorted({term for rank, term in alias_matches if rank == score}))
                retain(OntologyCandidate(concept, score, "alias", terms, context, supported))
                continue
            concept_text = " ".join((concept.concept_id.replace(".", " "),
                                     concept.name, concept.description, *aliases))
            overlap = query_tokens & _tokens(concept_text)
            if overlap:
                score = round(min(0.79, 0.45 + 0.34 * len(overlap) / max(len(query_tokens), 1)), 6)
                retain(OntologyCandidate(concept, score, "keyword", tuple(sorted(overlap)), context, supported))

        ranked = sorted(ranked_by_key.values(), key=lambda candidate: (
            -candidate.score, -candidate.concept_id.count("."), candidate.concept_id,
            candidate.context.model_dump_json() if candidate.context else "",
        ))
        return ranked[:limit]

    def retrieve_concepts(
        self,
        source: RawBlock | str,
        *,
        top_k: int | None = None,
    ) -> list[OntologyConcept]:
        """Convenience projection for callers that do not need scores."""

        return [candidate.concept for candidate in self.retrieve(source, top_k=top_k)]

    def source_profiles(self, block: RawBlock) -> list[dict[str, object]]:
        return [{"source_system": m.source_system, "instructions": m.definition.instructions}
                for m in self._source_mappings if m.applies_to(block.searchable_text())]

    def missing_pressure_unit(self, block: RawBlock) -> bool:
        text = block.searchable_text()
        required = any(m.definition.requires_pressure_unit and m.applies_to(text) for m in self._source_mappings)
        explicit_unit = re.search(r"(?<![A-Za-z])(?:bar|barsa|psi|psia|kPa|MPa|Pa)(?![A-Za-z])", text, re.I)
        unit_system = re.search(r"^\s*(?:METRIC|FIELD)\s*(?:--[^\n]*)?$", text, re.I | re.M)
        return required and not (explicit_unit or unit_system)
