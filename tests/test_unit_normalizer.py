import math

import pytest

from reservoir_data_translator.semantic import (
    IncompatibleUnitError,
    InvalidMagnitudeError,
    UnitNormalizer,
    UnsupportedUnitError,
)


@pytest.fixture(scope="module")
def normalizer() -> UnitNormalizer:
    return UnitNormalizer()


@pytest.mark.parametrize(
    ("value", "source_unit", "target_unit", "expected"),
    [
        (1, "bar", "psi", 14.503773773020923),
        (1000, "kPa", "bar", 10.0),
        (1, "MPa", "bar", 10.0),
        (1, "m3/day", "bbl/day", 6.289810770432105),
        (1, "Pa.s", "cP", 1000.0),
        (1, "g/cm3", "kg/m3", 1000.0),
        (3, "month", "day", 90.0),
        (5, "year", "day", 1825.0),
        (1, "1/psi", "1/bar", 14.503773773020923),
    ],
)
def test_supported_conversions_are_deterministic(
    normalizer: UnitNormalizer,
    value: float,
    source_unit: str,
    target_unit: str,
    expected: float,
) -> None:
    assert normalizer.normalize(value, source_unit, target_unit) == pytest.approx(
        expected,
        rel=1e-12,
    )


def test_identity_conversion_still_uses_explicit_vocabulary(
    normalizer: UnitNormalizer,
) -> None:
    assert normalizer.normalize(500, "m^3/day", "m3/day") == 500
    assert normalizer.normalize(0.8, "fraction", "fraction") == 0.8


def test_incompatible_supported_units_are_rejected(
    normalizer: UnitNormalizer,
) -> None:
    with pytest.raises(IncompatibleUnitError) as error:
        normalizer.normalize(1, "bar", "kg/m3")

    assert error.value.code == "INCOMPATIBLE_UNITS"


def test_unknown_units_are_not_silently_passed_through(
    normalizer: UnitNormalizer,
) -> None:
    with pytest.raises(UnsupportedUnitError) as error:
        normalizer.normalize(1, "furlong/fortnight", "m3/day")

    assert error.value.code == "UNSUPPORTED_UNIT"
    assert error.value.role == "source"


@pytest.mark.parametrize("value", [True, math.nan, math.inf, "500"])
def test_non_finite_or_non_numeric_values_are_rejected(
    normalizer: UnitNormalizer,
    value: object,
) -> None:
    with pytest.raises(InvalidMagnitudeError):
        normalizer.normalize(value, "bar", "psi")  # type: ignore[arg-type]
