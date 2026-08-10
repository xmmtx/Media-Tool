"""媒体管理器主窗口：Fluent 左侧导航 + 各功能页面。

基于 PyQt-Fluent-Widgets（PyQt6）的 ``FluentWindow``：

- 顶部导航：电影 / 节目 / 音乐 —— 每个媒体类型一个独立 :class:`MediaPage`，
  文件拖入后只保留在它对应的文件列表里。
- 底部导航：组列表（:class:`GroupsPage`）、设置（:class:`SettingsPage`）。
- 语言切换由设置页广播，驱动各页面与导航文本整体重译。
"""

from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtCore import Qt

from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition

from core.processing import Processor
from db import ConfigStore, SubgroupStore
from i18n import I18n
from .groups_page import GroupsPage
from .media_page import MediaPage
from .settings_page import SettingsPage


def enable_high_dpi() -> None:
    """High-DPI 自动缩放策略（需在创建 QApplication 之前调用）。"""
    from PyQt6.QtGui import QGuiApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )


class MainWindow(FluentWindow):
    """媒体管理器主窗口。"""

    def __init__(self, i18n: Optional[I18n] = None,
                 config: Optional[ConfigStore] = None,
                 subgroup_store: Optional[SubgroupStore] = None) -> None:
        self.config = config or ConfigStore()
        self.subgroups = subgroup_store or SubgroupStore()
        if i18n is None:
            # 启动时按 config 中的语言（支持 system 跟随系统）初始化界面
            i18n = I18n(self.config.get("language", "zh_CN"))
        self.i18n = i18n
        self.processor = Processor(self.config, self.subgroups)
        super().__init__()
        self._nav_items: Dict[str, object] = {}
        self._build_pages()
        self._retranslate()
        self.resize(1180, 720)

    # ── 翻译辅助 ──────────────────────────────────────────────────────────

    def _t(self, key: str, **kw) -> str:
        return self.i18n.t(key, **kw)

    # ── 页面与导航 ────────────────────────────────────────────────────────

    def _build_pages(self) -> None:
        # 电影 / 节目 / 音乐（顶部导航，各自独立文件列表）
        self.movie_page = MediaPage("movie", self.processor, self.i18n, self.config)
        self.movie_page.setObjectName("moviePage")
        self._nav_items["movie"] = self.addSubInterface(
            self.movie_page, FluentIcon.MOVIE, self._t("mode_movie"))

        self.tv_page = MediaPage("tv", self.processor, self.i18n, self.config)
        self.tv_page.setObjectName("tvPage")
        self._nav_items["tv"] = self.addSubInterface(
            self.tv_page, FluentIcon.VIDEO, self._t("mode_tv"))

        self.music_page = MediaPage("music", self.processor, self.i18n, self.config)
        self.music_page.setObjectName("musicPage")
        self._nav_items["music"] = self.addSubInterface(
            self.music_page, FluentIcon.MUSIC, self._t("mode_music"))

        # 组列表 / 设置（底部导航）
        self.groups_page = GroupsPage(self.subgroups, self.i18n)
        self.groups_page.setObjectName("groupsPage")
        self._nav_items["groups"] = self.addSubInterface(
            self.groups_page, FluentIcon.PEOPLE, self._t("nav_groups"),
            position=NavigationItemPosition.BOTTOM)

        self.settings_page = SettingsPage(self.config, self.i18n)
        self.settings_page.setObjectName("settingsPage")
        self.settings_page.language_changed.connect(self._on_language_changed)
        self._nav_items["settings"] = self.addSubInterface(
            self.settings_page, FluentIcon.SETTING, self._t("nav_settings"),
            position=NavigationItemPosition.BOTTOM)

    # ── 语言切换 / 重译 ───────────────────────────────────────────────────

    def _on_language_changed(self, code: str) -> None:
        self.i18n.set_lang(code)
        self.config.set("language", code)
        self._retranslate()

    def _retranslate(self) -> None:
        texts = {
            "movie": self._t("mode_movie"),
            "tv": self._t("mode_tv"),
            "music": self._t("mode_music"),
            "groups": self._t("nav_groups"),
            "settings": self._t("nav_settings"),
        }
        for key, item in self._nav_items.items():
            item.setText(texts[key])
        for page in (self.movie_page, self.tv_page, self.music_page,
                     self.groups_page, self.settings_page):
            page._retranslate()
        self.setWindowTitle(self._t("app_title"))
