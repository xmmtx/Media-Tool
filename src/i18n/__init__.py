"""轻量 i18n：JSON 词典加载与查询（zh_CN / zh_TW / en_US）。

- :class:`I18n` 加载 ``<dir>/<lang>.json``，提供 ``t(key, **kw)`` 查询，
  支持 ``{placeholder}`` 格式化；缺失 key 回退为 key 本身（便于发现漏译）。
- 词典为纯 JSON，便于扩展语言与翻译协作。
"""

from __future__ import annotations

import json
import locale
import os
from pathlib import Path
from typing import Dict, Optional

LANGS: tuple = ("zh_CN", "zh_TW", "en_US")
DEFAULT_LANG = "zh_CN"
SYSTEM = "system"  # 跟随系统语言


def detect_system_lang() -> str:
    """检测系统语言并映射到已支持的语言之一。"""
    try:
        code, _ = locale.getdefaultlocale()
    except Exception:
        code = os.environ.get("LANG") or ""
    code = (code or "").lower()
    if code.startswith("zh"):
        for tag in ("tw", "hk", "mo"):
            if tag in code:
                return "zh_TW"
        return "zh_CN"
    return "en_US"


class I18n:
    """多语言词典查询器。"""

    def __init__(self, lang: str = DEFAULT_LANG, directory: Optional[Path] = None) -> None:
        self.directory = Path(directory) if directory else Path(__file__).resolve().parent
        self._tables: Dict[str, dict] = {}
        for code in LANGS:
            self._tables[code] = self._read(code)
        self._lang = self._resolve(lang)

    @staticmethod
    def _resolve(lang: str) -> str:
        """把 ``SYSTEM`` 或未知语言解析为实际语言 code。"""
        if lang == SYSTEM:
            return detect_system_lang()
        return lang if lang in LANGS else DEFAULT_LANG

    def _read(self, code: str) -> dict:
        path = self.directory / f"{code}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    # ── 查询 ──────────────────────────────────────────────────────────────

    def t(self, key: str, default: Optional[str] = None, **kw) -> str:
        """翻译 key；带 ``**kw`` 时对结果做 ``str.format`` 占位替换。"""
        table = self._tables.get(self._lang, {})
        text = table.get(key, default if default is not None else key)
        return text.format(**kw) if kw else text

    def has(self, key: str) -> bool:
        return key in self._tables.get(self._lang, {})

    # ── 语言管理 ──────────────────────────────────────────────────────────

    @property
    def lang(self) -> str:
        return self._lang

    def set_lang(self, lang: str) -> None:
        """设置语言；传入 ``SYSTEM`` 时切换到跟随系统。"""
        resolved = self._resolve(lang)
        if resolved in LANGS:
            self._lang = resolved

    @staticmethod
    def available_langs() -> tuple:
        return LANGS

    def switch_tables(self) -> Dict[str, dict]:
        """全部语言表（供 UI 语言菜单/调试）。"""
        return dict(self._tables)
