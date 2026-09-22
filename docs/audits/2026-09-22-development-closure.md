# 2026-09-22 开发阶段收口报告

## 收口结论

本轮可以按“**Ontology 上下文重构与异构资料转换加固里程碑**”收口。

当前系统已从“概念 ID 直接耦合 Canonical 路径”的 v0.1 实现，演进为“可复用概念 +
显式 Scope/Context + 确定性路径解析”的 Ontology 0.2.0 流水线；10 份异构油藏资料
已经建立逐文件期望值并完成真实 DeepSeek 回归。离线质量门禁、安装包一致性和既有
人工审查安全门均通过。

本轮结论仍是 **PoC 技术路线和受控异构资料闭环成立**，不是生产就绪或模拟器兼容
认证。尤其没有完成真实 OPM Flow host deck 的短步运行，也没有商业 ECLIPSE/CMG
验证、持久化审批或带人工语义标签的准确率评估。

## 本轮工作基线与范围

本轮以本地 `main` 的 `9b7a520` 为已提交起点，该提交已合并 `origin/sun` 的 PDF
Parser、模型调用/重试诊断和 UI 缓存更新。收口批次包含其后的 Ontology 重构、异构
输入修复、真实模型回归、测试集、设计/运行文档以及本报告；被 `.gitignore` 排除的
Provider trace、请求响应和其他本地运行 artifact 不进入 Git。

2026-09-22 发布前抓取远端时，`origin/sun` 另有 `009084b`、`c50ee20`、`ef8749c`
三个尚未合入 `main` 的提交，内容涉及设计参考、Block 输入日志和 OCR 初步验证。
这些变更不在本轮 317 项测试及安装包一致性验收对应的代码中，因此本报告不对 OCR
能力作完成声明；后续应作为独立合入批次处理冲突并重新验收。

核心范围如下：

| 领域 | 本轮状态 |
|---|---|
| Ontology | 0.2.0；39 个可复用概念、7 个 Scope、41 个 contextual binding |
| Canonical 合同 | 34 条显式 Context → Path 规则；旧概念 ID 仅允许单向迁移 |
| Semantic IR | 显式 `concept + context + selectors + value/unit + provenance` |
| 输入 | TXT、JSON、CSV、XLSX、UTF-8 DAT、原生文本 PDF |
| 异构来源适配 | CoreLab、SWOF、生产表、供应商相渗、供应商日程等外部 Source Mapping |
| 安全门 | `UNMAPPED`、`AMBIGUOUS`、空映射及置信度 `< 0.80` 均停止自动构建/导出 |
| 目标输出 | Eclipse/OPM Demo INCLUDE；CMG 仍为未认证的 Demo 片段 |

## 已完成的主要更新

### 1. Ontology、Context 与存储职责分离

- 通用压力、密度、黏度、体积系数、相渗等物理含义只定义一次；相态、用途、体系和
  任务场景由 [`ontology/scopes.yaml`](../../ontology/scopes.yaml) 声明。
- 同一个概念在不同相态或用途下保留独立候选，不再用 Canonical 路径充当语义身份。
- [`canonical/mapping_contract.py`](../../src/reservoir_data_translator/canonical/mapping_contract.py)
  统一解析路径并校验 selector；Provider 不得自行发明存储位置。
- 兼容旧记录时只使用显式单向迁移表；缺少 Context、相态或 selector 时继续审查，
  不通过路径猜测含义。

完整设计与边界见
[`2026-09-20-ontology-context-refactor.md`](../updates/2026-09-20-ontology-context-refactor.md)。

### 2. 异构输入与模型输出合同加固

- Prompt 与重试信息现在携带 selector 的 required/type/pattern/enum 合同，区分确定性
  技术表 ID 和来源业务 sample ID。
- 补充表格相态列、同句物性/相态、JSON 直接父容器等有界上下文；禁止跨 Scope 或
  跨表借用相态和压力。
- 标题、样本信息、已知缺项等内容以 `source_annotations` 留存；真正未知、冲突或未
  消费的业务事实仍会触发审查。
- 新增 DAT 入口和 `%`、`percent`、`Pa`、`mPa_s`、`/bar` 等受控单位写法；未知或
  不兼容单位仍拒绝。
- 井控 target/constraint 必须关联到同一有效控制，季度数值合同和 PVT 必填压力在
  语义阶段即可进入有限重试或审查。

### 3. 可复现的异构数据验收

[`heterogeneous_reservoir_testset/`](../../heterogeneous_reservoir_testset/) 保留 01–10
原始文件，并新增机器可读的 [`expected_results.json`](../../heterogeneous_reservoir_testset/expected_results.json)。
期望结果逐文件定义状态、数值、单位、容差和分类事实，不从总说明向单文件补入缺失
证据。

真实 DeepSeek 最终结果：

| 结果 | 文件 | 判定 |
|---|---|---|
| 自动成功 | 01、02、03、05、06、07、08、10 | 153 个预期数值项逐项一致 |
| 按预期审查 | 04 | SWOF 片段未声明压力单位；本地停止，零模型调用 |
| 低置信度审查 | 09 | 两口井的 80 bar minimum BHP 均为 0.75，未降低 0.80 门槛 |

因此是 **8 个文件成功、2 个文件安全审查，9/10 个文件达到预先定义的最终状态**。
09 的值和关联关系虽已正确抽取，但未获人工批准，所以没有构建 Canonical 或输出目标
文件。详见
[`2026-09-21-deepseek-heterogeneous-regression.md`](2026-09-21-deepseek-heterogeneous-regression.md)。

真实回归共 35 次 API 请求，Provider 请求显式指定 `deepseek-v4-flash`；API 返回的
model 字段为 `deepseek-flash`。API 用量合计 248,105 tokens（208,716 input +
39,389 output），未查询人民币账单。本报告不把服务返回名进一步推断为版本等价。

## 当前验收证据

2026-09-22 在本次收口代码上重新执行：

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts='' -q` | **317 passed in 10.51s** |
| `.venv/bin/ontology-validate ontology` | **0 errors、0 warnings、2 infos** |
| `.venv/bin/python -m compileall -q src tests scripts` | 通过 |
| `.venv/bin/python -m pip check` | `No broken requirements found` |
| `git diff --check` | 通过 |
| 普通 wheel 安装路径与源码比对 | 60 个 `.py/.html/.js/.css` 文件，0 mismatch |

两条 Ontology INFO 是已记录的 `well.water_injector` / `well.gas_injector` 层级兼容
例外，不是未处理 warning。

真实回归保留 source SHA-256、完整 API 结果、trace 和 token 用量于本地忽略目录；
仓库提交测试 fixture 时只保留模型 JSON 输出，不包含凭据。此前的安装包 API 和浏览器
验证确认 `ontology-context-v4` 资源、Scope 展示及 Review Gate 生效；本轮最终源码与
安装包再次逐字节一致。

## 当前可交付能力

1. 对受支持输入进行结构解析，保留来源文件、块、页码/表格/字段位置等 provenance。
2. 在 Scope 内检索受控候选，由 DeepSeek 只完成来源证据到 Semantic IR 的非确定性
   映射。
3. 由确定性代码完成 Context/selector 合同、单位换算、Canonical 路径解析、模型构建
   和 L1–L4 校验。
4. 对明确且高置信度的资料生成稳定的 Eclipse/OPM Demo INCLUDE；缺失、冲突、歧义
   或低置信度资料 fail closed。
5. 通过 API、Translation Workbench 和 Ontology Explorer 查看阶段、候选、Context、
   审查原因、Canonical、校验及目标输出。

## 尚未收口的工程边界

| 优先级 | 缺口 | 完成判据 |
|---|---|---|
| P0 | Semantic Gold 与稳定性指标 | 人工标注 Concept/Context/Selector/Value/Unit；报告 precision、recall、F1、幻觉率及多次运行波动 |
| P0 | 真实模拟器消费 | 冻结 OPM Flow 版本、最小 host deck、WELSPECS/COMPDAT 与 timestep；保存运行日志和输入输出 hash |
| P0 | 审批与 Artifact Bundle | 持久化 source hash、模型/请求、Semantic IR、审批、Canonical、validation、target 和重放信息 |
| P1 | 09 人工决策 | 由有权限的工程人员确认两口井的 80 bar minimum BHP 后再继续构建/导出 |
| P1 | 平台兼容性 | 针对目标 ECLIPSE/CMG 产品与版本建立正式语法、语义和运行验收，不以 Demo Mapper 代替 |
| P1 | 产品化能力 | 身份权限、对象存储、队列、审计保留策略、监控、多租户与部署加固 |

这些缺口不会推翻当前 PoC 路线，但会阻止“真实工程试点已完全验收”“商业模拟器
兼容”或“生产可用”的声明。

## 收口判定

- **Ontology 0.2.0 上下文重构：完成。**
- **异构输入与真实 DeepSeek 受控回归：本轮完成；09 保留人工审查。**
- **PoC 技术路线：继续成立，并扩展到可复现的异构资料验收。**
- **真实模拟器工程试点：未完成。**
- **生产可用与商业平台认证：未完成。**

后续开发应以本报告列出的 P0 验收合同为新阶段起点，继续保持 LLM 与确定性工程逻辑
的边界，不以放宽审查门槛换取表面通过率。
