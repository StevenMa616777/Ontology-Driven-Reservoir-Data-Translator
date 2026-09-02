# 项目文档索引

文档按“入口说明、设计合同、验收证据、历史基线”分层，避免把当前事实、未来设想和
最初企划混在一起。

## 当前入口

- [`README.md`](../README.md)：仓库首页、快速开始、能力边界和文档导航。
- [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md)：项目目标、实现路线、组件职责和当前运行逻辑。
- [`RUNBOOK.md`](RUNBOOK.md)：开发、启动、测试、真实模型验收和故障排查。

## 设计合同

- [`DESIGN.md`](../DESIGN.md)：Ontology-Driven 架构和 Tasks 1-12 的详细设计基线。
- [`ONTOLOGY_CONVENTIONS.md`](../ONTOLOGY_CONVENTIONS.md)：Ontology 受控词汇、关系、单位和版本约定。
- [`mappings/README.md`](../mappings/README.md)：Source Mapping 与 Platform Mapping 的边界。

## 状态与验收

- [`POC_CLOSURE.md`](POC_CLOSURE.md)：2026-09-02 PoC 收口结论、冻结范围和证据。
- [`DESIGN_COMPLETENESS.md`](../DESIGN_COMPLETENESS.md)：逐 Task、逐层实现完成度和生产边界。
- [`example/demo_material_raw.txt`](../example/demo_material_raw.txt)：冻结的综合 Demo 原文。
- [`example/demo_material_eclipse.inc`](../example/demo_material_eclipse.inc)：Eclipse 输出 Golden。

## 历史基线

- [`archive/ORIGINAL_POC_BRIEF_ZH.md`](archive/ORIGINAL_POC_BRIEF_ZH.md)：原始 PoC 企划。保留为历史基线，不把其中尚未实现的生产设想当作当前能力。

状态类文档应在验收范围或证据变化时更新日期；设计合同只有在架构约束发生变化时
才修改；历史基线不回写。
