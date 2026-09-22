# 2026-09-04 GitHub 更新记录

本次将本地 `main` 从 `b2fb10d` 快进更新到 `42f8478`，同步同事 Sun Handa 在
`sun` 分支完成、经 PR #1 合入 `main` 的更新。以下记录依据实际提交差异与本次本地
验证，不仅依据提交标题。

## 同步范围

- 仓库：[Ontology-Driven-Reservoir-Data-Translator](https://github.com/StevenMa616777/Ontology-Driven-Reservoir-Data-Translator)。
- 更新前：`b2fb10d4d2155b6a37b54c280f5f788fc11f8a3e`。
- 更新后：`42f84788c916016797beac4b3211a39e0aed8d25`。
- 操作：`git fetch origin` 后执行 `git pull --ff-only origin main`，无冲突。
- 更新前本地工作区干净；更新后 `HEAD` 与本次获取的 `origin/main` 一致。
- 远端增量共 3 个提交，修改 28 个文件，新增 1,687 行、删除 76 行。

| 提交 | 作者 | 提交时间（UTC+8） | 内容 |
|---|---|---|---|
| `1baa053` | Sun Handa | 2026-09-02 15:14:33 | 添加点击打开已上传的文件的功能 |
| `e0afb3e` | Sun Handa | 2026-09-03 16:20:07 | 添加 ontology 可视化只读页面和 DeepSeek API 请求的监视功能 |
| `42f8478` | Yihan Ma | 2026-09-03 17:32:27 | 合并 [PR #1](https://github.com/StevenMa616777/Ontology-Driven-Reservoir-Data-Translator/pull/1)，来源分支为 `sun` |

## 同事具体做了什么

### 1. 已选文件可以点击打开

工作台的文件摘要支持点击以及 Enter/空格键打开文件。TXT、JSON、CSV 设置对应 MIME
类型，通过浏览器 Blob URL 打开新窗口；其他格式交由浏览器处理，不等于新增了 XLSX
在线预览器。增加弹窗拦截提示、对象 URL 回收和交互样式。

实现：[app.js](../../src/reservoir_data_translator/ui/app.js)、
[index.html](../../src/reservoir_data_translator/ui/index.html)、
[styles.css](../../src/reservoir_data_translator/ui/styles.css)。

### 2. 新增 Ontology Explorer 只读页面

工作台顶部新增入口 `/ontology`，从运行时 `OntologyRegistry` 获取真实概念与关系。
页面提供名称/ID/别名/描述搜索、Domain 和关系类型筛选、层级树、概念详情、邻域聚焦、
缩放拖拽和适合窗口；可以通过 `?concept=...` 定位概念。

后端提供层级边、语义关系边、入向关系及概念元数据。详情包括单位、维度、别名、
约束、来源文件和状态；此页面没有编辑或保存 Ontology 的接口。

实现：[ontology.html](../../src/reservoir_data_translator/ui/ontology.html)、
[ontology.js](../../src/reservoir_data_translator/ui/ontology.js)、
[ontology.css](../../src/reservoir_data_translator/ui/ontology.css)、
[api/main.py](../../src/reservoir_data_translator/api/main.py)。

### 3. DeepSeek 调用审计落盘，并在结果页按需展开

`DeepSeekCallTrace` 从精简的 dataclass 改为 Pydantic 模型。现在逐次记录实际 HTTP
请求，包括对应 block、初次调用/合同重试/输出重试/网络重试、尝试序号、耗时、HTTP
状态、Token usage、结果状态、错误，以及完整请求 Payload 和响应 Payload。

`capture_deepseek_traces()` 用 ContextVar 收集当前异步请求的调用。`/translate` 在
语义阶段结束或异常退出时尝试保存已捕获的调用，以 translation ID 命名生成 JSON
和 `.readable.log`；正常响应增加 `deepseek_trace` 汇总及读取地址。

结果页新增“模型调用与 Token 明细”，展示请求数、重试数、本地更正次数、避免重试
次数和 Token 汇总；点击后加载逐次调用表、请求/响应内容与易读日志。

实现：[semantic/deepseek.py](../../src/reservoir_data_translator/semantic/deepseek.py)、
[api/models.py](../../src/reservoir_data_translator/api/models.py)、
[api/main.py](../../src/reservoir_data_translator/api/main.py)、
[app.js](../../src/reservoir_data_translator/ui/app.js)。

### 4. 增加 JSON 本地语法修复和修复结果追踪

在已有 Markdown JSON 围栏处理基础上，增加尾逗号移除，以及从说明文字中提取单个
JSON 对象/数组的处理，保留字符串内部内容。修复后仍须通过 Pydantic Schema 和
语义合同检查；不能成功解析或校验时保留有界重试。新增
`record_contract_failure()` 钩子，将语义合同拒绝结果记入对应调用。

新增测试覆盖说明文字包裹、尾逗号、多个不同 JSON 候选、Schema 不匹配和业务合同
拒绝，防止把被拒绝结果统计为成功的本地修复。

### 5. 补充 PVT / SCAL 结构父概念候选

语义候选检索选中油/水/气 PVT 或相渗的子字段时，会补入所需结构父概念，并将父概念
排在对应子字段之前。Prompt 明确要求返回父映射，减少因缺少结构父映射引发的合同
重试；这次没有修改 Ontology YAML、Canonical Builder、单位换算或平台 Mapper。

实现：[mapping_agent.py](../../src/reservoir_data_translator/semantic/mapping_agent.py)。

### 6. 增加文字放大偏好

工作台与 Ontology Explorer 共用“放大文字/标准文字”按钮，以 localStorage 保存
偏好，调整页面、详情和日志字号，图谱在切换后重新适应窗口。

实现：[ui-preferences.js](../../src/reservoir_data_translator/ui/ui-preferences.js)。

### 7. 同步脚本、测试、说明和 IDE 配置

- 两个 DeepSeek 验证脚本改用 `model_dump()` 序列化新的 Trace；综合 Demo 验收摘要
  将 `prompts_recorded`、`responses_recorded` 标记为 `True`。
- 扩充 API、DeepSeek Provider、Semantic Mapping Agent 和 UI 测试。
- README、RUNBOOK 补充调用审计内容、目录配置和资料保存说明。
- 提交了 6 个 `.idea/` IDE 项目配置文件；`pyproject.toml` 与依赖声明没有变化。

## 新增页面与接口

| 路径 | 用途 |
|---|---|
| `GET /ontology` | Ontology Explorer 页面 |
| `GET /api/ontology/graph` | 概念、关系和 Ontology 元数据 |
| `GET /api/ontology/concepts/{concept_id}` | 单个概念详情；未知 ID 返回 404 |
| `GET /deepseek-traces/{translation_id}` | 读取持久化 JSON 调用审计 |
| `GET /deepseek-traces/{translation_id}/readable` | 读取易读日志 |

## 本次更新后的验证

| 检查 | 结果 |
|---|---|
| `.venv/bin/python -m pytest` | **140 passed in 4.64s**，包括现有 OPM Parser 测试；无跳过 |
| `.venv/bin/ontology-validate ontology` | PASS：0 errors、0 warnings、2 条既有井层级兼容性 INFO |
| `.venv/bin/python -m pip check` | 无依赖冲突 |
| Python `compileall`（src、tests、scripts） | 通过 |
| `node --check`（app.js、ontology.js、ui-preferences.js） | 通过 |
| `git diff b2fb10d..HEAD --check` | 通过 |
| 安装包导入及文件比对 | 新增符号可导入；56 个 Python/UI 文件与当前源码逐字节一致 |
| 安装包的 ASGI 离线检查 | 首页、Ontology 页面、2 个新增 JS 资源、图谱接口、概念详情接口均返回 200；图谱为 51 节点、87 条边 |

拉取后首先发现 `.venv` 仍安装着旧版包，无法导入新增的 `capture_deepseek_traces`。
已执行 `.venv/bin/python -m pip install --force-reinstall --no-deps .` 同步安装，并完成
上述安装验证。版本号仍为 `0.1.0`，因此额外比对安装文件与源码，而非仅检查版本号。

本次没有运行真实 DeepSeek 调用、浏览器交互验收或 Flow/商业模拟器运算；140 项测试
与离线接口检查不代表这些链路已经重新验收。

## 行为变化与待跟进事项

1. **Prompt 和响应现在会被保存。** Trace 不主动记录 API key 或 Authorization
   header，但完整 Prompt/响应可能含原始客户资料。源码运行的默认审计位置为
   `artifacts/deepseek_traces/`，可通过 `DEEPSEEK_TRACE_DIR` 显式指定。两个真实模型
   验证脚本生成的 Trace 也带完整请求/响应。这是此次更新的重要数据保存变化。
2. **wheel 安装后的默认日志目录与文档描述不同。** 默认路径由模块的 `__file__`
   推导。本次确认，在当前 `.venv` wheel 安装下，它指向
   `.venv/lib/python3.14/artifacts/deepseek_traces/`，而非仓库根目录。可在从仓库根
   目录启动前设置 `export DEEPSEEK_TRACE_DIR="$PWD/artifacts/deepseek_traces"`；后续
   可统一默认路径解析。本次仅记录，没有修改同事的实现。
3. **已有收口文档需要按日期理解。** `docs/POC_CLOSURE.md` 中“不记录 Prompt”和
   “trace 不持久化”描述的是 2026-09-02 基线，不能作为新版全部行为的说明。新版
   已有本地调用日志持久化，但仍没有完整的审批持久化、回放和生产审计系统。
4. **此次新增功能没有补齐生产鉴权。** Trace 读取接口没有新增身份认证或访问授权；
   现有本地 PoC 使用边界仍然适用。

## 本次记录工作与同事提交的区别

上面的 28 个文件属于远端提交。本次本地另外新增本记录，并在 `docs/README.md`
加入索引；同步了 `.venv` 安装包。记录文档尚未提交或推送，未修改同事的业务代码。
