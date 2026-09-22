"""Bounded source context and conservative, auditable metadata handling.

Only known non-quantity metadata is shared. Arbitrary other-block facts never
enter a mapping prompt. Original parser blocks remain unchanged in the API.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from reservoir_data_translator.ingestion import RawBlock, RawDocument
from .models import SourceAnnotation


def field_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def classify_metadata(block: RawBlock) -> SourceAnnotation | None:
    kind = None
    if block.block_type == "key_value":
        key, value = field_key(block.content["key"]), block.content["value"]
        if (key in {"asset", "sourcesample", "sampleid"} and type(value) in {str, int}):
            kind = "metadata"
        elif key == "saturationbasis" and isinstance(value, str) and value.casefold() in {"fraction", "percent", "%"}:
            kind = "metadata"
    elif block.block_type == "text":
        lines = [line.strip() for line in block.content.splitlines() if line.strip()]
        heading = r"(?:岩心驱替实验记录|流体与岩石物性说明|项目运行参数备忘)(?:[（(][^\d()（）]*[）)])?"
        metadata = r"(?:(?:试样编号|样本编号|样本号|Sample_ID|specimen)\s*[:：]\s*[\w-]+|实验类型\s*[:：]\s*(?:水驱油|油驱水))"
        if lines and all(re.fullmatch(heading, line) or re.fullmatch(metadata, line, re.I) for line in lines):
            kind = "metadata" if any(re.fullmatch(metadata, line, re.I) for line in lines) else "heading"
        elif re.fullmatch(r"(?:备注[:：]\s*)?本页没有记录相渗和PVT参数[，,]\s*相关参数见实验室资料[。.]?", block.content.strip()):
            kind = "absence_statement"
    if kind is None:
        return None
    return SourceAnnotation(source_block_id=block.block_id, source_location=block.source_location,
                            kind=kind, content=block.content,
                            reason="Recognized source metadata; retained without inventing a canonical quantity.")


def prepare_context(document: RawDocument) -> tuple[dict[str, list[SourceAnnotation]], list[SourceAnnotation]]:
    annotations = [a for b in document.blocks if (a := classify_metadata(b)) is not None]
    by_id = {a.source_block_id: a for a in annotations}
    contexts: dict[str, list[SourceAnnotation]] = {}
    pending: list[SourceAnnotation] = []
    for block in document.blocks:
        annotation = by_id.get(block.block_id)
        if document.source_type == "json":
            if annotation:
                continue
            location = block.source_location or ""
            # Only direct siblings of the same object; table A cannot use B's metadata.
            parent = location.rsplit(".", 1)[0]
            linked = [a for a in annotations if a.kind == "metadata"
                      and (a.source_location or "").rsplit(".", 1)[0] == parent
                      and field_key(a.content["key"]) != "asset"]
            if location.startswith("$."):
                container = location.rsplit(".", 1)[-1]
                if container in {"rows", "points"}:
                    container = parent.rsplit(".", 1)[-1]
                linked.append(SourceAnnotation(
                    source_block_id=block.block_id, source_location=location,
                    kind="structural_context", content={"container_key": container},
                    reason="Parser-owned JSON path for this block only.", related_block_ids=[block.block_id]))
            contexts[block.block_id] = linked
        else:
            if annotation:
                # A new heading starts a new section. Absence statements are never context.
                if annotation.kind in {"heading", "absence_statement"}:
                    pending = []
                if annotation.kind == "metadata":
                    pending.append(annotation)
                continue
            contexts[block.block_id] = pending
            pending = []
        for context in contexts.get(block.block_id, []):
            if block.block_id not in context.related_block_ids:
                context.related_block_ids.append(block.block_id)
    return contexts, annotations


def retrieval_block(block: RawBlock, context: Iterable[SourceAnnotation]) -> RawBlock:
    context = list(context)
    if not context:
        return block
    if block.block_type == "table":
        return block.model_copy(update={"content": {**block.content, "source_context": [a.content for a in context]}})
    return block.model_copy(update={"content": block.searchable_text() + "\n" + json.dumps(
        [a.content for a in context], ensure_ascii=False), "block_type": "text"})


def table_identity(block: RawBlock, context: Iterable[SourceAnnotation]) -> dict[str, object]:
    """Provide a stable technical ID separately from the observed sample ID."""
    samples = set()
    if block.block_type == "text":
        match = re.search(r"(?:试样编号|样本编号|样本号|specimen|Sample)\s*[:：]?\s+([\w-]+)", block.content, re.I)
        if match:
            samples.add(match[1])
    for annotation in context:
        content = annotation.content
        if isinstance(content, dict) and field_key(str(content.get("key", ""))) in {"sourcesample", "sampleid"}:
            samples.add(str(content["value"]))
        elif isinstance(content, str):
            match = re.search(r"(?:试样编号|样本编号|样本号|specimen)\s*[:：]\s*([\w-]+)", content, re.I)
            if match:
                samples.add(match[1])
    if block.block_type == "table":
        for i, column in enumerate(block.content["columns"]):
            if field_key(str(column)) in {"sampleid", "sourcesample", "specimen"}:
                values = {str(row[i]) for row in block.content["rows"]}
                samples.update(values)
    result = {"technical_table_id": re.sub(r"[^\w-]", "_", block.block_id)[:128]}
    if len(samples) > 1:
        result["conflicting_sample_ids"] = sorted(samples)
    elif samples:
        sample = samples.pop()
        result["sample_id"] = sample
        if re.fullmatch(r"[\w-]{1,128}", sample):
            result["technical_table_id"] = sample
    return result
