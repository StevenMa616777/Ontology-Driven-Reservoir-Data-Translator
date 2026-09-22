# Source Mapping Layer

This directory is reserved for platform- and customer-specific source terminology.
Mappings translate a source field or keyword to a stable ontology `concept_id`; they
do not define canonical meaning and are not loaded by the Task 1 ontology registry.

Current v0.1 demo registries are deliberately directional:

- `customer_a.yaml`, `customer_b.yaml`, and `customer_c.yaml` map source-only
  terminology into stable ontology concepts.
- `eclipse.yaml` and `cmg.yaml` map canonical concepts into target intermediate
  types. They do not add simulator keywords to ontology aliases.

`source_*.yaml` adds bounded source schemas for CoreLab, SWOF, production role
tables, and vendor relative-permeability/schedule records. A profile with
`activation_terms` activates automatically only when every signature term occurs
in the current block (including its bounded source metadata). Its `instructions`
and contextual entries are provided to the semantic mapper. These profiles do
not silently select a customer from a filename. The SWOF profile additionally
requires an explicit source pressure unit or METRIC/FIELD declaration.

See the [heterogeneous input repair record](../docs/updates/2026-09-21-heterogeneous-input-repair.md)
for the exact test contract and remaining real-provider verification.

The Eclipse registry is scoped to a METRIC OPM/ECLIPSE demo INCLUDE and uses the
public [OPM Flow reference manual](https://opm-project.org/?page_id=955) as its
open compatibility reference. This is not commercial ECLIPSE certification. The CMG
registry is explicitly an IMEX-style demo control fragment because no licensed
CMG product/version grammar has been frozen for this PoC.
