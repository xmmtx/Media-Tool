"""标题本地化：按 UI 语言优先级获取 ``{title_user}``（opencc 简繁转换）。

优先级链：
- 简体 UI（``zh_CN``）：zh-CN → zh-TW（opencc ``t2s`` 转简）→ en-US → 原始标题
- 繁体 UI（``zh_TW``）：zh-TW → zh-CN（opencc ``s2t`` 转繁）→ en-US → 原始标题
- 英文 UI（``en_US``）：en-US → 原始标题
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("core.localize")

_s2t = None
_t2s = None


def tmdb_lang(ui_lang: str) -> str:
    """i18n 语言 code（``zh_CN``/``zh_TW``/``en_US``）→ TMDB 语言代码。"""
    return {"zh_CN": "zh-CN", "zh_TW": "zh-TW"}.get(ui_lang, "en-US")


def title_language_chain(ui_lang: str) -> List[Tuple[str, Optional[str]]]:
    """按 UI 语言返回 ``[(tmdb_lang, opencc_mode|None)]`` 标题优先级链。

    ``opencc_mode`` 为 ``s2t``/``t2s`` 表示需要跨简繁转换，``None`` 表示直接用。
    """
    if ui_lang == "zh_TW":
        return [("zh-TW", None), ("zh-CN", "s2t"), ("en-US", None)]
    if ui_lang == "zh_CN":
        return [("zh-CN", None), ("zh-TW", "t2s"), ("en-US", None)]
    return [("en-US", None)]


def looks_localized(title: str, orig: str) -> bool:
    """标题是否已本地化：非空且与原始标题不同（相同说明该语言无翻译）。"""
    t = (title or "").strip()
    o = (orig or "").strip()
    if not t:
        return False
    return t.lower() != o.lower()


def convert_title(text: str, mode: Optional[str], engine: str = "opencc") -> str:
    """跨简繁转换标题；``mode`` 为 None 时不转换。

    ``engine`` 可选 ``"opencc"`` / ``"zhconv"``（设置页"简繁转换引擎"选择）。
    """
    if mode not in ("s2t", "t2s") or not text:
        return text
    if engine == "zhconv":
        try:
            import zhconv  # type: ignore

            target = "zh-hant" if mode == "s2t" else "zh-hans"
            return zhconv.convert(text, target)
        except Exception as e:
            logger.warning("zhconv 转换失败: %s", e)
            return text
    global _s2t, _t2s
    try:
        import opencc  # type: ignore

        if mode == "s2t":
            if _s2t is None:
                _s2t = opencc.OpenCC("s2t")
            return _s2t.convert(text)
        if _t2s is None:
            _t2s = opencc.OpenCC("t2s")
        return _t2s.convert(text)
    except Exception as e:  # opencc 不可用时原样返回
        logger.warning("opencc 转换失败: %s", e)
        return text
