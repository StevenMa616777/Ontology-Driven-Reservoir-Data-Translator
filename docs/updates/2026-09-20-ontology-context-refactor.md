# 2026-09-20 Ontology 上下文重构

本次按 [ONTOLOGY_REFRAC.md](../../ONTOLOGY_REFRAC.md) 将“概念定义”“任务上下文”
和“Canonical 存储规则”分开。指导方向合理，同时保留具有独立工程含义的专门概念，
并以显式上下文和确定性校验补足合并概念后容易产生的歧义。

## 当前组织

实读当前 Registry：Ontology **0.2.0，共 39 个概念、7 个 Scope、41 个 binding**。
独立 Canonical 层有 **34 条受支持的上下文映射规则**。Scope 列表为：

| Scope | 用途 | Binding 数 |
|---|---|---:|
| `fluid_properties` | 相态密度 | 3 |
| `pvt` | PVT 模型、压力坐标及物性 | 18 |
| `reference_condition` | 参考条件的压力、温度 | 2 |
| `relative_permeability` | 油水相渗表、坐标、相渗及毛管压力 | 5 |
| `rock` | 岩石压缩性与参考压力 | 2 |
| `schedule` | 模拟时长和报告间隔 | 2 |
| `well_controls` | 井、井类型、控制及约束 | 9 |

入口文件仍为 [`ontology/ontology_v0.1.yaml`](../../ontology/ontology_v0.1.yaml)，
保留旧文件名以兼容现有加载路径；其中版本字段已经是 0.2.0。Convention 和
Canonical Schema 的版本均保持 0.1.0。

核心词汇新增 `physical.*`，复用压力、黏度、密度、体积系数等定义；相态名词
`fluid.oil/water/gas` 保留为领域概念。旧相态 PVT 字段、密度字段和相渗叶子概念
不再逐个定义。`rock.compressibility` 继续表示孔隙体积压缩性，避免与流体压缩性
混为一谈；毛管压力及井约束也保留各自的工程含义。

概念定义位于 [`ontology/concepts/`](../../ontology/concepts/)，包含物理单位和
固有关系，不含 Canonical 路径。Scope 在
[`ontology/scopes.yaml`](../../ontology/scopes.yaml) 引用概念并声明上下文、
局部别名、适用相态和关系。`Bo/MUO/Krw` 这类带相态或用途的词汇随 binding 保存。

## 从资料到 Canonical

```text
RawBlock -> Scope Detection -> 概念及上下文候选 -> Semantic Mapping IR
         -> context / selector 校验 -> Canonical 路径解析 -> Builder
         -> L1-L3 -> Platform Mapper / L4 -> 导出
```

例如，模型识别一个气相 PVT 压力点时，语义核心为：

```json
{
  "ontology_concept": "physical.pressure",
  "context": {"scope": "pvt", "phase": "gas", "role": "coordinate"},
  "selectors": {"point_index": 0},
  "value": 100,
  "source_unit": "bar"
}
```

这是完整映射中与语义有关的片段；来源、置信度等字段仍按 API 合同提供。
[`canonical/mapping_contract.py`](../../src/reservoir_data_translator/canonical/mapping_contract.py)
独立解析出 `fluids.gas.pvt.points[0].pressure`。同一 Pressure 配合
`scope=rock, role=reference` 会解析到 `rock.reference_pressure`。
井号、表号和行号通过 `selectors` 传递，不能参与定义新概念。

Provider Prompt 提供受控概念、context、所需选择器及值合同，路径由代码解析。
输入若带有旧式 `canonical_path`，仍须与解析结果严格一致。Builder 再次验证
Scope、单位、选择器、路径及表格元数据，防止绕过语义 Agent 后错误组装。

## 检索和判别的连带调整

- Retriever 在检测到的 Scope 内检索，结合局部别名与相态证据构造候选；候选键
  改为 `concept_id + context`，结构父映射也按相同 Scope 和相态补齐。
- 相同概念的多个上下文可以构成 `AMBIGUOUS`，通过 `candidate_contexts` 保留。
  缺少足够证据时不得从路径或其他块的内容猜测相态。
- Source Mapping 和平台映射支持显式上下文；L2 按概念及上下文检查约束与关系。
- 工作台映射结果及 Ontology Explorer 展示真实 Scope/context，使合并后的语义
  仍可检查，而不会只显示一个失去用途信息的通用 Concept ID。
- `UNMAPPED`、`AMBIGUOUS` 和低置信度审查门继续有效；概念合并不降低审查要求。

## 兼容与支持边界

旧 ID 通过明确的单向兼容表迁移。例如 `fluid.gas.pvt.pressure` 转为
`physical.pressure + pvt/gas/coordinate`。旧记录可以从经过对应规则校验的路径
取回分组选择器；新通用概念必须显式给出 context 和所需 selectors。
矛盾的相态、用途、路径、额外选择器及不合法选择器都会拒绝。兼容层不把旧节点
加回核心 Ontology，也不会对未声明的旧 ID 猜测含义。

语义范围和实现范围分别维护：

- PVT Scope 可表达油、水、气压缩系数和黏压系数，但本次保留原转换支持范围：
  这两个字段的 Canonical 解析规则仍只开放水相。油、气对应组合不会因为 Scope
  合法就自动变为可导出的 `MAPPED`。
- `condition.reference.pressure/temperature` 可迁移到 `reference_condition`
  下的通用概念，但当前没有对应 Canonical 存储规则；不能借用岩石参考压力字段。
- 直接输入 Canonical 若含油、气压缩系数或黏压系数，Eclipse 导出校验明确返回
  `ECLIPSE_PVT_PROPERTY_UNSUPPORTED`，避免 PVDO/PVDG 静默丢弃数据。
- 本次未扩展真实模拟器执行、商业平台认证、生产审批持久化或语义 Gold 数据集。

## 验证范围

最终验收：

- `.venv/bin/python -m pytest -o addopts='' -q`：**289 passed**。
- `.venv/bin/ontology-validate ontology`：**0 errors、0 warnings**；2 条既有井层级例外 INFO。
- `compileall`、`git diff --check` 和 `pip check` 通过。
- 内置浏览器验证作用域筛选、按语义角色排列的关系图、context 详情及工作台导航；
  提交 `XYZ_COEFF = 12.5` 后停在 `review_required`，Canonical 与 Target 未生成，
  浏览器控制台无 warning/error。
- 构建并重新安装最终 wheel；从 `/tmp` 用普通 Python 导入，确认代码来自 `.venv`
  的 `site-packages`；7 个 UI 资源逐字节与源码一致，安装包 API 和 UNMAPPED 门禁通过。
- 离线固定 Provider 的新 context/selector IR 贯穿 `/translate`、Builder、L1–L4，
  与旧映射记录产生相同 Eclipse demo 内容。

回归包含混合 Scope、不能跨 Scope 借用相态、相态缺失时拒绝模型猜测、通用相渗表
不能默认油水体系、同概念歧义、旧 ID 迁移、选择器和上下文冲突、表 A 元数据不能
满足表 B、全部支持规则往返解析、L2/L4 及工作台/API。

本次没有调用真实付费 Provider，也没有运行 Flow 或商业模拟器。确定性测试和离线
接口验证不能替代这些外部链路的验收。本次已同步安装包入口；后续代码更新仍应独立验证普通导入，
不能仅依赖配置了 `pythonpath=src` 的 pytest。

组织规则详见 [ONTOLOGY_CONVENTIONS.md](../../ONTOLOGY_CONVENTIONS.md)。
原始指导文件保持不变。
