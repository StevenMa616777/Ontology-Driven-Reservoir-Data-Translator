"""Deterministic physical-unit conversion backed by Pint.

Only the explicitly supported v0.1 vocabulary is accepted. In particular,
``month``, ``quarter``, and ``year`` use the simulator policy defined by the
design fixtures: 30 days, 365/4 days, and 365 days respectively. This avoids
silently depending on calendar context or Pint's average-year definitions.
"""

from __future__ import annotations

import math
import numbers
import re
import unicodedata
from types import MappingProxyType
from typing import Mapping

from pint import UnitRegistry
from pint.errors import DimensionalityError


class UnitNormalizationError(ValueError):
    """Base error for deterministic unit normalization failures."""

    code = "UNIT_NORMALIZATION_ERROR"


class UnsupportedUnitError(UnitNormalizationError):
    """Raised when a source or target unit is outside the v0.1 vocabulary."""

    code = "UNSUPPORTED_UNIT"

    def __init__(self, unit: str, *, role: str) -> None:
        self.unit = unit
        self.role = role
        super().__init__(f"Unsupported {role} unit: {unit!r}")


class IncompatibleUnitError(UnitNormalizationError):
    """Raised when two supported units have different dimensionality."""

    code = "INCOMPATIBLE_UNITS"

    def __init__(self, source_unit: str, target_unit: str) -> None:
        self.source_unit = source_unit
        self.target_unit = target_unit
        super().__init__(
            f"Cannot convert {source_unit!r} to incompatible unit {target_unit!r}"
        )


class InvalidMagnitudeError(UnitNormalizationError):
    """Raised when a magnitude is non-numeric, boolean, NaN, or infinite."""

    code = "INVALID_MAGNITUDE"

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(f"Unit conversion requires a finite numeric value, got {value!r}")


def _unit_key(unit: str) -> str:
    normalized = unicodedata.normalize("NFKC", unit).strip().casefold()
    normalized = normalized.replace("·", ".").replace("⋅", ".")
    normalized = normalized.replace("per", "/")
    return re.sub(r"\s+", "", normalized)


class UnitNormalizer:
    """Convert v0.1 reservoir quantities without LLM involvement."""

    _UNIT_EXPRESSIONS: Mapping[str, str] = MappingProxyType(
        {
            # Pressure
            "bar": "bar",
            "psi": "psi",
            "kpa": "kilopascal",
            "mpa": "megapascal",
            # Surface volume rate. Semantic liquid/water meaning is retained by
            # the ontology concept; this layer only converts physical units.
            "m3/day": "meter ** 3 / day",
            "m^3/day": "meter ** 3 / day",
            "m3/d": "meter ** 3 / day",
            "bbl/day": "oil_barrel / day",
            "bbl/d": "oil_barrel / day",
            # Dynamic viscosity
            "cp": "centipoise",
            "pa.s": "pascal * second",
            "pa*s": "pascal * second",
            # Density
            "kg/m3": "kilogram / meter ** 3",
            "kg/m^3": "kilogram / meter ** 3",
            "g/cm3": "gram / centimeter ** 3",
            "g/cm^3": "gram / centimeter ** 3",
            # Project time policy
            "day": "day",
            "days": "day",
            "d": "day",
            "month": "simulator_month",
            "months": "simulator_month",
            "quarter": "simulator_quarter",
            "quarters": "simulator_quarter",
            "year": "simulator_year",
            "years": "simulator_year",
            # Compressibility
            "1/bar": "1 / bar",
            "bar^-1": "1 / bar",
            "1/psi": "1 / psi",
            "psi^-1": "1 / psi",
            # Canonical dimensionless quantities used by Task 4.
            "fraction": "dimensionless",
            "rm3/sm3": "dimensionless",
        }
    )

    def __init__(self) -> None:
        registry = UnitRegistry(autoconvert_offset_to_baseunit=True)
        registry.define("simulator_month = 30 * day")
        registry.define("simulator_quarter = 91.25 * day")
        registry.define("simulator_year = 365 * day")
        self._registry = registry

    @property
    def supported_units(self) -> tuple[str, ...]:
        """Return the accepted normalized spellings in stable order."""

        return tuple(sorted(self._UNIT_EXPRESSIONS))

    def normalize(
        self,
        value: int | float,
        source_unit: str,
        target_unit: str,
    ) -> float:
        """Return ``value`` converted from ``source_unit`` to ``target_unit``.

        Both units must belong to the explicit vocabulary even for identity
        conversions. This prevents misspelled or invented units from passing
        through the canonical layer unchecked.
        """

        if (
            isinstance(value, bool)
            or not isinstance(value, numbers.Real)
            or not math.isfinite(float(value))
        ):
            raise InvalidMagnitudeError(value)

        source_expression = self._resolve(source_unit, role="source")
        target_expression = self._resolve(target_unit, role="target")
        quantity = float(value) * self._registry.parse_units(source_expression)
        try:
            converted = quantity.to(target_expression)
        except DimensionalityError as exc:
            raise IncompatibleUnitError(source_unit, target_unit) from exc
        magnitude = float(converted.magnitude)
        return 0.0 if magnitude == 0 else magnitude

    def _resolve(self, unit: str, *, role: str) -> str:
        if not isinstance(unit, str) or not unit.strip():
            raise UnsupportedUnitError(str(unit), role=role)
        try:
            return self._UNIT_EXPRESSIONS[_unit_key(unit)]
        except KeyError as exc:
            raise UnsupportedUnitError(unit, role=role) from exc
