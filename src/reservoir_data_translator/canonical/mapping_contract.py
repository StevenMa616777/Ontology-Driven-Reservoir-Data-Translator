"""Public concept-to-canonical-path contracts for the v0.1 model.

The semantic agent receives these contracts and the deterministic builder uses
the same contracts for enforcement.  Keeping one source of truth prevents a
model from introducing canonical fields or path shapes that the builder does
not own.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping


_SELECTOR = r"[^.\[\]]+"
_INDEX = r"\d+"


@dataclass(frozen=True, slots=True)
class CanonicalMappingContract:
    """Allowed canonical path shape for one ontology concept."""

    concept_id: str
    path_template: str
    path_pattern: str

    def accepts(self, canonical_path: str) -> bool:
        return re.fullmatch(self.path_pattern, canonical_path) is not None


def _contract(
    concept_id: str,
    path_template: str,
    path_pattern: str,
) -> CanonicalMappingContract:
    return CanonicalMappingContract(concept_id, path_template, path_pattern)


_DIRECT: Mapping[str, CanonicalMappingContract] = MappingProxyType(
    {
        "rock.compressibility": _contract(
            "rock.compressibility",
            "rock.compressibility",
            r"rock\.compressibility",
        ),
        "rock.reference_pressure": _contract(
            "rock.reference_pressure",
            "rock.reference_pressure",
            r"rock\.reference_pressure",
        ),
        "fluid.oil.density": _contract(
            "fluid.oil.density",
            "fluids.oil.density",
            r"fluids\.oil\.density",
        ),
        "fluid.water.density": _contract(
            "fluid.water.density",
            "fluids.water.density",
            r"fluids\.water\.density",
        ),
        "fluid.gas.density": _contract(
            "fluid.gas.density",
            "fluids.gas.density",
            r"fluids\.gas\.density",
        ),
        "fluid.oil.pvt": _contract(
            "fluid.oil.pvt",
            "fluids.oil.pvt",
            r"fluids\.oil\.pvt",
        ),
        "fluid.water.pvt": _contract(
            "fluid.water.pvt",
            "fluids.water.pvt",
            r"fluids\.water\.pvt",
        ),
        "fluid.gas.pvt": _contract(
            "fluid.gas.pvt",
            "fluids.gas.pvt",
            r"fluids\.gas\.pvt",
        ),
        "scal.relative_permeability": _contract(
            "scal.relative_permeability",
            "scal.relative_permeability[{table_id}]",
            rf"scal\.relative_permeability\[{_SELECTOR}\]",
        ),
        "well": _contract(
            "well",
            "wells[{well_id}].id",
            rf"wells\[{_SELECTOR}\]\.id",
        ),
        "well.producer": _contract(
            "well.producer",
            "wells[{well_id}].well_type",
            rf"wells\[{_SELECTOR}\]\.well_type",
        ),
        "well.water_injector": _contract(
            "well.water_injector",
            "wells[{well_id}].well_type",
            rf"wells\[{_SELECTOR}\]\.well_type",
        ),
        "well.gas_injector": _contract(
            "well.gas_injector",
            "wells[{well_id}].well_type",
            rf"wells\[{_SELECTOR}\]\.well_type",
        ),
        "well.control.liquid_rate": _contract(
            "well.control.liquid_rate",
            "wells[{well_id}].controls[liquid_rate].target",
            rf"wells\[{_SELECTOR}\]\.controls\[liquid_rate\]\.target",
        ),
        "well.control.water_injection_rate": _contract(
            "well.control.water_injection_rate",
            "wells[{well_id}].controls[water_injection_rate].target",
            (
                rf"wells\[{_SELECTOR}\]\.controls\[water_injection_rate\]"
                r"\.target"
            ),
        ),
        "well.constraint.minimum_bhp": _contract(
            "well.constraint.minimum_bhp",
            (
                "wells[{well_id}].controls[{control_type}]."
                "constraints[minimum_bhp].value"
            ),
            (
                rf"wells\[{_SELECTOR}\]\.controls\[{_SELECTOR}\]\."
                r"constraints\[minimum_bhp\]\.value"
            ),
        ),
        "well.constraint.maximum_bhp": _contract(
            "well.constraint.maximum_bhp",
            (
                "wells[{well_id}].controls[{control_type}]."
                "constraints[maximum_bhp].value"
            ),
            (
                rf"wells\[{_SELECTOR}\]\.controls\[{_SELECTOR}\]\."
                r"constraints\[maximum_bhp\]\.value"
            ),
        ),
        "schedule.duration": _contract(
            "schedule.duration",
            "schedule.duration",
            r"schedule\.duration",
        ),
        "schedule.report_interval": _contract(
            "schedule.report_interval",
            "schedule.report_interval",
            r"schedule\.report_interval",
        ),
    }
)


_SCAL_FIELDS = {
    "scal.relative_permeability.water_saturation": "sw",
    "scal.relative_permeability.krw": "krw",
    "scal.relative_permeability.kro": "kro",
    "scal.relative_permeability.pcow": "pcow",
}


def get_canonical_mapping_contract(
    concept_id: str,
) -> CanonicalMappingContract | None:
    """Return the supported v0.1 path contract, if the concept is buildable."""

    direct = _DIRECT.get(concept_id)
    if direct is not None:
        return direct

    pvt_match = re.fullmatch(
        r"fluid\.(oil|water|gas)\.pvt\."
        r"(pressure|formation_volume_factor|viscosity)",
        concept_id,
    )
    if pvt_match:
        phase, field = pvt_match.groups()
        return _contract(
            concept_id,
            f"fluids.{phase}.pvt.points[{{point_index}}].{field}",
            rf"fluids\.{phase}\.pvt\.points\[{_INDEX}\]\.{field}",
        )

    water_property_match = re.fullmatch(
        r"fluid\.water\.pvt\.(compressibility|viscosibility)",
        concept_id,
    )
    if water_property_match:
        field = water_property_match.group(1)
        return _contract(
            concept_id,
            f"fluids.water.pvt.points[{{point_index}}].{field}",
            rf"fluids\.water\.pvt\.points\[{_INDEX}\]\.{field}",
        )

    field = _SCAL_FIELDS.get(concept_id)
    if field is not None:
        return _contract(
            concept_id,
            (
                "scal.relative_permeability[{table_id}]."
                f"points[{{point_index}}].{field}"
            ),
            (
                rf"scal\.relative_permeability\[{_SELECTOR}\]\."
                rf"points\[{_INDEX}\]\.{field}"
            ),
        )
    return None


def accepts_canonical_path(concept_id: str, canonical_path: str) -> bool:
    """Return false for unsupported concepts and invented path shapes."""

    contract = get_canonical_mapping_contract(concept_id)
    return contract is not None and contract.accepts(canonical_path)
