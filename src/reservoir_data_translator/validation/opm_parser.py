"""OPM 2025.10 parser validation for the PoC ECLIPSE INCLUDE slice."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import math
from typing import Any


POC_KEYWORDS = (
    "SWOF",
    "PVDO",
    "PVDG",
    "PVTW",
    "DENSITY",
    "ROCK",
    "WCONPROD",
    "WCONINJE",
    "TSTEP",
)
_SCHEDULE_KEYWORDS = frozenset({"WCONPROD", "WCONINJE", "TSTEP"})


class OpmParserUnavailable(RuntimeError):
    """The optional pinned OPM parser dependency is not installed."""


class OpmIncludeError(RuntimeError):
    """The INCLUDE cannot be parsed in the PoC host-deck context."""


def validate_eclipse_include(content: str) -> dict[str, Any]:
    """Parse an INCLUDE and return normalized, JSON-safe keyword semantics."""

    try:
        from opm.io.parser import Parser
    except ImportError as exc:  # pragma: no cover - depends on optional wheel
        raise OpmParserUnavailable(
            "Install the PoC parser with `pip install opm==2025.10`."
        ) from exc

    try:
        deck = Parser().parse_string(_minimal_host_deck(content))
    except Exception as exc:
        raise OpmIncludeError(str(exc)) from exc

    present = {keyword.name for keyword in deck}
    semantics = {
        keyword: _extract_keyword(deck[keyword], keyword)
        for keyword in POC_KEYWORDS
        if keyword in present
    }
    try:
        parser_version = version("opm")
    except PackageNotFoundError:  # pragma: no cover - import normally has metadata
        parser_version = "unknown"
    return {
        "parser": "opm.io.parser.Parser",
        "parser_version": parser_version,
        "parser_valid": True,
        "keywords": [keyword for keyword in POC_KEYWORDS if keyword in present],
        "semantics": semantics,
    }


def compare_eclipse_includes(golden: str, generated: str) -> dict[str, Any]:
    """Compare parser-normalized business fields, independent of formatting."""

    golden_report = validate_eclipse_include(golden)
    generated_report = validate_eclipse_include(generated)
    mismatches: list[dict[str, Any]] = []
    _compare_values(
        golden_report["semantics"],
        generated_report["semantics"],
        path="$",
        mismatches=mismatches,
    )
    return {
        "parser": golden_report["parser"],
        "parser_version": golden_report["parser_version"],
        "golden_parser_valid": golden_report["parser_valid"],
        "generated_parser_valid": generated_report["parser_valid"],
        "semantic_equal": not mismatches,
        "mismatches": mismatches,
        "golden": golden_report,
        "generated": generated_report,
    }


def _minimal_host_deck(content: str) -> str:
    lines = content.splitlines()
    schedule_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip().upper() in _SCHEDULE_KEYWORDS
        ),
        len(lines),
    )
    props = "\n".join(lines[:schedule_index])
    schedule = "\n".join(lines[schedule_index:])
    return (
        "RUNSPEC\n"
        "TITLE\n PoC parser host /\n"
        "DIMENS\n 1 1 1 /\n"
        "OIL\nWATER\nGAS\nMETRIC\n"
        "TABDIMS\n 1 1 20 20 1 20 /\n"
        "WELLDIMS\n 10 10 1 10 /\n"
        "GRID\nPROPS\n"
        f"{props}\n"
        "SOLUTION\nSCHEDULE\n"
        f"{schedule}\n"
        "END\n"
    )


def _extract_keyword(keyword: Any, name: str) -> Any:
    if name == "SWOF":
        return _rows(keyword.get_raw_array().tolist(), 4)
    if name in {"PVDO", "PVDG"}:
        return _rows(keyword.get_raw_array().tolist(), 3)
    if name == "PVTW":
        return [
            [_first_raw(record[index]) for index in range(5)]
            for record in keyword
        ]
    if name == "DENSITY":
        record = keyword[0]
        return [_first_raw(record[index]) for index in range(3)]
    if name == "ROCK":
        record = keyword[0]
        return [_first_raw(record[index]) for index in range(2)]
    if name == "WCONPROD":
        return [
            {
                "well": record[0].get_str(0),
                "status": record[1].get_str(0),
                "control_mode": record[2].get_str(0),
                "liquid_rate": _uda_number(record[6]),
                "bhp": _uda_number(record[8]),
            }
            for record in keyword
        ]
    if name == "WCONINJE":
        return [
            {
                "well": record[0].get_str(0),
                "phase": record[1].get_str(0),
                "status": record[2].get_str(0),
                "control_mode": record[3].get_str(0),
                "rate": _uda_number(record[4]),
                "bhp": _uda_number(record[6]),
            }
            for record in keyword
        ]
    if name == "TSTEP":
        return keyword[0][0].get_raw_data_list()
    raise AssertionError(f"Unsupported PoC keyword {name}")


def _rows(values: list[float], width: int) -> list[list[float]]:
    return [values[index : index + width] for index in range(0, len(values), width)]


def _first_raw(item: Any) -> Any:
    values = item.get_raw_data_list()
    return values[0] if values else None


def _uda_number(item: Any) -> float | None:
    value = item.get_uda(0)
    return value.get_double() if value.is_double() else None


def _compare_values(
    golden: Any,
    generated: Any,
    *,
    path: str,
    mismatches: list[dict[str, Any]],
) -> None:
    if isinstance(golden, dict) and isinstance(generated, dict):
        for key in sorted(set(golden) | set(generated)):
            child_path = f"{path}.{key}"
            if key not in golden or key not in generated:
                mismatches.append(
                    {
                        "path": child_path,
                        "golden": golden.get(key),
                        "generated": generated.get(key),
                    }
                )
            else:
                _compare_values(
                    golden[key],
                    generated[key],
                    path=child_path,
                    mismatches=mismatches,
                )
        return
    if isinstance(golden, list) and isinstance(generated, list):
        if len(golden) != len(generated):
            mismatches.append(
                {"path": path, "golden": golden, "generated": generated}
            )
            return
        for index, (left, right) in enumerate(zip(golden, generated)):
            _compare_values(
                left,
                right,
                path=f"{path}[{index}]",
                mismatches=mismatches,
            )
        return
    if isinstance(golden, (int, float)) and isinstance(generated, (int, float)):
        if math.isclose(float(golden), float(generated), rel_tol=1e-9, abs_tol=1e-12):
            return
    elif golden == generated:
        return
    mismatches.append({"path": path, "golden": golden, "generated": generated})
