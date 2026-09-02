# PoC v0.1 收口报告

**收口日期：2026-09-02**

## 结论

PoC v0.1 可以在当前冻结范围内收口。

已经证明的结论是：综合油藏 Demo 原文可以经过 DeepSeek V4 Flash 的受控语义映射，
构建平台无关 Canonical Model，通过 L1-L4 校验，生成确定性的 Eclipse/OPM INCLUDE，
并通过 OPM Python Parser 2025.10 解析和输出 Golden 语义比较。

该结论只代表**技术路线和本仓库 Demo 闭环成立**。它不代表生产就绪，也不代表输出已
通过真实 OPM Flow host deck 或商业 ECLIPSE/CMG 软件认证。

## 冻结的验收范围

| 项目 | v0.1 口径 |
|---|---|
| 综合源资料 | `example/demo_material_raw.txt`，3 个 source blocks |
| Semantic Provider | DeepSeek Responses API，`deepseek-v4-flash` |
| 中间语义 | Ontology v0.1 + `SemanticMapping` + Canonical v0.1 |
| 工程域 | Rock、oil/water/gas PVT、SCAL、Well Control、Schedule |
| 主验收目标 | METRIC Eclipse/OPM INCLUDE |
| 输出 Keywords | SWOF、PVDO、PVDG、PVTW、DENSITY、ROCK、WCONPROD、WCONINJE、TSTEP |
| 输出基准 | `example/demo_material_eclipse.inc` |
| 公开兼容性代理 | OPM Python Parser 2025.10 |
| 第二平台 | CMG IMEX-style Demo 井控片段，仅验证架构扩展性 |

TXT/JSON/CSV/XLSX ingestion 和三客户 Source Mapping 属于工程回归范围；真实 Provider
综合验收以冻结的 TXT Demo 为准。PDF、DOCX、OCR、完整 ECLIPSE deck 和商业模拟器
执行不在本次收口范围内。

## 当前验收证据

### 本地确定性门禁

2026-09-02 在项目 `.venv` 中重新执行：

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest` | `129 passed` |
| `.venv/bin/ontology-validate ontology --json` | 0 error，0 warning，2 个已记录 hierarchy info |
| `.venv/bin/python -m pip check` | `No broken requirements found` |
| OPM Golden self-parse/compare | Parser 2025.10，9 个目标 Keyword，`semantic_equal=true` |

自动测试覆盖 Ontology、Canonical Schema/Builder、单位换算、ingestion、retrieval、
Semantic Agent 合同与重试、DeepSeek Provider mock、三源等价、Eclipse/CMG Mapper、
FastAPI、浏览器工作台和 OPM Parser 对比。

### 真实 DeepSeek 综合 Demo

2026-09-02 的本地验收 artifact 记录：

| 指标 | 结果 |
|---|---|
| Provider / response model | `deepseek-v4-flash` / `deepseek-v4-flash` |
| 调用次数 | 4 |
| Semantic mappings | 71 |
| unresolved | 0 |
| review required | false |
| Canonical validation | valid |
| Export validation | valid |
| Golden 和 generated OPM parse | 均通过 |
| Parser-normalized comparison | `semantic_equal=true` |
| 安全记录 | 不记录 credential，不记录 Prompt |

真实调用产生的 mapping、Canonical、validation、generated INCLUDE、trace 和 summary 默认
写入被 Git 忽略的 `artifacts/demo_deepseek_evaluation/`。仓库只提交复现脚本、冻结输入、
输出 Golden 和本报告，不提交凭据或可能包含客户资料的运行 artifact。

## 本次收口包含的系统能力

- 外部、版本化、平台无关的 Company Ontology 和自动定义校验。
- 客户 Source Mapping 与平台 Output Mapping 分离。
- TXT/JSON/CSV/XLSX 的 format-neutral ingestion。
- 受控候选检索和 Provider-neutral Semantic Model 合同。
- DeepSeek V4 Flash 接入、有界网络/输出重试和 secret-safe trace。
- Concept/Path/Unit/结构父项/实体关系的后置合同校验。
- 低置信度、未映射和歧义结果的 fail-closed review gate。
- 确定性单位换算、Canonical Builder 和稳定集合排序。
- L1-L4 Validation。
- Eclipse/OPM Demo Mapper、PVTW、Golden 对比和固定版本 OPM Parser 验证。
- CMG Demo Mapper。
- 六个 FastAPI endpoint、完整 `/translate` 和本地浏览器工作台。

逐 Task 和逐层状态见 [`DESIGN_COMPLETENESS.md`](../DESIGN_COMPLETENESS.md)。

## 尚不能宣称什么

### 没有 Semantic Gold 准确率

`demo_material_eclipse.inc` 是**输出 Golden**，不是逐字段标注的 Semantic Gold。
目前没有人工 Concept/Path/Value/Unit 标签，因此不能把“最终输出相等”替代 extraction
precision、recall、F1 或 hallucination rate。

### 没有真实模拟器运行证据

OPM Python Parser 能证明 Keyword 可解析并支持标准化比较，但不会替代：

- 带 `WELSPECS/COMPDAT` 的真实 host deck；
- 固定版本 OPM Flow 的短 timestep run；
- 商业 ECLIPSE 版本兼容性确认；
- CMG 产品/版本语法验证。

### 没有生产工作流

当前 review、trace 和审批不持久化；也没有用户身份、权限、对象存储、任务队列、重放、
运行监控、多租户和部署加固。这些是产品化工作，不是本轮 PoC 技术路线的阻塞项。

## 下一里程碑建议

建议把下一阶段定义为“真实材料与真实消费验证”，按以下顺序推进：

1. 冻结代表性源资料并建立人工 Semantic Gold。
2. 对同一资料至少重复运行三次真实模型，统计字段/数值/单位/幻觉/稳定性指标。
3. 冻结 OPM Flow 版本和最小真实 host deck，执行 parser + short-step run。
4. 把 source hash、provider/model、mapping、Canonical、validation、target、trace 和审批
   组装成可持久化 Artifact Bundle。
5. 再决定是否扩展 PDF/DOCX/OCR、更多 Provider、CMG 全域 Mapper 和生产基础设施。

## 收口判定

- **PoC v0.1：完成并收口。**
- **真实工程试点：尚未完成。** 需要 Semantic Gold 和 simulator run。
- **生产可用：尚未开始验收。** 需要持久化、安全、运维和规模化能力。
