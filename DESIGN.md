# Ontology-Driven Reservoir Data Translator
## 开发设计文档 v0.2

> 本次修订重点收紧 Reservoir Simulation Ontology 的建模规范、受控词汇、
> Reference Condition、Entity/Role 边界、Source Mapping 分层、版本机制和
> 自动 Validator；不扩大 PoC 产品 v0.1 的功能范围。

## 1. 项目目标

开发一个面向油藏数值模拟场景的 **Ontology-Driven Data Translation System**。

系统需要能够接收来自不同客户、不同项目、不同格式、不同字段命名规范的数据，通过统一的 Company Ontology 对数据进行语义理解和映射，并转换为公司内部唯一的 Canonical Data Model。

Ontology 的目标不是复刻 Eclipse、CMG、tNavigator、Petrel 或某个客户数据库的
Schema，而是建立稳定、平台无关的 **Reservoir Simulation Canonical Semantic Layer**。

随后，由确定性的 Platform Mapper 将 Canonical Data Model 转换为 Eclipse、CMG、Petrel 等目标平台所需的数据格式。

核心数据流：

```text
Heterogeneous Source Data
        ↓
Parser / Extractor
        ↓
Semantic Mapping Agent
        ↓
Company Ontology
        ↓
Semantic Mapping Result
        ↓
Canonical Builder
        ↓
Canonical Data Model
        ↓
Validation Engine
        ↓
Platform Mapper
        ↓
Eclipse / CMG / Petrel / ...
```

核心原则：

> Agent 负责理解数据，而不负责生成最终数模软件文件。

> Canonical Data Model 是系统内部唯一标准数据表达。

> Canonical → Target Platform 必须尽可能 deterministic，不依赖 LLM 自由生成。

> Ontology 回答“What does this mean?”；Source Mapping 回答“What does this source call it?”。

---

# 2. PoC Scope

v0.1 不实现完整油藏数据体系。

第一阶段只支持以下 Domain：

```text
ReservoirSimulation
│
├── Rock
│   ├── Compressibility
│   └── ReferencePressure
│
├── FluidSystem
│   ├── Oil
│   │   ├── Density
│   │   └── PVT
│   ├── Water
│   │   ├── Density
│   │   └── PVT
│   └── Gas
│       ├── Density
│       └── PVT
│
├── SCAL
│   └── RelativePermeability
│       ├── WaterSaturation
│       ├── WaterRelativePermeability
│       ├── OilRelativePermeability
│       └── OilWaterCapillaryPressure
│
├── Well
│   ├── Producer (MVP Entity Type / 长期为 Role)
│   └── Injector (MVP Entity Type / 长期为 Role)
│
├── WellControl
│   ├── LiquidRateControl
│   ├── WaterInjectionRateControl
│   ├── MinimumBHP
│   └── MaximumBHP
│
├── SimulationSchedule
│   ├── Duration
│   └── ReportInterval
│
└── PhysicalCondition
    └── ReferenceCondition
        ├── ReferencePressure
        ├── ReferenceTemperature
        ├── StandardCondition
        └── ReservoirCondition
```

v0.1 的目标不是完整支持 Eclipse/CMG，而是验证：

1. 不同格式的数据是否能够映射到同一个 Ontology。
2. 不同字段命名是否能够映射到相同 Concept。
3. 不同单位是否能够统一。
4. 不同自然语言表达是否能够统一。
5. Canonical Data Model 是否可以稳定生成。
6. Canonical Data 是否可以通过确定性 Mapper 转换为不同平台格式。

---

# 3. Demo 基准数据

第一版测试数据来自 `demo_material.txt`。

其中包含：

- 样品 X-12 水驱油相渗实验；
- Sw；
- Krw；
- Krow；
- Pcow；
- 油相 PVT；
- 气相 PVT；
- 水相 PVT；
- 油/水/气密度；
- 岩石压缩系数；
- 生产井 A15、B2；
- 注水井 C1；
- 井控条件；
- 5 年模拟周期；
- 季度 Report Step。

相渗数据：

```text
Sw      Krw     Krow    Pcow(bar)
0.15    0.000   0.900   0.35
0.30    0.048   0.552   0.18
0.50    0.205   0.251   0.07
0.70    0.512   0.048   0.02
0.90    0.800   0.000   0.00
```

生产制度包括：

```text
A15 / B2:
Producer
Liquid Rate = 500 m3/day
Minimum BHP = 80 bar

C1:
Water Injector
Water Injection Rate = 800 m3/day
Maximum BHP = 420 bar

Simulation Duration = 5 years
Report Interval = quarterly
```

`quarterly` 在这里是 `report_interval = 3 months` 的实例 Value，不是
`schedule.report_interval` Concept 的 alias。

这些数据直接来自 Demo 文件。

---

# 4. 系统架构

建议采用以下模块：

```text
src/
│
├── ingestion/
│
├── ontology/
│
├── semantic/
│
├── canonical/
│
├── validation/
│
├── mappers/
│
└── api/
```

详细目录：

```text
reservoir-data-translator/
│
├── README.md
├── ONTOLOGY_CONVENTIONS.md
├── pyproject.toml
├── .env.example
│
├── config/
│   └── settings.yaml
│
├── ontology/
│   ├── ontology_v0.1.yaml
│   ├── conventions_v0.1.yaml
│   └── concepts/
│       ├── rock.yaml
│       ├── fluid.yaml
│       ├── scal.yaml
│       ├── well.yaml
│       ├── schedule.yaml
│       └── condition.yaml
│
├── mappings/
│   ├── README.md
│   ├── eclipse.yaml        # 后续任务按实际上下文增加
│   ├── cmg.yaml            # 后续任务按实际上下文增加
│   └── customer_*.yaml     # 不进入 Canonical Ontology
│
├── canonical/
│   └── schemas/
│       ├── reservoir.schema.json
│       ├── rock.schema.json
│       ├── fluid.schema.json
│       ├── scal.schema.json
│       ├── well.schema.json
│       └── schedule.schema.json
│
├── src/
│   └── reservoir_data_translator/
│       ├── ingestion/
│       │   ├── base.py
│       │   ├── text_parser.py
│       │   ├── csv_parser.py
│       │   ├── excel_parser.py
│       │   └── json_parser.py
│       ├── ontology/
│       │   ├── convention.py
│       │   ├── loader.py
│       │   ├── models.py
│       │   ├── registry.py
│       │   └── validator.py
│       ├── semantic/
│       │   ├── mapping_agent.py
│       │   ├── entity_resolver.py
│       │   ├── unit_normalizer.py
│       │   └── models.py
│       ├── canonical/
│       │   ├── builder.py
│       │   └── models.py
│       ├── validation/
│       │   ├── schema_validator.py
│       │   ├── ontology_validator.py
│       │   ├── domain_validator.py
│       │   └── export_validator.py
│       ├── mappers/
│       │   ├── base.py
│       │   ├── eclipse/
│       │   │   └── mapper.py
│       │   └── cmg/
│       │       └── mapper.py
│       └── api/
│           └── main.py
│
├── tests/
│   ├── fixtures/
│   │   ├── client_a.txt
│   │   ├── client_b.json
│   │   └── client_c.xlsx
│   │
│   ├── expected/
│   │   └── canonical_demo.json
│   │
│   ├── test_semantic_mapping.py
│   ├── test_canonical_builder.py
│   ├── test_validation.py
│   └── test_mapper.py
│
└── examples/
    └── demo_material.txt
```

---

# 5. Company Ontology

Ontology 使用 YAML 定义，不得写死在 Python 代码中。

Ontology 本身是平台无关的 Canonical Semantic Layer，不得绑定具体客户字段、
数据库列名、项目名称或 Eclipse/CMG 等模拟器 Keyword。

```text
Source Data
    ↓
Source Mapping
    ↓
Canonical Ontology Concept
    ↓
Canonical Data / Application / Simulator Adapter
```

## 5.1 Concept 基本结构

每个 Concept 必须使用相同的必填字段：

```yaml
concept_id:
name:
parent:
description:
value_type:
dimension:
canonical_unit:
aliases:
constraints:
relationships:
```

可选的生命周期字段：

```yaml
status: active | deprecated
replaced_by: another.stable.concept_id | null
```

Ontology YAML 文件必须共用同一份 `conventions_v0.1.yaml`，不允许各文件自行
解释字段语义。

## 5.2 concept_id 规则

`concept_id` 是稳定的机器语义标识符：

1. 全局唯一；
2. 使用小写 snake_case namespace；
3. 发布后原则上不得修改；
4. 不得包含客户、项目、数据库表或平台名称；
5. 不得直接使用平台专有 Keyword，除非它已是通用领域概念；
6. namespace 层级应与 `parent` hierarchy 一致；
7. 无法一致时，必须在 Convention 的 `hierarchy_exceptions` 中记录明确理由。

例如：

```text
reservoir_simulation
fluid
fluid.oil
fluid.oil.pvt
fluid.oil.pvt.viscosity
rock.compressibility
well.control.liquid_rate
```

## 5.3 parent 与 relationships

`parent` 只表达 taxonomy / containment hierarchy。

```yaml
concept_id: fluid.oil.pvt.viscosity
parent: fluid.oil.pvt
```

表示 Oil Viscosity 在 Ontology namespace 中属于 Oil PVT；它不能用来表达
“Viscosity depends on Pressure”。函数依赖必须使用：

```yaml
relationships:
  dependent_on:
    - fluid.oil.pvt.pressure
```

因此必须严格保持：

```text
parent = Ontology hierarchy
relationships = Semantic relationships
```

## 5.4 aliases、Value 与 Source Mapping

`aliases` 只保存“同一 semantic concept 的不同名称”。

允许：

```text
oil viscosity
crude oil viscosity
mu oil
MUO
油粘度
原油粘度
```

禁止将以下内容写入 aliases：

1. 具体数值；
2. 枚举值；
3. 示例数据；
4. 时间表达；
5. 单位；
6. 相关但语义不同的 Concept；
7. 平台专有或客户专有字段。

例如 `quarterly`、`monthly`、`每季度` 是 `schedule.report_interval` 的 Value，
不是 alias。

通用石油工程缩写，例如 `Krw`、`Bo`、`MUO`，可保留在 aliases；平台或客户
专有名称必须进入独立 `mappings/` 层。

```yaml
# mappings/customer_a.yaml
source_term: 原油粘度
concept_id: fluid.oil.pvt.viscosity

# mappings/eclipse.yaml
source_term: platform-specific field with context
concept_id: fluid.oil.pvt.viscosity
```

Ontology 回答“What does this mean?”；Mapping 回答“What does this source call it?”。

## 5.5 value_type 受控词汇

v0.1 只允许：

```text
object
entity
entity_type
float
integer
string
boolean
enum
table
duration
date
datetime
```

其中：

- `object`：结构性概念，本身通常没有直接数值；
- `entity`：可独立识别的领域实体，例如 Well；
- `entity_type`：Entity 的分类或 MVP Role；
- `float/integer`：数值属性；
- `enum`：有限枚举；
- `table`：coordinate 与 dependent variables 组成的表格/函数关系；
- `duration`：时间长度。

新增 `value_type` 必须修改全局 Convention，不得在单个 Concept YAML 中自行创造。

## 5.6 dimension 与 canonical_unit

```text
dimension = 物理维度
canonical_unit = Ontology 内部标准单位
```

例如：

```yaml
# Oil Viscosity
dimension: dynamic_viscosity
canonical_unit: cP

# Pressure
dimension: pressure
canonical_unit: bar

# Water Saturation
dimension: dimensionless
canonical_unit: fraction
```

要求：

1. 同一 dimension 只使用 Convention 允许的 canonical unit；
2. Source unit 不属于 Ontology Concept 定义；
3. Source Mapping / Unit Normalizer 负责单位转换；
4. 不得因为客户使用 `psi` 或软件使用 `kPa` 创建新的 Pressure Concept；
5. 流量不得只按单位粗暴合并；必须区分地面液量、水量、气量和地下体积流量等语义维度。

## 5.7 constraints

`constraints` 只表达 Concept 本身的数据合法性：

```yaml
# Saturation / Relative Permeability
minimum: 0
maximum: 1

# Pressure
exclusive_minimum: 0
```

客户业务规则、模拟器限制或项目经验阈值不得写入全局 Ontology constraint。
例如“某油田要求 BHP > 120 bar”属于 Case Constraint。

## 5.8 relationships 受控词汇

v0.1 允许的 relationship：

| Relationship | 语义 | Inverse |
|---|---|---|
| `applies_to` | Property / Control / Constraint 可应用于某 Entity / Entity Type | 无 |
| `dependent_on` | 因变量依赖一个或多个 coordinate / condition | 无 |
| `coordinate_for` | Concept 作为 table / function 的坐标 | 无 |
| `referenced_at` | Property 在某参考条件下定义 | `reference_for` |
| `reference_for` | Reference Condition 反向指向被定义的 Property | `referenced_at` |
| `configures` | Configuration 对结构性概念进行配置 | 无 |
| `has_property` | Entity / Object 拥有稳定属性 | 无 |
| `has_role` | Entity 拥有可随时间变化的 Role | 无 |
| `valid_during` | Role / Control / Fact 在时间区间内有效 | 无 |

每种 Relationship 的 source value type、target value type、多值性和 inverse 必须在
Convention 中定义。不得自行创造 `depends`、`depends_on`、`related_to`、
`reference_at` 等近义词。

## 5.9 Property 与 Reference Condition

只有在特定物理条件下才有完整语义的 Property，必须能机器可读地表达
Reference Condition。

```text
Oil Density = 850 kg/m3
Reference Temperature = 20 degC
Reference Pressure = 1.01325 bar
```

v0.1 至少定义：

```text
condition.reference
condition.reference.pressure
condition.reference.temperature
condition.reference.standard
condition.reference.reservoir
```

Density 等 Property 使用 `referenced_at` 指向 Reference Condition，并由
`reference_for` 反向验证。不得只在 description 中隐式描述关键参考条件。

## 5.10 Table / Functional Property

PVT、SCAL 不是一组互不相关的列，必须表达：

```text
Coordinate → Dependent Variables
```

例如 Oil-Water Relative Permeability：

```text
Sw --coordinate_for--> relative_permeability
Krw --dependent_on--> Sw
Kro --dependent_on--> Sw
Pcow --dependent_on--> Sw
```

PVT 同理：

```text
Pressure → Bo
Pressure → Oil Viscosity
Pressure → Rs
```

`dependent_on` 必须允许多 Concept，以支持 `Property = f(Pressure, Temperature)`。
每个 `table` 必须至少有一个 coordinate 和一个 dependent variable。

## 5.11 Entity 与 Role

Well 是稳定的 Entity；Producer、Water Injector、Gas Injector、Shut-in 等状态可随时间
变化，长期建模应优先使用 Role：

```text
well
  --has_role--> well.role.producer
  --valid_during--> time interval
```

v0.1 MVP 暂时保留 `well.producer`、`well.water_injector`、`well.gas_injector` 作为
`entity_type`。其中与 parent namespace 不完全一致的 ID 必须记录为有理由的
`HIERARCHY_EXCEPTION`，不得静默忽略。

## 5.12 Concept 与 Instance

Ontology YAML 只定义 Concept，不存储具体业务 Instance。

```text
Ontology Concept:
well.control.liquid_rate

Canonical Instance:
Well A12 / Liquid Rate = 100 m3/day / Date = 2026-01-01
```

具体井名、日期、油田、数值和生产制度属于 Canonical Data / Case Data，不得作为 Concept。

## 5.13 正确示例

```yaml
concept_id: scal.relative_permeability.krw
name: Water Relative Permeability
parent: scal.relative_permeability
description: >
  Relative permeability of the water phase
  as a function of water saturation.
value_type: float
dimension: dimensionless
canonical_unit: fraction
aliases:
  - Krw
  - water relative permeability
  - water relperm
  - 水相相对渗透率
  - 水相相渗
constraints:
  minimum: 0
  maximum: 1
relationships:
  dependent_on:
    - scal.relative_permeability.water_saturation
```

Well Control：

```yaml
concept_id: well.control.liquid_rate
name: Liquid Rate Control
parent: well.control
description: Surface liquid production-rate target for a producer.
value_type: float
dimension: surface_liquid_volume_per_time
canonical_unit: m3/day
aliases:
  - liquid rate
  - liquid production rate
  - 定液
  - 定液量
  - 日产液
  - 日产液量
constraints:
  minimum: 0
relationships:
  applies_to:
    - well.producer
```

`LRAT` 等平台/source mnemonic 不应写入这个 Concept，而应在具备平台上下文的
Source Mapping 中处理。

## 5.14 Ontology Versioning

Ontology manifest 使用 `MAJOR.MINOR.PATCH`：

- PATCH：alias、description 等不改变 canonical semantics 的修正；
- MINOR：新增 Concept / Relationship，不破坏现有映射；
- MAJOR：删除/重命名 ID，或改变已有 Concept 核心语义。

已投入使用的 `concept_id` 不得直接删除；必须先标记 `deprecated`，并用
`replaced_by` 指向存在的 migration target。

---

# 6. Ontology Registry

实现统一 Registry：

```python
OntologyRegistry
```

至少提供：

```python
get_concept(concept_id)

search_by_alias(text)

get_relationships(concept_id)

validate_relationship(source, relation, target)

list_concepts()
```

Ontology Loader：

```python
registry = OntologyRegistry.load("./ontology")

assert registry.validation.valid
```

系统启动时一次性加载：

```text
ontology manifest
+
ontology convention
+
all concept YAML files
↓
Ontology Validator
↓
immutable OntologyRegistry
```

Runtime 不应频繁读取 YAML。

## 6.1 Ontology Definition Validator

每次新增或修改 Ontology YAML 时必须自动检查：

- `concept_id` 是否全局唯一且符合命名规则；
- `parent` 是否存在、是否循环、是否与 namespace hierarchy 一致；
- relationship target 是否存在；
- `value_type`、dimension、canonical unit 是否属于受控词汇；
- canonical unit 是否与 dimension 兼容；
- relationship 名称、source/target type、多值性和 inverse 是否合法；
- alias 是否重复、冲突，或疑似将 Value / Unit 写入 aliases；
- constraint key、数值类型和 minimum/maximum 区间是否合法；
- `table` 是否至少存在 coordinate 和 dependent variable；
- `referenced_at/reference_for` 是否双向一致；
- `coordinate_for/dependent_on` 的 table hierarchy 是否合理；
- 是否出现平台/客户专有名称污染 Canonical Ontology；
- deprecated Concept 是否有有效的 `replaced_by` migration target。

校验结果必须是结构化的：

```json
{
  "valid": false,
  "errors": [],
  "warnings": [],
  "infos": []
}
```

级别定义：

- `ERROR`：违反硬性 Convention，Registry 加载失败，禁止 merge；
- `WARNING`：可能存在建模问题，需人工确认；
- `INFO`：已记录理由的例外或建议性优化。

命令行入口：

```bash
ontology-validate ontology
ontology-validate ontology --json
```

注意：这里校验的是 **Ontology Definition 本身**；后续 L2 Ontology Validation
校验的是 **Canonical Instance 是否符合 Ontology relationship**。两者不得混为一层。

---

# 7. Ingestion Layer

Ingestion Layer 只负责：

> 将不同文件变成统一的 Raw Document Representation。

禁止在 Parser 内进行领域语义判断。

例如：

```python
class RawDocument:
    source_id: str
    source_type: str
    file_name: str

    blocks: list[RawBlock]
```

RawBlock：

```python
class RawBlock:
    block_id: str

    block_type: Literal[
        "text",
        "table",
        "key_value"
    ]

    content: Any

    source_location: str | None
```

例如 Excel：

```text
Well | Rate | Min BHP
A15  | 500  | 80
```

转换为：

```json
{
  "block_type": "table",
  "content": {
    "columns": [
      "Well",
      "Rate",
      "Min BHP"
    ],
    "rows": [
      ["A15", 500, 80]
    ]
  }
}
```

Parser 不判断：

```text
Rate == LiquidRate
```

这属于 Semantic Layer。

---

# 8. Semantic Mapping Agent

这是系统唯一主要依赖 LLM 的模块。

职责：

```text
Raw Document
      +
Relevant Ontology Concepts
      +
Canonical Schema
      ↓
Semantic Mapping Result
```

Agent 必须输出 Structured Output。

禁止输出自由文本作为后续系统输入。

---

# 9. Semantic Mapping Result

定义：

```python
class SemanticMapping:
    source_text: str | None
    source_block_id: str

    ontology_concept: str

    canonical_path: str

    value: Any
    source_unit: str | None
    canonical_unit: str | None

    confidence: float

    provenance: Provenance
```

例如：

```json
{
  "source_text": "定液量 500 方/天",

  "source_block_id": "block_21",

  "ontology_concept":
    "well.control.liquid_rate",

  "canonical_path":
    "wells[].controls[].target",

  "value": 500,

  "source_unit":
    "方/天",

  "canonical_unit":
    "m3/day",

  "confidence":
    0.98
}
```

---

# 10. Agent Prompt 原则

System Prompt 至少包含：

```text
You are a semantic data mapping engine for reservoir simulation data.

Your task is NOT to generate Eclipse, CMG, Petrel or other simulator files.

Your task is to map source data into concepts defined by the Company Ontology and fields defined by the Canonical Data Model.

Rules:

1. Never invent missing values.
2. Never infer numerical values without explicit evidence.
3. Use only ontology concepts supplied to you.
4. Preserve source provenance.
5. Identify source units explicitly.
6. Normalize units only when conversion is deterministic.
7. Return confidence for every mapping.
8. If mapping is ambiguous, return ambiguity instead of guessing.
9. If no valid ontology concept exists, mark the source field as UNMAPPED.
10. Output structured JSON only.
```

Agent 不允许自己创造新的：

```text
ontology_concept
canonical_path
unit
```

---

# 11. Ontology Retrieval

不要每次把整个 Ontology 全塞给 LLM。

实现：

```python
OntologyRetriever
```

流程：

```text
Raw Block
   ↓
Candidate Retrieval
   ↓
Top-K Ontology Concepts
   ↓
Semantic Agent
```

v0.1 可以非常简单：

```text
alias matching
+
keyword matching
+
optional embedding similarity
```

不需要一开始使用复杂 RAG。

---

# 12. Unit Normalizer

Unit conversion 必须 deterministic。

禁止让 LLM 负责最终换算。

接口：

```python
normalize(
    value,
    source_unit,
    target_unit
)
```

v0.1 支持：

```text
Pressure:
bar
psi
kPa
MPa

Rate:
m3/day
bbl/day

Viscosity:
cP
Pa.s

Density:
kg/m3
g/cm3

Time:
day
month
year

Compressibility:
1/bar
1/psi
```

例如：

```text
500 方/天

Agent:
方/天 → m3/day

Unit Normalizer:
500 → 500
```

---

# 13. Canonical Data Model

Canonical Data Model 是系统内部唯一 Source of Truth。

Canonical Model 不允许包含：

```text
SWOF
WCONPROD
WCONINJE
PVTO
...
```

这些属于目标 Simulator。

---

# 14. Canonical Root Model

建议使用 Pydantic Model，同时生成 JSON Schema。

```python
class ReservoirSimulationModel(BaseModel):

    schema_version: str

    rock: RockModel

    fluids: FluidSystemModel

    scal: SCALModel

    wells: list[WellModel]

    schedule: SimulationSchedule
```

---

# 15. Canonical Property

所有带物理量纲的 Property 尽量统一：

```python
class PhysicalValue(BaseModel):

    value: float

    unit: str

    provenance: Provenance | None

    confidence: float | None
```

不要使用：

```python
pressure: float
```

而使用：

```python
pressure: PhysicalValue
```

---

# 16. Provenance Model

```python
class Provenance(BaseModel):

    source_id: str

    source_file: str | None

    source_block_id: str | None

    source_location: str | None

    raw_text: str | None

    extraction_method: str
```

要求 Canonical Data 可以追溯：

```text
Target Simulator
        ↑
Mapper
        ↑
Canonical Field
        ↑
Semantic Mapping
        ↑
Raw Block
        ↑
Source File
```

---

# 17. Canonical SCAL

```python
class RelativePermeabilityPoint(BaseModel):

    sw: PhysicalValue

    krw: PhysicalValue

    kro: PhysicalValue

    pcow: PhysicalValue | None
```

```python
class RelativePermeabilityModel(BaseModel):

    id: str

    sample_id: str | None

    phase_system: list[str]

    displacement_type: str | None

    points: list[RelativePermeabilityPoint]
```

---

# 18. Canonical PVT

设计成平台无关形式：

```python
class PVTPoint(BaseModel):

    pressure: PhysicalValue

    formation_volume_factor: PhysicalValue | None

    viscosity: PhysicalValue | None
```

```python
class PVTModel(BaseModel):

    model_type: Literal[
        "table",
        "constant"
    ]

    points: list[PVTPoint]
```

Demo 中油 PVT 为 100–400 bar 下对应 Bo 和 viscosity。

---

# 19. Canonical Well

```python
class WellModel(BaseModel):

    id: str

    well_type: Literal[
        "producer",
        "water_injector",
        "gas_injector",
        "unknown"
    ]

    controls: list[WellControl]
```

Control：

```python
class WellControl(BaseModel):

    control_type: Literal[
        "liquid_rate",
        "oil_rate",
        "water_rate",
        "gas_rate",
        "water_injection_rate",
        "gas_injection_rate",
        "bhp"
    ]

    target: PhysicalValue

    constraints: list[WellConstraint]
```

Constraint：

```python
class WellConstraint(BaseModel):

    constraint_type: Literal[
        "minimum_bhp",
        "maximum_bhp"
    ]

    value: PhysicalValue
```

---

# 20. Canonical Builder

Agent 不直接生成完整 Canonical Model。

实现：

```python
CanonicalBuilder
```

输入：

```python
list[SemanticMapping]
```

输出：

```python
ReservoirSimulationModel
```

职责：

```text
Semantic Mapping
        ↓
Entity Grouping
        ↓
Relationship Resolution
        ↓
Unit Normalization
        ↓
Canonical Object Construction
```

例如：

```text
A15
+
producer
+
liquid_rate = 500
+
minimum_bhp = 80
```

Builder 将它们组合为一个：

```text
WellModel("A15")
```

---

# 21. Entity Resolution

必须解决同一个 Entity 在多个位置出现的问题。

例如：

```text
A15
Well A15
A-15
A15井
```

系统需要：

```text
EntityResolver
```

v0.1 可以：

```text
Exact Match
→ Normalized String Match
→ Alias Match
→ Agent-assisted Match
```

如果不能确定：

```text
Human Review Required
```

禁止强行合并。

---

# 22. Validation Engine

Validation 分成四层：

```text
L1 Schema Validation
L2 Ontology Validation
L3 Domain Validation
L4 Export Validation
```

---

# 23. L1 Schema Validation

检查：

```text
required field
data type
enum
schema structure
```

优先依赖 Pydantic / JSON Schema。

---

# 24. L2 Ontology Validation

检查 Canonical Instance 中的 Entity / Property / Control / Constraint 组合是否符合
Ontology relationship。

它不重复检查 Ontology YAML 定义是否合法；Ontology Definition 由 6.1 节的
Validator 在加载时检查。

例如：

```text
Producer
+
WaterInjectionRateControl
```

应返回：

```text
ONTOLOGY_RELATIONSHIP_ERROR
```

因为：

```text
WaterInjectionRateControl
applies_to
WaterInjector
```

对 Role / Time-aware Model，L2 还应检查：

```text
Well
+ has_role
+ valid_during
+ WellControl effective time
```

禁止将某个时间段的 Producer / Injector Role 错误当成井的永久类型。

---

# 25. L3 Domain Validation

v0.1 至少：

```text
0 <= Sw <= 1

0 <= Krw <= 1

0 <= Kro <= 1

Pressure > 0

Viscosity > 0

Density > 0

Rate >= 0
```

可以增加 Warning：

```text
Krw generally expected to increase with Sw.

Kro generally expected to decrease with Sw.
```

注意：

违反强物理规则：

```text
ERROR
```

违反经验趋势：

```text
WARNING
```

不要混在一起。

---

# 26. Validation Result

统一输出：

```json
{
  "valid": false,

  "errors": [
    {
      "code": "VALUE_OUT_OF_RANGE",
      "path": "scal.relative_permeability[0].points[2].krw",
      "message": "Krw must be between 0 and 1."
    }
  ],

  "warnings": []
}
```

---

# 27. Platform Mapper

定义统一 Interface：

```python
class PlatformMapper(ABC):

    @abstractmethod
    def validate_export(
        self,
        canonical_model
    ):
        pass

    @abstractmethod
    def map(
        self,
        canonical_model
    ):
        pass

    @abstractmethod
    def render(
        self,
        mapped_model
    ) -> str:
        pass
```

---

# 28. Mapper 原则

Mapper：

```text
Canonical
    ↓
Platform Intermediate Model
    ↓
Template Renderer
    ↓
Target File
```

不要：

```text
Canonical
    ↓
LLM
    ↓
Target File
```

---

# 29. Mapping Registries

系统必须区分两种方向相反的 Mapping。

## 29.1 Source Mapping Registry

用于将客户字段、平台输入字段或自然语言表达映射到 Canonical Concept：

```yaml
mapping_version: "0.1.0"
source_system: customer_a

entries:
  - source_term: OilVisc
    concept_id: fluid.oil.pvt.viscosity

  - source_term: 原油粘度
    concept_id: fluid.oil.pvt.viscosity
```

Source Mapping 不得改变 Concept 的核心语义、dimension 或 canonical unit。

## 29.2 Platform Output Mapping Registry

用于将 Canonical Concept 确定性转换为目标平台的 Intermediate Model / Keyword：

```yaml
platform: eclipse

version: "v0.1-demo"

mappings:

  scal.relative_permeability:
    target_type: SWOF

  well.control.liquid_rate:
    target_type: WCONPROD

  well.control.water_injection_rate:
    target_type: WCONINJE
```

具体字段位置、必填上下文和 simulator-specific transformation 在 mapper 内实现。

必须保持：

```text
Source-specific term → Source Mapping → Canonical Concept
Canonical Concept → Platform Mapping → Target representation
```

不得为了方便 Platform Mapper，把 `SWOF`、`WCONPROD`、`WCONINJE` 等写成
Canonical `concept_id` 或无上下文 alias。

---

# 30. Export Validation

每个平台定义自己的 Requirement。

例如：

```python
EclipseExportValidator
```

输出：

```json
{
  "ready": false,

  "missing": [
    "..."
  ],

  "warnings": [
    "..."
  ]
}
```

关键原则：

> Canonical Valid 不代表 Target Export Ready。

如果缺失目标软件必需数据：

```text
DO NOT GUESS
DO NOT GENERATE FAKE VALUES
```

直接阻止 Export。

---

# 31. API

PoC 建议 FastAPI。

核心接口：

```text
POST /ingest

POST /semantic-map

POST /canonical/build

POST /validate

POST /export/{platform}
```

同时提供一个完整 Pipeline：

```text
POST /translate
```

输入：

```json
{
  "source": "...",
  "target_platform": "eclipse"
}
```

内部：

```text
ingest
→ semantic map
→ canonical build
→ validate
→ export validate
→ map
→ render
```

---

# 32. Translate Result

不要只返回最终文件。

返回：

```json
{
  "status": "success",

  "source": {},

  "semantic_mapping": [],

  "canonical_model": {},

  "validation": {},

  "export_validation": {},

  "target": {
    "platform": "eclipse",
    "content": "..."
  }
}
```

这样 Demo UI 可以展示完整过程。

---

# 33. Human Review

设计 Review Queue。

当：

```text
confidence >= 0.95
```

默认：

```text
AUTO_ACCEPTED
```

当：

```text
0.80 <= confidence < 0.95
```

：

```text
ACCEPTED_WITH_WARNING
```

当：

```text
confidence < 0.80
```

：

```text
REVIEW_REQUIRED
```

当：

```text
multiple ontology candidates
```

：

```text
AMBIGUOUS
```

---

# 34. Unknown Field

这是系统非常重要的能力。

如果客户出现：

```text
XYZ_COEFF
```

Ontology 找不到对应 Concept。

Agent 必须：

```json
{
  "status": "UNMAPPED",

  "source_field": "XYZ_COEFF",

  "candidate_concepts": [],

  "confidence": 0
}
```

不要为了追求 Mapping Rate 强行匹配。

以后可以基于大量：

```text
UNMAPPED
```

反过来完善 Company Ontology。

---

# 35. 测试数据设计

不要只测试 Demo Material。

创建三套语义完全一样但 Schema 完全不同的数据。

### Client A

```text
Well_ID | Type | LiquidRate | MinBHP
A15     | PROD | 500        | 80
```

### Client B

```json
{
  "well_name": "A15",
  "operation": "production",
  "control_mode": "LRAT",
  "target": 500,
  "pressure_floor": 80
}
```

### Client C

```text
A15井采用定液生产制度，
日产液控制在500方，
井底流压不得低于80 bar。
```

Expected Canonical Output 必须相同：

```json
{
  "id": "A15",
  "well_type": "producer",

  "controls": [
    {
      "control_type": "liquid_rate",

      "target": {
        "value": 500,
        "unit": "m3/day"
      },

      "constraints": [
        {
          "constraint_type": "minimum_bhp",

          "value": {
            "value": 80,
            "unit": "bar"
          }
        }
      ]
    }
  ]
}
```

---

# 36. 测试目标

核心测试不是：

```text
LLM output == exact string
```

而是：

### Semantic Equivalence Test

```text
Canonical(Client A)
≈
Canonical(Client B)
≈
Canonical(Client C)
```

忽略：

```text
provenance
confidence
source_id
```

后，业务数据必须相等。

这是 PoC 最重要的自动测试。

---

# 37. Evaluation Metrics

v0.1 至少记录：

### Mapping Accuracy

```text
正确 Ontology Mapping 数
/
全部 Ground Truth Mapping 数
```

### Canonical Accuracy

```text
正确 Canonical Field 数
/
Ground Truth Field 数
```

### Unit Accuracy

```text
正确单位转换数
/
全部单位转换数
```

### Unmapped Precision

系统不知道时，能否正确说“不知道”。

### Cross-Source Consistency

不同输入表达：

```text
A / B / C
```

是否生成等价 Canonical Data。

---

# 38. Logging

每次转换建立：

```text
translation_id
```

记录：

```text
Source
↓
Raw Blocks
↓
Ontology Candidates
↓
Agent Mapping
↓
Confidence
↓
Canonical Build
↓
Validation
↓
Mapper
↓
Target
```

以后出现错误可以完整回放。

---

# 39. 非目标

v0.1 明确不做：

```text
完整 Eclipse 支持
完整 CMG 支持
完整 Petrel 支持

完整油藏 Ontology

复杂知识图谱数据库

自动修改 Ontology

Multi-Agent architecture

复杂 Agent orchestration

Agent 自主调用 Simulator

自动补全缺失工程参数
```

不要为了“Agent 化”增加不必要架构。

---

# 40. 技术选型

建议：

```text
Language:
Python 3.12+

API:
FastAPI

Schema:
Pydantic v2
JSON Schema

Ontology:
YAML

Ontology Convention:
YAML controlled vocabularies

Ontology Definition Validation:
Python deterministic rules + CLI JSON output

Structured LLM Output:
Pydantic / JSON Schema

Unit:
Pint

Excel:
openpyxl / pandas

CSV:
Python csv / pandas

Testing:
pytest

Template:
Jinja2

Logging:
structlog
```

v0.1 不需要：

```text
Neo4j
LangGraph
Kafka
Spark
Airflow
Kubernetes
```

除非后续需求明确需要。

---

# 41. LLM Provider Abstraction

不要把项目绑定到某一个模型。

定义：

```python
class SemanticModelProvider(ABC):

    @abstractmethod
    async def structured_generate(
        self,
        prompt,
        response_model
    ):
        pass
```

实现：

```text
OpenAIProvider
DeepSeekProvider
LocalModelProvider
```

Semantic Agent 只依赖：

```text
SemanticModelProvider
```

不依赖具体 SDK。

---

# 42. PoC UI

如果需要 Demo UI，第一版可以使用简单 Web UI。

页面布局：

```text
┌──────────────────────────────────────────┐
│ Source                                   │
│                                          │
│ [Upload / Paste]                         │
└──────────────────────────────────────────┘

                    ↓

┌──────────────────────────────────────────┐
│ Semantic Mapping                         │
│                                          │
│ 定液量 → LiquidRateControl     98%       │
│ 方/天   → m3/day               99%       │
│ BHP下限 → MinimumBHP            97%       │
└──────────────────────────────────────────┘

                    ↓

┌──────────────────────────────────────────┐
│ Canonical Data Model                     │
│                                          │
│ { JSON Viewer }                          │
└──────────────────────────────────────────┘

                    ↓

        Target Platform

       [Eclipse] [CMG]

                    ↓

┌──────────────────────────────────────────┐
│ Generated Target Format                  │
└──────────────────────────────────────────┘
```

重点不是 UI 美观，而是让用户能够看见：

```text
Source
→ Semantic
→ Canonical
→ Target
```

---

# 43. Implementation Phases

## Phase 1 — Canonical Foundation

首先实现：

```text
Ontology YAML
Ontology Convention + Definition Validator
Canonical Pydantic Models
Unit Normalizer
Validation Engine
```

不接 LLM。

手工创建 SemanticMapping 测试 Canonical Builder。

Acceptance：

```text
Ontology Definition Validation: 0 ERROR / 0 WARNING

Manual SemanticMapping
→ Canonical
→ Validation PASS
```

---

## Phase 2 — Semantic Agent

实现：

```text
Ontology Retriever
Semantic Mapping Agent
Structured Output
Confidence
UNMAPPED
AMBIGUOUS
```

Acceptance：

```text
demo_material.txt
→ Semantic Mapping
→ Canonical Model
```

结果与 Ground Truth 基本一致。

---

## Phase 3 — Heterogeneous Input

增加：

```text
TXT
JSON
CSV
XLSX
```

Acceptance：

```text
Client A
Client B
Client C

→ Equivalent Canonical Model
```

---

## Phase 4 — Platform Mapper

先只实现：

```text
Eclipse Demo Mapper
```

然后：

```text
CMG Demo Mapper
```

Acceptance：

```text
Same Canonical Model
→ Eclipse Representation

Same Canonical Model
→ CMG Representation
```

---

## Phase 5 — Demo UI

实现完整：

```text
Upload
→ Mapping
→ Review
→ Canonical
→ Validate
→ Export
```

---

# 44. Codex 实施顺序

不要一次性要求 Codex 实现整个系统。

建议按照以下顺序逐步提交任务。

### Task 1

```text
Initialize the Python project.

Implement the ontology YAML structure, machine-readable Convention,
ontology loader/registry and structured Ontology Definition Validator.

Use semantic versioning for the ontology manifest.

Enforce controlled vocabularies for value_type, dimension/canonical_unit,
constraints and relationships.

Keep Alias, Value, Instance and Source Mapping strictly separated.

Model table coordinates/dependent variables and machine-readable
Reference Conditions.

Support ERROR / WARNING / INFO findings and active/deprecated/replaced_by lifecycle.

Provide an ontology-validate CLI with text and JSON output.

Do not implement any LLM integration.

Add unit tests for ontology loading, alias lookup, relationship validation,
hierarchy, units, constraints, table topology, inverse relationships,
source pollution and version migration.

Acceptance:

Current ontology loads with 0 ERROR and 0 WARNING.
Any documented MVP hierarchy exception is returned as INFO with a reason.
```

### Task 2

```text
Implement the canonical Pydantic data models described in the design document.

Implement PhysicalValue and Provenance as reusable base models.

Add JSON serialization and schema generation.

Add unit tests.
```

### Task 3

```text
Implement UnitNormalizer using Pint.

Support pressure, rate, viscosity, density, time and compressibility units.

Add deterministic unit conversion tests.
```

### Task 4

```text
Implement SemanticMapping models and CanonicalBuilder.

Do not use an LLM yet.

Use manually constructed SemanticMapping fixtures to build the canonical demo model.

Add tests.
```

### Task 5

```text
Implement the four-level validation architecture:

schema validation
ontology validation
domain validation
export validation

Add structured ValidationResult objects.
```

### Task 6

```text
Implement RawDocument / RawBlock abstractions and TXT, JSON, CSV and XLSX ingestion.

Parsers must not perform domain semantic mapping.
```

### Task 7

```text
Implement OntologyRetriever.

Start with deterministic alias and keyword retrieval.

Keep the interface extensible for embedding retrieval later.
```

### Task 8

```text
Implement SemanticMappingAgent using an abstract SemanticModelProvider.

Require structured output.

The agent must only select concepts supplied by OntologyRetriever.

Implement UNMAPPED and AMBIGUOUS states.

Never allow the agent to invent canonical paths.
```

### Task 9

```text
Create three heterogeneous test datasets representing equivalent reservoir data.

Verify that all three produce semantically equivalent canonical models.
```

### Task 10

```text
Implement PlatformMapper interface and the first Eclipse demo mapper.

Do not use LLM generation inside the mapper.

Separate mapping logic from text rendering.
```

### Task 11

```text
Implement a CMG demo mapper using the same PlatformMapper architecture.

Verify that Eclipse and CMG exporters consume exactly the same canonical model.
```

### Task 12

```text
Expose the pipeline using FastAPI.

Add:

POST /ingest
POST /semantic-map
POST /canonical/build
POST /validate
POST /export/{platform}
POST /translate
```

---

# 45. Definition of Done — v0.1

PoC 完成必须满足：

```text
✓ TXT input
✓ JSON input
✓ CSV input
✓ XLSX input

✓ Company Ontology
✓ Machine-readable Ontology Convention
✓ Semantic-versioned Ontology Manifest
✓ Controlled value_type / dimension / canonical_unit / relationship vocabularies
✓ Structured Ontology Definition Validator (ERROR / WARNING / INFO)
✓ Reference Condition concepts and inverse relationships
✓ Table coordinate / dependent-variable validation
✓ Concept / Value / Instance / Source Mapping separation
✓ deprecated / replaced_by migration support

✓ Semantic Agent

✓ Structured Mapping Result

✓ Provenance

✓ Confidence

✓ UNMAPPED handling

✓ Unit normalization

✓ Canonical Data Model

✓ Schema validation

✓ Ontology validation

✓ Domain validation

✓ Eclipse demo export

✓ CMG demo export

✓ Cross-source consistency test
```

最终 Demo 必须能够证明：

```text
Client A Data ─┐
Client B Data ─┼──→ Company Ontology
Client C Data ─┘          │
                          ▼
                  Canonical Data Model
                          │
                 ┌────────┴────────┐
                 ▼                 ▼
              Eclipse             CMG
```

---

# 46. 最重要的 Architecture Rules

Codex 在开发过程中必须遵守以下规则：

**Rule 1**

```text
Ontology ≠ Canonical Schema
```

Ontology 描述“这个东西是什么”。

Canonical Schema 描述“公司内部如何存储”。

---

**Rule 2**

```text
Canonical Model ≠ Eclipse Model
Canonical Model ≠ CMG Model
Canonical Model ≠ Petrel Model
```

Canonical 必须保持平台无关。

---

**Rule 3**

```text
LLM:
Source → Semantic Mapping

Deterministic Code:
Semantic Mapping → Canonical → Target
```

---

**Rule 4**

任何无法确定的数据：

```text
UNMAPPED
AMBIGUOUS
MISSING
REVIEW_REQUIRED
```

禁止 hallucination。

---

**Rule 5**

所有关键数据必须：

```text
Traceable
```

必须能够回答：

```text
这个值来自哪个文件？
哪个 block？
原始文本是什么？
映射到了哪个 Ontology Concept？
为什么进入这个 Canonical Field？
最后被哪个 Mapper 转换成什么？
```

---

**Rule 6**

新增客户不应该修改 Canonical Schema 或 Canonical Ontology，除非真实数据暴露出了
真正缺失且换一个客户/模拟器后仍然成立的领域 Concept。

新增客户原则上只影响：

```text
Source Mapping Knowledge
Parser
```

只有新发现的表达是通用领域同义词时，才能进入 Ontology Alias；客户列名本身
必须留在 Source Mapping。

---

**Rule 7**

新增 Simulator 不应该修改 Source Parser 或 Semantic Agent。

原则上只增加：

```text
New Platform Mapper
+
Export Validator
```

---

**Rule 8**

```text
Alias ≠ Value ≠ Example
```

`quarterly`、`500 m3/day`、具体日期或井名不得作为 Concept alias。

---

**Rule 9**

```text
Ontology Concept ≠ Canonical Instance
Ontology ≠ Source Mapping
```

Ontology 不存储业务实例；客户/平台专有名称不得污染 Canonical Concept。

---

**Rule 10**

```text
parent = taxonomy / containment
relationships = semantic relations
```

依赖、参考条件、应用对象、Role 和有效时间不得用 `parent` 代替。

---

**Rule 11**

PVT / SCAL 必须建模为 coordinate 与 dependent variables；物性必须在需要时使用
`referenced_at/reference_for` 显式表达 Reference Condition。

---

**Rule 12**

可随时间变化的 Producer / Injector / Shut-in 等分类长期建模为 Role，并与
`valid_during` 结合。已发布的 Concept ID 不得静默重命名/删除；使用 SemVer、
`deprecated` 和 `replaced_by` 进行迁移。

---

# 47. 项目最终设计思想

系统要解决的核心问题不是：

> 如何利用 LLM 把一个文件转换成另一个文件。

而是：

> 如何把不同客户、不同系统、不同项目产生的异构油藏数据，映射到一个稳定、可追溯、平台无关的企业语义数据层。

因此系统真正需要长期积累的资产依次是：

```text
Company Ontology
        +
Canonical Data Model
        +
Source Mapping Knowledge
        +
Validation Rules
        +
Platform Mapping Knowledge
```

LLM/Agent 是利用这些资产处理长尾异构输入的工具，而不是系统本身。

最终目标：

```text
        Heterogeneous Data

 Client A    Client B    Client C
     \          |          /
      \         |         /
       Semantic Translation
                |
        Company Ontology
                |
                ▼
       Canonical Data Model
                |
        ┌───────┼───────┐
        ▼       ▼       ▼
     Eclipse   CMG    Petrel
```

将传统：

```text
N Sources × M Platforms
```

的大量 point-to-point integration，转化为：

```text
N Source Adapters
+
1 Canonical Semantic Layer
+
M Platform Mappers
```

这就是 v0.1 所需要验证的核心架构假设。
