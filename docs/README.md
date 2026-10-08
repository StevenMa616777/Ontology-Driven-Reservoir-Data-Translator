# 项目文档索引

文档按“入口说明、设计合同、验收证据、历史基线”分层，避免把当前事实、未来设想和
最初企划混在一起。

## 当前入口

- [`README.md`](../README.md)：仓库首页、快速开始、能力边界和文档导航。
- [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md)：项目目标、实现路线、组件职责和当前运行逻辑。
- [`RUNBOOK.md`](RUNBOOK.md)：开发、启动、测试、真实模型验收和故障排查。

## 设计合同

- [`DESIGN.md`](../DESIGN.md)：Ontology-Driven 架构和 Tasks 1-12 的详细设计基线。
- [`OCR_ENGINE_REQUIREMENTS_AND_IMPLEMENTATION_PLAN.md`](OCR_ENGINE_REQUIREMENTS_AND_IMPLEMENTATION_PLAN.md)：项目 OCR Engine、逻辑子表递归拆分、人工复核的总体需求与分阶段实施方案；接口、编排、复核和下游衔接已实现，逻辑拆表模型仍待验证。
- [`LOGICAL_TABLE_DECOMPOSITION_REQUIREMENTS_AND_MODEL_EVALUATION.md`](LOGICAL_TABLE_DECOMPOSITION_REQUIREMENTS_AND_MODEL_EVALUATION.md)：逻辑子表拆分的专项目标、数据契约、现成模型调研矩阵、评价指标和接入条件。
- [`ONTOLOGY_CONVENTIONS.md`](../ONTOLOGY_CONVENTIONS.md)：Ontology 受控词汇、关系、单位和版本约定。
- [`mappings/README.md`](../mappings/README.md)：Source Mapping 与 Platform Mapping 的边界。

## 状态与验收

- [`audits/2026-09-22-development-closure.md`](audits/2026-09-22-development-closure.md)：当前开发阶段收口结论、验收证据、真实回归汇总和下一阶段 P0 边界。
- [`audits/2026-09-21-deepseek-heterogeneous-regression.md`](audits/2026-09-21-deepseek-heterogeneous-regression.md)：真实付费回归、逐文件结果、后续合同修复与 317 项离线测试。
- [`updates/2026-09-21-heterogeneous-input-repair.md`](updates/2026-09-21-heterogeneous-input-repair.md)：异构测试集判断、配合修复、311 项离线验证和真实模型验收边界。
- [`audits/2026-09-21-heterogeneous-dataset-diagnosis.md`](audits/2026-09-21-heterogeneous-dataset-diagnosis.md)：修复前的逐文件诊断与历史响应证据。
- [`POC_CLOSURE.md`](POC_CLOSURE.md)：2026-09-02 PoC 收口结论、冻结范围和证据。
- [`DESIGN_COMPLETENESS.md`](../DESIGN_COMPLETENESS.md)：逐 Task、逐层实现完成度和生产边界。
- [`example/demo_material_raw.txt`](../example/demo_material_raw.txt)：冻结的综合 Demo 原文。
- [`example/demo_material_eclipse.inc`](../example/demo_material_eclipse.inc)：Eclipse 输出 Golden。

## 历史基线

- [`updates/2026-09-04-github-sync.md`](updates/2026-09-04-github-sync.md)：同步同事经 PR #1 合入的更新，记录文件预览、Ontology Explorer、DeepSeek 调用审计、验证结果和行为变化。

- [`archive/ORIGINAL_POC_BRIEF_ZH.md`](archive/ORIGINAL_POC_BRIEF_ZH.md)：原始 PoC 企划。保留为历史基线，不把其中尚未实现的生产设想当作当前能力。

状态类文档应在验收范围或证据变化时更新日期；设计合同只有在架构约束发生变化时
才修改；历史基线不回写。
