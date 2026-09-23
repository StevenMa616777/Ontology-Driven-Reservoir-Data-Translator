# Reservoir Simulation Ontology 组织约定

本文对应 2026-09-20 的 Ontology 0.2.0。受控词汇文件仍使用 Convention 0.1.0；
Canonical Model 的 Schema 版本仍为 0.1.0。这三种版本分别管理。

## 1. 职责边界

| 层 | 定义什么 | 实现位置 |
|---|---|---|
| Ontology | 可复用的工程概念、维度、单位、约束和固有关系 | `ontology/concepts/` |
| Scope | 概念在任务中的用途、相态、角色、局部别名和关系 | `ontology/scopes.yaml` |
| Semantic IR | 当前来源表达的概念、上下文、值和分组标识 | `semantic/models.py` |
| Canonical 规则 | 受支持的语义组合对应哪个存储位置 | `canonical/mapping_contract.py` |
| Platform Mapping | Canonical 内容如何生成目标平台表示 | `mappings/`、`mappers/` |

```text
Source -> Scope Detection -> Contextual Retrieval -> Semantic Mapping IR
       -> Deterministic Canonical Resolution -> Canonical Model -> Adapter
```

核心概念不包含 `canonical_path` 或路径模板。客户井号、日期、测量值、表格行号及
具体限制属于实例数据。`canonical_unit` 是统一物理单位，不是存储路径。

## 2. 一个语义概念定义一次

`concept_id` 使用全局唯一的小写命名空间。它不能包含客户、项目、数据库或模拟器名；
除根的直接子项和明确例外外，命名空间应与 `parent` 层级一致。

`parent` 表达概念分类或稳定的领域结构。不要因为一个量出现在油、气、水 PVT
表内，就分别创建三个 Pressure 或 Viscosity 定义。应复用 `physical.pressure`、
`physical.viscosity`，把相态及用途放到 Scope binding。

不能仅为压缩概念数量而合并不同的工程含义。例如 `rock.compressibility` 表达
孔隙体积压缩性，与流体压缩性语义不同，继续保留；`physical.capillary_pressure`
保留毛管压力含义，井底压力上下限也继续作为井约束概念。

每个概念仍包含以下必需字段：

```text
concept_id, name, parent, description, value_type, dimension,
canonical_unit, aliases, constraints, relationships
```

## 3. Scope 与上下文

Scope 引用概念 ID，不复制概念定义。一个 binding 的身份是
`(concept_id, SemanticContext)`。Context 字段为：

```text
scope, phase?, role?, phase_system?
```

允许的组合必须在 `ontology/scopes.yaml` 声明。例如：

```yaml
concept_id: physical.pressure
context:
  scope: pvt
  phase: gas
  role: coordinate
```

同一 Pressure 在 `scope: rock, role: reference` 下表示岩石参考压力。
相渗使用 `scope: relative_permeability, phase_system: oil_water`，再以 `phase`
区分油、水相对渗透率。不得用 Canonical 路径倒推出缺失的语义上下文。

语义范围可以比当前可构建、可导出范围宽。某组合通过 Scope 校验，只说明语义合法，
并不意味着已有 Canonical 或目标平台支持。

## 4. 别名与来源映射

核心别名只能表达概念本身。相态或任务相关的别名放在 Scope binding：例如 `Bo`、
`MUO`、`Krw` 分别携带油相体积系数、油相黏度、水相相渗上下文。

别名不能是具体值、数字示例、单位、时间取值或仅相关的概念。Registry 会归一化
大小写及分隔符，避免重复变体。客户字段和平台限定名称放入独立 Source Mapping，
以 `concept_id + context` 指向已声明的语义组合。

检索先识别 Scope，并使用局部别名和相态证据选择候选。缺少相态或用途证据时，应
保留不同上下文候选，不能因为 Concept ID 相同就合并。`AMBIGUOUS` 的
`candidate_contexts` 可以表达同一概念在多个上下文之间的歧义。

## 5. 关系、表格和参考条件

跨上下文成立的固有关系放在核心概念；只在某个 Scope 或相态下成立的关系放在
Scope binding。例如：

```text
PVT: Pressure @ gas/coordinate --coordinate_for--> PVT @ gas/model
     Viscosity @ gas/property --dependent_on--> Pressure @ gas/coordinate
SCAL: Water Saturation --coordinate_for--> Relative Permeability Table
      Relative Permeability / Capillary Pressure --dependent_on--> Water Saturation
```

每个表格的具体 binding 必须有兼容上下文的坐标和因变量。`dependent_on` 可指向
多个概念。参考条件使用 `referenced_at` 与反向 `reference_for`；校验同时检查
关系端点类型、作用域兼容性和需要的逆关系。

机器可读受控词汇见
[`ontology/conventions_v0.1.yaml`](ontology/conventions_v0.1.yaml)，包括值类型、
维度及单位、约束键、关系名称与基数，以及来源污染检测规则。

## 6. 确定性路径解析

模型返回概念、完整上下文、值和 `selectors`。井号、表号及行号使用
`well_id`、`table_id`、`point_index` 等选择器；它们是实例分组信息。
Canonical 规则按概念和上下文精确查找，验证选择器后产生路径。

若输入同时携带 `canonical_path`，它必须与解析结果一致。缺少上下文、未声明的
组合、额外或不合法的选择器、相态冲突均应拒绝。Builder 再次校验语义、单位、
分组和路径，不能以模型曾经输出 `MAPPED` 代替检查。

## 7. 实体、角色与时间

`well` 是实体。生产井、注入井等可能随时间变化；当前保留 `well.producer`、
`well.*_injector` 的 MVP 表示。未来若实现随时间变化的角色，应采用
`well --has_role--> role --valid_during--> interval`，不能把这一后续设计当作
当前 Schedule 已支持的能力。

## 8. 校验和版本迁移

- `ERROR`：加载或构建失败。
- `WARNING`：保留为可审查的建模提示；不等于已完成业务审批。
- `INFO`：非阻塞的优化或迁移说明。

Registry 加载时检查核心概念与 Scope 定义。`ontology-validate` 提供同样的
定义校验；实例约束、领域规则和目标可导出性仍分别由 L2、L3、L4 负责。

当前仍是 0.x PoC，0.2.0 合并了旧概念 ID，属于明确记录的接口迁移。
旧 `fluid.*.pvt.*` 等 ID 仅通过独立、显式的单向兼容表进入新语义，不再恢复成
核心概念节点。旧 ID 携带矛盾上下文时必须拒绝，未列入兼容表的 ID 不自动推断。
通用新概念必须提供 context；少数未迁移、语义唯一的旧概念保留明确的兼容默认值。

移除或重命名已发布 ID 必须同时提供迁移策略和回归测试。进入稳定版本后，破坏性
变更应提升主版本；别名或描述修正使用补丁版本，新增兼容概念或关系使用次版本。
生命周期模型支持 `deprecated/replaced_by`，但单个 successor ID 不能完整表达
本次“旧 ID → 新 ID + context”的迁移，因此不能替代独立兼容表。

## 9. 新增概念前的检查

确认新项表达了新的工程含义，而非已有概念乘以相态、表类型或存储位置。先检查
能否复用概念并增加 Scope binding；再补充单位、关系、局部别名及必要约束。
只有需要新 Canonical 支持时才新增路径规则，并同步实例校验与平台导出边界。

本次变更与示例见
[2026-09-20 Ontology 上下文重构记录](docs/updates/2026-09-20-ontology-context-refactor.md)。
