"""Generate deterministic JSON Schema artifacts from canonical Pydantic models."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import BaseModel

from .models import (
    FluidSystemModel,
    ReservoirSimulationModel,
    RockModel,
    SCALModel,
    SimulationSchedule,
    WellModel,
)


SCHEMA_MODELS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        "reservoir.schema.json": ReservoirSimulationModel,
        "rock.schema.json": RockModel,
        "fluid.schema.json": FluidSystemModel,
        "scal.schema.json": SCALModel,
        "well.schema.json": WellModel,
        "schedule.schema.json": SimulationSchedule,
    }
)


def generate_json_schemas() -> dict[str, dict[str, Any]]:
    """Return one JSON Schema document for each design-level model group."""

    return {
        file_name: model.model_json_schema(mode="validation")
        for file_name, model in SCHEMA_MODELS.items()
    }


def write_json_schemas(output_dir: str | Path) -> tuple[Path, ...]:
    """Write canonical schemas using stable UTF-8, indentation, and key order."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for file_name, schema in generate_json_schemas().items():
        path = destination / file_name
        path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return tuple(written)
