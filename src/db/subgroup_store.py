"""字幕组（release group）字典，JSON 持久化。

每个组记录区分两个名称概念：

- ``rename_to`` — 重命名后输出到文件名的名称（简称，规范化后的名称）。
- ``aliases`` — 可被识别的名称列表（含中文/英文等变体，用于从文件名中匹配该组）。

数据结构（``subgroups.json``）::

    {
      "groups": {
        "NekomoeKissaten": {
          "rename_to": "NekomoeKissaten",
          "aliases": ["喵萌", "喵萌字幕组"],
          "added": "2026-08-09"
        }
      }
    }

- ``recognize(text)`` 在 ``aliases``、``rename_to`` 及主键中做大小写/特殊字符无关的模糊匹配，
  返回内部主键；``display_name(key)`` 再取回该组应写入文件名的名称。
"""

from __future__ import annotations

import logging
import re
import shutil
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from .json_store import JsonStore
from .paths import app_data_dir, is_frozen

logger = logging.getLogger("db.subgroups")


def normalize(name: str) -> str:
    """归一化组名：小写并去除标点/空格/下划线等非字母数字字符（保留中文），
    用于模糊匹配。"""
    return re.sub(r"[\W_]+", "", name.lower())


def _soft_normalize(text: str) -> str:
    """轻归一化：转小写、全角转半角、非字母数字/中文字符统一替换为 ``-``，
    保留分隔边界。供「边界感知」的子串匹配使用，避免短 tag（如 SKY、DON）
    命中普通单词（如 Skyfall、Don't Look Up）。

    撇号（``'``）视为词内字符直接删除（缩写 ``Don't`` 不应被当成 `don` 边界词），
    其余标点/空格才替换为 ``-`` 分隔符。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("'", "")
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", text)
    return text.strip("-").lower()


def _word_pattern(cand: str) -> Optional[re.Pattern]:
    """为纯英文/数字候选名构造边界感知正则：候选名两侧不允许紧邻字母/数字，
    使 ``[SKY]`` 命中而 ``Skyfall`` 不命中；连字符候选（如 ``Airota-Raws``）
    也按原样保留连字符参与匹配。"""
    core = _soft_normalize(cand)
    if not core:
        return None
    return re.compile(r"(?<![0-9a-zA-Z])" + re.escape(core) + r"(?![0-9a-zA-Z])")


class SubgroupStore(JsonStore):
    """字幕组字典存储。"""

    DEFAULT: Dict = {"groups": {}}

    def __init__(self, path: Optional[Path] = None) -> None:
        path = path or self._default_path()
        super().__init__(path, default_data=self.DEFAULT)

    def _default_path(self) -> Path:
        """安装版首次运行把内置 subgroups.json 复制到 %APPDATA% 后可写；开发版直接用 src/db。"""
        default = app_data_dir() / "subgroups.json"
        if is_frozen() and not default.exists():
            bundled = Path(__file__).resolve().parent / "subgroups.json"
            if bundled.exists():
                default.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(bundled, default)
        return default

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

        匹配分两档：
        - 整段完全匹配：``text`` 去掉标点后与候选名一致。
        - 子串匹配（候选名 >= 3 字符）：
          * 纯英文/数字候选（如 SKY、DON、Airota-Raws）用「边界感知」匹配——
            要求候选名两侧不是字母/数字，因此 ``[SKY]``、``[DON]`` 能命中，
            但普通单词 ``Skyfall``、``Don't Look Up`` 不会误命中；同时
            ``Airota-Raws`` 也天然优先于 ``Airota``（不再依赖存储顺序）。
          * 含中文的候选维持宽松子串匹配（中文词组误命中率低）。
        """
        if not text:
            return None
        n_text = normalize(text)
        if not n_text:
            return None
        soft_text = _soft_normalize(text)
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
                if len(n_cand) < 3:
                    continue
                if n_cand.isascii():
                    # 纯英文/数字 tag：边界感知匹配
                    pat = _word_pattern(cand)
                    if pat and pat.search(soft_text):
                        return key
                elif n_cand in n_text:
                    # 含中文候选：宽松子串匹配
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
        rename_to: Optional[str] = None,
    ) -> bool:
        """新增一个组。

        - ``name``: 内部唯一标识（主键，即全称）。
        - ``rename_to``: 重命名后输出到文件名的名称（简称）；省略时等于 ``name``。
        - ``aliases``: 可被识别的名称列表（用于从文件名中匹配该组）。

        若已存在则合并别名并保留原简称，返回 ``False``。
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
                self.save()
                logger.info("组已存在，合并别名: %s (别名=%s, 简称=%s)",
                            name, clean_aliases, output)
                return False
            groups[name] = {
                "rename_to": output,
                "aliases": clean_aliases,
                "added": date.today().isoformat(),
            }
            self.save()
            logger.info("新增组: %s (简称=%s, 别名=%s)", name, output, clean_aliases)
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
                logger.info("组 %s 补充别名: %s", name, alias)
            return True

    def update(
        self,
        name: str,
        rename_to: Optional[str] = None,
        aliases: Optional[List[str]] = None,
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
            self.save()
            logger.info("更新组 %s: 简称=%s, 别名=%s",
                        name, meta.get("rename_to"), meta.get("aliases"))
            return True

    def remove(self, name: str) -> bool:
        with self._lock:
            if self.data["groups"].pop(name, None) is not None:
                self.save()
                logger.info("删除组: %s", name)
                return True
            return False
