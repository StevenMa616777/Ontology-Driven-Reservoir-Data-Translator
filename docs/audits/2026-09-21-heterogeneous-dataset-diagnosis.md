# 异构测试集与 Ontology 重构配合问题诊断

日期：2026-09-21。实际数据目录：`heterogeneous_reservoir_testset/`。

本报告记录修复前状态；后续代码修复与验证见[异构输入配合修复记录](../updates/2026-09-21-heterogeneous-input-repair.md)。

## 结论

测试集总体适合作为异构转换测试，不能把大量失败归咎于数据不合理。当前存在两类问题叠加：重构引入的上下文硬门禁及 selector 合同传递不完整，以及原先就存在的分块上下文缺失、元数据误触发审查、单位和字段召回缺口。

Ontology YAML 自身能够加载并通过校验，Canonical/导出层也并非整体失效。主要断点在 **Ingestion → Retrieval → Semantic Agent** 的配合。01 号原始响应回放可完整构建 Canonical、通过校验并导出 SWOF；05 号即使输入正确映射，也会被上下文门禁降级。

## 实测范围和边界

- 当前 `.venv/bin/python -m pytest -o addopts='' -q`：**289 passed，7.26 秒**。
- `.venv/bin/ontology-validate ontology`：**0 errors、0 warnings、2 infos**。
- 10 个源文件逐块检查解析、Scope、候选、`context_supported`；没有把候选可用等同于模型必然正确。
- 用 `git archive HEAD` 提取提交 `9b7a520` 的源码/ontology/mappings 到 `/tmp/reservoir-ontology-pre-refactor-20260921`，进行同输入检索对照。它是当前已提交基线，不能证明重构前所有样例都能端到端成功。
- 源码和安装包内全部 `.py/.html/.js/.css` 文件逐字节一致；运行服务的 `/api/ontology/graph` 返回 39 个概念、7 个 Scope。排除这次由旧安装包造成失败的解释。
- 找到今天三个真实调用日志，使用日志里的模型响应离线回放 `/translate`，没有新增外部 Provider 请求，也没有运行模拟器。
- 只新增诊断报告和 `artifacts/ontology_diagnosis_2026-09-21/` 下的审计脚本/结果，没有改业务代码、ontology 或测试原始数据。

## 测试样例是否合理

相渗 Sw、Krw、Kro 均在 0–1 内，Sw 递增、Krw 非递减、Kro 非递增。02 的百分比/kPa 和 10 的 Pa 换算后与 01 一致；03 的 psi 舍入造成最大约 `0.00002212 bar` 的差异，符合测试数据精度。PVT 数值为正，油气表随压力变化的方向没有发现明显自相矛盾；这是测试数据内部一致性判断，不代表实验真实性认证。

不同文件只覆盖事实子集，验收应比较各自已声明的事实，不应要求每个文件生成全部 PVT、SCAL、井控和日程。当前 Eclipse mapper 本来就支持部分 INCLUDE；缺少无关模块本身不是失败理由。

| 文件 | 数据判断 | 当前已确认的障碍 |
|---|---|---|
| 01 `lab_A_relperm.csv` | 合理的基础正例；5 行数据与 Ground Truth 一致 | 今天日志的 21 条映射离线回放成功，Canonical 和 SWOF 数值一致 |
| 02 `lab_B_相渗实验.txt` | 合理；样本号、水驱油、百分比及 kPa 明确 | 标题块无候选而自动 UNMAPPED；X-12 与数据表分块；真实响应的 table_id 无效，重试后缺项；修正响应副本后 Builder 拒绝 `%` |
| 03 `overseas_corelab.dat` | 内容合理，psi 有正常舍入差异；属于格式扩展测试 | API 没有 `.dat` parser，直接 `UNSUPPORTED_SOURCE_TYPE`。改扩展名只能绕过入口，不能证明后续字段识别成功 |
| 04 `legacy_simulator_keyword.txt` | 可作有条件正例；独立片段未声明 METRIC/FIELD 或 Pc 单位 | 当前未召回 SWAT 对应的水饱和度；不能仅因目标是 METRIC 就把来源 Pc 当作 bar |
| 05 `pvt_lab_metric.csv` | 合理的油气混合表；行内 `DEAD_OIL/GAS` 是相态证据 | `detect()` 返回空 Scope；油气 PVT 候选全部 `context_supported=false`。正确 MAPPED 也会被降为 AMBIGUOUS |
| 06 `fluid_properties_report.txt` | 工程文字合理；气相引用前文压力仍位于同一数据块 | 标题自动 UNMAPPED；油相 PVT 候选缺失、三个密度候选均被判证据不足；岩石压缩系数没有有效召回。部分漏召回在旧基线已存在 |
| 07 `scada_well_constraints.json` | 井型、控制、数量、单位和日程清楚，适合作为正例 | 今天 C1 的 maximum_bhp 选择器失败；`asset` 自动 UNMAPPED；原始响应还重复将已消费的 fluid/controlMode/unit/pressureUnit 标成 UNMAPPED |
| 08 `customer_B_production.csv` | 适合作为客户字段适配测试，但需要角色/目标定义、实体标识策略和数值容差 | 现有 `client_b` 配置是 well_name/LRAT/pressure_floor，并不匹配这份表；PROD/WINJ、Operating_Target 的解释主要依赖弱候选和模型。未取得今天该文件的真实调用日志 |
| 09 `engineer_notes.txt` | 自然语言测试合理，但“每 3 个月”与本系统季度定义不完全等价 | 主体只有效召回 `well` 和 `liquid_rate`；注水、上下限及日程无法从当前候选中完整选择。标题自动 UNMAPPED，末尾“没有 PVT”却召回 PVT |
| 10 `integrated_vendor_export.json` | 相渗数值和 1825/91.25 天合理；需要结构上下文关联 | sourceSample、saturationBasis、rows 被分块；油水相渗候选全被门禁否定；`$.schedule` 路径不参与检索，endDay/outputEveryDays 无候选；Pa 不支持 |

04 的格式语义可参照 [OPM 官方 SWOF 定义](https://raw.githubusercontent.com/OPM/opm-common/master/opm/input/eclipse/share/keywords/000_Eclipse100/S/SWOF)：四列分别具有三个无量纲维度和一个 Pressure 维度。应在测试输入或明确的来源配置中给出压力单位，不能从 Ground Truth 偷渡到源文档。

需要修订的验收约定：

1. **08 数值并非仅最终一位正常舍入**：3144.7 bbl/d = 499.967346 m³/d；5031.6 bbl/d = 799.960473 m³/d。若要保留一位小数逼近 500/800，正确值是 3144.9/5031.8 bbl/d。应修正生成数据或明确容差，不能用浮点全等。压力换算分别约 79.999869、420.001035 bar。
2. **09 时间口径有真实差异**：当前 month=30 day、quarter=91.25 day、year=365 day。因此“3 个月”是 90 天，07 的 QUARTERLY 和 10 的 91.25 天是一致的，09 则不是。应明确是固定天数、固定月份还是日历季度。
3. **08 复合井身份的规则未声明**：Field+Block+Well_No 是否映射到短井名 A15，需独立约定。若直接拼接长名，当前 Eclipse demo 的井名长度检查可能失败；不能把元数据随意丢掉来满足导出。
4. Ground Truth 目前只是自然语言说明，缺少每文件的期望事实集合、允许缺失/歧义、来源配置、单位策略、比较容差及期望最终状态。不能把一份说明中的全部事实都视为每个单文件的可用证据。

## 根因一：selector 的后端合同没有完整发送给模型（新 IR 配合缺口）

代码位置：`semantic/mapping_agent.py:204` 只发送 `entity_selectors` 名字；`canonical/mapping_contract.py:31` 实际还限制每个 selector 的类型、模式和枚举。新的响应模型允许任意字符串/整数 selector，Prompt 未给出这些字段各自的完整合同。

真实日志及离线回放：

| 文件 / 日志 ID | 首次输出 | 重试输出 | 回放结果 |
|---|---|---|---|
| 02 / `582fba9f-f0e4-4284-a307-518ca4514bdb` | table_id=`02_lab_B_相渗实验.txt`，句点不符合 `[\w-]{1,128}` | 删除 table_id | HTTP 502；semantic_map；`CANONICAL_PATH_OUTSIDE_CONTRACT: Missing selectors: ['table_id']` |
| 07 / `ef8e87f3-5af8-4ebe-97e3-88181b8be0a9` | control_type=`INJECTION_RATE`，不是合法枚举；此处应为 `water_injection_rate` | 删除 control_type | HTTP 502；semantic_map；`CANONICAL_PATH_OUTSIDE_CONTRACT: Missing selectors: ['control_type']` |
| 01 / `fa9bf6cf-920e-46df-b02b-b684fe22bc47` | X-12、完整 5 行相渗 | 无需重试 | HTTP 200、success、正确 SWOF |

不是 HTTP Provider 连接失败：上述失败调用的原始网络 HTTP 状态均为 200，失败来自本地语义合同校验。错误统一包装成 `CANONICAL_PATH_OUTSIDE_CONTRACT`，修正 Prompt 只有“invalid selector”文字，没有给出允许值，模型就把字段删除了。

02 还缺少当前表块中的 X-12 证据，不能只要求模型换个 ID。应由解析/上下文层提供有来源的表身份，或定义确定性技术表 ID，与实际 sample_id 分开。

## 根因二：词法检索的证据被当成不可推翻的语义判定（确定性回归）

代码位置：`semantic/retriever.py:155–234`、`semantic/mapping_agent.py:411–425`。

- 05 中 pressure/formation_volume_factor/viscosity 能让 fallback 推导出可能的 Scope，但 `context_supported` 仍要求 Scope 出现在更窄的 `explicit_scopes` 中。该样例没有字面 PVT 或相态专属物性别名，导致所有正确油气候选被否定。
- 离线给出“第一行 10 MPa、oil、pvt/coordinate、confidence=0.99”的正确草稿，Agent 仍强制返回 **AMBIGUOUS、0.79**，甚至加入 rock/reference 选项。因此多次重试模型不能解决这个门禁。
- 10 的 `waterRelPerm/oilRelPerm` 规范化为 `water rel perm/oil rel perm`，与 binding 的 `Krw/Kro/oil relperm to water` 等别名不能完整对上。出现 waterSat 的水相提示却没有油相提示，油水体系被判不充分；所有真实相渗候选不可接受。
- 06 的三相密度被标成无充分证据。原油 PVT 没有进入候选，不能归为模型漏读；这一油相漏召回在 HEAD 基线也存在，重构没有修复它。
- 04 旧检索依靠 Sw 子串碰巧命中 SWAT；新单词边界收紧后不再命中，又没有来源方言定义补上。应补充显式 SWAT/SWOF 语义，而不是恢复任意子串匹配。

Scope/phase 的防串用是必要的，但应结合行、列、段落、JSON 父节点的有界证据。不能把“没有匹配到某个别名”直接等同于“源文档没有相态证据”，也不能通过取消所有门禁来提高通过率。

## 根因三：分块、元数据消费和 review gate 不匹配（旧问题仍在）

代码位置：`ingestion/text_parser.py`、`ingestion/json_parser.py:72–125`、`ingestion/models.py:110`、`semantic/mapping_agent.py:137–139,219–240`、`api/main.py:673–694`。

- TextParser 按空行拆分；JsonParser 递归把 sibling 元数据和 rows 拆开。检索只读取 content，不读取 source_location；Prompt 的 document_structure 只有位置，没有相邻块内容，并声明不能作为映射证据。
- 02 的 X-12、10 的 sourceSample/saturationBasis 与表数据分离，07 的 asset 独立成块；02/06/09 的标题无可构建概念。无候选直接变成 UNMAPPED，不调用模型。
- 任意 UNMAPPED 都让整份文档在 review gate 停止，Canonical 尚未构建。当前状态无法表达“标题”“已被其他映射使用的单位/角色”“已保留的来源元数据”和“确实未解决的业务事实”的区别。
- 07 原始响应已经使用 fluid=WATER 决定井型、使用 unit/pressureUnit 解释数值，却又把这些字段逐个标 UNMAPPED。只在响应副本中修正 control_type 后，仍是 **4 MAPPED + 4 UNMAPPED**；仅取 4 条有效映射作诊断时，C1 的 Canonical 和导出校验均通过。完整文档仍不能绕过审查。

与 HEAD 对照可确认，raw_block-only 规则、标题无候选和 review gate 并非这次重构新引入。需要引入可追溯的元数据/上下文处理；不能把所有 UNMAPPED 全部忽略。

## 根因四：来源单位与字段词汇支持不完整（多为旧缺口）

`semantic/unit_normalizer.py:72–116` 的显式白名单不含 `%`、percent、Pa。实测 `15 % → fraction` 和 `35000 Pa → bar` 都是 `UNSUPPORTED_UNIT`；正常物理数据被实现边界拒绝。02 在响应副本修正 table_id、仅诊断表映射后正是该错误。

`mPa.s → cP` 当前已经支持且换算正确；字面 `mPa_s` 仍不支持，05 的列名需要明确的单位提取规范。因此不能再次简单归因于旧的 mPa.s bug。

09 的自然语言同义表达、10 的 endDay/outputEveryDays、08 的客户编码没有足够的来源映射。现有 client_b 配置与文件名中的 customer_B 并不是同一个已定义 schema，不能自动等同。

## 为什么 289 项测试仍然全部通过

`tests/test_contextual_pipeline.py:162–188` 的整链路测试从已知正确映射出发，用 binding 的第一个 alias 拼出输入，再用 FixedIRProvider 返回预制的 context/selectors。它验证了给定正确 IR 后的装配，却天然绕过了这里的别名、真实字段、selector 生成和分块难题。

`tests/test_cross_source_consistency.py` 的三个 fixture 也使用固定响应；当前测试目录并未加载这批 `heterogeneous_reservoir_testset` 文件。测试结果能说明覆盖范围内没有回归，不能代表真实异构输入通过率。

## 建议修复顺序及验收

1. **补齐 IR 合同**：每个 selector 的 required/type/pattern/enum 与代码共用定义；纠错明确指出字段和值域。把 02/07 的真实响应固化为离线回归，不扩大网络重试次数来掩盖问题。
2. **修正候选和上下文判别**：支持 05 的行内流体字段、10 的列语义/父节点、06 的局部物性陈述；保留未知相态和跨 Scope 串用的负例。
3. **建立有界上下文及字段消费记录**：样本号/单位/角色可关联到本表，标题及非业务元数据可保留但不误触发审查；真正未知或冲突的业务事实继续阻断。
4. **补足明确单位和来源适配**：percent、Pa、CSV 单位拼写；DAT 是否纳入支持；SWOF 的来源单位制；客户 B schema 和复合井身份规则。
5. **建立逐文件验收清单**：期望字段和值、相态/井型、单位、允许缺项、容差、期望状态；同时比较数据覆盖率与错误映射，防止删掉难字段后显示 success。先过离线检查，再跑真实模型回归。

## 可复查证据

- `artifacts/ontology_diagnosis_2026-09-21/current.json`：当前 10 文件分块与全部候选。
- `artifacts/ontology_diagnosis_2026-09-21/baseline-head.json`：HEAD 同输入对照。
- `artifacts/ontology_diagnosis_2026-09-21/replay-summary.json`：三个真实响应回放和标明干预的诊断探针。
- `artifacts/ontology_diagnosis_2026-09-21/replayed-01.json`、`replayed-02.json`、`replayed-07.json`：完整离线 API 响应。
- `artifacts/ontology_diagnosis_2026-09-21/data-consistency.json`：相渗跨来源单位一致性。
- 同目录 `snapshot.py`、`replay.py`：复现实验脚本。`PYTHONPATH=src .venv/bin/python artifacts/ontology_diagnosis_2026-09-21/replay.py` 不访问外部模型。

实际调用日志位于 `.venv/lib/python3.14/artifacts/deepseek_traces/`，而非仓库根 artifacts。原因是安装模式下 `api/main.py:63–65` 从 `__file__` 推导 PROJECT_ROOT。排查时只看仓库根日志会遗漏今天的运行记录；建议后续显式配置 DEEPSEEK_TRACE_DIR。
