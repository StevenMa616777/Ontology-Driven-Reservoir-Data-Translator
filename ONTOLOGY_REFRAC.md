# Ontology Refactoring Guidance

## Goal

Refactor the current ontology so that it represents reusable reservoir-engineering semantics rather than mirroring the canonical data schema.

The ontology should answer:

> "What does this source data mean?"

It should NOT primarily answer:

> "Where should this value be stored in the canonical schema?"

Keep ontology semantics, task scope, and canonical storage structure conceptually separated.

---

## 1. Core Principle: Define Concepts Once

A physical or domain concept should normally be defined only once.

For example:

- Pressure
- Viscosity
- Density
- Saturation
- Formation Volume Factor
- Relative Permeability
- Compressibility

Do not create a new concept merely because the same quantity appears in a different data structure.

Avoid unnecessary concepts such as:

- Gas PVT Pressure
- Oil PVT Pressure
- Water PVT Pressure
- Gas PVT Viscosity
- Oil PVT Viscosity

when these are fundamentally the same physical concepts used under different contexts.

Prefer:

    Pressure
    Viscosity
    Formation Volume Factor

combined with contextual information such as:

    scope = PVT
    phase = gas
    role = coordinate

Only create a specialized ontology concept when it has distinct domain semantics.

For example, these may legitimately be specialized pressure concepts:

    Pressure
      ├── Bottom Hole Pressure
      ├── Capillary Pressure
      ├── Bubble Point Pressure
      └── Reservoir Pressure

because these terms carry domain meaning beyond simply being "pressure".

---

## 2. Scope Defines the Allowed Semantic Space

Introduce explicit scopes for different reservoir-simulation data domains.

Examples:

    PVT
    Relative Permeability
    Rock Properties
    Well Controls
    Schedule

A scope should define which ontology concepts and relationships are relevant to that type of data.

Example:

    PVT scope

    phases:
      oil
      gas
      water

    concepts:
      pressure
      viscosity
      formation_volume_factor
      compressibility
      viscosibility

A relative-permeability scope might contain:

    Relative Permeability scope

    concepts:
      water_saturation
      water_relative_permeability
      oil_relative_permeability
      capillary_pressure

The scope should REFERENCE ontology concepts rather than redefine them.

Conceptually:

    Ontology = reusable vocabulary
    Scope    = subset/context of that vocabulary for a task

This also means candidate retrieval should preferably happen within the detected scope instead of searching the entire ontology.

---

## 3. Context Is Not Necessarily a New Concept

Represent differences such as phase, table type, coordinate role, or application context separately where possible.

For example:

    concept = Pressure
    scope   = PVT
    phase   = gas
    role    = coordinate

rather than:

    concept = Gas PVT Pressure

Likewise:

    concept = Viscosity
    scope   = PVT
    phase   = oil

rather than defining an entirely independent Oil PVT Viscosity concept.

Relationships/context can carry information such as:

- applies_to phase
- coordinate_for
- dependent_on
- reference_for
- control role
- table membership

Do not encode every combination of these dimensions into a new concept ID.

---

## 4. Keep Canonical Paths Out of Core Ontology Semantics

The canonical schema is a storage/representation model.

For example:

    fluids.gas.pvt.points[i].pressure

is a canonical storage path, not the fundamental semantic identity of the concept.

The semantic representation could instead be approximately:

    concept: pressure
    scope: pvt
    phase: gas
    role: coordinate

A separate mapping layer can resolve this to:

    fluids.gas.pvt.points[i].pressure

Therefore avoid designing ontology concepts primarily around canonical paths.

The intended flow should be approximately:

    Source Data
        ↓
    Scope Detection
        ↓
    Ontology / Semantic Mapping
        ↓
    Semantic Mapping IR
        ↓
    Canonical Path Resolution
        ↓
    Canonical Schema
        ↓
    Simulator-specific adapters

---

## 5. Prevent Combinatorial Ontology Growth

Before adding a new ontology concept, ask:

> Does this represent genuinely new reservoir-engineering semantics,
> or is it an existing concept appearing in a new context?

If it is only:

    existing concept
    × phase
    × scope
    × table
    × canonical location

prefer representing those dimensions as context/relationships instead of creating another ontology concept.

The ontology is expected to grow as domain coverage grows, but it should grow because new SEMANTICS are introduced, not because new schema fields or paths are introduced.

---

## 6. Implication for the Current Implementation

Review the current ontology for concepts whose identity is unnecessarily tied to:

- PVT table location
- oil/gas/water canonical paths
- relative-permeability table structure
- canonical-schema nesting

Where appropriate, consolidate them into reusable concepts and move the distinguishing information into:

- scope
- phase
- role
- relationships
- canonical mapping rules

Do not aggressively flatten genuinely distinct reservoir-engineering concepts.

The objective is not to make the ontology as small as possible.

The objective is:

> one semantic concept, one definition; multiple contexts may reuse it.

---

## Design Rule of Thumb

Use the following mental model throughout the refactor:

**Ontology defines what a concept IS.**

**Scope defines where that concept is relevant and how it participates in a particular data domain.**

**Canonical Schema defines where the resulting information is STORED.**

Do not let these three responsibilities collapse into the same hierarchy.
