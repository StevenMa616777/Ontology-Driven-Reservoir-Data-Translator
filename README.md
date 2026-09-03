# Ontology-Driven Reservoir Data Translator

面向油藏数值模拟资料的语义转换 PoC：把异构的 TXT、JSON、CSV、XLSX
资料转换为统一的 Canonical Data Model，再由确定性程序生成 Eclipse/OPM
INCLUDE 或 CMG Demo 片段。

> **PoC v0.1 已于 2026-09-02 完成阶段性收口。** 当前结论是“技术路线和
> Demo 闭环成立”，不是“已经达到生产环境或商业模拟器认证标准”。详细证据和
> 边界见 [PoC 收口报告](docs/POC_CLOSURE.md)。

## 我们想解决什么

油藏工程资料通常来自不同客户、文件格式和字段体系。人工转换既要理解自然语言和
表格语义，又要处理单位、物理约束和模拟器 Keyword 语法，过程重复且难以审计。

本项目验证一条可追溯的转换路线：

- LLM 只负责非确定性的“这段资料表达了什么”；
- Company Ontology 定义统一的业务语义；
- Canonical Model 是平台无关的唯一内部表达；
- 单位换算、模型构建、校验和目标文件生成全部由确定性代码负责；
- 缺失、歧义或低置信度信息必须停在人工审查门，不允许模型猜测补全。

## 系统如何工作

```mermaid
flowchart LR
    A[TXT / JSON / CSV / XLSX] --> B[Ingestion<br/>RawDocument]
    B --> C[Ontology Retrieval]
    C --> D[LLM Semantic Mapping]
    D --> E{Review Gate}
    E -->|未映射 / 歧义 / 低置信度| F[Human Review]
    E -->|通过| G[Canonical Builder]
    F -->|批准可接受的低置信度项| G
    G --> H[L1-L3 Validation]
    H --> I[Platform Mapper]
    I --> J[L4 Export Validation]
    J --> K[Eclipse / CMG Artifact]
```

一次 `/translate` 请求按以下顺序运行：

1. Parser 只解析文件结构，生成带来源位置的 `RawDocument/RawBlock`。
2. Retriever 从 Ontology 和外部 Source Mapping 中提供受控候选。
3. DeepSeek 返回结构化 `MAPPED/UNMAPPED/AMBIGUOUS` 结果；系统重新校验
   Concept、Canonical Path、单位、结构值和实体关系。
4. Review Gate 阻断未解决项和置信度低于 0.80 的结果。
5. `CanonicalBuilder` 做单位归一和平台无关模型构建，不创造缺失值。
6. L1-L3 分别验证 Schema、Ontology 实例约束和领域规则。
7. Eclipse/CMG Mapper 生成确定性目标中间模型与文本，L4 验证目标可导出性。
8. 响应返回 translation ID、各阶段 trace、Canonical、Validation 和目标片段。

完整的组件职责、启动逻辑和异常分支见
[项目总览与运行逻辑](docs/PROJECT_OVERVIEW.md)。

## 当前 PoC 能力

| 能力 | 当前状态 |
|---|---|
| 输入 | TXT、JSON、CSV、XLSX；保留 block 级来源位置 |
| 语义层 | 外部 YAML Ontology、Source Mapping、受控检索、DeepSeek V4 Flash |
| Canonical | Rock、Fluid/PVT、SCAL、Well/Control、Schedule |
| 单位 | 压力、速率、黏度、密度、时间、压缩系数的受控换算 |
| 安全门 | UNMAPPED、AMBIGUOUS、低置信度阻断；浏览器会话内人工批准 |
| 校验 | L1 Schema、L2 Ontology、L3 Domain、L4 Platform Export |
| Eclipse | `SWOF/PVDO/PVDG/PVTW/DENSITY/ROCK/WCONPROD/WCONINJE/TSTEP` |
| CMG | 未冻结版本的 IMEX-style 井控 Demo 片段 |
| 接口 | 六个分阶段 FastAPI endpoint 和完整 `/translate` 流水线 |
| UI | 本地 Upload/Paste → Mapping → Review → Canonical → Export 工作台 |

## 快速开始

要求 Python 3.12+。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,opm]'
.venv/bin/ontology-validate ontology
.venv/bin/python -m pytest
.venv/bin/uvicorn reservoir_data_translator.api.main:app --reload
```

浏览器打开 `http://127.0.0.1:8000/`。在没有 Provider 凭据时，确定性 endpoint
仍可使用，语义转换 endpoint 会明确返回 `SEMANTIC_PROVIDER_NOT_CONFIGURED`。

真实转换会把逐次 DeepSeek 请求、Token、Prompt 和响应审计写入被 Git 忽略的
`artifacts/deepseek_traces/`，并可在结果页按按钮展开。该目录包含客户原始内容，
不可提交或直接分享；可用 `DEEPSEEK_TRACE_DIR` 改到项目外的受控目录。

DeepSeek 凭据建议通过环境变量提供：

```bash
export DEEPSEEK_API_KEY='your-key'
export DEEPSEEK_MODEL='deepseek-v4-flash'
.venv/bin/uvicorn reservoir_data_translator.api.main:app --reload
```

项目也兼容 `DEEPSEEK_API_KEY_FILE`；凭据文件、运行 artifact、Prompt 和模型原始
响应都不应提交到 Git。更完整的启动、验证和故障排查命令见
[运行手册](docs/RUNBOOK.md)。

## PoC 验收命令

不调用外部模型的本地门禁：

```bash
.venv/bin/python -m pytest
.venv/bin/ontology-validate ontology --json
.venv/bin/python -m pip check
```

在明确配置 DeepSeek 凭据后，完整 Demo 验收链为：

```bash
.venv/bin/python scripts/evaluate_demo_deepseek.py
```

该脚本执行：Demo 原文 → DeepSeek → Semantic Mapping → Canonical → L1-L4 →
Eclipse INCLUDE → OPM 2025.10 Parser → Golden 语义比较，并把不含密钥/Prompt 的
本地证据写入 `artifacts/demo_deepseek_evaluation/`。

## 仓库结构

```text
.
├── src/reservoir_data_translator/
│   ├── ingestion/      # 文件结构解析
│   ├── ontology/       # Ontology 加载、Registry、定义校验
│   ├── semantic/       # 检索、Source Mapping、Provider、语义安全门
│   ├── canonical/      # 平台无关模型与确定性 Builder
│   ├── validation/     # L1-L4 与 OPM Parser 对比
│   ├── mappers/        # Eclipse / CMG 确定性 Mapper
│   ├── api/            # FastAPI 分阶段接口与完整流水线
│   └── ui/             # 本地浏览器工作台
├── ontology/           # 平台/客户无关的 v0.1 Ontology
├── mappings/           # 客户 Source Mapping 与平台 Output Mapping
├── example/            # Demo 原文与 Eclipse 输出 Golden
├── scripts/            # 真实 Provider smoke / 完整验收脚本
├── tests/              # 自动化回归测试
└── docs/               # 项目说明、收口报告与运行手册
```

## 文档导航

- [项目总览与运行逻辑](docs/PROJECT_OVERVIEW.md)：我们想做什么、怎么做、系统现在如何运行。
- [PoC 收口报告](docs/POC_CLOSURE.md)：冻结范围、当前证据、完成结论和遗留边界。
- [运行手册](docs/RUNBOOK.md)：安装、启动、API、测试、真实模型验收和安全约束。
- [开发设计文档](DESIGN.md)：v0.1/v0.2 的详细设计合同与 Tasks 1-12。
- [设计完成度审计](DESIGN_COMPLETENESS.md)：逐 Task、逐层完成度矩阵。
- [Ontology 约定](ONTOLOGY_CONVENTIONS.md)：概念、别名、关系、单位和版本规则。
- [原始 PoC 企划](docs/archive/ORIGINAL_POC_BRIEF_ZH.md)：最初的业务目标与成功标准，作为历史基线保留。

## 明确边界

- Eclipse 输出通过固定版本 OPM Python Parser 和输出 Golden 比较，但尚未在真实
  host deck 中运行 Flow，也未经过商业 ECLIPSE 认证。
- 当前没有带人工 Concept/Path 标签的代表性 Semantic Gold 数据集，因此不宣称
  extraction precision、recall 或 F1。
- Review 批准和 trace 仅在本次请求/浏览器会话内存在，没有持久化审批、回放或审计库。
- 没有认证授权、任务队列、对象存储、多租户、生产监控和部署加固。
- CMG 只证明同一 Canonical 可驱动第二个平台 Mapper，不代表 CMG 语法已验证。

这些限制不会否定 PoC 结论，但它们决定了下一阶段应优先补“业务证据和真实消费”，
而不是继续无边界扩展架构。
