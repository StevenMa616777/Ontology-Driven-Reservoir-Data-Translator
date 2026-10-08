# sun 合并与非 OCR 验收收尾（2026-10-08）

## 结论与范围

本地 `codex/integrate-sun-scope2-ocr` 完成此前中断的 `sun@bd7742f` 集成，保留
Scope/Context 0.2、候选上下文、selectors、确定性 Canonical 路径和低置信度安全门。
纳入 sun 的 Block 观察/Prompt Log、OCR/PDF 来源追踪、后台任务、OCR 人工审查和
OCR Lab。非 OCR 转换主链路在当前测试范围内正常，真实 OCR 和模拟器运行不在本次
通过结论内。未 push，main 仍为 `90169ea`。

本次续接原任务锁定的提交，没有将验收范围扩展到 2026-10-08 可能新增的远端提交。
9 月 23 日的 [合并评估](2026-09-23-sun-merge-assessment.md) 是历史计划，完成状态以本文为准。

## 合并方法与修复

先用 `dafbeea` 保存已有测试、数据和文档，再以 `b885469` 恢复完整收口分支的合并
祖先关系，最后分层解决 sun 的 9 个冲突文件。Scope、Ontology、Canonical 核心合同
保持本地实现，没有整文件采用 sun 的旧语义路径假设。

- 保留源块证据边界：Prompt 的 `document_structure` 不展开 `SourceRegion.parts`，
  避免其他块的内容泄漏为当前块的事实。
- 严格数值类型：拒绝布尔值被解析成数值 0/1，保留有限数值和来源单位合同。
- 整表排除也标记为 partial/incomplete；API 和 UI 都禁止将不完整结果当作正常完成导出。
- 语义观察面板优先显示 `source_unit`。例如 03 中显示 `5.076 psi`，Canonical 为
  `0.34997788020122617 bar`，避免原始数值配上换算后单位。
- 保留详细失败诊断、DAT 输入、Scope Explorer 和语义人工审核边界。

## 异构样例验收

真实 DeepSeek 调用完成于 **2026-09-23**；10 月 8 日核对全部源文件 SHA-256，并在
源码和独立安装 wheel 中分别逐字匹配保存的 Prompt、回放结构化响应。每份结果的
status/source/semantic_mapping/canonical_model/validation/export_validation/target/trace
与保存的真实运行结果一致。回放是确定性回归证据，不代表重新测量模型抽取能力。

| 样例 | 实际状态 | 原始预期 | 结论 |
| --- | --- | --- | --- |
| 01 CSV 相渗 | success | success | 通过 |
| 02 中文 TXT 相渗 | success | success | 通过 |
| 03 海外 DAT 相渗 | success | success | 通过 |
| 04 旧模拟器 keyword | review_required | review_required | 缺压力单位，正确阻断；无模型请求 |
| 05 公制 PVT CSV | success | success | 通过 |
| 06 流体性质报告 TXT | success | success | 通过 |
| 07 SCADA 井约束 JSON | success | success | 通过 |
| 08 客户生产 CSV | success | success | 通过 |
| 09 工程师记录 TXT | review_required | success | 未达原始预期；安全门正常 |
| 10 厂商综合导出 JSON | success | success | 通过 |

8 份成功结果的 **153 个预期数量值**、单位、类别、L1–L4 校验及非空目标输出均匹配。
04 和 09 均无 Canonical/目标产物。09 的 A15/B2 最低井底压力项置信度为 **0.75**，
低于 0.80 门槛；不降阈值、不强制放行，也不更改 `expected_results.json` 掩盖差异。
因此：**8 成功 + 2 待审核；原始预期符合率 9/10；确定性回放一致率 10/10**。

批量真实调用 13 次，输入 79,783、输出 15,907，共 95,690 tokens。
此前浏览器 DAT 真实调用另计 1 次、10,084 tokens；总计 **14 次、105,774 tokens**。
请求模型为 `deepseek-v4-flash`，响应记录为 `deepseek-flash`。本次续接新增付费请求为 0，
未查询账单，未将 token 数折算成实际费用。

## 本次验证证据

| 检查 | 结果 |
| --- | --- |
| Python 全量回归 | 417 passed，15.77s |
| Node 布局和单位回归 | 7 passed |
| Ontology validator | 0 errors / 0 warnings / 2 既有兼容性 infos |
| pip check | 无依赖冲突 |
| 源码回放 | 10/10 与保存结果一致，原始预期 9/10 |
| 独立 wheel 安装回放 | 10/10 与保存结果一致，原始预期 9/10 |
| wheel 源码和 UI 资源 | 79 个文件逐字节一致；安装后的 CLI 验证通过 |
| observe trace API | 原始 JSON 与 readable 路由均 HTTP 200 |
| 浏览器 DAT | 文件上传 → 后台任务 → 21 mapped / 0 unresolved → L1–L4 PASS → SWOF |
| 浏览器单位 | 语义面板 5.076 psi，Canonical 约 0.349978 bar |
| 浏览器缺单位样例 | 粘贴 04 内容 → review_required；无导出按钮、无下游产物 |
| 浏览器 Scope | 7 个 Scope；选择 PVT 后显示 6 concepts / 15 visible relations |
| 浏览器日志 | 上述操作无 error/warn |

10 月浏览器使用明确标记为 `offline-browser-replay` 的临时模型适配器，真实走解析、
API 后台任务、语义合同、Canonical、校验和渲染。DAT 请求 Prompt 必须匹配历史记录。
下载按钮已点击，但内嵌浏览器没有返回下载完成事件；复制操作也未取得可核对的剪贴板
文本。因此浏览器下载实际落盘与复制结果未验收，不将按钮点击本身视为功能通过。

证据目录（保存在本地，Git 忽略）：

- `artifacts/heterogeneous-sun-merged-20260923/`：逐文件结果、任务、summary、脱敏 provider traces。
- `artifacts/heterogeneous-sun-browser-20260923/`：此前真实浏览器调用 trace。
- `artifacts/sun-acceptance-20261008/`：pytest.log、source-replay.json、installed-replay.json、
  verification-summary.json、浏览器 DOM/截图、服务日志和 wheel。

构建 wheel SHA-256：`2b63879d6bf6d92a73c192a710a1c49a9a263f9f88f6124b323081e75d1d319c`。
该 wheel 对应最终运行源码；随后仅补充验收脚本与文档。

## 复验与后续同步

```sh
PYTHONPATH=src .venv/bin/python scripts/verify_saved_heterogeneous.py \
  --evidence-dir artifacts/heterogeneous-sun-merged-20260923 \
  --output artifacts/replay-check.json
.venv/bin/python -m pytest -o addopts='' -q
node --test tests/test_ocr_comparison_layout.js tests/test_ui_semantic_rendering.js
```

该回放脚本不联网、不读取模型密钥；历史 trace 未随 Git 发布，复验需要保留本地证据。
真实 OCR 文本/表格识别、逻辑子表拆分效果、完整浏览器矩阵、商业模拟器执行均未验收。

后续活跃开发每天开始前 fetch，按小功能集成，并保留 merge 祖先关系；本次恢复历史已
减少重复冲突。共享的 API/UI 大文件改动应及时协调。以上为同步建议，没有创建自动任务。
