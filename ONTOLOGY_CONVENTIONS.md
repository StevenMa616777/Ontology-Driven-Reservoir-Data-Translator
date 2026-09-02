# Reservoir Simulation Ontology Convention v0.1.0

## 1. Boundary

The ontology is a stable, platform-independent canonical semantic layer. It answers
"what does this mean?" and must not reproduce a customer schema, database table, or
simulator keyword layout.

```text
Source data -> Semantic mapping -> Canonical ontology -> Application adapter
```

Ontology files contain concepts only. Customer wells, dates, measurements, schedule
values, and case-specific limits belong to canonical instance data. Platform and
customer field mappings belong under a separate `mappings/` layer.

## 2. Required concept fields

Every concept defines the same required fields:

```text
concept_id, name, parent, description, value_type, dimension,
canonical_unit, aliases, constraints, relationships
```

`concept_id` is a globally unique lowercase snake-case namespace ID. It is stable
after release and cannot contain customer, project, database, or simulator names.
Except for direct children of the ontology root, its namespace must align with its
`parent` hierarchy.

`parent` expresses taxonomy or containment only. Functional dependency, reference
conditions, applicability, role, and time validity use controlled relationships.

## 3. Aliases and mappings

An alias is another name for exactly the same semantic concept. It cannot be a value,
number, example, time expression, unit, related concept, or case-specific field.
Case and separators are normalized by the registry, so duplicate case variants are
unnecessary.

Generic petroleum-engineering terms and symbols such as `Krw`, `Bo`, and `MUO` may
remain aliases. Platform-qualified or customer-specific fields belong in a source
mapping file. The `mappings/` directory is deliberately separate from `ontology/`.

## 4. Controlled vocabularies

The machine-readable source of truth is
[`ontology/conventions_v0.1.yaml`](ontology/conventions_v0.1.yaml). It controls:

- value types;
- physical dimensions and their compatible canonical units;
- permitted constraint keys;
- relationship names, endpoint value types, cardinality, and inverses;
- source-specific pollution tokens and suspicious alias patterns.

A source unit is never a new concept. Source mapping and unit normalization convert
all compatible values to the canonical unit declared by the concept.

## 5. Tables and reference conditions

Tables express coordinates and dependent variables. For example:

```text
Sw --coordinate_for--> Oil-water relative permeability
Krw, Kro, Pcow --dependent_on--> Sw
```

Every table must have at least one coordinate and one dependent variable.
`dependent_on` may contain multiple concepts for multidimensional functions.

Properties whose meaning depends on physical conditions use `referenced_at` rather
than hiding the condition in prose. The inverse `reference_for` must also be present.

## 6. Entity, role, and time

`Well` is an entity. Producer, injector, shut-in, and observation are temporal roles,
not necessarily permanent entity types. The MVP retains `well.producer` and
`well.*_injector` for compatibility, but future schedule-aware modeling should use:

```text
well --has_role--> well.role.* --valid_during--> time interval
```

## 7. Validation severity

- `ERROR`: hard convention violation; loading and merge must fail.
- `WARNING`: likely modeling problem; human confirmation is required.
- `INFO`: non-blocking optimization or migration guidance.

The validator runs whenever the registry loads and is also available through the
`ontology-validate` command. It checks identifiers, vocabulary, hierarchy, unit
compatibility, constraints, relationships, aliases, table topology, inverse
relationships, reference conditions, and likely source-specific pollution.

## 8. Versioning

Ontology and Convention versions use semantic versioning:

- PATCH: alias or description corrections that preserve semantics;
- MINOR: additive concepts or relationships;
- MAJOR: removed/renamed IDs or changed core semantics.

Production concept IDs are not deleted directly. A later convention revision will
mark them `deprecated` and declare `replaced_by` as a machine-readable migration
target. Active concepts cannot declare a replacement, and deprecated concepts must
point to an existing successor.

## 9. Merge questions

Before adding a concept, confirm that it is a stable domain meaning rather than a
source field or value; classify it as entity, role, property, coordinate, constraint,
or configuration; define hierarchy, relationships, dimensions, units, reference
conditions, and true aliases; then verify that the concept remains valid when both
the customer and simulator change.
