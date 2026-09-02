"""Format-neutral source document models.

The ingestion layer deliberately preserves source structure without assigning
reservoir-domain meaning.  A parser may identify text, table, and key/value
shapes because those are properties of a file format; it must not decide that a
column named ``Rate`` is a particular canonical control.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Mapping

from pydantic import model_validator

from reservoir_data_translator.canonical.models import CanonicalModel, NonEmptyString


BlockType = Literal["text", "table", "key_value"]


class RawBlock(CanonicalModel):
    """One addressable, format-level unit of source content."""

    block_id: NonEmptyString
    block_type: BlockType
    content: Any
    source_location: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_content_shape(self) -> "RawBlock":
        if self.block_type == "text" and not isinstance(self.content, str):
            raise ValueError("text block content must be a string")
        if self.block_type == "table":
            self._validate_table(self.content)
        if self.block_type == "key_value":
            self._validate_key_value(self.content)
        return self

    def searchable_text(self) -> str:
        """Return a deterministic textual projection for retrieval and prompts."""

        if isinstance(self.content, str):
            return self.content
        return json.dumps(
            self.content,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )

    @staticmethod
    def _validate_table(content: Any) -> None:
        if not isinstance(content, Mapping):
            raise ValueError("table block content must be a mapping")
        columns = content.get("columns")
        rows = content.get("rows")
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise ValueError("table block content requires list columns and rows")
        for row in rows:
            if not isinstance(row, list):
                raise ValueError("each table row must be a list")
            if len(row) != len(columns):
                raise ValueError("table row width must match the column count")

    @staticmethod
    def _validate_key_value(content: Any) -> None:
        if not isinstance(content, Mapping):
            raise ValueError("key_value block content must be a mapping")
        if set(content) != {"key", "value"}:
            raise ValueError("key_value block content requires exactly key and value")
        if not isinstance(content["key"], str) or not content["key"].strip():
            raise ValueError("key_value block key must be a non-empty string")


class RawDocument(CanonicalModel):
    """A parsed source file before any semantic or unit interpretation."""

    source_id: NonEmptyString
    source_type: NonEmptyString
    file_name: NonEmptyString
    blocks: list[RawBlock]

    @model_validator(mode="after")
    def block_ids_are_unique(self) -> "RawDocument":
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("RawDocument block_id values must be unique")
        return self
