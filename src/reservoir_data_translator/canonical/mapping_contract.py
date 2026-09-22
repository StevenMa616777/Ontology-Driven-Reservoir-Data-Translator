"""Resolve contextual semantic identities to versioned canonical storage paths.

These rules belong to the canonical representation, not the ontology.  The
ontology defines each reusable concept once; this layer owns the concrete
combinations that the canonical v0.1 model can currently represent.  Old IDs
are accepted only through an explicit migration table, never by interpreting a
caller-supplied canonical path as semantic evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from string import Formatter
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import ValidationError

from reservoir_data_translator.ontology.context import SemanticContext


class SemanticResolutionError(ValueError):
    """A semantic identity cannot be resolved without guessing or contradiction."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


_CONTROL_TYPES = (
    "liquid_rate", "oil_rate", "water_rate", "gas_rate", "water_injection_rate",
    "gas_injection_rate", "bhp",
)

_SELECTOR_PATTERNS = {
    "well_id": r"[\w-]{1,128}",
    "table_id": r"[\w-]{1,128}",
    "point_index": r"(?:0|[1-9][0-9]*)",
    "control_type": "(?:" + "|".join(_CONTROL_TYPES) + ")",
}


@dataclass(frozen=True, slots=True)
class CanonicalMappingContract:
    """One supported semantic identity and its canonical storage contract."""

    concept_id: str
    context: SemanticContext
    path_template: str
    path_pattern: str
    selector_names: tuple[str, ...]

    def selector_schema(self) -> dict[str, Any]:
        """Expose the same selector restrictions used by the path resolver."""
        properties = {}
        for name in self.selector_names:
            if name == "point_index":
                properties[name] = {"type": "integer", "minimum": 0}
            elif name == "control_type":
                properties[name] = {
                    "type": "string", "enum": list(_CONTROL_TYPES),
                    "description": "The operating control whose target this constraint limits. Use water_injection_rate for a water injection rate target. A pressure limit does not itself imply a bhp operating control.",
                }
            else:
                properties[name] = {"type": "string", "pattern": "^" + _SELECTOR_PATTERNS[name] + "$"}
        return {"type": "object", "properties": properties,
                "required": list(self.selector_names), "additionalProperties": False}

    def accepts(self, canonical_path: str) -> bool:
        return re.fullmatch(self.path_pattern, canonical_path) is not None

    def extract_selectors(self, canonical_path: str) -> dict[str, str | int]:
        """Read grouping keys from a path already bound to this semantic rule."""
        match = re.fullmatch(self.path_pattern, canonical_path)
        if match is None:
            raise SemanticResolutionError(
                "CANONICAL_PATH_MISMATCH",
                f"Path {canonical_path!r} does not satisfy this contextual contract",
            )
        return {
            name: int(value) if name == "point_index" else value
            for name, value in match.groupdict().items()
        }


def _context(scope: str, role: str, phase: str | None = None,
             phase_system: str | None = None) -> SemanticContext:
    return SemanticContext(
        scope=scope, role=role, phase=phase, phase_system=phase_system,
    )


def _contract(concept_id: str, context: SemanticContext,
              path_template: str) -> CanonicalMappingContract:
    pattern: list[str] = []
    selectors: list[str] = []
    for literal, selector, _, _ in Formatter().parse(path_template):
        pattern.append(re.escape(literal))
        if selector is not None:
            selectors.append(selector)
            pattern.append(f"(?P<{selector}>{_SELECTOR_PATTERNS[selector]})")
    return CanonicalMappingContract(
        concept_id, context, path_template, "".join(pattern), tuple(selectors),
    )


def _build_rules() -> tuple[CanonicalMappingContract, ...]:
    rules = [
        _contract("rock.compressibility", _context("rock", "property"),
                  "rock.compressibility"),
        _contract("physical.pressure", _context("rock", "reference"),
                  "rock.reference_pressure"),
    ]
    for phase in ("oil", "water", "gas"):
        rules.extend([
            _contract("physical.density", _context("fluid_properties", "property", phase),
                      f"fluids.{phase}.density"),
            _contract("fluid.pvt", _context("pvt", "model", phase),
                      f"fluids.{phase}.pvt"),
        ])
        fields = ["pressure", "formation_volume_factor", "viscosity"]
        if phase == "water":
            fields.extend(["compressibility", "viscosibility"])
        for field in fields:
            rules.append(_contract(
                f"physical.{field}",
                _context("pvt", "coordinate" if field == "pressure" else "property", phase),
                f"fluids.{phase}.pvt.points[{{point_index}}].{field}",
            ))
    for concept_id, phase, role, field in (
        ("physical.water_saturation", "water", "coordinate", "sw"),
        ("physical.relative_permeability", "water", "property", "krw"),
        ("physical.relative_permeability", "oil", "property", "kro"),
        ("physical.capillary_pressure", None, "property", "pcow"),
    ):
        rules.append(_contract(
            concept_id, _context("relative_permeability", role, phase, "oil_water"),
            f"scal.relative_permeability[{{table_id}}].points[{{point_index}}].{field}",
        ))
    rules.append(_contract(
        "scal.relative_permeability",
        _context("relative_permeability", "model", phase_system="oil_water"),
        "scal.relative_permeability[{table_id}]",
    ))
    for concept_id, role, path in (
        ("well", "entity", "wells[{well_id}].id"),
        ("well.producer", "type", "wells[{well_id}].well_type"),
        ("well.water_injector", "type", "wells[{well_id}].well_type"),
        ("well.gas_injector", "type", "wells[{well_id}].well_type"),
        ("well.control.liquid_rate", "control", "wells[{well_id}].controls[liquid_rate].target"),
        ("well.control.water_injection_rate", "control", "wells[{well_id}].controls[water_injection_rate].target"),
        # Preserve the producer-specific minimum-BHP contract.
        ("well.constraint.minimum_bhp", "constraint", "wells[{well_id}].controls[liquid_rate].constraints[minimum_bhp].value"),
        ("well.constraint.maximum_bhp", "constraint", "wells[{well_id}].controls[{control_type}].constraints[maximum_bhp].value"),
    ):
        rules.append(_contract(concept_id, _context("well_controls", role), path))
    for field in ("duration", "report_interval"):
        rules.append(_contract(f"schedule.{field}", _context("schedule", field),
                               f"schedule.{field}"))
    return tuple(rules)


_RULES = _build_rules()
_RULE_INDEX = MappingProxyType({(rule.concept_id, rule.context): rule for rule in _RULES})
if len(_RULE_INDEX) != len(_RULES):
    raise RuntimeError("Canonical mapping rules contain duplicate semantic identities")

# Compatibility is a one-way migration of previous semantic records.  None of
# these IDs is added back into the reusable ontology vocabulary.
_legacy: dict[str, tuple[str, SemanticContext]] = {
    "rock.reference_pressure": ("physical.pressure", _context("rock", "reference")),
    # Reference-condition quantities had ontology definitions but no v0.1
    # storage fields. Preserve their semantic migration without inventing a
    # canonical destination or conflating them with rock reference pressure.
    "condition.reference.pressure": (
        "physical.pressure", _context("reference_condition", "reference"),
    ),
    "condition.reference.temperature": (
        "physical.temperature", _context("reference_condition", "reference"),
    ),
}
for _phase in ("oil", "water", "gas"):
    _legacy[f"fluid.{_phase}.density"] = (
        "physical.density", _context("fluid_properties", "property", _phase),
    )
    _legacy[f"fluid.{_phase}.pvt"] = ("fluid.pvt", _context("pvt", "model", _phase))
    for _field in ("pressure", "formation_volume_factor", "viscosity"):
        _legacy[f"fluid.{_phase}.pvt.{_field}"] = (
            f"physical.{_field}",
            _context("pvt", "coordinate" if _field == "pressure" else "property", _phase),
        )
for _field in ("compressibility", "viscosibility"):
    _legacy[f"fluid.water.pvt.{_field}"] = (
        f"physical.{_field}", _context("pvt", "property", "water"),
    )
for _old, _concept, _phase, _role in (
    ("water_saturation", "water_saturation", "water", "coordinate"),
    ("krw", "relative_permeability", "water", "property"),
    ("kro", "relative_permeability", "oil", "property"),
    ("pcow", "capillary_pressure", None, "property"),
):
    _legacy[f"scal.relative_permeability.{_old}"] = (
        f"physical.{_concept}", _context("relative_permeability", _role, _phase, "oil_water"),
    )
_LEGACY_IDENTITIES = MappingProxyType(_legacy)
_COMPATIBILITY_DEFAULT_IDS = frozenset({
    "rock.compressibility",
    "scal.relative_permeability",
    "well", "well.producer", "well.water_injector", "well.gas_injector",
    "well.control.liquid_rate", "well.control.water_injection_rate",
    "well.constraint.minimum_bhp", "well.constraint.maximum_bhp",
    "schedule.duration", "schedule.report_interval",
})
_DEFAULT_CONTEXTS = MappingProxyType({
    rule.concept_id: rule.context for rule in _RULES
    if rule.concept_id in _COMPATIBILITY_DEFAULT_IDS
})
if len(_DEFAULT_CONTEXTS) != sum(
    rule.concept_id in _COMPATIBILITY_DEFAULT_IDS for rule in _RULES
):
    raise RuntimeError("Compatibility defaults must identify a unique contextual rule")


def normalize_semantic_identity(
    concept_id: str,
    context: SemanticContext | Mapping[str, Any] | None = None,
) -> tuple[str, SemanticContext]:
    """Migrate an explicit old ID or require context for a reusable concept.

    Conflicting explicit context is never overwritten by migration.  Unique
    non-migrated concepts retain declared compatibility defaults; generic
    concepts require context even if only one canonical rule currently exists.
    """
    if context is not None and not isinstance(context, SemanticContext):
        try:
            context = SemanticContext.model_validate(context)
        except ValidationError as exc:
            raise SemanticResolutionError("INVALID_SEMANTIC_CONTEXT", str(exc)) from exc
    legacy = _LEGACY_IDENTITIES.get(concept_id)
    if legacy is not None:
        normalized_id, expected = legacy
        if context is not None and context != expected:
            raise SemanticResolutionError(
                "SEMANTIC_CONTEXT_CONFLICT",
                f"Legacy concept {concept_id!r} contradicts the supplied semantic context",
            )
        return normalized_id, expected
    if context is None:
        context = _DEFAULT_CONTEXTS.get(concept_id)
    if context is None:
        raise SemanticResolutionError(
            "SEMANTIC_CONTEXT_REQUIRED",
            f"Concept {concept_id!r} requires explicit semantic context",
        )
    return concept_id, context


def list_canonical_mapping_contracts() -> tuple[CanonicalMappingContract, ...]:
    """Return all contextual rules supported by the current canonical model."""
    return _RULES


def get_canonical_mapping_contract(
    concept_id: str,
    context: SemanticContext | Mapping[str, Any] | None = None,
) -> CanonicalMappingContract | None:
    """Return the exact contextual contract, or None when it is unsupported."""
    try:
        identity = normalize_semantic_identity(concept_id, context)
    except SemanticResolutionError:
        return None
    return _RULE_INDEX.get(identity)


def _validated_selectors(selectors: Mapping[str, str | int]) -> dict[str, str]:
    if not isinstance(selectors, Mapping):
        raise SemanticResolutionError("INVALID_CANONICAL_SELECTORS", "Selectors must be a mapping")
    validated: dict[str, str] = {}
    for name, value in selectors.items():
        pattern = _SELECTOR_PATTERNS.get(name)
        if pattern is None or isinstance(value, bool) or not isinstance(value, (str, int)):
            raise SemanticResolutionError("INVALID_CANONICAL_SELECTORS", f"Invalid selector {name!r}")
        if name != "point_index" and not isinstance(value, str):
            raise SemanticResolutionError("INVALID_CANONICAL_SELECTORS", f"Selector {name!r} must be text")
        if re.fullmatch(pattern, str(value)) is None:
            raise SemanticResolutionError("INVALID_CANONICAL_SELECTORS", f"Unsafe or invalid selector {name!r}")
        validated[name] = str(value)
    return validated


def resolve_canonical_path(
    concept_id: str,
    context: SemanticContext | Mapping[str, Any] | None,
    selectors: Mapping[str, str | int],
    canonical_path: str | None = None,
) -> str:
    """Resolve a complete semantic IR and verify any supplied canonical path.

    Old records may carry selectors solely in a path, but only an explicit
    legacy or unique-default ID enables that migration.  Generic identities
    must carry their selectors separately, so paths cannot supply semantics.
    """
    identity = normalize_semantic_identity(concept_id, context)
    contract = _RULE_INDEX.get(identity)
    if contract is None:
        raise SemanticResolutionError(
            "UNSUPPORTED_CANONICAL_MAPPING",
            f"No canonical mapping supports concept {identity[0]!r} with context {identity[1].model_dump(exclude_none=True)!r}",
        )
    supplied = _validated_selectors(selectors)
    extra = set(supplied) - set(contract.selector_names)
    if extra:
        raise SemanticResolutionError("INVALID_CANONICAL_SELECTORS", f"Unexpected selectors: {sorted(extra)!r}")
    path_match = None
    if canonical_path is not None:
        path_match = re.fullmatch(contract.path_pattern, canonical_path)
        if path_match is None:
            raise SemanticResolutionError(
                "CANONICAL_PATH_MISMATCH",
                f"Path {canonical_path!r} contradicts the resolved semantic identity",
            )
    compatibility_record = concept_id in _LEGACY_IDENTITIES or (
        context is None and concept_id in _DEFAULT_CONTEXTS
    )
    if not supplied and path_match is not None and compatibility_record:
        supplied = _validated_selectors(path_match.groupdict())
    missing = set(contract.selector_names) - set(supplied)
    if missing:
        raise SemanticResolutionError("CANONICAL_SELECTORS_REQUIRED", f"Missing selectors: {sorted(missing)!r}")
    resolved = contract.path_template.format(**supplied)
    if canonical_path is not None and canonical_path != resolved:
        raise SemanticResolutionError("CANONICAL_PATH_MISMATCH", "Canonical path disagrees with explicit selectors")
    return resolved


def accepts_canonical_path(
    concept_id: str,
    canonical_path: str,
    context: SemanticContext | Mapping[str, Any] | None = None,
) -> bool:
    """Check path shape for an explicit contextual identity, without inference."""
    contract = get_canonical_mapping_contract(concept_id, context)
    return contract is not None and contract.accepts(canonical_path)
