# 基于大模型的油藏资料结构化与 ECLIPSE 输入文件自动生成工具 PoC 企划书

> 历史基线文档：保留最初的业务设想和成功标准；当前能力与边界以仓库 README、
> `docs/POC_CLOSURE.md` 和 `DESIGN_COMPLETENESS.md` 为准。

## 一、项目背景

油藏数值模拟前期资料通常来源复杂，包括实验室报告、PVT 分析、相渗数据、岩石物性、井控条件、生产制度等。这些资料往往以 PDF、Word、Excel、TXT 等形式存在，数据格式不统一，且同时包含自然语言描述、表格和工程单位。

在实际工作中，将这些资料整理为 ECLIPSE 可接受的输入格式，需要工程人员完成以下工作：

1. 从报告中识别有效数据；
2. 判断数据对应的油藏工程含义；
3. 进行字段整理和单位统一；
4. 映射到对应的 ECLIPSE Keyword；
5. 按 ECLIPSE 语法生成 `.DATA` 或 `.INC` 文件；
6. 检查数据是否完整、物理关系是否合理以及输入格式是否正确。

该过程具有较强的专业性，同时存在大量重复性的数据整理和格式转换工作。

因此，本项目计划开发一个轻量化工具，利用大语言模型完成非结构化油藏资料的理解和数据提取，再通过确定性的规则程序将结构化数据转换为 ECLIPSE 可接受的输入格式。

PoC 阶段不追求覆盖全部 ECLIPSE 功能，而是首先验证：

> **“非结构化油藏资料 → 大模型结构化提取 → 数据校验 → ECLIPSE INCLUDE 文件生成”这一技术路线是否稳定可行。**

---

## 二、项目目标

### 2.1 总体目标

建设一个可接入不同大语言模型的油藏资料自动转换工具，使用户能够上传包含油藏实验数据和生产制度的资料，系统自动完成：

**资料解析 → 数据识别 → 标准结构化 → 完整性检查 → ECLIPSE 格式转换。**

系统不绑定某一种大模型，并通过统一的模型接口支持后续接入：

- OpenAI 系列模型；
- DeepSeek；
- Qwen；
- Gemini；
- Claude；
- 本地部署 Llama 类模型；
- 其他兼容 OpenAI API 格式的模型。

### 2.2 PoC 阶段目标

PoC 第一阶段重点验证四类数据：

- 油水相对渗透率；
- PVT 数据；
- 岩石及流体基础物性；
- 井生产/注入控制条件。

并能够生成对应的 ECLIPSE INCLUDE 数据，包括：

- `SWOF`
- `PVDO/PVTO`
- `PVDG/PVTG`
- `PVTW`
- `DENSITY`
- `ROCK`
- `WCONPROD`
- `WCONINJE`

对于资料中缺失、无法确定或不足以生成某个 ECLIPSE Keyword 的信息，系统应明确提示，而不是利用大模型进行推测或补全。

---

# 三、PoC 示例数据

当前测试资料已经包含较完整的 PoC 验证条件。

例如，相渗实验部分给出了 `Sw、Krw、Krow、Pcow` 四类数据，可用于测试 `SWOF` 自动生成。

PVT 部分包含油相压力、体积系数、粘度数据，同时提供气相 PVT、水相 PVT、流体密度和岩石压缩系数，可用于测试 PVT、DENSITY 和 ROCK 等 Keyword 的生成。

生产制度部分则给出了 A15、B2 两口生产井的定液量控制和最低井底流压，以及 C1 注水井的注入量和最大井底压力，同时规定模拟总时长和报告步，可作为井控制条件提取的测试数据。

因此，该测试资料可以作为 PoC 阶段的第一个标准样例，用于验证从自然语言及表格数据到 ECLIPSE 输入文件的完整流程。

---

# 四、总体技术架构

系统总体采用：

> **大模型负责非确定性的资料理解，程序负责确定性的工程规则。**

整体架构如下：

```text
PDF / Word / Excel / TXT
          │
          ▼
    Document Parser
          │
          ▼
标准化文本 / 表格 / Metadata
          │
          ▼
      LLM Extractor
          │
          ▼
Reservoir Intermediate Representation
          │
          ▼
       Validator
          │
          ├── Schema 检查
          ├── 单位检查
          ├── 物理合理性检查
          └── 数据完整性检查
          │
          ▼
     ECLIPSE Compiler
          │
          ▼
     ECLIPSE .INC
```

其中，系统的核心并不是让大模型直接生成 ECLIPSE 文件，而是建立一套统一的：

## Reservoir Intermediate Representation，RIR

即“油藏模拟中间数据结构”。

---

# 五、核心设计原则

## 5.1 LLM 不直接生成最终 ECLIPSE 文件

不采用：

```text
原始报告
   ↓
LLM
   ↓
ECLIPSE DATA 文件
```

原因是这种模式要求模型同时完成：

- 报告理解；
- 数据提取；
- 工程语义判断；
- 单位转换；
- ECLIPSE Keyword 选择；
- ECLIPSE 语法生成；
- 格式检查。

多个任务耦合后，一个错误可能直接进入最终模拟输入文件。

PoC 采用：

```text
原始报告
   ↓
LLM
   ↓
标准 JSON
   ↓
Python Compiler
   ↓
ECLIPSE
```

这样可以大幅减少模型自由度。

---

# 六、Reservoir Intermediate Representation 设计

PoC 阶段首先建立统一 JSON Schema。

例如：

```json
{
  "relative_permeability": {
    "oil_water": {
      "data": [
        {
          "sw": 0.15,
          "krw": 0.0,
          "krow": 0.9,
          "pcow": 0.35
        }
      ],
      "pc_unit": "bar"
    }
  },

  "oil_pvt": {
    "pressure_unit": "bar",
    "bo_unit": "rm3/sm3",
    "viscosity_unit": "cP",
    "data": []
  },

  "water_pvt": {
    "reference_pressure": null,
    "bw": null,
    "compressibility": null,
    "viscosity": null
  },

  "rock": {
    "reference_pressure": null,
    "compressibility": null
  },

  "wells": [],

  "simulation": {}
}
```

模型只能向既定 Schema 填充数据，而不能自由决定输出结构。

### 该设计的优势

第一，模型可以替换。

只要新模型能够按照 JSON Schema 输出结果，就无需修改后续 ECLIPSE 转换程序。

第二，可以独立评估大模型性能。

例如分别评估：

- 字段识别准确率；
- 数值识别准确率；
- 单位识别准确率；
- 表格结构恢复准确率。

第三，可以脱离 ECLIPSE。

未来如果需要支持：

- CMG；
- tNavigator；
- INTERSECT；
- 自研油藏模拟器；

只需要增加新的 Compiler，而无需重新设计资料解析流程。

---

# 七、模型接入层设计

为实现模型无关性，在系统中设置统一 Model Adapter。

业务层只调用统一方法：

```python
result = llm.extract(
    document=document,
    schema=ReservoirSchema
)
```

底层可分别实现：

```text
OpenAIProvider

DeepSeekProvider

QwenProvider

GeminiProvider

ClaudeProvider

OllamaProvider

OpenAICompatibleProvider
```

统一接口可以设计为：

```python
class LLMProvider:

    def structured_generate(
        self,
        prompt,
        schema
    ) -> dict:

        ...
```

对于支持 OpenAI-compatible API 的模型，可通过配置：

```text
base_url
api_key
model_name
```

直接完成切换。

这样能够满足后续私有化部署、国产模型替换和模型性能对比需求。

---

# 八、数据来源追踪机制

PoC 阶段建议保留每个数据的来源信息。

例如：

```json
{
  "value": 500,
  "unit": "m3/day",

  "source": {
    "document": "demo_material.txt",
    "section": "生产制度",
    "text": "A15 与 B2 两口生产井按定液量 500 方/天生产"
  },

  "confidence": 0.98
}
```

这样可以在系统界面中实现：

```text
A15
生产方式：定液量

目标液量：
500 m3/day

来源：
生产制度

原文：
“A15 与 B2 两口生产井按定液量500方/天生产”
```

该机制主要解决工程应用中的数据可追溯问题。

系统不仅要回答：

> “提取出了什么数据？”

还应能够回答：

> “这个数据是从报告什么位置提取出来的？”

---

# 九、Validator 设计

数据从 LLM 输出后不能直接进入 ECLIPSE Compiler，需要通过确定性校验程序。

PoC 阶段设置四级验证。

## 9.1 Schema Validation

检查：

- 必填字段；
- 字段类型；
- 数组结构；
- 数值类型；
- Null 值。

例如：

```text
pressure 必须为 number

well.name 必须为 string

relative_permeability.data 必须为 array
```

---

## 9.2 Unit Validation

系统内部首先建立标准单位体系。

例如：

```text
Pressure → bar

Viscosity → cP

Rate → m3/day

Density → kg/m3

Compressibility → 1/bar
```

对于报告中出现：

```text
psi

MPa

STB/day

Pa·s
```

等单位，由确定性程序完成转换。

不依赖大模型自行换算。

---

## 9.3 Physical Validation

检查数据是否满足基本工程物理条件。

例如：

```text
0 ≤ Sw ≤ 1

Krw ≥ 0

Krow ≥ 0

Bo > 0

Bg > 0

Viscosity > 0

Pressure 数据顺序正确
```

对于相渗数据还可以检查：

```text
Sw 是否递增

Krw 是否总体递增

Krow 是否总体递减
```

异常数据不自动修改，而是产生 Warning。

---

## 9.4 ECLIPSE Dependency Validation

每一个 ECLIPSE Keyword 建立明确的数据依赖条件。

例如：

```text
SWOF

需要：
Sw
Krw
Krow
Pcow
```

满足条件即可生成。

而：

```text
COMPDAT
```

需要：

```text
well name

grid I

grid J

K1

K2
```

如果缺少这些信息，则：

```text
COMPDAT: NOT READY

Missing:

I coordinate
J coordinate
Completion interval
```

禁止大模型自动补全。

---

# 十、ECLIPSE Compiler

ECLIPSE Compiler 不调用 LLM。

它是一个完全确定性的 Python 模块。

例如：

```text
Reservoir JSON
      ↓
Keyword Mapper
      ↓
Syntax Generator
      ↓
.INC
```

PoC 可以按照不同 Keyword 分模块：

```text
eclipse/
│
├── swof.py
├── pvt.py
├── density.py
├── rock.py
├── well_control.py
└── compiler.py
```

例如相渗数据：

```json
{
  "sw": 0.15,
  "krw": 0.0,
  "krow": 0.9,
  "pcow": 0.35
}
```

自动生成：

```text
SWOF

-- SW      KRW      KROW     PCOW
   0.15     0.000    0.900    0.35
   0.30     0.048    0.552    0.18
   0.50     0.205    0.251    0.07
   0.70     0.512    0.048    0.02
   0.90     0.800    0.000    0.00
/
```

这样 ECLIPSE 语法是否正确，不再依赖模型训练知识。

---

# 十一、PoC 功能范围

为了避免第一阶段范围过大，PoC 建议只支持以下功能。

## 输入格式

第一阶段支持：

```text
TXT
Excel
Word
文本型 PDF
```

扫描 PDF 和 OCR 可在后续版本扩展。

---

## 数据类型

### 1. Relative Permeability

识别：

```text
Sw
Krw
Krow
Pcow
```

输出：

```text
SWOF
```

---

### 2. Oil / Gas / Water PVT

识别：

```text
Pressure

Bo / Bg / Bw

Viscosity

Compressibility
```

根据数据类型生成：

```text
PVDO / PVTO

PVDG / PVTG

PVTW
```

---

### 3. Rock / Fluid Property

识别：

```text
Rock Compressibility

Reference Pressure

Oil Density

Water Density

Gas Density
```

生成：

```text
ROCK

DENSITY
```

---

### 4. Well Control

识别：

```text
Producer / Injector

Oil Rate

Liquid Rate

Water Injection Rate

BHP Limit
```

生成：

```text
WCONPROD

WCONINJE
```

---

# 十二、PoC 暂不支持范围

第一阶段暂不考虑自动生成：

```text
GRID

COORD

ZCORN

ACTNUM

PORO

PERMX/PERMY/PERMZ
```

以及：

```text
WELSPECS

COMPDAT
```

除非输入资料明确给出完整的：

```text
I/J/K

井轨迹

射孔层段

井网信息
```

因为这些信息通常来自模型网格文件、井轨迹或其他结构化数据，而不是一般油藏实验报告。

PoC 首先解决：

> **“实验和工程报告 → ECLIPSE 参数文件”**

而不是直接解决：

> **“任意资料 → 完整 ECLIPSE 模型”。**

---

# 十三、系统界面设计

PoC 第一版可采用 Streamlit 实现。

界面主要分为四部分。

## 1. 文件上传

```text
上传油藏资料

[选择文件]

支持：
PDF / Word / Excel / TXT
```

---

## 2. 模型配置

```text
LLM Provider

OpenAI
DeepSeek
Qwen
Custom API

Model:
xxx

API Base URL:
xxx

API Key:
********
```

---

## 3. 数据识别结果

系统完成解析后显示：

```text
数据识别结果

Relative Permeability      ✓

Oil PVT                    ✓

Gas PVT                    ✓

Water PVT                  ✓

Rock                       ✓

Density                    ✓

Well Control               ✓

Well Location              ✗

Completion                 ✗
```

用户可以继续查看：

```text
原始文档

提取数据

标准 JSON

数据来源
```

---

## 4. ECLIPSE 输出

例如：

```text
ECLIPSE Conversion

SWOF.INC              ✓

PVT.INC               ✓

ROCK.INC              ✓

SCHEDULE.INC          ✓

WELSPECS               -

COMPDAT                -
```

同时显示：

```text
Warnings

井坐标信息缺失。

Completion interval 缺失。

当前无法生成 COMPDAT。
```

---

# 十四、建议的软件结构

```text
reservoir_converter/
│
├── app.py
│
├── parsers/
│   ├── txt_parser.py
│   ├── pdf_parser.py
│   ├── docx_parser.py
│   └── excel_parser.py
│
├── llm/
│   ├── base.py
│   ├── openai.py
│   ├── openai_compatible.py
│   └── ollama.py
│
├── schema/
│   ├── reservoir.py
│   ├── fluid.py
│   ├── relperm.py
│   ├── rock.py
│   └── well.py
│
├── extraction/
│   ├── extractor.py
│   └── prompts.py
│
├── validators/
│   ├── schema_validator.py
│   ├── unit_validator.py
│   ├── physics_validator.py
│   └── completeness_validator.py
│
├── converters/
│   └── units.py
│
├── eclipse/
│   ├── swof.py
│   ├── pvt.py
│   ├── density.py
│   ├── rock.py
│   ├── schedule.py
│   └── compiler.py
│
└── tests/
    ├── demo_material.txt
    ├── expected.json
    └── expected_eclipse/
```

---

# 十五、PoC 开发阶段划分

## Phase 1：Reservoir Schema

首先确定中间结构，而不是首先写 Prompt。

需要完成：

- 数据字段定义；
- 单位定义；
- Required / Optional 字段定义；
- 数据来源字段定义；
- Pydantic / JSON Schema。

成果：

```text
ReservoirSchema V0.1
```

---

## Phase 2：LLM Structured Extraction

实现：

```text
TXT
   ↓
LLM
   ↓
Reservoir JSON
```

首先使用当前 demo_material.txt 作为测试文件。

测试重点：

```text
数据有没有漏

数字有没有错

单位有没有错

字段有没有映射错误

模型会不会自行添加不存在的数据
```

---

## Phase 3：Validator

实现：

```text
Schema Validator

Unit Validator

Physics Validator

Completeness Validator
```

输出：

```text
PASS

WARNING

ERROR

MISSING
```

---

## Phase 4：ECLIPSE Compiler

优先开发：

```text
SWOF

PVTW

PVDO

PVDG

DENSITY

ROCK

WCONPROD

WCONINJE
```

并为每个 Keyword 建立 unit test。

---

## Phase 5：Web PoC

使用 Streamlit 完成：

```text
上传文件

选择模型

执行识别

查看 JSON

查看 Warning

生成 ECLIPSE

下载 INC 文件
```

---

## Phase 6：模型对比测试

分别选择：

```text
云端强模型

国产 API 模型

本地小模型
```

使用完全相同的数据和 Schema 测试。

比较：

```text
字段准确率

数值准确率

单位准确率

完整率

幻觉率

调用时间

Token 消耗

成本
```

从而判断该任务实际上需要多大的模型。

---

# 十六、PoC 评价指标

建议不采用“感觉生成结果不错”作为验收标准，而是建立明确指标。

## 结构化提取

### Numeric Accuracy

```text
正确提取数字数量
──────────────
应提取数字数量
```

---

### Field Mapping Accuracy

判断数据是否映射到了正确字段。

例如：

```text
0.45 cP
```

必须进入：

```text
water.viscosity
```

不能误进入：

```text
oil.viscosity
```

---

### Unit Accuracy

判断：

```text
数值

单位

标准单位转换
```

是否正确。

---

### Hallucination Rate

重点统计：

> 原始资料不存在，但模型自行产生的数据数量。

PoC 阶段该指标应重点控制。

---

### ECLIPSE Generation Accuracy

通过 Golden File 测试：

```text
实际输出
vs
人工准备标准 ECLIPSE 文件
```

Compiler 部分理论上应该达到接近 100% 的确定性一致。

---

# 十七、PoC 成功标准

PoC 可以设定以下最低成功条件：

1. 当前测试文档中的主要实验和生产制度数据可以稳定提取；
2. 同一文档使用不同模型能够输出兼容的 Reservoir Schema；
3. 不存在的井位、完井等信息不会被模型自动生成；
4. 相渗、PVT、ROCK 和 Well Control 可以由确定性程序转换为正确 ECLIPSE Keyword；
5. 所有生成的数据能够追踪回原始资料；
6. 系统能够明确区分：
   - 已提取；
   - 缺失；
   - 存疑；
   - 无法生成；
7. 替换 LLM Provider 时，Validator 和 ECLIPSE Compiler 不需要修改。

---

# 十八、后续扩展方向

PoC 验证通过后，可以逐步扩展为完整的油藏模拟资料转换平台。

## V2：更多 ECLIPSE Keyword

支持：

```text
SCAL

PVT

SOLUTION

SUMMARY

SCHEDULE
```

更多数据类型。

---

## V3：井轨迹与完井

接入：

```text
Well trajectory

Completion

Perforation

Grid mapping
```

生成：

```text
WELSPECS

COMPDAT
```

---

## V4：网格及静态模型数据

接入：

```text
GRDECL

Corner-point grid

Petrophysical property
```

---

## V5：多模拟器支持

整体架构演变为：

```text
                       → ECLIPSE Compiler

报告
   ↓
Reservoir IR           → CMG Compiler

                       → tNavigator Compiler

                       → INTERSECT Compiler

                       → 自研模拟器
```

---

# 十九、项目核心价值

该项目表面上解决的是：

> **油藏报告自动转换成 ECLIPSE 输入文件。**

但从系统架构上，更重要的是建立：

> **非结构化油藏资料与油藏模拟软件之间的标准数据层。**

通过 Reservoir Intermediate Representation，可以将：

```text
自然语言报告
实验数据
历史技术文档
Excel 表格
```

统一转换成机器可理解的油藏工程数据。

在这一架构下：

```text
LLM = 数据理解层

Reservoir Schema = 标准数据层

Validator = 工程约束层

Compiler = 模拟器适配层
```

模型可以不断替换，模拟器也可以不断扩展，而系统核心数据层保持稳定。

因此，PoC 阶段的重点不应是让大模型写出一个看起来正确的 ECLIPSE 文件，而应该首先验证：

> **是否能够建立一条稳定、可验证、可追溯、模型无关的“资料 → Reservoir IR → ECLIPSE”工程链路。**

这将作为后续向生产级油藏数据 Agent、模拟器 Agent 以及一体化油藏智能建模工具扩展的基础。
