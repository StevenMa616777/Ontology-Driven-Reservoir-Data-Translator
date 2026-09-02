# 项目总览与当前运行逻辑

## 1. 项目要做什么

本项目要验证的是：不同客户、不同格式、不同命名体系的油藏资料，能否经过一套
可审查、可追溯、可重复的流程，转换成统一的油藏数模语义，再稳定生成目标模拟器
输入片段。

它不是“让大模型直接写 ECLIPSE 文件”。真正的目标是建立一个中间语义层，让资料
理解和目标平台语法解耦：

```text
异构源资料 → 统一语义 → Canonical Model → 确定性目标文件
```

这条路线解决三个核心问题：

1. **来源差异**：同一个业务字段在客户 A/B/C 中可能有不同名称、单位和文件结构。
2. **模型不确定性**：LLM 适合理解语义，但不适合作为工程规则和最终文件的唯一责任方。
3. **平台耦合**：Eclipse、CMG 等平台格式会变化，内部业务模型不应跟随某个平台 Schema 摆动。

## 2. 我们怎么做

系统把职责拆成七个可独立验证的层：

| 层 | 负责什么 | 不负责什么 |
|---|---|---|
| Ingestion | 识别 TXT/JSON/CSV/XLSX 结构和来源位置 | 不判断油藏工程语义 |
| Ontology + Source Mapping | 定义公司语义和客户词汇到 Concept 的候选 | 不存客户数据值，不生成平台 Keyword |
| Semantic Agent | 在受控候选中理解原文，输出结构化映射 | 不自由创造 Concept/Path/单位，不生成目标文件 |
| Canonical Builder | 单位归一、实体归组、构建平台无关模型 | 不猜测缺失值，不处理平台语法 |
| Validation | 验证结构、Ontology 关系、领域规则和可导出性 | 不自动修复歧义或硬错误 |
| Platform Mapper | Canonical → 中间记录 → 目标文本 | 不调用 LLM，不改变 Canonical |
| API + UI | 暴露分阶段流程、审查门、结果和 trace | 当前不提供持久化审批/回放和生产认证 |

贯穿所有层的原则是：

- **Alias ≠ Value ≠ Example**：术语、业务值和示例数据分开管理。
- **Ontology ≠ Source Mapping ≠ Platform Mapping**：公司语义、客户叫法和平台语法分层。
- **Evidence 优先于 confidence**：置信度不能替代来源位置、原文和规则校验。
- **Fail closed**：无法确定时停止，不让下游继续生成看似合法的文件。
- **Deterministic downstream**：从 Canonical 开始，单位、校验和渲染必须可重复。

## 3. 系统启动时发生什么

默认 FastAPI 应用启动时会：

1. 从 `RESERVOIR_ONTOLOGY_PATH`、当前目录或项目目录定位 `ontology/`。
2. 加载 Ontology manifest、Convention 和 domain concept YAML，并检查重复 ID、
   悬空/循环关系、别名冲突、受控单位和 canonical path。
3. 从 `RESERVOIR_MAPPING_PATH` 或 `mappings/` 加载：
   - `customer_*.yaml` Source Mapping；
   - `eclipse.yaml` 和 `cmg.yaml` Platform Output Mapping。
4. 按 `RESERVOIR_SEMANTIC_PROVIDER` 配置 Semantic Provider。当前内置 DeepSeek；
   没有凭据时只关闭语义 endpoint，不影响确定性 endpoint。
5. 注册六个 API endpoint，并把本地工作台挂载到 `/` 和 `/ui/*`。

所有业务状态都在进程/请求内；当前没有数据库、队列或对象存储。

## 4. 一次完整转换如何运行

### 4.1 Ingestion

`POST /translate` 首先接收 `SourceInput`。TXT/JSON/CSV 使用 UTF-8 文本，XLSX
使用 base64。Parser 生成 `RawDocument` 和若干 `RawBlock`，每个 block 保留
`source_id`、文件名、block ID、位置和原始内容。

这一阶段只回答“资料长什么样”，不回答“它是什么意思”。

### 4.2 Ontology Retrieval 与 Semantic Mapping

Retriever 使用三类确定性信息生成候选：

1. Ontology alias；
2. 受控关键词；
3. 选定客户的 Source Mapping。

Semantic Agent 把一个 block、来源元数据、候选 Concept、允许的 Canonical Path
模板、Canonical Unit 和结构值合同交给 Provider。DeepSeek 必须返回符合 Pydantic
Schema 的结构化结果。

返回后，系统再次检查：

- Concept 是否来自提供的候选；
- Concept 与 Canonical Path 是否属于同一合同；
- 单位是否符合 Ontology；
- PVT/SCAL 等结构父项是否存在；
- Canonical Path 是否重复；
- 井类型和控制方式的 Ontology relationship 是否相容；
- Provenance 是否仍指向 Parser 提供的源 block。

Provider 输出截断、JSON/Schema 错误或合同错误只做有界重试；超过次数后以结构化
错误结束，不无限重试。

### 4.3 Review Gate

每个结果只能是：

- `MAPPED`：有明确 Concept、Path、值、单位和 provenance；
- `UNMAPPED`：没有合法候选；
- `AMBIGUOUS`：多个候选仍无法确定。

`UNMAPPED`、`AMBIGUOUS`、空映射或 `confidence < 0.80` 会让 `/translate`
返回 `review_required`，不会构建 Canonical。浏览器工作台允许审查人对当前会话中
低置信度但已 `MAPPED` 的项逐项确认，然后调用分阶段 endpoint 继续；未映射或歧义项
不能被强制放行。

### 4.4 Canonical Build

`CanonicalBuilder` 按外部合同把 Semantic Mapping 写入 v0.1 Canonical Model：

- Rock；
- oil/water/gas Fluid 与 PVT points；
- SCAL relative-permeability tables；
- Wells、controls 和 constraints；
- Schedule duration/report interval。

Builder 使用 Pint 和明确的 30-day month、91.25-day quarter、365-day year 政策做
单位换算；集合按稳定键排序，以保证同一输入得到可重复 JSON。

### 4.5 L1-L4 Validation

- **L1 Schema**：类型、必填字段、枚举、有限数值和 unknown field。
- **L2 Ontology Instance**：Concept relationship、Canonical Unit 和适用对象。
- **L3 Domain**：物理边界、表格关系和经验趋势；硬错误与 warning 分开。
- **L4 Platform Export**：目标 Mapper 是否能在当前数据和目标约束下安全导出。

L1-L3 无效时不进入目标导出；没有配置目标 Validator 时明确返回错误，不假装可导出。

### 4.6 Platform Mapping 与输出

Eclipse 和 CMG Mapper 读取同一个 Canonical Model，先构造可检查的中间记录，再渲染
文本。整个阶段不调用 LLM，也不修改 Canonical。

Eclipse PoC 生成 METRIC INCLUDE，覆盖 `SWOF`、`PVDO`、`PVDG`、`PVTW`、
`DENSITY`、`ROCK`、`WCONPROD`、`WCONINJE` 和 `TSTEP`。井控片段仍依赖
真实 host deck 的 `WELSPECS/COMPDAT` 上下文。

CMG Mapper 只用于证明平台扩展边界成立，目前是未冻结产品版本的 IMEX-style 井控
片段，不作为可运行 CMG 文件声明。

## 5. 两种使用方式

### 完整流水线

`POST /translate` 适合 Demo 和应用集成。它一次返回：

- `translation_id`；
- 解析后的 source；
- semantic mapping；
- review/validation 状态；
- Canonical model；
- export validation；
- target artifact；
- 按阶段排列的 trace。

### 分阶段接口

`/ingest`、`/semantic-map`、`/canonical/build`、`/validate`、`/export/{platform}`
适合调试、审查和外部编排。浏览器工作台也用这组接口完成低置信度批准后的继续执行。

## 6. 当前代码地图

| 路径 | 所有权 |
|---|---|
| `ontology/` | 平台/客户无关的 v0.1 语义与 Convention |
| `mappings/customer_*.yaml` | 客户词汇和 Source Mapping |
| `mappings/eclipse.yaml`, `cmg.yaml` | Canonical Concept 到平台 Keyword 的映射 |
| `src/.../ingestion/` | 文件解析和 RawDocument |
| `src/.../semantic/` | 检索、Provider、结构化语义合同和安全门 |
| `src/.../canonical/` | Pydantic Canonical Model、Schema 和 Builder |
| `src/.../validation/` | L1-L4、遍历工具和 OPM Parser 对比 |
| `src/.../mappers/` | 确定性 Eclipse/CMG 输出 |
| `src/.../api/` | 服务编排和 HTTP 合同 |
| `src/.../ui/` | 无构建步骤的本地浏览器工作台 |
| `example/` | 冻结 Demo 源资料和输出 Golden |
| `scripts/` | 真实 Provider smoke 和验收链 |
| `tests/` | 单元、契约、跨源、API、UI、OPM 回归测试 |

## 7. PoC 结论和下一阶段

当前代码和证据足以说明：受控 LLM 语义理解、Ontology/Canonical 中间层、确定性工程
规则和平台输出可以组成一条完整 Demo 链路。

下一阶段的优先级不应是无边界增加功能，而是补强真实业务证据：

1. 建立人工标注的 Semantic Gold，计算字段、数值、单位和幻觉指标并做重复运行。
2. 把 INCLUDE 放进真实 host deck，运行固定版本 OPM Flow/商业模拟器短步测试。
3. 冻结一个 CMG 产品/版本后验证语法，或明确将其移出下一里程碑。
4. 持久化 review、artifact bundle、审批身份和 replay。
5. 在这些证据成立后，再评估 PDF/DOCX/OCR、认证、队列、存储和生产部署。

详细验收证据和不能宣称的范围见 [PoC 收口报告](POC_CLOSURE.md)。
