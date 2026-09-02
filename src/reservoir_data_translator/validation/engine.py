"""Four-level deterministic validation orchestration."""

from __future__ import annotations

from typing import Any, Mapping

from reservoir_data_translator.canonical import ReservoirSimulationModel
from reservoir_data_translator.ontology import OntologyRegistry

from .domain_validator import DomainValidator
from .export_validator import ExportValidator
from .models import ValidationResult
from .ontology_validator import OntologyInstanceValidator
from .schema_validator import SchemaValidator


class ValidationEngine:
    """Run L1-L3 and optional target-specific L4 validation in order."""

    def __init__(
        self,
        registry: OntologyRegistry,
        *,
        export_validator: ExportValidator | None = None,
    ) -> None:
        self.schema = SchemaValidator()
        self.ontology = OntologyInstanceValidator(registry)
        self.domain = DomainValidator(registry)
        self.export = export_validator or ExportValidator()

    def validate(
        self,
        payload: ReservoirSimulationModel | Mapping[str, Any] | str | bytes,
        *,
        target_platform: str | None = None,
    ) -> ValidationResult:
        schema_result, model = self.schema.validate_with_model(payload)
        if model is None:
            return schema_result

        ontology_result = self.ontology.validate(model)
        results = [schema_result, ontology_result]
        if not ontology_result.valid:
            return ValidationResult.merge(*results)

        domain_result = self.domain.validate(model)
        results.append(domain_result)
        if not domain_result.valid:
            return ValidationResult.merge(*results)

        if target_platform is not None:
            results.append(self.export.validate(model, target_platform))
        return ValidationResult.merge(*results)
