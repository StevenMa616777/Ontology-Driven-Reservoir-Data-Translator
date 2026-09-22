# 本地运行与验收手册

## 1. 环境要求

- Python 3.12+
- macOS/Linux shell 或 Windows PowerShell（示例使用 POSIX 环境变量语法）
- 完整 PoC 验收需要可安装的 `opm==2025.10`
- 真实语义转换需要 DeepSeek API key

## 2. 安装

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install '.[dev,opm]'
```

如果 editable/source 安装在当前 Python 版本下出现 import/CLI 路径异常，可重装普通 wheel
形式的当前项目：

```bash
.venv/bin/python -m pip install --force-reinstall --no-deps .
```

### 2.1 可选 OCR 依赖

只有需要处理整页扫描 PDF 时才安装：

```bash
.venv/bin/python -m pip install '.[ocr]'
```

该 extra 安装 PaddleOCR 3.x 文档解析能力和 PaddlePaddle GPU 运行时。GPU wheel 应先从
Paddle 官方 CUDA 源安装；例如兼容 CUDA 12.6 的 NVIDIA 环境：

```powershell
python -m pip uninstall -y paddlepaddle
python -m pip install "paddlepaddle-gpu==3.3.1" --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/
python -m pip install ".[ocr]"
python -c "import paddle; print(paddle.is_compiled_with_cuda()); print(paddle.device.cuda.device_count()); paddle.utils.run_check()"
```

这些命令必须使用运行后端的同一 Python 解释器。CPU 和 GPU 包共享 `paddle` 模块，
不要同时安装。CPU 环境可安装 `.[ocr-cpu]` 并显式设置 `RESERVOIR_OCR_DEVICE=cpu`。
安装/切换运行时后需重启后端。CUDA wheel 与驱动的匹配以 Paddle 官方安装文档为准。
首次运行可能下载
OCR/版面分析模型；生产环境应预先缓存模型，或用 PaddleX pipeline 配置指向本地模型。

## 3. 本地确定性验证

```bash
.venv/bin/ontology-validate ontology --json
.venv/bin/python -m pytest
.venv/bin/python -m pip check
```

只验证固定 Eclipse Golden 可被 OPM Parser 消费并得到相同标准化语义：

```bash
.venv/bin/python -c "from pathlib import Path; from reservoir_data_translator.validation import compare_eclipse_includes; p=Path('example/demo_material_eclipse.inc').read_text(); print(compare_eclipse_includes(p, p)['semantic_equal'])"
```

## 4. 扫描 PDF OCR 配置

应用默认选择延迟加载的项目 `OcrEngine`，由独立注册的 Paddle 组件提供推理；原生文本 PDF 不会触发模型加载。安装 OCR
依赖后，扫描件以及禁止文本提取但可渲染的 PDF 会自动进入 OCR：

```bash
export RESERVOIR_OCR_LANGUAGES='ch,en'
```

Windows PowerShell 可使用 `$env:RESERVOIR_OCR_BACKEND='reservoir'` 显式选择项目引擎；
`paddleocr` 保留为 PP-StructureV3 整套对照后端。

可选环境变量：

| 变量 | 默认值 | 作用 |
|---|---:|---|
| `RESERVOIR_OCR_BACKEND` | `reservoir` | 项目组件引擎；`paddleocr` 选旧整套基线，`disabled` 停用 |
| `RESERVOIR_OCR_LANGUAGES` | `ch,en` | 写入提取证据的语言列表；首项作为 PaddleOCR `lang` |
| `RESERVOIR_OCR_LANG` | 未设置 | 显式覆盖 PaddleOCR `lang` |
| `RESERVOIR_OCR_DEVICE` | `gpu:0` | 默认第一个 NVIDIA CUDA GPU；可显式指定其他 GPU 或 `cpu` |
| `RESERVOIR_OCR_RENDER_DPI` | `300` | PDF 页渲染分辨率 |
| `RESERVOIR_OCR_MAX_PIXELS_PER_PAGE` | `40000000` | 单页像素预算 |
| `RESERVOIR_OCR_MIN_CONFIDENCE` | `0.70` | 文字和表格低置信度标记阈值，适用于项目引擎及旧整套基线 |
| `RESERVOIR_OCR_REJECT_LOW_CONFIDENCE` | `true` | 为 true 时低置信度区域进入异步 OCR 人工审查；为 false 时不生成这类区域审查项，表格结构审查仍独立触发 |
| `RESERVOIR_OCR_PADDLEX_CONFIG` | 未设置 | 自定义 PP-StructureV3 pipeline 配置文件 |
| `RESERVOIR_OCR_ENABLE_MKLDNN` | `false` | Windows 默认关闭以规避部分 oneDNN 算子兼容问题 |
| `RESERVOIR_OCR_CPU_THREADS` | `8` | Paddle CPU 线程数 |

路由规则：

- 有可用文本层：继续走原生 PDF 解析，不调用 OCR；
- 所有页面均无原生文本且存在页面图像：整份文件走 OCR；图像覆盖率仅记录为诊断证据，不作为硬门槛；
- 原生页和扫描页并存：返回 `PDF_HYBRID_UNSUPPORTED`；
- OCR 未启用但文件需要 OCR：返回 `PDF_OCR_REQUIRED`；
- 请求 GPU 但安装了 CPU 运行时：返回 `PDF_OCR_GPU_RUNTIME_UNAVAILABLE`；GPU/驱动/设备编号不可用：返回 `PDF_OCR_GPU_UNAVAILABLE`，网页显示停止阶段为 OCR 识别，不自动退回 CPU；
- PDF 禁止复制/文本提取但可以渲染：自动逐页渲染并走 OCR，在每个块的提取证据中记录 `SOURCE_TEXT_EXTRACTION_RESTRICTED`；
- PDF 需要密码或页面无法渲染：返回 `PDF_ENCRYPTED` 或 `PDF_RENDER_FAILED`；
- 单页超过资源预算、OCR 后端失败或输出结构错误：返回对应的稳定 PDF 错误码，且整份文件不产生部分结果。

OCR 的文本、表格和图片区域统一适配为现有 `RawBlock`。每个块保留页码、PDF 坐标、
OCR 引擎/模型、置信度、渲染 DPI、版面标签和质量标记。图片块当前保留区域和证据，
不做图表曲线数字化或图像语义理解。

## 5. Provider 配置

推荐使用进程环境变量，不要把 key 写入源代码或提交到 Git：

```bash
export DEEPSEEK_API_KEY='your-key'
export DEEPSEEK_MODEL='deepseek-v4-flash'
export RESERVOIR_SEMANTIC_PROVIDER='deepseek'
```

也可以设置：

```bash
export DEEPSEEK_API_KEY_FILE='/absolute/path/to/api_key'
export DEEPSEEK_BASE_URL='https://api.deepseek.com'
export DEEPSEEK_TIMEOUT_SECONDS='120'
```

项目兼容被忽略的 `LLM/DeepSeek/api_key`，但环境变量或项目外 key 文件更适合可控运行。
应用不会把凭据或 Authorization header 写进 trace。浏览器 `/translate` 流程的本地
调用审计会保存完整 Prompt 和 Provider 响应，因此其中可能包含客户原始资料；该目录
不得共享或提交到 Git。

如果只需要 deterministic endpoint，可显式关闭 Provider：

```bash
export RESERVOIR_SEMANTIC_PROVIDER='disabled'
```

## 6. 启动 API 和工作台

```bash
.venv/bin/uvicorn reservoir_data_translator.api.main:app --reload
```

- 工作台：`http://127.0.0.1:8000/`
- OpenAPI：`http://127.0.0.1:8000/docs`

默认从项目中的 `ontology/` 和 `mappings/` 加载配置。部署到其他当前目录时可设置：

```bash
export RESERVOIR_ONTOLOGY_PATH='/absolute/path/to/ontology'
export RESERVOIR_MAPPING_PATH='/absolute/path/to/mappings'
```

## 7. API 运行方式

主要业务 endpoint：

```text
POST /ingest
POST /semantic-map
POST /canonical/build
POST /validate
POST /export/{platform}
POST /translate
GET /deepseek-traces/{translation_id}
```

文本输入示例：

```json
{
  "source": {
    "file_name": "demo.txt",
    "source_id": "demo",
    "content_encoding": "utf-8",
    "content": "模拟总时长 5 年，按季度出报。"
  },
  "target_platform": "eclipse"
}
```

XLSX 必须把二进制内容编码为 base64。浏览器工作台自动处理，当前 PoC 输入限制为
16 MB。

### DeepSeek 调用 Trace

每次 `/translate` 对一个文件完成语义阶段后，应用把该文件触发的每一个真实 DeepSeek
HTTP 请求保存为：

```text
artifacts/deepseek_traces/{translation_id}.json
```

记录包括 block、初次调用/合同重试/输出重试/网络重试、尝试序号、耗时、HTTP 状态、
Token usage、完整请求 Payload（含 Prompt）和完整响应 Payload，但不包含 API key 和
Authorization header。可以用 `DEEPSEEK_TRACE_DIR` 将目录改到项目外的受控位置。

工作台转换结果中的“显示调用明细”按钮按需读取对应 Trace，并展示汇总表及每次请求/
响应详情。由于 Trace 含原始资料和模型返回内容，应按客户资料的最高保密等级管理并
定期清理。

### OCR 中间结果与后台任务

安装项目依赖时会安装 ReportLab，用于生成可见的 OCR 中间 PDF。既有环境可运行
`python -m pip install -e .` 更新。无需开启保存开关，OCR 路径默认保存至
`tmp/ocr_intermediates/`；可用 `RESERVOIR_OCR_ARTIFACT_DIR` 指定其他目录。

文件采用本地日期：`原始文件名_YYYY-MM-DD.pdf`，同名追加 `(2)`、`(3)`。
同名 `.raw.json` 和 `.manifest.json` 分别保存原始 OCR 输出与完成状态。保存采用
独占名称预留和临时文件原子发布；只有完成验证的 PDF 才开放浏览。服务不会自动清理这些文件。

PDF 显示 OCR 原始文字、模型区域自身的 HTML 表格、对应图片区域；依坐标排版，
不纠正数字、不做语义补全、不应用业务切片。排版字体、边框及字号是展示层重建，
不是原始版式的像素级复刻；原始 JSON 是完整识别证据。表格不按列表顺序配对。
Windows 默认嵌入宋体子集；其他平台可用 `RESERVOIR_OCR_PDF_FONT` 指定支持中文的
TrueType 字体。没有可嵌入字体时回退到 PDF 标准中文 CID 字体，由阅读器提供字体支持。

接口：

- `POST /translation-jobs`：请求体与 `/translate` 相同，返回 202、`task_id` 和 `status_url`。
- `GET /translation-jobs/{task_id}`：返回排队/运行/完成/失败状态、阶段、OCR 页进度；
  文件可用后包含 `ocr_intermediate`，完成后包含 `result`，失败后包含 `error`。
- `GET /ocr-intermediates/{artifact_id}/pdf`：内嵌浏览；加 `?download=true` 下载。
- `GET /ocr-intermediates/{artifact_id}/raw`：下载原始 JSON。
- `/translate` 同步接口仍然支持，并在响应中附带可用的 `ocr_intermediate`。

一个服务进程同时执行一个翻译任务，最多接受 8 个未完成任务，保留最近约 100 个任务状态。
部署此版本请使用单个 Uvicorn worker；OCR 在后台线程运行，使状态查询不被同步推理阻塞。
这不是推理内存限制或可强制终止的 OCR 子进程。任务状态在服务重启后丢失，已落盘文件及其
基于 manifest 的下载地址保留。浏览器刷新不会自动恢复正在执行的任务。

低置信度检查之前保存全份 OCR 结果。后续页模型失败时，已完成页面保存为 partial PDF，
界面明确显示原始页号与完成页数。保存失败返回 `PDF_OCR_ARTIFACT_SAVE_FAILED`，停止阶段为
“保存 OCR 中间结果”；已保存的 JSON 可用于诊断。未使用 OCR 的输入不产生中间文件。

验收覆盖原始 JSON/文字保真、表格关联、同名并发、低置信度前保存、部分失败保留、
语义运行期间预览、语义失败后下载，以及重启后文件访问：

```bash
python -m pytest tests/test_ocr_artifacts.py tests/test_pdf_ingestion.py tests/test_api.py tests/test_ui.py
```

### PDF / OCR 统一结构整理与切片

原生 PDF 和 OCR 的提取适配器均先输出未切分区域，再调用
`PdfParser._structure_and_chunk`，最后通过 `_materialize_blocks` 输出兼容的 `RawBlock`。
单栏按坐标恢复阅读顺序；明确的并排栏保留提取顺序并分开组织。
标题与后续正文先组成结构段，再统一按段落、句子和 token 预算切分。
续块携带 `section_title`，预算包含该标题；正文主证据不重叠。
表题和紧邻说明归属表格，所有表格使用同一按行组切分方法，重复保留列头和上下文。
表格或图片会中断正文段，图片仍为独立 figure 块。

`source_region.parts` 保留合并前区域的坐标、实际贡献文本片段及原区域内字符范围、
证据/上下文角色、置信度和质量标记；合并采用最低有效置信度与质量标记并集，
不会用平均值掩盖风险。其他块的 `document_structure` 不包含这些文本证据。
OCR 中间 PDF / raw JSON 仍在结构整理前保存，不重新解析重建 PDF，也不修改原始 OCR 内容。
当前标题与表题关联是确定性规则，不会修复 OCR 数字错误或推断缺失的表格列结构。

### Crop 级 OCR Lab

应用在同一端口提供 `/ocr-lab`，与 Translation Workbench、Ontology Explorer 并列。
OCR Lab 只接受 PNG、JPEG、WebP 或 TIFF 图片 crop，不接受 PDF，也不会调用 Ontology、
语义映射、Canonical 构建或平台导出。页面支持文件选择、拖放、剪贴板图片，以及在输入图像上
拖动画框后只运行所选子区域。

OCR Lab 不隐式选择 OCR 引擎。用户必须显式选择一个 composite baseline，或选择已经注册的
Layout、Text Detection、Text Recognition、Table 组件组合。项目引擎已注册独立的 Paddle
组件；原 PP-StructureV3 仍注册为 `paddle-ppstructure-v3` 整体基线。

低置信度 OCR 人工审查卡会显示“送到 OCR Lab 测试”入口。表格结构与区域置信度审查项
在同一次 OCR 审查会话中展示。该入口使用中间 clean-source PDF
按原 OCR DPI 重建区域图像，并在 provenance 中标记为非原始内存像素。OCR Lab 会读取页面、
原区域 bbox、扩边 bbox 和渲染 DPI；1× 以原文 100% 逻辑尺度显示，虚线框表示实际 OCR bbox。
缩放滑条范围为 0.5×–3×、步长 0.5×，拖动只更新浏览器预览，不运行 OCR；点击运行后同一个
倍率才用于模型输入重采样。直接上传或粘贴的 crop 保留原始字节；每次运行另存实际 crop、
预处理后图像、选择的引擎、参数、阶段耗时和规范化结果到 `tmp/ocr_lab/runs/<run-id>/`。
处理后图像另有像素预算检查，避免大 crop 在高倍率下耗尽内存。此功能用于单个问题区域的参数
诊断，不等同于批量 benchmark。

### 失败任务的 DeepSeek Trace

语义调用阶段结束后，无论契约校验成功还是失败，已捕获的调用日志都会落盘。
后台任务状态通过 `deepseek_trace` 发布日志入口；`/translate` 错误 detail 及后台失败任务的
error 也返回该摘要。网页在运行中或失败后均可浏览调用明细、初次/纠正重试、错误、
原始输入输出和易读日志，不要求 Canonical 构建或导出成功。
未产生 DeepSeek 调用的任务不显示该入口。重启后任务状态会丢失，落盘日志仍保留。

### PVT 语义完整性校验

同一来源块的 MAPPED 字段按 `fluids.<phase>.pvt.points[index]` 分组。
每个已创建的点必须有自己的 pressure；黏度等可选属性不强制要求。
缺失返回 `SEMANTIC_PVT_POINT_INCOMPLETE`，附缺失字段路径和来源块，进入现有契约纠正重试。
纠正只能依据当前块原文，不能借用其他相、点或来源块的压力；重试耗尽则停止语义处理。
table 和 constant 均遵守相同点契约，不自动填值或按下标合并跨块数据。
UI 将上述错误显示为“语义映射”，`CANONICAL_*` 显示为“Canonical 构建”。

## 8. 真实模型验收

单字段 smoke：

```bash
.venv/bin/python scripts/smoke_deepseek.py \
  --output artifacts/deepseek_semantic_smoke.json
```

完整综合 Demo：

```bash
.venv/bin/python scripts/evaluate_demo_deepseek.py
```

完整脚本必须同时通过：

1. 所有 source block 都得到可接受映射；
2. 没有 unresolved 或 review-required outcome；
3. Canonical 和 L1-L4 有效；
4. Golden 与 generated INCLUDE 都通过 OPM Parser 2025.10；
5. Parser-normalized keyword semantics 相等；
6. trace 证明 response model 与请求的 `deepseek-v4-flash` 一致。

输出目录默认是 `artifacts/demo_deepseek_evaluation/`，其中包含：

```text
semantic_mapping.json
canonical.json
validation.json
generated_eclipse.inc
opm_golden_comparison.json
provider_trace.json
run_summary.json
```

该目录被 Git 忽略。分享前仍应按输入资料的保密等级做人工检查。

## 9. 常见阻断

| 错误/现象 | 处理 |
|---|---|
| `SEMANTIC_PROVIDER_NOT_CONFIGURED` | 配置 DeepSeek 凭据或只使用确定性 endpoint |
| `DEEPSEEK_CREDENTIAL_UNAVAILABLE` | 检查 key 环境变量/文件存在性和权限，不打印 key 内容 |
| `SOURCE_MAPPING_NOT_CONFIGURED` | 检查 `source_system` 和 `mappings/customer_*.yaml` |
| `review_required` | 查看 unresolved、ambiguous 和低置信度项；补充上下文或受控映射 |
| `EXPORT_VALIDATOR_NOT_CONFIGURED` | 为目标平台注册 Mapper/L4 Validator |
| `OpmParserUnavailable` | 安装 `.[opm]` 或固定的 `opm==2025.10` |
| 本地源码测试通过但 CLI/import 失败 | 执行普通 wheel 重装并再次 smoke import/CLI |

## 10. Git 提交前检查

```bash
git diff --check
git status --short --ignored
git diff --cached --name-only
```

确认 staged 文件中不包含：

- `LLM/**/api_key*`；
- `.env` 或凭据；
- `artifacts/` 真实运行结果；
- `.venv/`、`build/`、`*.egg-info/`、coverage 和缓存文件；
- 未获授权的客户原始资料。
