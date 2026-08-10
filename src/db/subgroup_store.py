"""字幕组（release group）字典，JSON 持久化。

每个组记录区分两个名称概念：

- ``rename_to`` — 重命名后输出到文件名的名称（规范化后的名称）。
- ``aliases`` — 可被识别的名称列表（含中文/英文等变体，用于从文件名中匹配该组）。

数据结构（``subgroups.json``）::

    {
      "groups": {
        "NekomoeKissaten": {
          "rename_to": "NekomoeKissaten",
          "aliases": ["喵萌", "喵萌字幕组"],
          "source": "llm",            # llm | manual | seed
          "added": "2026-08-09"
        }
      }
    }

- ``source`` 记录该组是来自 LLM 识别、手动干预还是种子数据。
- ``recognize(text)`` 在 ``aliases`` 及主键中做大小写/特殊字符无关的模糊匹配，
  返回内部主键；``display_name(key)`` 再取回该组应写入文件名的名称。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from .json_store import JsonStore


def normalize(name: str) -> str:
    """归一化组名：小写并去除标点/空格/下划线等非字母数字字符（保留中文），
    用于模糊匹配。"""
    return re.sub(r"[\W_]+", "", name.lower())


class SubgroupStore(JsonStore):
    """字幕组字典存储。"""

    DEFAULT: Dict = {"groups": {}}

    def __init__(self, path: Optional[Path] = None) -> None:
        path = path or Path(__file__).resolve().parent / "subgroups.json"
        super().__init__(path, default_data=self.DEFAULT)

    # ── 查询 ──────────────────────────────────────────────────────────────

    def names(self) -> List[str]:
        return list(self.data["groups"].keys())

    def get(self, name: str) -> Optional[Dict]:
        return self.data["groups"].get(name)

    def has(self, name: str) -> bool:
        return name in self.data["groups"]

    def size(self) -> int:
        return len(self.data["groups"])

    # ── 识别 ──────────────────────────────────────────────────────────────

    def recognize(self, text: str) -> Optional[str]:
        """在 ``text`` 中查找可被识别的名称（``aliases`` + 主键 + ``rename_to``），
        返回内部主键；未命中返回 ``None``。

        既支持整段完全匹配，也支持作为较长文本的子串出现（如
        ``[NekomoeKissaten 1920x1080]``），以去除标点/括号后的归一化文本做匹配，
        避免因分隔符差异而漏判。
        """
        if not text:
            return None
        n_text = normalize(text)
        if not n_text:
            return None
        for key, meta in self.data["groups"].items():
            rename_to = meta.get("rename_to") or key
            candidates = [key, rename_to, *meta.get("aliases", [])]
            for cand in candidates:
                n_cand = normalize(cand)
                if not n_cand:
                    continue
                if n_cand == n_text:
                    return key
                # 名称至少 3 个字符才做子串匹配，避免过短的名称误命中
                if len(n_cand) >= 3 and n_cand in n_text:
                    return key
        return None

    def display_name(self, name: str) -> str:
        """返回该组「重命名后」应写入文件名的名称（``rename_to``）；未配置时回退为主键。"""
        meta = self.data["groups"].get(name)
        if meta:
            return meta.get("rename_to") or name
        return name

    # ── 增删改 ────────────────────────────────────────────────────────────

    def add(
        self,
        name: str,
        aliases: Optional[List[str]] = None,
        source: str = "manual",
        rename_to: Optional[str] = None,
    ) -> bool:
        """新增一个组。

        - ``name``: 内部唯一标识（主键）。
        - ``rename_to``: 重命名后输出到文件名的名称；省略时等于 ``name``。
        - ``aliases``: 可被识别的名称列表（用于从文件名中匹配该组）。

        若已存在则合并别名并保留原来源，返回 ``False``。
        """
        if not name:
            return False
        clean_aliases = list(dict.fromkeys(a for a in (aliases or []) if a))
        output = rename_to or name
        with self._lock:
            groups = self.data["groups"]
            existing = groups.get(name)
            if existing is not None:
                existing.setdefault("aliases", [])
                for a in clean_aliases:
                    if a not in existing["aliases"]:
                        existing["aliases"].append(a)
                existing.setdefault("rename_to", output)
                existing.setdefault("source", source)
                self.save()
                return False
            groups[name] = {
                "rename_to": output,
                "aliases": clean_aliases,
                "source": source,
                "added": date.today().isoformat(),
            }
            self.save()
            return True

    def add_alias(self, name: str, alias: str) -> bool:
        """为已有组补充一个别名。"""
        if not name or not alias or name not in self.data["groups"]:
            return False
        with self._lock:
            meta = self.data["groups"][name]
            meta.setdefault("aliases", [])
            if alias not in meta["aliases"]:
                meta["aliases"].append(alias)
                self.save()
            return True

    def update(
        self,
        name: str,
        rename_to: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        source: Optional[str] = None,
    ) -> bool:
        """更新已有组的信息（只覆盖传入的字段）。"""
        if name not in self.data["groups"]:
            return False
        with self._lock:
            meta = self.data["groups"][name]
            if rename_to is not None:
                meta["rename_to"] = rename_to or name
            if aliases is not None:
                meta["aliases"] = list(dict.fromkeys(a for a in aliases if a))
            if source is not None:
                meta["source"] = source
            self.save()
            return True

    def remove(self, name: str) -> bool:
        with self._lock:
            if self.data["groups"].pop(name, None) is not None:
                self.save()
                return True
            return False
