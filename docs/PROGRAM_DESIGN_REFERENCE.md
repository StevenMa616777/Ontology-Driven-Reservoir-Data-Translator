# 程序设计与代码结构参考

> 状态：当前实现参考（2026-09-08）  
> 适用版本：`reservoir-data-translator 0.1.0`  
> 范围：当前仓库中的 Python 服务、Ontology/Mapping 配置、浏览器工作台、脚本与测试；PDF ingestion 仅作为拟议扩展，不表示已经实现。

## 1. 文档定位

本文回答四类问题：

1. 当前程序由哪些层和模块组成；
2. 一份资料在系统中如何流动；
3. 各代码文件、主要类、函数和方法分别负责什么；
4. 在不破坏现有边界的前提下，PDF ingestion 应从哪里接入。

本文描述的是**当前代码事实**。架构原则和最初设计见 [`../DESIGN.md`](../DESIGN.md)，实现完成度与生产边界见 [`../DESIGN_COMPLETENESS.md`](../DESIGN_COMPLETENESS.md)，安装、启动和验收命令见 [`RUNBOOK.md`](RUNBOOK.md)。当这些文档与代码不一致时，应先核对代码和测试，再更新状态类文档。

函数条目只说明职责、输入输出、调用位置和重要失败条件，不解释函数内部每条语句。

## 2. 总体结构

系统以平台无关的 Canonical Model 为中心，把源资料理解和模拟器文件生成分开：

```text
SourceInput
    ↓
Ingestion ──→ RawDocument / RawBlock
    ↓
Ontology Retrieval + Source Mapping
    ↓
Semantic Model Provider ──→ SemanticMappingBatch
    ↓                         │
Review Gate  ←────────────────┘
    ↓ accepted mappings only
CanonicalBuilder ──→ ReservoirSimulationModel
    ↓
ValidationEngine ──→ L1 Schema → L2 Ontology → L3 Domain → L4 Export
    ↓
PlatformMapper ──→ PlatformIntermediateModel ──→ target text
```

核心责任边界：

| 层 | 输入 | 输出 | 主要责任 | 明确不负责 |
|---|---|---|---|---|
| Ingestion | 文件或上传内容 | `RawDocument` | 保留格式结构和来源位置 | 油藏语义判断 |
| Ontology | YAML 定义 | `OntologyRegistry` | 公司语义、关系、单位和约束 | 客户数据值、平台语法 |
| Retrieval | `RawBlock` | 候选 Concept | 用 alias、关键词和 Source Mapping 找受控候选 | 作最终语义决定 |
| Semantic | block、候选、Provider | `SemanticMappingBatch` | 在受控合同内解释资料 | 自由创造 Concept、Path 或单位 |
| Canonical | 已接受 mapping | `ReservoirSimulationModel` | 单位归一、实体归组、稳定建模 | 平台渲染、缺失值猜测 |
| Validation | Canonical/原始 payload | `ValidationResult` | 分层阻断结构和业务错误 | 静默修复硬错误 |
| Mapper | 已通过必要校验的 Canonical | 中间模型和文本 | 确定性平台映射与渲染 | 调用 LLM、修改 Canonical |
| API/UI | 请求和用户操作 | HTTP/页面结果 | 编排、审查、展示和 trace | 持久化审批与生产任务调度 |

## 3. 仓库级代码地图

| 路径 | 当前用途 |
|---|---|
| `src/reservoir_data_translator/ingestion/` | TXT、JSON、CSV、XLSX 的格式级解析 |
| `src/reservoir_data_translator/ontology/` | Ontology 加载、Convention、Registry 和定义校验 |
| `src/reservoir_data_translator/semantic/` | 候选检索、Source Mapping、Provider、DeepSeek 和语义合同 |
| `src/reservoir_data_translator/canonical/` | Canonical 数据模型、映射合同、Builder 和 JSON Schema |
| `src/reservoir_data_translator/validation/` | L1-L4 校验、物理值遍历和 OPM 对比 |
| `src/reservoir_data_translator/mappers/` | Eclipse/CMG 中间映射、导出校验和文本渲染 |
| `src/reservoir_data_translator/api/` | FastAPI 请求模型、服务对象、endpoint 和 trace 持久化 |
| `src/reservoir_data_translator/ui/` | 无构建步骤的工作台和 Ontology Explorer |
| `ontology/` | manifest、Convention 和各领域 Concept YAML |
| `mappings/` | 客户 Source Mapping 与目标平台 Mapping |
| `example/` | 冻结的综合 Demo 输入和 Eclipse Golden |
| `scripts/` | 真实 DeepSeek smoke 与综合验收脚本 |
| `tests/` | 单元、契约、集成、API、UI 和 OPM 回归测试 |

## 4. 启动和依赖装配

默认应用由 `api/main.py` 中的 `create_app()` 创建。启动时完成以下装配：

1. 定位并加载 `ontology/`；
2. 加载 `mappings/customer_*.yaml`、`eclipse.yaml` 和 `cmg.yaml`；
3. 按环境变量配置 Semantic Provider；
4. 建立 `PipelineServices`、Validator 和 Mapper Registry；
5. 注册 API endpoint；
6. 挂载工作台和 Ontology Explorer 静态资源。

当前状态保存在进程和单次请求中。除本地 DeepSeek trace 文件外，没有数据库、队列、对象存储或持久化 review 状态。

## 5. 一次完整转换的调用链

`POST /translate` 的主要路径如下：

1. `TranslateRequest` 校验 HTTP 输入；
2. `PipelineServices.ingest()` 解码上传内容并调用 `parse_document()`；
3. 格式 Parser 生成 `RawDocument`；
4. `PipelineServices.semantic_map()` 创建 Retriever 和 `SemanticMappingAgent`；
5. Agent 为 block 检索候选、构建受控 prompt、调用 Provider 并复核返回合同；
6. unresolved、空结果或低置信度结果触发 review gate；
7. `CanonicalBuilder.build()` 只使用被接受的 `SemanticMapping`；
8. `ValidationEngine.validate()` 顺序执行 L1-L4；
9. 对应 `PlatformMapper.export()` 先校验，再 map，最后 render；
10. API 返回 source、mapping、Canonical、validation、target 和阶段 trace。

分阶段 endpoint `/ingest`、`/semantic-map`、`/canonical/build`、`/validate` 和 `/export/{platform}` 使用相同的核心服务，不维护另一套业务逻辑。

## 6. Ingestion

### 6.1 设计边界

Ingestion 只回答“源资料在格式上是什么结构、来自哪里”，不回答“字段在油藏工程上是什么意思”。例如 Parser 可以识别表头 `Rate`，但不能在这一层把它映射为某一种井控制量。

当前支持：

| 格式 | Parser | 输出方式 |
|---|---|---|
| `.txt` | `TextParser` | 非空段落形成 text block |
| `.json` | `JsonParser` | 叶节点形成 key/value block；同构对象数组可形成 table block |
| `.csv` | `CsvParser` | 一个结构一致的 table block |
| `.xlsx` | `ExcelParser` | 每个非空 worksheet 形成一个 table block |

当前不支持 PDF、DOCX、OCR、加密文件和流式大文件。

### 6.2 `ingestion/base.py`

#### `IngestionError`

表示输入无法由选定 Parser 安全表示。保留机器可读 `code` 和可选 `path`，供 API 转换为稳定错误响应。

#### `DocumentParser`

所有格式 Parser 的抽象基类。

- `parse(path, source_id=None) -> RawDocument`：格式 Parser 必须实现的统一入口。
- `_source_path(path) -> Path`：检查文件存在性和后缀是否属于当前 Parser。
- `_source_id(path, source_id) -> str`：校验显式来源 ID，未提供时暂以文件名代替。

### 6.3 `ingestion/models.py`

#### `RawBlock`

一个可寻址的格式级内容单元。当前字段为 `block_id`、`block_type`、`content` 和字符串形式的 `source_location`。

- `validate_content_shape()`：保证 text、table、key/value 三种 block 的内容结构与类型一致。
- `searchable_text() -> str`：把 block 稳定投影为文本，供 Retriever 和 prompt 使用。
- `_validate_table(content)`：检查 columns、rows 和行宽。
- `_validate_key_value(content)`：检查 key/value block 只含合法键和值。

#### `RawDocument`

表示尚未进行语义和单位解释的完整源文件。

- `block_ids_are_unique()`：保证一个文档内的 block ID 不重复。

### 6.4 `ingestion/__init__.py`

- `_PARSERS`：后缀到 Parser 类型的注册表。
- `parse_document(path, source_id=None) -> RawDocument`：按文件后缀路由到对应 Parser；未知后缀返回结构化 ingestion 错误。

### 6.5 格式 Parser

#### `text_parser.py`

- `TextParser.parse()`：读取 UTF-8 文本，按非空段落生成 block，并保存行号范围。
- `TxtParser`：`TextParser` 的兼容别名。

#### `csv_parser.py`

- `CsvParser.parse()`：读取 CSV，将首行作为 columns、后续行作为 rows；无法形成一致表格时失败，不把不规则行静默丢弃。

#### `json_parser.py`

- `_json_path_key(key) -> str`：生成可用于来源路径的 JSON key 表示。
- `_homogeneous_object_table(value)`：判断对象数组能否无损表示为同构表格。
- `JsonParser.parse()`：递归保留 JSON 叶节点路径，并对适合的对象数组生成 table block。

#### `excel_parser.py`

- `ExcelParser.parse()`：以只读方式处理 workbook，为每个非空 sheet 生成表格 block。
- `_non_empty_bounds()`：计算 worksheet 实际使用的非空矩形区域。
- `XlsxParser`：`ExcelParser` 的兼容别名。

### 6.6 Ingestion 的主要测试

- `tests/test_ingestion.py`：block 合同、来源位置、四种 Parser 和后缀路由。
- `tests/test_api.py`：文本上传、base64 XLSX、大小限制和 endpoint 集成。
- `tests/test_cross_source_consistency.py`：不同格式最终形成等价业务 Canonical 的端到端证据。

## 7. Ontology

### 7.1 配置结构

`ontology/ontology_v0.1.yaml` 是 manifest，引用 `conventions_v0.1.yaml` 和 `concepts/*.yaml`。领域文件当前包括 rock、fluid、scal、well、schedule 和 condition。

Ontology 保存平台/客户无关的 Concept、层级、aliases、value type、dimension、canonical unit、constraints 和 relationships。客户特有字段名应进入 Source Mapping，而不是污染公司 Ontology。

### 7.2 `ontology/convention.py`

#### 数据类型

- `RelationshipRule`：关系名称、允许的源/目标类型及逆关系规则。
- `SuspiciousAliasPattern`：识别可能混入客户值或示例值的 alias。
- `OntologyConvention`：Ontology 受控词汇和结构规则的不可变表示。

#### 方法

- `OntologyConvention.from_mapping(document)`：从 YAML mapping 构建并校验 Convention。
- `_require_string()`、`_string_list()`：解析 Convention 时使用的严格字段辅助检查。

### 7.3 `ontology/loader.py`

- `OntologyLoadError`：manifest、YAML 或引用结构无法安全加载。
- `OntologyMetadata`：Ontology 名称、版本和 manifest 信息。
- `LoadedOntology`：加载后的 metadata、Convention、Concept 和来源文件集合。
- `OntologyLoader.load(path)`：解析 manifest、Convention 与全部 Concept 文件，并检查引用目标。
- `_resolve_manifest()`：允许从目录或 manifest 文件开始加载。
- `_resolve_child_path()`：安全解析 manifest 中的子文件。
- `_read_yaml()`：读取 YAML 并要求顶层 mapping。
- `_validate_manifest()`：检查 manifest 必填结构。
- `_validate_concept_document()`、`_validate_concept_payload()`：检查 Concept 文件的基本形状。

### 7.4 `ontology/models.py`

- `_freeze_mapping()`：把嵌套 mapping 转换为只读结构。
- `OntologyConcept`：单个 Concept 的不可变运行时模型。
- `OntologyConcept.from_mapping()`：从已加载配置构造 Concept，并保留来源文件信息。

### 7.5 `ontology/registry.py`

#### `OntologyRegistry`

运行时查询入口，建立 Concept、alias 和 relationship 索引。

- `load(path) -> OntologyRegistry`：通过 Loader 创建 Registry。
- `get_concept(concept_id)`：按 ID 获取 Concept；未知 ID 明确失败。
- `search_by_alias(text)`：进行大小写和常见分隔符归一后的 alias 查询。
- `get_relationships(concept_id, relationship=None)`：读取一个 Concept 的声明关系。
- `validate_relationship(source, relationship, target)`：检查关系名、端点和声明是否有效。
- `list_concepts()`：返回稳定排序的 Concept 列表。
- `__len__()`：返回 Concept 数量。

### 7.6 `ontology/validator.py`

Ontology Definition Validator 验证定义本身，而不是验证业务实例。

- `ValidationSeverity`：ERROR、WARNING、INFO 严重级别。
- `OntologyIssue`：单个定义问题，`to_dict()` 提供结构化输出。
- `OntologyValidationResult`：聚合问题，提供 `valid`、`errors`、`warnings`、`infos` 和 `to_dict()`。
- `OntologyValidator.validate()`：运行完整定义检查。
- `_validate_identity_and_references()`：ID、parent、引用和循环。
- `_validate_concept()`：单个 Concept 基础合同。
- `_validate_lifecycle()`：状态、弃用和迁移目标。
- `_validate_dimension_and_unit()`：dimension 与 canonical unit 词汇。
- `_validate_constraints()`：约束字段和值。
- `_validate_aliases()`、`_validate_cross_concept_aliases()`：alias 形状、重复和冲突。
- `_validate_relationships()`、`_validate_inverse_relationships()`：关系词汇、端点和逆关系。
- `_validate_source_pollution()`：客户值或来源特有内容污染检查。
- `_validate_tables()`：表格 Concept 的坐标和依赖变量合同。
- `main(argv=None) -> int`：`ontology-validate` CLI 入口。

### 7.7 Ontology 的主要测试

`test_ontology_loading.py`、`test_ontology_alias_lookup.py`、`test_ontology_relationships.py` 和 `test_ontology_validator.py` 分别覆盖加载、查询、关系和定义治理。

## 8. Semantic Mapping

### 8.1 `semantic/source_mapping.py`

Source Mapping 保存特定客户或来源系统的术语到 Concept 的受控提示，不存业务数据值。

- `SourceMappingEntry`：一个来源术语及目标 Concept。
- `SourceMappingDefinition`：一个来源系统的完整 YAML 合同。
- `SourceMappingMatch`：查询命中及匹配信息。
- `SourceMappingRegistry.load()`：从 YAML 加载 Registry。
- `source_system`：返回该 Registry 所属来源系统。
- `search(text)`：按归一文本查询匹配，结果供 Retriever 排序。

### 8.2 `semantic/retriever.py`

- `OntologyCandidate`：交给 Agent 的受控候选，包含 Concept、得分和命中理由。
- `concept_id`、`name`：候选的便捷属性。
- `as_prompt_dict()`：生成允许进入 prompt 的候选合同。
- `OntologyRetriever.retrieve(block)`：综合 alias、关键词和 Source Mapping，为整个 block 排序候选。
- `retrieve_concepts(text)`：对文本执行概念级检索。
- `_normalize()`、`_compact()`、`_tokens()`：确定性文本归一和 token 辅助函数。

### 8.3 `semantic/models.py`

- `SemanticMapping`：合法的 MAPPED 结果，包含 Concept、Canonical Path、值、单位、confidence 和 provenance。
- `UnmappedSemanticMapping`：无合法映射的显式结果。
- `AmbiguousSemanticMapping`：多个候选仍无法确定的显式结果。
- `SemanticMappingBatch`：一个文档的结果集合。
- `mapped()`：返回所有 MAPPED 项。
- `unresolved()`：返回 UNMAPPED/AMBIGUOUS 项。
- `review_required()`：根据 unresolved、空映射和 confidence 阈值判断是否必须审查。
- `accepted_with_warning()`、`auto_accepted()`：区分低置信度和自动接受项。
- `MappedMappingDraft`、`UnmappedMappingDraft`、`AmbiguousMappingDraft`：Provider 输出的受限草稿模型。
- `SemanticModelResponse`：Provider 一次结构化响应的 envelope。
- `_validate_provenance_block()`：保证 evidence 仍指向原始 block。

### 8.4 `semantic/provider.py`

- `SemanticProviderError`：Provider 失败的稳定错误类型和 code。
- `SemanticModelProvider`：所有模型 Provider 的抽象接口。
- `provider_name`：Provider 的可观察名称。
- `structured_generate(prompt, response_model)`：返回符合指定 Pydantic Schema 的结构化结果。
- `record_contract_failure()`：让 Provider trace 记录网络调用后发生的业务合同失败。

### 8.5 `semantic/deepseek.py`

- `DeepSeekCallTrace`：一次调用、重试、token、耗时、响应状态和本地修正信息。
- `capture_deepseek_traces()`：在当前上下文收集调用 trace。
- `DeepSeekProvider.from_environment()`：从环境配置 endpoint、model、key 和重试策略。
- `structured_generate()`：提交 Responses 风格的 JSON Schema 请求并验证返回模型。
- `_post_with_retry()`：对允许重试的网络、状态码或结构错误执行有界重试。
- `_validated_output()`：执行 JSON 解码与 Pydantic 验证。
- `_emit_trace()`、`_mark_latest_trace()`：维护 trace 状态。
- `record_contract_failure()`：记录 Agent 侧合同拒绝。
- `_decode_structured_json()`：执行保守的结构化 JSON 解码。
- `_extract_single_json_value()`、`_balanced_json_end()`、`_remove_trailing_commas()`：只修复确定性的包装或格式问题，不做语义修复。

### 8.6 `semantic/mapping_agent.py`

#### `SemanticMappingAgent`

Semantic 层的核心安全门。

- `map_document(document)`：依次处理文档 block 并聚合 batch。
- `map_block(document, block)`：检索候选、调用 Provider、验证并实例化一个 block 的结果。
- `_buildable_candidates()`：只保留存在 Canonical Mapping Contract 的候选。
- `_build_prompt()`：将 block、候选、允许的 Path/Unit 和值合同写入 prompt。
- `_required_structural_parent()`：识别 PVT/SCAL 等必须同时存在的结构父项。
- `_structurally_ordered_candidates()`：把结构父项放在子项前，减少不完整映射。
- `_value_contract()`：描述 Concept 允许的标量、枚举或结构值形状。
- `_correction_prompt()`：在返回违反合同时生成受限重试提示。
- `_validate_structured_response()`：验证返回项数量、候选范围和 provenance。
- `_materialize()`：把 Provider 草稿转换为最终 mapping 类型。
- `_validate_structural_value()`：验证表格/PVT/SCAL 等结构值。
- `_validate_mapping_relationships()`：检查 well type、control 等 Ontology 关系。
- `_validate_mapping_completeness()`：检查必需结构父项、重复 Path 等批次合同。
- `_automatic_unmapped()`：没有候选时不调用 Provider，直接形成 UNMAPPED。
- `_provenance()`、`_source_field()`：从 RawBlock 构建可追溯 evidence。

### 8.7 `semantic/unit_normalizer.py`

- `UnitNormalizationError`：单位归一错误基类。
- `UnsupportedUnitError`：源或目标单位不在受控词汇。
- `IncompatibleUnitError`：源和目标维度不相容。
- `InvalidMagnitudeError`：数值不是有限数字。
- `UnitNormalizer.supported_units()`：列出当前显式支持的单位词汇。
- `normalize(value, source_unit, target_unit)`：使用确定性规则和明确时间政策换算。
- `_resolve()`：把别名解析为受控 Pint 单位。

### 8.8 Semantic 的主要测试

`test_ontology_retriever.py`、`test_semantic_mapping.py`、`test_semantic_mapping_agent.py`、`test_deepseek_provider.py` 和 `test_unit_normalizer.py` 覆盖候选排序、review gate、合同防护、重试/trace 和单位换算。

## 9. Canonical Model

### 9.1 `canonical/models.py`

`CanonicalModel` 是严格 Pydantic 基类，统一禁止未知字段并提供稳定序列化。主要模型：

| 模型 | 作用 |
|---|---|
| `Provenance` | 保存 source、block、location、raw text 和识别信息 |
| `PhysicalValue` | 数值、canonical unit、Concept、confidence 和 provenance |
| `RelativePermeabilityPoint/Model` | SCAL 相对渗透率点和表 |
| `PVTPoint/PVTModel` | 压力相关流体属性点和 PVT 集合 |
| `RockModel` | 岩石属性 |
| `FluidPhaseModel/FluidSystemModel` | oil/water/gas 及流体系统 |
| `SCALModel` | 相渗模型集合 |
| `WellConstraint/WellControl/WellModel` | 井、控制方式和约束 |
| `SimulationSchedule` | 模拟时长和报告间隔 |
| `ReservoirSimulationModel` | Canonical 根模型 |

模型层只保证结构、类型、枚举和有限数值；更晚的 Ontology/领域规则由 Validation 层负责。

### 9.2 `canonical/mapping_contract.py`

- `CanonicalMappingContract`：某个 Concept 可写入的 Canonical Path 模板及值类型合同。
- `accepts(canonical_path)`：判断具体 Path 是否符合模板。
- `get_canonical_mapping_contract(concept_id)`：取得 Concept 的外部映射合同。
- `accepts_canonical_path(concept_id, canonical_path)`：便捷检查 Concept/Path 是否一致。

该文件是 Semantic Agent 和 Canonical Builder 的共同边界，防止两层各自维护不同的允许路径。

### 9.3 `canonical/builder.py`

- `CanonicalBuildError`：mapping 无法安全构建时的结构化错误。
- `CanonicalBuilder.build(mappings, schema_version)`：验证每项 mapping、归一单位、按 selector 归组、稳定排序并构造根模型。
- `_mapping_value()`：把 mapping 的值转换成符合 Canonical 目标的数值或结构。
- `_parse_path()`：解析包含集合 selector 的 Canonical Path。
- `_validate_selector_semantics()`：检查 selector 与 Concept/值的业务含义相符。
- `_assign()`：沿解析后的 Path 写入中间文档。
- `_merge_assignment()`：阻止重复证据被静默覆盖。
- `_materialize()`：将 selector mapping 转换为稳定排序的列表结构。
- `_selector_sort_key()`：定义确定性集合顺序。
- `_add_collection_defaults()`：补充允许的空集合默认值，而不是猜测业务数据。

### 9.4 `canonical/schema.py`

- `generate_json_schemas()`：为主要 Canonical 模型生成分组 JSON Schema。
- `write_json_schemas(output_dir)`：以确定性格式写出 Schema artifact。

### 9.5 Canonical 的主要测试

`test_canonical_models.py`、`test_canonical_schema.py` 和 `test_canonical_builder.py` 覆盖严格模型、Schema 稳定性、单位归一、顺序无关构建及冲突阻断。

## 10. Validation

### 10.1 分层策略

| 层 | 实现 | 目的 |
|---|---|---|
| L1 | `SchemaValidator` | 类型、必填字段、枚举、未知字段和有限数值 |
| L2 | `OntologyInstanceValidator` | Concept、unit、applicability 和 relationship |
| L3 | `DomainValidator` | 物理边界、表格关系和经验趋势 |
| L4 | `ExportValidator` + Mapper | 目标平台当前是否能安全输出 |

后续层不会在前一层存在硬错误时继续执行。warning 可保留，但 error 必须阻断相应下游。

### 10.2 `validation/models.py`

- `ValidationIssue`：code、level、path 和 message。
- `ValidationResult`：`valid` 与 issues 集合。
- `derive_valid_from_errors()`：由 error 列表推导有效性，防止调用者声明矛盾状态。
- `merge(*results)`：按阶段合并多组校验结果。

### 10.3 `validation/schema_validator.py`

- `format_location()`：把 Pydantic 错误位置转换成可寻址 Path。
- `SchemaValidator.validate()`：只返回 L1 结果。
- `validate_with_model()`：同时返回 L1 结果和成功构建的 Canonical Model，供 Engine 避免重复解析。

### 10.4 `validation/ontology_validator.py`

- `OntologyInstanceValidator.validate(model)`：检查 PhysicalValue 的 Concept、canonical unit、所属对象和关系。
- `_applies_to()`：检查 property 是否适用于目标实体。
- `_same_or_descendant()`：允许 Ontology 层级中的合法子类型。

### 10.5 `validation/domain_validator.py`

- `DomainValidator.validate(model)`：运行数值约束、表格长度/坐标、实体重复和相渗趋势检查。
- `_constraint_violation()`：把 Ontology constraint 与实际值比较并生成 issue。
- `_validate_relperm_trends()`：将强物理错误和经验性趋势 warning 分开。

### 10.6 `validation/export_validator.py`

- `PlatformExportValidator`：目标平台校验接口，要求 `target_platform` 和 `validate_export()`。
- `ExportValidator.validate(model, target_platform)`：按平台注册表委托 L4；未知平台明确返回不可导出结果。

### 10.7 `validation/engine.py`

- `ValidationEngine.validate(payload, target_platform=None)`：顺序执行 L1、L2、L3 和可选 L4，并在必要位置 fail closed。

### 10.8 `validation/traversal.py`

- `PhysicalObservation`：一个带模型位置和所属实体上下文的 `PhysicalValue` 视图。
- `iter_physical_values(model)`：稳定遍历 Canonical 中所有物理量，供多个 Validator 共用。

### 10.9 `validation/opm_parser.py`

- `validate_eclipse_include(content)`：把 INCLUDE 放入最小 host deck，用固定 OPM Parser 验证并提取规范化语义。
- `compare_eclipse_includes(golden, generated)`：比较 Golden 和生成内容的规范化 keyword 值。
- `_minimal_host_deck()`：建立仅用于 Parser 验证的最小上下文。
- `_extract_keyword()` 及数值辅助函数：把 OPM 对象转成稳定可比较结构。

OPM Parser 成功只证明语法和 Golden 语义对比，不等同于真实 Flow/商业模拟器运行。

## 11. Platform Mappers

### 11.1 `mappers/models.py`

- `PlatformToken`：渲染前的单个 token 及 quoted 信息。
- `PlatformRecord`：一条平台记录。
- `PlatformBlock`：同一 keyword 下的记录集合。
- `PlatformIntermediateModel`：目标平台的可检查中间表示。
- `PlatformExportResult`：平台、validation、中间模型和最终文本。

### 11.2 `mappers/registry.py`

- `PlatformMappingEntry/Definition`：平台 YAML Mapping 合同。
- `PlatformMappingRegistry.load()`：从 YAML 加载。
- `from_mapping()`：从内存 mapping 构建并校验。
- `platform`、`dialect`：目标平台及方言标识。
- `target_for(concept_id)`：取得 Concept 的目标 keyword。
- `supports(concept_id)`：查询平台是否声明支持某 Concept。

### 11.3 `mappers/base.py`

- `PlatformMappingError`：导出未就绪或映射失败。
- `PlatformMapper.map()`：Canonical 到平台中间表示。
- `render()`：中间表示到文本，不包含 Canonical 业务判断。
- `export()`：先 `validate_export()`，再 map 和 render。
- `PlatformMapperRegistry.get(platform)`：按名称获取 Mapper。
- `list_platforms()`：列出已配置平台。

### 11.4 `mappers/eclipse/mapper.py`

- `EclipseDemoMapper.validate_export()`：检查当前 Eclipse PoC 所需数据、表格和限制。
- `map()`：生成 SWOF、PVDO、PVDG、PVTW、DENSITY、ROCK、WCONPROD、WCONINJE 和 TSTEP 等中间 block。
- `render()`：把中间 block 渲染为 METRIC INCLUDE。
- `_time_steps()`：把 duration/report interval 转换为确定性 TSTEP 序列。
- `_render_token()`：处理数值、空值和引号。
- `_has_exportable_content()`：避免生成形式正确但无业务内容的文件。

### 11.5 `mappers/cmg/mapper.py`

- `CMGDemoMapper.validate_export()`：检查当前 demo well-control 片段是否可输出，并明确拒绝未实现的非井域声明。
- `map()`：将 Canonical 井控转换成 IMEX-style 中间记录。
- `render()`：生成 demo 文本。
- `_has_nonwell_data()`：识别当前 CMG demo 不支持的数据域。

CMG 输出尚未冻结到一个经验证的产品/版本语法，不能声明为可运行完整数据文件。

## 12. API 和服务编排

### 12.1 `api/models.py`

| 模型 | 用途 |
|---|---|
| `SourceInput` | 文件名、内容、utf-8/base64 编码和可选 source ID |
| `SemanticMapRequest` | RawDocument 和可选 source system |
| `CanonicalBuildRequest` | 被接受 mappings 和 schema version |
| `ValidateRequest` | Canonical 和可选 target platform |
| `ExportRequest` | 待导出的 Canonical |
| `TranslateRequest` | 完整流水线输入 |
| `TargetArtifact/ExportResponse` | 导出结果 |
| `TranslationTraceEvent` | 阶段、状态和说明 |
| `DeepSeekTraceSummary` | 调用、重试、修正、token 和日志链接摘要 |
| `TranslateResult` | 完整流水线 response envelope |

### 12.2 `api/service.py`

- `MAX_SOURCE_BYTES`：当前单次输入 16 MiB 上限。
- `SemanticProviderNotConfigured`：未配置语义 Provider。
- `UnknownSourceSystemError`：请求了未加载的客户 Source Mapping。
- `UnconfiguredSemanticModelProvider`：确定性 endpoint 可运行时使用的显式占位 Provider。
- `PipelineServices.__init__()`：装配 Registry、Provider、Mapper、Validator、Builder 和 Source Mapping。
- `ingest(source)`：检查安全文件名，解码内容，限制大小，经临时文件调用格式 Parser，并恢复原始文件名。
- `semantic_map(document, source_system=None)`：按来源系统选择 Source Mapping，创建 Retriever 和 Agent。
- `build_canonical(mappings, schema_version)`：委托 Canonical Builder。

### 12.3 `api/main.py`

- `_configured_path()`：从环境变量或默认目录定位配置。
- `_default_services()`：加载 Registry、Mappings 和 Mapper，构造默认服务。
- `_default_semantic_provider()`：根据环境配置 DeepSeek 或保持未配置状态。
- `_trace_root()`：取得 DeepSeek trace 保存目录。
- `_persist_deepseek_trace()`：保存结构化 trace 和可读日志。
- `_readable_deepseek_log()`：生成面向调试的文本日志。
- `create_app()`：建立 FastAPI 应用、静态页面、依赖和全部 route。

`create_app()` 内部 endpoint：

| Route | 函数 | 作用 |
|---|---|---|
| `GET /` | `workbench()` | 返回主工作台 |
| `GET /ontology` | `ontology_explorer()` | 返回 Ontology Explorer |
| `GET /api/ontology/graph` | `ontology_graph()` | 返回运行时节点和关系图 |
| `GET /api/ontology/concepts/{id}` | `ontology_concept()` | 返回单个 Concept 详情 |
| `POST /ingest` | `ingest()` | 只执行格式解析 |
| `POST /semantic-map` | `semantic_map()` | 执行受控语义映射 |
| `POST /canonical/build` | `canonical_build()` | 构建 Canonical |
| `POST /validate` | `validate()` | 执行 L1-L4 |
| `POST /export/{platform}` | `export()` | 校验并导出目标文本 |
| `GET /deepseek-traces/{id}` | `deepseek_trace()` | 读取结构化 trace |
| `GET /deepseek-traces/{id}/readable` | `readable_deepseek_trace()` | 读取文本 trace |
| `POST /translate` | `translate()` | 编排完整流水线和 review gate |

- `_http_error()`：把内部 code/message 转换成统一 HTTPException。

## 13. 浏览器 UI

### 13.1 工作台

`ui/index.html` 和 `styles.css` 提供上传、目标平台选择、阶段 rail、mapping review、Canonical、validation、输出和 trace 区域。

`ui/app.js` 的主要函数组：

- 输入：`setSelectedFile()`、`fileToSource()`、`openSelectedFile()`；
- HTTP：`postJson()`、`getJson()`、`getText()`；
- 状态：`setRunning()`、`updateRail()`、`renderLoading()`、`renderError()`；
- 审查：`reviewState()`、`renderMapping()`、`renderReview()`、`continueAfterReview()`；
- 结果：`renderBlocks()`、`renderValidationCard()`、`renderTrace()`、`renderResult()`；
- DeepSeek：`renderDeepSeekTraceShell()`、`renderDeepSeekTraceDetail()`；
- 用户操作：`downloadText()`、`copyWithFeedback()`、`wireResultActions()`；
- 主流程：`runTranslation()`。

低置信度 MAPPED 项可以在当前页面会话中确认；UNMAPPED 和 AMBIGUOUS 不能被前端强制改成合法 mapping。

### 13.2 Ontology Explorer

`ontology.html`、`ontology.css` 和 `ontology.js` 构成与 Demo 文件分离的只读浏览页。

- `buildControls()`：建立 domain 和 relationship 过滤器；
- `renderTree()`：显示层级树；
- `layout()`、`renderGraph()`：计算并绘制 SVG 图；
- `selectConcept()`、`renderDetail()`：选择节点并展示属性、约束和关系；
- `renderSearch()`：按 ID、名称、说明和 alias 搜索；
- `fitGraph()`、`updateTransform()`：处理自适应、缩放和平移；
- `init()`：加载 `/api/ontology/graph` 并初始化页面。

### 13.3 全局可读性

`ui-preferences.js` 的 `setReadableText()` 在 localStorage 中保存文本放大偏好。页面通过共享 class/变量让设置同时影响静态区域和动态渲染结果。

## 14. 配置和 Mapping

### 14.1 Source Mapping

`mappings/customer_a.yaml`、`customer_b.yaml` 和 `customer_c.yaml` 记录客户词汇到公司 Concept 的映射提示。它们只参与候选检索，不直接写入 Canonical，也不应反向进入 Ontology aliases。

### 14.2 Platform Mapping

`mappings/eclipse.yaml` 和 `cmg.yaml` 记录 Concept 到目标 keyword 的声明。Mapper 仍负责记录结构、顺序、缺省策略和渲染；YAML 不是通用模板引擎。

### 14.3 环境配置

- `RESERVOIR_ONTOLOGY_PATH`：Ontology 根目录或 manifest。
- `RESERVOIR_MAPPING_PATH`：Mapping 目录。
- `RESERVOIR_SEMANTIC_PROVIDER`：语义 Provider 选择。
- DeepSeek 相关 endpoint、model、key、timeout 和 retry 环境项由 `DeepSeekProvider.from_environment()` 读取。

未配置 Provider 时，Ontology、Ingestion、Canonical、Validation 和 Mapper 等确定性能力仍可使用；语义 endpoint 明确返回未配置错误。

## 15. 测试与功能对应

| 测试文件 | 主要覆盖 |
|---|---|
| `test_ingestion.py` | Raw 模型和四类 Parser |
| `test_ontology_*.py` | 加载、alias、关系、检索和定义校验 |
| `test_semantic_mapping*.py` | mapping 状态、候选约束、结构合同和 provenance |
| `test_deepseek_provider.py` | HTTP、JSON、重试、trace 和本地修正边界 |
| `test_unit_normalizer.py` | 单位词汇、维度和确定性换算 |
| `test_canonical_*.py` | 模型、Schema 和 Builder |
| `test_validation.py` | L1-L4 与短路行为 |
| `test_platform_mappers.py` | map/render 分离和 Eclipse/CMG 输出边界 |
| `test_opm_parser.py` | Eclipse Golden 的 OPM 解析与规范化比较 |
| `test_api.py` | 分阶段/完整 endpoint、review stop 和 trace |
| `test_ui.py` | 工作台、Ontology Explorer 和静态资源合同 |
| `test_cross_source_consistency.py` | 三种客户输入的 Canonical 等价性 |

新增功能时，应同时在本表对应的测试域中增加证据，避免只更新实现或只更新文档。

## 16. 失败与审查路径

系统使用显式状态而不是“尽量继续”：

- 文件不存在、后缀不支持、编码/结构非法：`IngestionError`；
- Provider 未配置或请求失败：`SemanticProviderError` 或明确未配置错误；
- 无候选：UNMAPPED，不调用 Provider；
- 多候选无法确定：AMBIGUOUS；
- 低置信度 MAPPED：`review_required`；
- Concept/Path/Unit/provenance/relationship 合同不一致：Agent 拒绝并进行有界重试；
- mapping 冲突或重复 evidence：`CanonicalBuildError`；
- L1-L3 error：阻断平台输出；
- L4 不满足或未知平台：不生成目标 artifact；
- Mapper 不可导出：`PlatformMappingError`。

本地 JSON 格式修正只有在 JSON、Schema 和后续业务合同都成立时，才可被记作避免了一次网络重试。

## 17. 当前实现边界

当前版本是可审查的 PoC，而不是生产文档处理平台。重要边界包括：

- 没有 PDF/DOCX/OCR；
- 没有持久化 review、mapping 编辑和 replay；
- 没有作业队列、对象存储、权限和认证；
- Entity Resolution 只按明确 selector 归组；
- Semantic Gold 数据集和准确率指标尚未完成；
- Eclipse INCLUDE 尚未在真实 host deck 中运行模拟器；
- CMG 只证明扩展边界，没有冻结产品版本语法；
- RawDocument 主要面向小型结构化/半结构化文件，不包含复杂页面布局模型。

## 18. PDF ingestion 拟议扩展

> 本节是设计建议，不是当前实现。任何类名、字段或阶段只有在代码和测试合入后才能移入“当前实现”章节。

### 18.1 为什么不能只增加 `.pdf` 后缀

PDF 是页面描述格式。同一个文件可能同时包含：

- 可直接提取的文本；
- 扫描图像；
- 多栏、浮动标题和页眉页脚；
- 有线/无线表格和跨页表格；
- 图像、曲线和图例；
- 文本层与页面视觉顺序不一致的内容。

因此 `PdfParser` 不应简单返回整篇文本。它必须保留页级证据、阅读顺序、提取方式和必要的布局信息，否则 Semantic Mapping 即使得到正确数值，也无法提供可靠 provenance。

### 18.2 建议的分阶段范围

#### Phase PDF-1：原生文本 PDF

- 支持未加密、具有可用文本层的 PDF；
- 生成页级/段落级 text block；
- 保存页码和提取顺序；
- 检测而不处理扫描页；
- 不承诺复杂表格重建。

#### Phase PDF-2：布局与表格

- 引入结构化页面位置；
- 处理多栏顺序、重复页眉页脚；
- 提取表格并保存单元格/页范围；
- 为跨页表格定义合并证据和置信状态。

#### Phase PDF-3：OCR 与混合页面

- 对扫描页按明确策略调用 OCR；
- 标记 `native_text`、`ocr` 或 `hybrid` 来源；
- 保存 OCR 置信度和图像区域；
- 低质量 OCR 必须进入 review，而不是伪装成确定文本。

图表数字化、曲线识别和公式理解应视为后续独立能力，不自动包含在“支持 PDF”中。

### 18.3 建议先冻结的数据合同

当前 `RawBlock.source_location: str | None` 对 TXT/CSV 足够，但对 PDF 可审计性偏弱。建议先评估新增结构化位置模型，例如：

```text
SourceLocation
├── locator_type       # text_lines / json_path / sheet_range / pdf_region
├── page               # 1-based PDF page
├── page_end           # 跨页内容可选
├── bbox                # 可选页面坐标
├── sheet               # Excel 可选
├── cell_range          # Excel 可选
├── line_start/end      # Text 可选
└── display             # 面向 UI 的稳定字符串
```

兼容策略可以是新增结构化字段并暂时保留 `source_location` 展示字符串，待调用方迁移后再考虑收紧旧字段。不要直接把任意 PDF 元数据塞入 `content`，否则 Retriever、Provider prompt 和 UI 都会依赖不稳定字典。

还需要决定是否扩展 `BlockType`。建议只有当下游确实需要区别 `image`、`figure` 或 `page` 时才新增类型；第一阶段可继续使用 text/table，并通过明确的 metadata/location 表达页面证据。

### 18.4 建议组件边界

```text
PdfParser
    ↓
PDF capability inspection
    ↓
native extraction ─────┐
layout/table extraction├─→ normalized RawBlock assembly
OCR adapter (later) ───┘
    ↓
RawDocument
```

- `PdfParser`：实现 `DocumentParser`，协调 PDF 子能力并输出统一 RawDocument；
- capability inspection：识别加密、页数、文本层覆盖率和扫描页；
- extractor adapter：隔离具体 PDF/OCR 库，避免库对象泄漏到核心模型；
- block assembler：负责稳定 block ID、顺序、去重和 provenance；
- policy/config：定义页数、文件大小、OCR、超时和部分失败策略。

Ingestion 层仍然不做油藏语义判断。OCR 只把视觉字符转换为候选文本，不决定某个数字是不是压力、饱和度或井控制值。

### 18.5 对现有模块的影响点

| 模块 | 需要评估的变化 |
|---|---|
| `ingestion/base.py` | Parser 是否需要从 path 扩展为 stream，错误 code 如何细分 |
| `ingestion/models.py` | 结构化 location、metadata、提取方式和 block 类型 |
| `ingestion/__init__.py` | 注册 `.pdf` 和可选依赖不可用错误 |
| `api/models.py` | PDF 继续使用 base64，或改为 multipart/upload artifact |
| `api/service.py` | 16 MiB 限制、临时文件生命周期、处理超时和资源上限 |
| `semantic/retriever.py` | 页眉噪声、长 block 和表格文本投影 |
| `semantic/mapping_agent.py` | block batching、上下文窗口和同页/跨页父子关系 |
| `canonical/models.py` | `Provenance` 是否引用结构化 source location |
| `ui/app.js` | 页面预览、页码跳转、OCR/低质量提示 |
| trace | 记录解析器、页数、提取方式、warning 和耗时，不记录敏感全文 |

### 18.6 必需的失败代码和策略

至少应区分：

- `PDF_ENCRYPTED`；
- `PDF_INVALID`；
- `PDF_DEPENDENCY_UNAVAILABLE`；
- `PDF_PAGE_LIMIT_EXCEEDED`；
- `PDF_NO_EXTRACTABLE_CONTENT`；
- `PDF_OCR_REQUIRED`；
- `PDF_EXTRACTION_TIMEOUT`；
- `PDF_PARTIAL_EXTRACTION`。

是否接受 partial extraction 必须是显式政策。若接受，应把缺失页和 warning 带入 RawDocument/trace，并在进入 Semantic 前设置可审查状态；不能只在日志中提示后继续生成目标文件。

### 18.7 PDF 测试矩阵

建议建立不含客户敏感信息的小型固定 fixture：

| 类别 | 最低验证 |
|---|---|
| 单页原生文本 | 文本、页码、block 顺序和 raw evidence |
| 多页文本 | page boundary 和稳定 block ID |
| 双栏 | 阅读顺序策略 |
| 简单表格 | columns/rows 和来源区域 |
| 跨页表格 | 合并或显式不支持行为 |
| 扫描 PDF | 明确返回 OCR_REQUIRED |
| 混合 PDF | 页级 capability 和 partial policy |
| 加密/损坏 PDF | 稳定错误 code |
| 超大/超页数 | 资源门限 |
| 重复页眉页脚 | 保留/过滤规则和可追溯性 |
| API base64 | 上传、大小和临时文件清理 |
| 端到端 | PDF → RawDocument → controlled mapping，不要求一开始生成完整 deck |

第一批验收应聚焦“格式解析正确且可追溯”，不要用最终 Eclipse 文件是否生成来代替 PDF ingestion 自身的质量评价。

### 18.8 建议实施顺序

1. 用真实但可公开/脱敏的 PDF 样本建立需求分类；
2. 冻结 page/block/provenance 合同；
3. 通过小型技术试验比较 PDF 库，但不让库类型进入领域模型；
4. 实现 Phase PDF-1 和独立 ingestion 测试；
5. 接入 `/ingest`，观察 RawDocument，不立即接 LLM；
6. 评估 block 大小、页眉噪声和 Retriever 行为；
7. 接入 Semantic Mapping 并增加 PDF provenance 合同测试；
8. 再推进布局表格和 OCR；
9. 根据实现结果更新本参考文档与 `DESIGN_COMPLETENESS.md`。

## 19. 文档维护规则

为了让本文以后能够转换为网页文档，应保持以下规则：

- 使用稳定且唯一的标题；
- 路径、类和函数使用代码格式；
- “当前实现”和“拟议扩展”不得混写；
- 新增/删除公开类或函数时同步更新对应章节；
- 架构边界改变时先更新 `DESIGN.md`，再更新本文的代码映射；
- 验收证据改变时更新 `DESIGN_COMPLETENESS.md`；
- 命令和运维流程只在 `RUNBOOK.md` 维护，本文只链接；
- 每个模块保留测试入口，方便检查说明是否过期；
- 未来拆分时，以本文件的二级章节作为网页导航单元。

建议在重要版本发布前执行一次文档审计：枚举 `src/` 的模块、公开类/函数、API route 和测试文件，与本文目录逐项比对。
