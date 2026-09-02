"""Structured output contract for semantic mapping results."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from reservoir_data_translator.canonical.models import (
    CanonicalModel,
    Confidence,
    NonEmptyString,
    Provenance,
)


class SemanticMapping(CanonicalModel):
    """One evidence-backed source value mapped to ontology and canonical paths."""

    status: Literal["MAPPED"] = "MAPPED"
    source_text: str | None = None
    source_block_id: NonEmptyString
    ontology_concept: NonEmptyString
    canonical_path: NonEmptyString
    value: Any
    source_unit: NonEmptyString | None = None
    canonical_unit: NonEmptyString | None = None
    confidence: Confidence
    provenance: Provenance

    @model_validator(mode="after")
    def source_block_matches_provenance(self) -> "SemanticMapping":
        provenance_block = self.provenance.source_block_id
        if provenance_block is not None and provenance_block != self.source_block_id:
            raise ValueError(
                "source_block_id must match provenance.source_block_id when both "
                "are supplied"
            )
        return self


class UnmappedSemanticMapping(CanonicalModel):
    """Source content for which no supplied ontology concept is valid."""

    status: Literal["UNMAPPED"] = "UNMAPPED"
    source_text: str | None = None
    source_field: NonEmptyString | None = None
    source_block_id: NonEmptyString
    candidate_concepts: list[NonEmptyString] = Field(default_factory=list)
    confidence: Confidence = 0.0
    provenance: Provenance

    @model_validator(mode="after")
    def validate_unmapped(self) -> "UnmappedSemanticMapping":
        if self.confidence != 0:
            raise ValueError("UNMAPPED confidence must be 0")
        if len(self.candidate_concepts) != len(set(self.candidate_concepts)):
            raise ValueError("candidate_concepts must not contain duplicates")
        _validate_provenance_block(self.source_block_id, self.provenance)
        return self


class AmbiguousSemanticMapping(CanonicalModel):
    """Source content with at least two unresolved supplied concepts."""

    status: Literal["AMBIGUOUS"] = "AMBIGUOUS"
    source_text: str | None = None
    source_field: NonEmptyString | None = None
    source_block_id: NonEmptyString
    candidate_concepts: list[NonEmptyString] = Field(min_length=2)
    value: Any = None
    source_unit: NonEmptyString | None = None
    confidence: Confidence
    provenance: Provenance

    @model_validator(mode="after")
    def validate_ambiguous(self) -> "AmbiguousSemanticMapping":
        if len(self.candidate_concepts) != len(set(self.candidate_concepts)):
            raise ValueError("candidate_concepts must not contain duplicates")
        _validate_provenance_block(self.source_block_id, self.provenance)
        return self


SemanticMappingOutcome = Annotated[
    SemanticMapping | UnmappedSemanticMapping | AmbiguousSemanticMapping,
    Field(discriminator="status"),
]


class SemanticMappingBatch(CanonicalModel):
    """All structured semantic outcomes produced for one raw document."""

    source_id: NonEmptyString
    mappings: list[SemanticMappingOutcome]

    @property
    def mapped(self) -> list[SemanticMapping]:
        return [
            mapping
            for mapping in self.mappings
            if isinstance(mapping, SemanticMapping)
        ]

    @property
    def unresolved(
        self,
    ) -> list[UnmappedSemanticMapping | AmbiguousSemanticMapping]:
        return [
            mapping
            for mapping in self.mappings
            if not isinstance(mapping, SemanticMapping)
        ]

    @property
    def review_required(self) -> bool:
        """Follow the design threshold without silently accepting low confidence."""

        return bool(self.unresolved) or any(
            mapping.confidence < 0.80 for mapping in self.mapped
        )

    @property
    def accepted_with_warning(self) -> list[SemanticMapping]:
        return [
            mapping
            for mapping in self.mapped
            if 0.80 <= mapping.confidence < 0.95
        ]

    @property
    def auto_accepted(self) -> list[SemanticMapping]:
        return [mapping for mapping in self.mapped if mapping.confidence >= 0.95]


class MappedMappingDraft(CanonicalModel):
    """Provider-owned structured fields before trusted provenance is attached."""

    status: Literal["MAPPED"] = "MAPPED"
    source_text: str | None = None
    source_block_id: NonEmptyString
    ontology_concept: NonEmptyString
    canonical_path: NonEmptyString
    value: Any
    source_unit: NonEmptyString | None = None
    canonical_unit: NonEmptyString | None = None
    confidence: Confidence


class UnmappedMappingDraft(CanonicalModel):
    status: Literal["UNMAPPED"] = "UNMAPPED"
    source_text: str | None = None
    source_field: NonEmptyString | None = None
    source_block_id: NonEmptyString
    candidate_concepts: list[NonEmptyString] = Field(default_factory=list)
    confidence: Confidence = 0.0

    @model_validator(mode="after")
    def confidence_is_zero(self) -> "UnmappedMappingDraft":
        if self.confidence != 0:
            raise ValueError("UNMAPPED confidence must be 0")
        if len(self.candidate_concepts) != len(set(self.candidate_concepts)):
            raise ValueError("candidate_concepts must not contain duplicates")
        return self


class AmbiguousMappingDraft(CanonicalModel):
    status: Literal["AMBIGUOUS"] = "AMBIGUOUS"
    source_text: str | None = None
    source_field: NonEmptyString | None = None
    source_block_id: NonEmptyString
    candidate_concepts: list[NonEmptyString] = Field(min_length=2)
    value: Any = None
    source_unit: NonEmptyString | None = None
    confidence: Confidence

    @model_validator(mode="after")
    def candidates_are_unique(self) -> "AmbiguousMappingDraft":
        if len(self.candidate_concepts) != len(set(self.candidate_concepts)):
            raise ValueError("candidate_concepts must not contain duplicates")
        return self


SemanticMappingDraft = Annotated[
    MappedMappingDraft | UnmappedMappingDraft | AmbiguousMappingDraft,
    Field(discriminator="status"),
]


class SemanticModelResponse(CanonicalModel):
    """Only valid structured response accepted from a semantic provider."""

    mappings: list[SemanticMappingDraft] = Field(min_length=1)


def _validate_provenance_block(
    source_block_id: str,
    provenance: Provenance,
) -> None:
    provenance_block = provenance.source_block_id
    if provenance_block is not None and provenance_block != source_block_id:
        raise ValueError(
            "source_block_id must match provenance.source_block_id when both "
            "are supplied"
        )
