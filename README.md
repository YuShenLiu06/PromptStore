# PromptStore

**不可变多租户提示词片段注册与聚合引擎**

将 LLM 提示词拆分为独立的 `PromptFragment`，按 section_type / template 注册到 `PromptStore`，查询时自动合并 WILD_CARD (`*`) 全局片段 + 专属片段，按 `order`/`dispatch` 双键调度输出。

## 安装

```bash
pip install prompt-store
# 或本地安装
pip install -e /path/to/PromptStore
```

**依赖**: 零外部依赖，仅标准库 (`dataclasses`, `json`, `pathlib`, `logging`)。

## 核心概念

| 概念 | 说明 |
|------|------|
| `PromptFragment` | 单个提示词片段：归属 section_type、生效 template、order/dispatch、内容 |
| `PromptStore` | 不可变注册表：`register()` 返回新实例，`get_full_prompt()` 聚合查询 |
| `WILD_CARD` (`*`) | 注册到 `*` 的片段自动注入所有 section |
| `order` / `dispatch` | 双键调度：按 order 分组，组内 dispatch 高者胜出 |
| body fallback | 无专属片段的 section 自动回退到 body |

## 快速开始

### 基本用法

```python
from prompt_store import PromptStore, PromptFragment, WILD_CARD

# 创建空 store
store = PromptStore()

# 注册全局片段
base = PromptFragment(
    section_type=[WILD_CARD],
    template=["*"],
    source="builtin",
    order=0,
    dispatch=0,
    content="你是一个专业的论文排版助手。",
)
store = store.register(base)

# 注册 body 专属片段
body_instruction = PromptFragment(
    section_type=["body"],
    template=["*"],
    source="builtin",
    order=10,
    dispatch=0,
    content="将以下 Markdown 正文转换为 LaTeX：\n{markdown}",
)
store = store.register(body_instruction)

# 查询聚合提示词
prompt = store.get_full_prompt("body")
print(prompt)
# 输出:
# 你是一个专业的论文排版助手。
#
# 将以下 Markdown 正文转换为 LaTeX：
# {markdown}
```

### 多片段注册

```python
abstract_inst = PromptFragment(
    section_type=["abstract"],
    template=["*"],
    source="builtin",
    order=10,
    dispatch=0,
    content="将以下摘要转换为 LaTeX：\n{abstract}",
)

abstract_examples = PromptFragment(
    section_type=["abstract"],
    template=["*"],
    source="builtin",
    order=20,
    dispatch=0,
    content="示例：\n\\begin{{abstract}}\n...\n\\end{{abstract}}",
)

store = store.register_many([abstract_inst, abstract_examples])
prompt = store.get_full_prompt("abstract")
# 输出按 order 升序拼接: order=0(WILD_CARD) → order=10(instruction) → order=20(examples)
```

### 模板维度过滤

```python
# 仅对 thesis_v1 模板生效的片段
thesis_only = PromptFragment(
    section_type=["body"],
    template=["thesis_v1"],
    source="template:thesis_v1",
    order=5,
    dispatch=10,  # dispatch 高于其他同 order 片段
    content="本模板使用 ctexbook 文档类。",
)
store = store.register(thesis_only)

# 查询时指定 template_id
prompt = store.get_full_prompt("body", template_id="thesis_v1")
# thesis_only 会被 include

prompt = store.get_full_prompt("body", template_id="other_template")
# thesis_only 不会被 include
```

### 同 order 冲突解决 (dispatch)

```python
# 两个片段在同一个 order，通过 dispatch 仲裁
default_inst = PromptFragment(
    section_type=["body"], template=["*"],
    source="builtin", order=10, dispatch=0,
    content="默认指令。",
)

template_override = PromptFragment(
    section_type=["body"], template=["thesis_v1"],
    source="template:thesis_v1", order=10, dispatch=100,
    content="模板专属指令（覆盖默认）。",
)

store = store.register_many([default_inst, template_override])

# 查询 thesis_v1: dispatch 100 > 0，template_override 胜出
prompt = store.get_full_prompt("body", template_id="thesis_v1")
```

### 从目录加载

```python
from pathlib import Path
from prompt_store import create_store_from_dir

store = create_store_from_dir(Path("./prompts/builtin"))
print(store.list_sections())  # ["abstract", "body", "cover", ...]
```

JSON 片段文件格式：

```json
{
  "section_type": ["abstract"],
  "template": ["*"],
  "source": "builtin",
  "order": 10,
  "dispatch": 0,
  "content": "将以下摘要转换为 LaTeX：\n{abstract}",
  "enabled": true
}
```

目录结构：

```
builtin/
├── _shared/          # 共享片段
│   └── system.json   # section_type: ["*"] → 全局生效
├── body/
│   └── instruction.json
├── abstract/
│   ├── instruction.json
│   ├── examples.json
│   └── auto_heading.json
└── references/
    ├── instruction.json
    └── examples.json
```

## API 参考

### `PromptFragment` (frozen dataclass)

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `section_type` | `list[str]` | 是 | 归属 section 列表（如 `["body"]` 或 `["*"]`） |
| `template` | `list[str]` | 否 | 生效模板列表（默认 `["*"]`） |
| `source` | `str` | 否 | 来源标签（默认 `"unknown"`） |
| `order` | `int` | 是 | 在提示词中的位置（升序排列） |
| `dispatch` | `int` | 否 | 同 order 冲突解决（高者胜出，默认 0） |
| `content` | `str` | 是 | 片段内容 |
| `enabled` | `bool` | 否 | 是否启用（默认 `True`） |

### `PromptStore` (frozen dataclass)

| 方法 | 返回 | 说明 |
|------|------|------|
| `register(fragment)` | `PromptStore` | 注册片段，返回新实例（不可变） |
| `register_many(fragments)` | `PromptStore` | 批量注册，返回新实例 |
| `get_full_prompt(section_type, template_id="*")` | `str` | 聚合该 section 的完整提示词 |
| `get_fragments(section_type)` | `tuple` | 返回指定 section 的所有已注册片段 |
| `list_sections()` | `list[str]` | 返回所有已注册 section 列表 |

### 工具函数

| 函数 | 说明 |
|------|------|
| `load_fragment_json(path)` | 从 JSON 文件加载单个 `PromptFragment` |
| `load_fragments_from_dir(dir_path)` | 递归加载目录下所有 .json 为 `list[PromptFragment]` |
| `create_store_from_dir(dir_path)` | 从目录直接创建 `PromptStore` |

### 常量

| 常量 | 值 | 说明 |
|------|------|------|
| `WILD_CARD` | `"*"` | 全局匹配符 |

## 调度规则详解

`get_full_prompt(section_type, template_id)` 的聚合流程：

```
1. 收集候选片段
   ├── WILD_CARD (*) 片段          （自动注入所有 section）
   ├── section_type 专属片段
   └── body 片段                   （仅当无专属片段时 fallback）

2. 过滤
   ├── enabled=False → 排除
   ├── id() 去重
   └── template_id 不匹配 → 排除

3. 按 order 分组
   ├── 同 order 只有 1 个 → 直接采用
   └── 同 order 有多个 → dispatch 仲裁（高者胜出）

4. 按 order 升序拼接 → 返回完整提示词
```

## 设计原则

- **不可变**: `register()` 返回新 `PromptStore`，原实例不变。安全用于多线程/异步环境
- **零依赖**: 仅使用 Python 标准库
- **扩展友好**: 通过 `source` 字段区分来源（builtin / template / plugin / runtime）
- **JSON 驱动**: 片段文件为纯 JSON，无需代码即可新增/修改提示词

## License

MIT