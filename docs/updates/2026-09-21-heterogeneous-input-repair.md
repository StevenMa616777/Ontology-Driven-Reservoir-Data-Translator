# 异构输入与 Ontology 重构配合修复

日期：2026-09-21。此前的[诊断报告](../audits/2026-09-21-heterogeneous-dataset-diagnosis.md)保留修复前证据；本文记录修复后的行为与验收边界。

后续已获得明确付费测试授权并完成[DeepSeek 真实回归](../audits/2026-09-21-deepseek-heterogeneous-regression.md)：8 个文件成功、04 按预期审查、09 低置信度审查；进一步补齐控制关联、季度数值合同、`/bar` 和 PVT 压力检查，离线测试增至 317 项。下文 311 项是首次本地收尾时的历史基线。

## 数据判断与验收约定

01–10 号原始文件没有修改。测试集总体合理，但各文件只应验证自己声明的事实，不能从总 Ground Truth 补入缺失内容。

- 04 的 SWOF 片段没有压力单位或来源单位制，预期为 `review_required`；系统现在在调用模型前给出明确原因。补全来源信息后才可作为成功转换正例。
- 03 的 psi、08 的 bbl/day 和 psi 存在源数据舍入误差；使用逐字段容差。08 的流量以原始数值换算，不能改写成精确 500/800。
- 09 的“每 3 个月”按现有 month=30 day 规则输出 90 天；07 的季度和 10 的显式 91.25 天保持 91.25 天。需要日历季度时应另行定义业务合同。
- 08 的来源配置明确 Well_No 为本地模拟器井号，Field/Block 仍保留在原始记录与溯源中；同名异身份不能合并。

新增 [`expected_results.json`](../../heterogeneous_reservoir_testset/expected_results.json)，逐文件列出期望状态、Canonical 数值、单位、容差及分类事实。01–10 的期望数值项分别为 20、20、20、0、24、33、8、6、8、22。期望成功不代表已通过真实模型抽取。

## 修复内容

1. **完整传递 selector 合同。** 候选 Prompt 包含 required、类型、正则和枚举，表格获得确定性技术 ID，业务 sample_id 独立保留。修正重试明确要求保留必填 selector；不再只给字段名字。同表样本身份冲突在模型调用前停止。
2. **补充有界语义证据。** 从表格相态列、同句中的物性/相态以及本 JSON 容器名补充 Scope/phase 证据；为受支持的子项补齐相应表模型候选。仍拒绝缺少相态证据的映射，不跨表借用物性事实。
3. **保留并关联来源元数据。** 只将已知标题、样本信息、明确缺项说明列为 `source_annotations`；JSON 只关联直接同级元数据，文本只关联下一业务块。未知数量与未知 saturationBasis 继续审查。对单井记录，仅消除已经被同井有效映射消费的 fluid/controlMode/unit/pressureUnit 重复 UNMAPPED，并保留审计记录。
4. **补齐来源方言和入口。** `mappings/source_*.yaml` 用完整字段组合自动激活 CoreLab、SWOF、生产表、供应商相渗与日程配置；格式方言留在 Source Mapping 层。增加 UTF-8 `.dat` 输入以及 `%`、percent、Pa、mPa_s 单位支持。
5. **界面与运行时。** 界面支持 DAT，展示来源说明及具体审查原因；版本为 `ontology-context-v4`。默认 trace 在从仓库启动时写入仓库 `artifacts/deepseek_traces`，显式 `DEEPSEEK_TRACE_DIR` 优先。

## 已完成验证

- 完整离线测试：**311 passed（9.88 秒）**，较诊断基线增加 22 项。
- 新测试直接读取 01–10 原始文件，使用受控、经审阅的语义响应验证候选、Prompt、语义合同、Canonical、单位和导出；另包含未知字段、未知饱和度口径、空业务响应、跨表元数据、样本冲突等负例。
- 将 02、07 的历史失败响应去除请求与凭据后保存为测试 fixture，验证非法 selector 仍被拒绝，并携带完整合同重试。修正响应是测试提供的受控响应，不能证明真实模型重试必然成功。
- Ontology：**0 errors、0 warnings、2 infos**，两项为已有井型层级兼容说明；`compileall`、`git diff --check`、`pip check` 通过。
- 重新构建并安装 wheel；从 `/tmp` 普通导入安装包，60 个 `.py/.html/.js/.css` 文件与源码逐字节一致。正在运行的 `127.0.0.1:8000` 返回 `x-reservoir-ui-version: ontology-context-v4`。
- 内置浏览器实测“已知标题 + 未知业务字段”及原始 04 内容：均在 Review 停止，Canonical/目标输出未生成；来源说明可以展开，04 显示缺少压力单位原因。浏览器未记录 warning/error。两次路径均在模型调用前完成，没有外部请求。

## 复现命令与剩余验证

离线测试：

```sh
.venv/bin/python -m pytest -o addopts='' -q
.venv/bin/ontology-validate ontology
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

真实模型评估入口已准备好，必须先明确授权将 01–10 号文件发送到项目现有 DeepSeek 服务并发生模型费用：

```sh
PYTHONPATH=src .venv/bin/python scripts/evaluate_heterogeneous.py --real-provider
```

脚本固定使用 `deepseek-v4-flash`，逐文件保存 API 结果、来源 SHA-256、差异和模型调用证据到 `artifacts/heterogeneous-regression/`；普通 pytest 不调用外部模型。默认检查十个文件，也可用 `--files 2 5 7` 限定范围。

首次真实调用曾被自动审批拦截；用户随后明确授权发送 01–10 号文件到 DeepSeek 做付费测试，已执行并记录在上述真实回归报告中。没有运行 Flow，也不构成完整模拟器或商业平台兼容性验收。
