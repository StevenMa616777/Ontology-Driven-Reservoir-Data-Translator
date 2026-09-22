"""Semantic participation independent of canonical storage paths."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SemanticContext(BaseModel):
    """A concept's explicit, immutable use within one semantic task domain."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    scope: str = Field(min_length=1)
    phase: Literal["oil", "water", "gas"] | None = None
    role: str | None = Field(default=None, min_length=1)
    phase_system: str | None = Field(default=None, min_length=1)
