"""Load and structurally validate the YAML Company Ontology."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .convention import OntologyConvention
from .models import OntologyConcept
from .validator import OntologyValidationResult, OntologyValidator


class OntologyLoadError(ValueError):
    """Raised when ontology configuration is missing or structurally invalid."""


@dataclass(frozen=True, slots=True)
class OntologyMetadata:
    """Metadata declared by the ontology manifest."""

    version: str
    name: str
    namespace: str
    domain: str
    manifest_path: Path
    convention_path: Path
    convention_version: str


@dataclass(frozen=True, slots=True)
class LoadedOntology:
    """Fully loaded ontology, convention, and structured validation result."""

    metadata: OntologyMetadata
    concepts: tuple[OntologyConcept, ...]
    convention: OntologyConvention
    validation: OntologyValidationResult


class OntologyLoader:
    """YAML loader with fail-fast cross-file validation."""

    REQUIRED_CONCEPT_FIELDS = frozenset(
        {
            "concept_id",
            "name",
            "parent",
            "description",
            "value_type",
            "dimension",
            "canonical_unit",
            "aliases",
            "constraints",
            "relationships",
        }
    )
    DEFAULT_MANIFEST = "ontology_v0.1.yaml"

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        enforce_convention: bool = True,
    ) -> LoadedOntology:
        manifest_path = cls._resolve_manifest(Path(path))
        manifest = cls._read_yaml(manifest_path)
        cls._validate_manifest(manifest, manifest_path)

        ontology = manifest["ontology"]
        ontology_root = manifest_path.parent.resolve()
        convention_path = cls._resolve_child_path(
            ontology_root,
            ontology["convention_file"],
            label="Convention file",
        )
        try:
            convention = OntologyConvention.from_mapping(
                cls._read_yaml(convention_path)
            )
        except ValueError as exc:
            raise OntologyLoadError(
                f"Invalid ontology convention in {convention_path}: {exc}"
            ) from exc

        metadata = OntologyMetadata(
            version=str(ontology["version"]),
            name=ontology["name"],
            namespace=ontology["namespace"],
            domain=ontology["domain"],
            manifest_path=manifest_path,
            convention_path=convention_path,
            convention_version=convention.version,
        )

        loaded_concepts: list[OntologyConcept] = []
        for relative_name in ontology["concept_files"]:
            concept_path = cls._resolve_child_path(
                ontology_root,
                relative_name,
                label="Concept file",
            )
            document = cls._read_yaml(concept_path)
            cls._validate_concept_document(document, concept_path)
            for index, payload in enumerate(document["concepts"]):
                cls._validate_concept_payload(payload, concept_path, index)
                concept = OntologyConcept.from_mapping(
                    payload,
                    source_file=str(concept_path.relative_to(ontology_root)),
                )
                loaded_concepts.append(concept)

        if not loaded_concepts:
            raise OntologyLoadError(f"No concepts loaded from {manifest_path}")

        concepts = tuple(loaded_concepts)
        validation = OntologyValidator(convention).validate(
            concepts,
            ontology_version=metadata.version,
        )
        if enforce_convention and validation.errors:
            details = "\n".join(
                f"- {issue.code}: {issue.message} ({issue.path or 'ontology'})"
                for issue in validation.errors
            )
            raise OntologyLoadError(
                f"Ontology convention validation failed with "
                f"{len(validation.errors)} error(s):\n{details}"
            )
        return LoadedOntology(metadata, concepts, convention, validation)

    @classmethod
    def _resolve_manifest(cls, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if resolved.is_dir():
            resolved = resolved / cls.DEFAULT_MANIFEST
        if not resolved.is_file():
            raise OntologyLoadError(f"Ontology manifest not found: {resolved}")
        return resolved

    @staticmethod
    def _resolve_child_path(root: Path, relative_name: str, *, label: str) -> Path:
        resolved = (root / relative_name).resolve()
        if not resolved.is_relative_to(root):
            raise OntologyLoadError(f"{label} escapes ontology root: {relative_name!r}")
        return resolved

    @staticmethod
    def _read_yaml(path: Path) -> Mapping[str, Any]:
        if not path.is_file():
            raise OntologyLoadError(f"Ontology YAML file not found: {path}")
        try:
            with path.open("r", encoding="utf-8") as stream:
                payload = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            raise OntologyLoadError(f"Invalid YAML in {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise OntologyLoadError(f"YAML root must be a mapping: {path}")
        return payload

    @staticmethod
    def _validate_manifest(manifest: Mapping[str, Any], path: Path) -> None:
        ontology = manifest.get("ontology")
        required = {
            "version",
            "name",
            "namespace",
            "domain",
            "convention_file",
            "concept_files",
        }
        if not isinstance(ontology, dict):
            raise OntologyLoadError(f"Missing 'ontology' mapping in {path}")
        missing = required - ontology.keys()
        if missing:
            raise OntologyLoadError(
                f"Missing ontology manifest fields {sorted(missing)} in {path}"
            )
        for field in ("name", "namespace", "domain"):
            if not isinstance(ontology[field], str) or not ontology[field].strip():
                raise OntologyLoadError(f"ontology.{field} must be a non-empty string")
        if (
            not isinstance(ontology["convention_file"], str)
            or not ontology["convention_file"].strip()
        ):
            raise OntologyLoadError(
                "ontology.convention_file must be a non-empty path"
            )
        concept_files = ontology["concept_files"]
        if (
            not isinstance(concept_files, list)
            or not concept_files
            or not all(isinstance(item, str) and item.strip() for item in concept_files)
        ):
            raise OntologyLoadError(
                "ontology.concept_files must be a non-empty list of paths"
            )

    @staticmethod
    def _validate_concept_document(document: Mapping[str, Any], path: Path) -> None:
        concepts = document.get("concepts")
        if not isinstance(concepts, list) or not concepts:
            raise OntologyLoadError(
                f"Concept file must contain a non-empty 'concepts' list: {path}"
            )

    @classmethod
    def _validate_concept_payload(
        cls,
        payload: Any,
        path: Path,
        index: int,
    ) -> None:
        location = f"{path} concepts[{index}]"
        if not isinstance(payload, dict):
            raise OntologyLoadError(f"Concept must be a mapping: {location}")
        missing = cls.REQUIRED_CONCEPT_FIELDS - payload.keys()
        if missing:
            raise OntologyLoadError(
                f"Missing concept fields {sorted(missing)} at {location}"
            )

        for field in ("concept_id", "name", "description", "value_type"):
            if not isinstance(payload[field], str) or not payload[field].strip():
                raise OntologyLoadError(f"{field} must be a non-empty string at {location}")
        for field in ("parent", "dimension", "canonical_unit"):
            value = payload[field]
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise OntologyLoadError(f"{field} must be a string or null at {location}")

        aliases = payload["aliases"]
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) and alias.strip() for alias in aliases
        ):
            raise OntologyLoadError(f"aliases must be a list of strings at {location}")
        if not isinstance(payload["constraints"], dict):
            raise OntologyLoadError(f"constraints must be a mapping at {location}")

        relationships = payload["relationships"]
        if not isinstance(relationships, dict):
            raise OntologyLoadError(f"relationships must be a mapping at {location}")
        normalized_relationships: dict[str, list[str]] = {}
        for relation, targets in relationships.items():
            if not isinstance(relation, str) or not relation.strip():
                raise OntologyLoadError(
                    f"relationship names must be non-empty strings at {location}"
                )
            if isinstance(targets, str):
                targets = [targets]
            if not isinstance(targets, list) or not all(
                isinstance(target, str) and target.strip() for target in targets
            ):
                raise OntologyLoadError(
                    f"relationship {relation!r} must target strings at {location}"
                )
            normalized_relationships[relation] = targets
        payload["relationships"] = normalized_relationships
        status = payload.get("status", "active")
        if not isinstance(status, str) or not status.strip():
            raise OntologyLoadError(f"status must be a non-empty string at {location}")
        replaced_by = payload.get("replaced_by")
        if replaced_by is not None and (
            not isinstance(replaced_by, str) or not replaced_by.strip()
        ):
            raise OntologyLoadError(
                f"replaced_by must be a string or null at {location}"
            )
