# 2026-09-23 sun 分支更新与合并评估

## 结论与本次执行范围

建议以现有 Ontology 0.2 Scope/Context 实现为语义基线，恢复已有合并历史后，吸收
sun 的 Block 观察界面、OCR 引擎、人工审查、OCR Lab 和 PDF 来源追踪。
不能通过整文件采用 sun 的 API、Semantic Agent 或 UI 来解决冲突。

本次已 fetch、比较代码、在临时克隆中制作当前工作区快照并进行 Git 合并演算，
分别运行双方离线测试。未在主工作区执行 merge、切换分支或提交，未 push，
未安装 PaddleOCR、下载模型或调用真实模型。本文件是评估记录，不是合并完成报告。
用户所说的 scope2 在本评估中指当前 Ontology 0.2 的 Scope/Context 重构。

## 当前基线与更新

| 对象 | 提交 |
| --- | --- |
| 本地 main / origin/main | `90169ea42869537538347228c55382fa75988b2c` |
| fetch 前 origin/sun | `ef8749c` |
| fetch 后 origin/sun | `bd7742fa1680a34b0979ab9aabecb657a3d1cfe0` |
| 本地完整收口分支 codex/full-closure-local-20260922 | `70ddf5b609e6936c2926a6df9ef4a2c98976a759` |

本次从 GitHub 新取回 6 个提交：

| 提交 | 内容 |
| --- | --- |
| `554549a` | OCR 标签和内容查看；formula 的部分低置信度标记豁免 |
| `262fb81` | OCR 低置信度人工审查 |
| `52b459c` | gitignore 更新 |
| `f030944` | OCR Lab |
| `7e7789f` | OCR 组件组织调整、表格拆分审查、上下文归属 |
| `bd7742f` | 逻辑子表拆分需求及模型验证方案；2026-09-23 14:51 +08:00 |

此前尚未吸收的功能还包括 `c50ee20` 的 Block 输入日志格式化/下载，以及
`ef8749c` 的 OCR 初步实现。按提交祖先计算，main..sun 有 11 个提交；其中
`80e56b7`、`5aa6560` 的 PDF 和 Token 诊断内容已经进入 main，不能把 11 个提交
都解释为全新功能。当前两侧 `semantic/deepseek.py` 和 `semantic/provider.py`
内容相同，观察层的新增重点在 API 和 UI。

## 冲突成因与已验证的历史修复方式

main 的共同祖先停在 `e0afb3e`。完整收口分支保留了 `9b7a520` 的真实 merge，
与 sun 的共同祖先为 `5aa6560`。运行代码随后通过 `90169ea` 发布，但该提交
不包含完整收口分支的祖先关系，造成 PDF 文件被 Git 再次视为双方新增。

将当前全部 Git 可见的修改及未跟踪文件复制到临时克隆并提交快照后，发现该快照
与 `70ddf5b` **仅 README 不同**，运行代码、测试、测试数据及其他文档一致。
因此可使用已有收口分支恢复历史，不需要重新移植一次 Scope 重构。

临时克隆中的 `git merge-tree --write-tree` 结果：

| 演算 | 冲突文件数 | 结果 |
| --- | --- | --- |
| 当前工作区快照 + sun | 14 | 包含 PDF parser 和 PDF tests 的 add/add 冲突 |
| 当前工作区快照 + 完整收口分支 | 0 | 仅 README 内容变化，运行代码不变 |
| 将上一步建立为双亲提交，再合 sun | 9 | 消除 5 个历史重复引起的冲突 |

剩余 9 个冲突文件为 README、`api/main.py`、`semantic/mapping_agent.py`、
`ui/app.js`、`ui/index.html`、`ui/ontology.html`、`ui/styles.css`、
`tests/test_api.py`、`tests/test_ui.py`。自动无冲突并不代表语义兼容，仍需完整验收。

## 分层合入规则

| 层 | 采用方式 |
| --- | --- |
| Ontology / Scope | 保留本地 0.2 的 concepts、scopes、Registry 和 contextual bindings |
| Semantic / Canonical | 保留本地 context、selectors、candidate_contexts、source_annotations、确定性路径解析及验证 |
| OCR / PDF | 吸收 sun 的 ingestion/ocr、ExtractionEvidence、SourceRegion.parts、页面组织和表格树；保留 DAT 支持 |
| API | 合入 OCR 配置、任务进度、暂停审查/恢复、中间产物与 OCR Lab；保留本地 scopes API 和完整失败诊断 |
| Workbench observe | 合入 Block 请求格式化/下载、OCR 原文对照、标签和审查记录；保留 context/selector/annotation 与失败阶段展示 |
| Ontology Explorer | 保留 scope-filter、Scope 数据及独立 Canonical 合同展示，只增加 OCR Lab 导航和必要样式 |
| 测试 / 文档 | 合并双方覆盖；新 OCR 设计与旧版本设计参考明确标注适用版本，不能覆盖 Scope 0.2 合同 |

OCR 识别和版面归属只生成带来源证据的 RawBlock。工程相态、用途、Scope 及
Canonical 路径仍由现有检索、语义合同和确定性解析处理。OCR 人工接受不能替代
后续语义审查；UNMAPPED、AMBIGUOUS、空映射及语义置信度低于 0.80 仍须阻止导出。

## 必须随合并解决或验证的事项

1. **排除整张表后的完整性状态有遗漏。** sun `api/main.py:989-998` 分别记录
   `excluded_count` 与 `excluded_table_count`，但 `:813` 只用前者决定 partial。
   对该实际 AST 表达式求值，`excluded_count=0, excluded_table_count=1` 返回
   success。UI 的下载按钮和重新导出状态同样只检查 excluded_count。合并时应统一
   完整性判断，并增加“排除整表、其余内容有效”的 API/UI 回归；不能将其显示为完整成功。
2. **Prompt 来源边界。** sun 在 document_structure 中使用
   `source_region.model_dump(..., exclude={"parts"})`。新增 parts 含其他块的正文，
   保留本地 Agent 时必须同时移入这个投影限制，否则文档结构元数据会重新带入其他块事实。
   raw_block 内保留本块证据，source_context 继续使用受控合同。
3. **版面上下文与工程语义分开。** sun 页面组织器会关联相邻标题/正文，表格树另有
   人工 context_assignments。需要验证相邻油/气 PVT 表、单位脚注、分块行号及
   相渗 sample/table ID，避免版面邻近被当成跨表借用数值或相态的依据。
4. **错误诊断不可退化。** sun execute_translation 多处仍使用简化 _http_error。
   合入异步任务和 OCR 阶段时，要保留本地 translation_id、失败阶段、位置、trace
   和 DeepSeek exchange，并贯通任务轮询及审查恢复。
5. **OCR 部署依赖。** 默认设备为 gpu:0；当前 Mac 未装可选 PaddleOCR，离线测试
   不证明真实识别可运行。OCR 依赖保持可选；开发 CPU 配置与 GPU 服务配置分开验证。
   不应为了普通单元测试强制安装 GPU 包或下载权重。
6. **拆表能力边界。** sun 最新需求文档明确说明当前 PaddleSubtableProposalEngine
   使用版面检测权重提出 table box，专用逻辑拆表模型尚未选定和验证。可吸收拆表树、
   裁剪重识别接口、复核机制和设计文档，不应宣称复杂逻辑子表已可靠自动识别。
7. **识别置信度与语义置信度。** OCR 默认阈值 0.70 与语义阈值 0.80 分别解释；
   formula 的 OCR 标记豁免需要明确展示，不能被解释为公式内容或数值已验证。

## 建议实施顺序与验收

1. 在独立 `codex/integrate-sun-scope2-ocr` 分支保存现有工作区快照；恢复
   `codex/full-closure-local-20260922` 的祖先关系。保留 main 的祖先，不重写远端历史。
2. 在该分支以真实 merge 引入固定的 sun 提交 `bd7742f`，按上表解决 9 个冲突。
   优先打通 RawBlock/OCR 合同，再处理语义边界、API 任务流程和 UI。
3. 修复整表排除状态问题，补充其他块 parts 不进入 Prompt、跨表上下文隔离、
   OCR 复核不能绕过语义门禁、失败诊断继续可见的回归。
4. 跑双方合并后的 Python 测试与 Node 布局测试、Ontology 验证、wheel 安装冒烟；
   浏览器验证 DAT/原生 PDF、OCR 对照/审查/恢复、Scope Explorer 和错误链路。
5. 在明确 OCR 环境中用代表性扫描件验收文本/表格/单位/bbox。真实 OCR 和真实
   Semantic Provider 验收独立记录，未执行的项不能记为通过。
6. 审阅后再确定发布范围。此前 README 明确记录测试、测试数据及阶段文档没有随
   运行代码发布；恢复完整分支会使这些文件进入集成历史，不能无说明直接推到 main。

## 本次验证结果

| 检查 | 结果 |
| --- | --- |
| 当前工作区 Python 测试 | 317 passed，10.38s |
| sun 原样 Python 测试，复用当前依赖环境 | 254 passed，1 failed，10.96s |
| sun 失败原因 | test_default_pdf_parser_uses_project_ocr_engine 调用 registry.get 时，可选 PaddleOCR 未安装；测试未隔离 availability |
| sun OCR 对照布局 Node 测试 | 5 passed |
| Git 历史桥接及冲突演算 | 已执行，14 → 9 个冲突文件 |
| 真实 OCR / 合并后完整回归 / 浏览器验收 | 本次未执行 |

临时克隆：`/tmp/reservoir-sun-merge-audit-20260923`。演算输出保存在
`/tmp/reservoir-sun-current-worktree-merge.txt`、`/tmp/reservoir-sun-history-bridge.txt`
和 `/tmp/reservoir-sun-bridged-merge.txt`。临时克隆中的 audit/local-snapshot
保留了本次演算的完整工作区快照；这些均未推送。

## 后续同步约定建议

- 活跃开发时每天开始工作先 fetch；开发跨日时，再比较 main...sun 的提交与共享文件。
- 按可验收的小功能建立 PR，不积累到 OCR、API、语义和 UI 一起大幅分叉后才集成。
- 集成期间频繁修改的 api/main.py、ui/app.js 明确协调人，并逐步将 OCR 路由和
  观察面板拆成独立模块，减少多人同时修改同一大文件。
- 每次集成及时把共同基线带回 sun；已共享分支保留 merge 祖先，不重复复制文件提交。
- CI 至少覆盖 Scope/Context 合同、OCR fake-backend 审查流程及 UI 静态资源；真实 OCR
  模型测试另设可选作业。以上是流程建议，本次未建立自动任务或修改 CI。
