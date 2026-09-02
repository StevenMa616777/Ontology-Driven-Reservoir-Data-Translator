# Source Mapping Layer

This directory is reserved for platform- and customer-specific source terminology.
Mappings translate a source field or keyword to a stable ontology `concept_id`; they
do not define canonical meaning and are not loaded by the Task 1 ontology registry.

Current v0.1 demo registries are deliberately directional:

- `customer_a.yaml`, `customer_b.yaml`, and `customer_c.yaml` map source-only
  terminology into stable ontology concepts.
- `eclipse.yaml` and `cmg.yaml` map canonical concepts into target intermediate
  types. They do not add simulator keywords to ontology aliases.

The Eclipse registry is scoped to a METRIC OPM/ECLIPSE demo INCLUDE and uses the
public [OPM Flow reference manual](https://opm-project.org/?page_id=955) as its
open compatibility reference. This is not commercial ECLIPSE certification. The CMG
registry is explicitly an IMEX-style demo control fragment because no licensed
CMG product/version grammar has been frozen for this PoC.
