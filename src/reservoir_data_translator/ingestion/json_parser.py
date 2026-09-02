"""JSON ingestion that preserves structural paths and native scalar types."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .base import DocumentParser, IngestionError
from .models import RawBlock, RawDocument


def _json_path_key(key: str) -> str:
    if key.isidentifier():
        return f".{key}"
    return f"[{json.dumps(key, ensure_ascii=False)}]"


def _homogeneous_object_table(value: list[Any]) -> tuple[list[str], list[list[Any]]] | None:
    if not value or not all(isinstance(item, Mapping) for item in value):
        return None
    columns = list(value[0])
    if not columns or any(set(item) != set(columns) for item in value[1:]):
        return None
    return columns, [[item[column] for column in columns] for item in value]


class JsonParser(DocumentParser):
    """Represent JSON leaves as key/value blocks and record arrays as tables."""

    source_type = "json"
    suffixes = (".json",)

    def parse(
        self,
        path: str | Path,
        *,
        source_id: str | None = None,
    ) -> RawDocument:
        source_path = self._source_path(path)
        try:
            with source_path.open("r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except UnicodeDecodeError as exc:
            raise IngestionError(
                "SOURCE_DECODE_ERROR",
                f"JSON source must be UTF-8: {source_path.name}",
                path=source_path,
            ) from exc
        except json.JSONDecodeError as exc:
            raise IngestionError(
                "MALFORMED_JSON",
                (
                    f"Could not parse JSON source {source_path.name} at "
                    f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
                ),
                path=source_path,
            ) from exc

        blocks: list[RawBlock] = []

        def append(block_type: str, content: Any, location: str) -> None:
            blocks.append(
                RawBlock(
                    block_id=f"block_{len(blocks) + 1:04d}",
                    block_type=block_type,
                    content=content,
                    source_location=location,
                )
            )

        def visit(value: Any, location: str, field_name: str) -> None:
            if isinstance(value, Mapping):
                if not value:
                    append("key_value", {"key": field_name, "value": {}}, location)
                    return
                if all(
                    not isinstance(child, (Mapping, list))
                    for child in value.values()
                ):
                    columns = [str(key) for key in value]
                    append(
                        "table",
                        {
                            "columns": columns,
                            "rows": [[value[key] for key in value]],
                        },
                        location,
                    )
                    return
                for key, child in value.items():
                    child_name = str(key)
                    visit(child, location + _json_path_key(child_name), child_name)
                return

            if isinstance(value, list):
                table = _homogeneous_object_table(value)
                if table is not None:
                    columns, rows = table
                    append(
                        "table",
                        {"columns": columns, "rows": rows},
                        location,
                    )
                    return
                if not value or all(
                    not isinstance(item, (Mapping, list)) for item in value
                ):
                    append(
                        "key_value",
                        {"key": field_name, "value": value},
                        location,
                    )
                    return
                for index, child in enumerate(value):
                    visit(child, f"{location}[{index}]", f"{field_name}[{index}]")
                return

            if location == "$" and isinstance(value, str):
                append("text", value, location)
            else:
                append(
                    "key_value",
                    {"key": field_name, "value": value},
                    location,
                )

        visit(payload, "$", "$")
        return RawDocument(
            source_id=self._source_id(source_path, source_id),
            source_type=self.source_type,
            file_name=source_path.name,
            blocks=blocks,
        )
