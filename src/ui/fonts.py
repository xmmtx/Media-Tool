"""字体加载与全局应用。

- 内置字体：优先使用 ``reference/`` 下的字体文件（默认字体）。
- 全局应用：通过 Fluent 的 ``setFontFamilies`` 让新控件使用指定字体族，
  并遍历已有控件刷新，使字体切换立即生效。
"""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication

from qfluentwidgets import getFont, setFontFamilies

# reference 内置字体（本地开发默认字体；注意 reference/ 目录是 gitignore 的）
DEFAULT_FONT_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "reference",
    "HarmonyOS_Sans_SC_Regular.ttf"))


def bundled_font_family() -> Optional[str]:
    """加载内置字体文件并返回其字体族名；文件缺失或加载失败返回 ``None``。"""
    if not os.path.exists(DEFAULT_FONT_PATH):
        return None
    fid = QFontDatabase.addApplicationFont(DEFAULT_FONT_PATH)
    if fid < 0:
        return None
    families = QFontDatabase.applicationFontFamilies(fid)
    return families[0] if families else None


def system_font_families() -> List[str]:
    """返回系统已安装的字体族名列表。"""
    return QFontDatabase.families()


def apply_font_family(family: Optional[str]) -> None:
    """把字体族应用到全局：Fluent 新控件 + 刷新所有已有控件。"""
    if not family:
        return
    setFontFamilies([family], save=False)
    app = QApplication.instance()
    if app is None:
        return
    app.setFont(getFont(14))
    for w in app.allWidgets():
        f = w.font()
        size = f.pixelSize()
        nf = getFont(size if size > 0 else 14, f.weight())
        w.setFont(nf)


def resolve_font_family(config_family: str) -> Optional[str]:
    """解析要应用的字体族：配置为空时回退到内置字体。"""
    if config_family:
        return config_family
    return bundled_font_family()
