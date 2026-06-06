"""PromptStore — section_type → prompt_fragment 不可变映射。

核心职责:
- 支持 PromptFragment 多片段注册 + 聚合查询（order/dispatch 双键调度）
- 支持 WILD_CARD (*) 全局片段（自动注入所有 section）
- body fallback：无专属片段的 section 自动回退到 body
- 内置提示词可选从目录加载（JSON 片段文件）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# WILD_CARD: 注册到 * 的片段会自动出现在所有 section 的输出中
WILD_CARD = "*"


# ---------------------------------------------------------------------------
# PromptFragment — 单个提示词片段
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptFragment:
    """单个提示词片段。可独立注册，由 section 的 get_full_prompt() 聚合。"""

    section_type: list[str]  # 归属 section 列表（如 ["body"] 或 ["*"]）
    template: list[str]      # 生效模板列表（如 ["thesis_v1"] 或 ["*"]）
    source: str        # "builtin" / "template:xxx" / "runtime:xxx" / "plugin:xxx"
    order: int         # 在提示词中的位置（升序排列）
    dispatch: int      # 同 order 冲突解决（高者胜出）
    content: str       # 片段内容
    enabled: bool = True


# ---------------------------------------------------------------------------
# PromptStore — 不可变注册表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptStore:
    """section_type → 提示词片段列表 的不可变映射。

    调度逻辑:
    - get_full_prompt(section) 合并 WILD_CARD (*) + section 专属片段
    - 无 section 专属片段时 fallback 到 body
    - 按 order 分组，组内按 dispatch 仲裁（高者胜出）
    """

    _fragments: dict[str, tuple[PromptFragment, ...]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 注册接口（不可变，返回新 PromptStore）
    # ------------------------------------------------------------------

    def register(self, fragment: PromptFragment) -> PromptStore:
        """注册片段。从 fragment.section_type 读取归属 section。"""
        updated = dict(self._fragments)
        for section in fragment.section_type:
            existing = updated.get(section, ())
            updated[section] = existing + (fragment,)
        return PromptStore(_fragments=updated)

    def register_many(self, fragments: list[PromptFragment]) -> PromptStore:
        """批量注册片段。"""
        if not fragments:
            return self
        store = self
        for f in fragments:
            store = store.register(f)
        return store

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def get_full_prompt(self, section_type: str, template_id: str = "*") -> str:
        """聚合该 section + template 的完整提示词。

        调度流程:
        1. 合并 WILD_CARD (*) + section_type 专属片段
        2. 无 section 专属片段时 fallback 到 body
        3. 按 order 分组，组内按 dispatch 仲裁（高者胜出）
        4. 按 order 升序拼接
        """
        # 收集候选片段
        candidates: list[PromptFragment] = []
        seen: set[int] = set()  # id(fragment) 去重

        def add_to_candidates(fragments: tuple[PromptFragment, ...]) -> None:
            for f in fragments:
                if not f.enabled:
                    continue
                fid = id(f)
                if fid in seen:
                    continue
                if template_id not in f.template and "*" not in f.template:
                    continue
                seen.add(fid)
                candidates.append(f)

        # WILD_CARD 全局片段
        add_to_candidates(self._fragments.get(WILD_CARD, ()))

        # section 专属片段
        section_frags = self._fragments.get(section_type, ())
        add_to_candidates(section_frags)

        # fallback 到 body（仅当无专属片段时）
        if not section_frags and section_type != "body":
            body_frags = self._fragments.get("body", ())
            add_to_candidates(body_frags)

        if not candidates:
            return ""

        # 按 order 分组，dispatch 仲裁
        order_groups: dict[int, list[PromptFragment]] = {}
        for f in candidates:
            order_groups.setdefault(f.order, []).append(f)

        resolved: list[PromptFragment] = []
        for order in sorted(order_groups.keys()):
            group = order_groups[order]
            if len(group) == 1:
                resolved.append(group[0])
            else:
                winner = max(group, key=lambda f: f.dispatch)
                resolved.append(winner)

        # 按 order 升序拼接
        resolved.sort(key=lambda f: f.order)
        return "\n\n".join(f.content for f in resolved)

    def get_fragments(self, section_type: str) -> tuple[PromptFragment, ...]:
        """返回指定 section 的所有已注册片段（不含 WILD_CARD）。"""
        return self._fragments.get(section_type, ())

    def list_sections(self) -> list[str]:
        """返回所有已注册 section 的列表。"""
        return sorted(self._fragments.keys())


# ---------------------------------------------------------------------------
# JSON 片段加载
# ---------------------------------------------------------------------------


def load_fragment_json(path: Path) -> PromptFragment:
    """从 JSON 文件加载单个 PromptFragment。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    return PromptFragment(
        section_type=data["section_type"],
        template=data.get("template", ["*"]),
        source=data.get("source", "builtin"),
        order=data["order"],
        dispatch=data.get("dispatch", 0),
        content=data["content"],
        enabled=data.get("enabled", True),
    )


def load_fragments_from_dir(dir_path: Path) -> list[PromptFragment]:
    """从目录递归加载所有 .json 文件作为 PromptFragment。

    目录结构示例:
        builtin/
          body/instruction.json
          abstract/examples.json
          _shared/xxx.json

    每个 JSON 文件包含一个 PromptFragment 的字段。
    """
    fragments: list[PromptFragment] = []
    if not dir_path.is_dir():
        return fragments
    for json_file in sorted(dir_path.rglob("*.json")):
        try:
            fragment = load_fragment_json(json_file)
            fragments.append(fragment)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("跳过无效片段文件 %s: %s", json_file, e)
    return fragments


def create_store_from_dir(dir_path: Path) -> PromptStore:
    """从目录加载所有 .json 片段文件创建 PromptStore。

    加载流程:
    1. 扫描目录下所有子目录的 .json 文件
    2. 每个 JSON 文件对应一个 PromptFragment（含 section_type）
    3. 按 section_type 展开，填充 _fragments dict
    """
    fragments = load_fragments_from_dir(dir_path)

    frag_dict: dict[str, tuple[PromptFragment, ...]] = {}
    for frag in fragments:
        for section in frag.section_type:
            existing = frag_dict.get(section, ())
            frag_dict[section] = existing + (frag,)

    return PromptStore(_fragments=frag_dict)


__all__ = [
    "PromptFragment",
    "PromptStore",
    "WILD_CARD",
    "load_fragment_json",
    "load_fragments_from_dir",
    "create_store_from_dir",
]
