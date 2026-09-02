import json
from pathlib import Path

from reservoir_data_translator.canonical import (
    ReservoirSimulationModel,
    generate_json_schemas,
    write_json_schemas,
)


EXPECTED_SCHEMA_FILES = {
    "reservoir.schema.json",
    "rock.schema.json",
    "fluid.schema.json",
    "scal.schema.json",
    "well.schema.json",
    "schedule.schema.json",
}


def test_root_model_generates_json_schema() -> None:
    schema = ReservoirSimulationModel.model_json_schema()

    assert schema["title"] == "ReservoirSimulationModel"
    assert set(schema["required"]) == {
        "schema_version",
        "rock",
        "fluids",
        "scal",
        "wells",
        "schedule",
    }
    assert schema["additionalProperties"] is False
    assert "PhysicalValue" in schema["$defs"]
    assert "Provenance" in schema["$defs"]


def test_grouped_schema_generation_matches_design_artifacts() -> None:
    schemas = generate_json_schemas()

    assert set(schemas) == EXPECTED_SCHEMA_FILES
    assert schemas["well.schema.json"]["title"] == "WellModel"
    assert schemas["fluid.schema.json"]["title"] == "FluidSystemModel"


def test_schema_writer_emits_valid_deterministic_json(tmp_path: Path) -> None:
    written = write_json_schemas(tmp_path)
    first_contents = {path.name: path.read_text(encoding="utf-8") for path in written}

    second_written = write_json_schemas(tmp_path)

    assert {path.name for path in written} == EXPECTED_SCHEMA_FILES
    assert tuple(path.name for path in written) == tuple(
        path.name for path in second_written
    )
    assert first_contents == {
        path.name: path.read_text(encoding="utf-8") for path in second_written
    }
    assert all(json.loads(content) for content in first_contents.values())
