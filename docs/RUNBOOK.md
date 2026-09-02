# 本地运行与验收手册

## 1. 环境要求

- Python 3.12+
- macOS/Linux shell
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

## 4. Provider 配置

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
应用不会把凭据写进 trace；验收 artifact 也不保存 Prompt。

如果只需要 deterministic endpoint，可显式关闭 Provider：

```bash
export RESERVOIR_SEMANTIC_PROVIDER='disabled'
```

## 5. 启动 API 和工作台

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

## 6. API 运行方式

六个 endpoint：

```text
POST /ingest
POST /semantic-map
POST /canonical/build
POST /validate
POST /export/{platform}
POST /translate
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

## 7. 真实模型验收

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

## 8. 常见阻断

| 错误/现象 | 处理 |
|---|---|
| `SEMANTIC_PROVIDER_NOT_CONFIGURED` | 配置 DeepSeek 凭据或只使用确定性 endpoint |
| `DEEPSEEK_CREDENTIAL_UNAVAILABLE` | 检查 key 环境变量/文件存在性和权限，不打印 key 内容 |
| `SOURCE_MAPPING_NOT_CONFIGURED` | 检查 `source_system` 和 `mappings/customer_*.yaml` |
| `review_required` | 查看 unresolved、ambiguous 和低置信度项；补充上下文或受控映射 |
| `EXPORT_VALIDATOR_NOT_CONFIGURED` | 为目标平台注册 Mapper/L4 Validator |
| `OpmParserUnavailable` | 安装 `.[opm]` 或固定的 `opm==2025.10` |
| 本地源码测试通过但 CLI/import 失败 | 执行普通 wheel 重装并再次 smoke import/CLI |

## 9. Git 提交前检查

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
