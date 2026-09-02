from pathlib import Path

import pytest


pytest.importorskip("opm.io.parser")

from reservoir_data_translator.validation import (
    compare_eclipse_includes,
    validate_eclipse_include,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "example" / "demo_material_eclipse.inc"


def test_example_golden_include_parses_with_pinned_opm() -> None:
    report = validate_eclipse_include(GOLDEN_PATH.read_text())

    assert report["parser_version"] == "2025.10"
    assert report["parser_valid"] is True
    assert report["keywords"] == [
        "SWOF",
        "PVDO",
        "PVDG",
        "PVTW",
        "DENSITY",
        "ROCK",
        "WCONPROD",
        "WCONINJE",
        "TSTEP",
    ]


def test_parser_normalized_comparison_ignores_format_but_detects_values() -> None:
    golden = GOLDEN_PATH.read_text()

    same = compare_eclipse_includes(golden, golden.replace("850   1010", "850 1010"))
    changed = compare_eclipse_includes(golden, golden.replace("850   1010", "851   1010"))

    assert same["semantic_equal"] is True
    assert same["mismatches"] == []
    assert changed["semantic_equal"] is False
    assert changed["mismatches"] == [
        {"path": "$.DENSITY[0]", "golden": 850.0, "generated": 851.0}
    ]
