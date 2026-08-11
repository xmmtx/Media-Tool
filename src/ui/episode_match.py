"""剧集手动匹配对话框（参考 FileBot Episodes）：搜索节目 → 列出全部集 → 多选发送。

- 搜索 TMDB 剧集并列出候选节目。
- 选定节目后加载该剧所有季的每一集，表格列为 **季编号 | 集编号 | 集标题**。
- 表格支持 shift / ctrl 多选（与 Windows 文件选择逻辑一致），
  点「发送到节目页」把选中的集交付给调用方。
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
)

from core.localize import tmdb_lang
from core.providers import BaseProvider, MediaMatch


class EpisodeMatchDialog(QDialog):
    """剧集手动匹配对话框。"""

    def __init__(self, provider: BaseProvider, query: str = "",
                 language: str = "zh-CN", i18n=None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.language = language
        self._tmdb_lang = tmdb_lang(language)  # i18n code → TMDB 语言代码
        self.i18n = i18n
        self.selected_show: Optional[MediaMatch] = None
        self.selected_episodes: List[dict] = []
        self._shows: List[MediaMatch] = []
        self.setMinimumSize(680, 560)
        self._build_ui()
        self._retranslate()
        if query:
            self.search_edit.setText(query)
            self._on_search()

    def _t(self, key: str, **kw) -> str:
        return self.i18n.t(key, **kw) if self.i18n else key

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        self.title_label = SubtitleLabel(self._t("manual_match_title"))
        root.addWidget(self.title_label)

        # 搜索栏
        row = QHBoxLayout()
        self.search_edit = LineEdit(self)
        self.search_edit.returnPressed.connect(self._on_search)
        self.btn_search = PrimaryPushButton(self)
        self.btn_search.clicked.connect(self._on_search)
        row.addWidget(self.search_edit, 1)
        row.addWidget(self.btn_search)
        root.addLayout(row)

        # 搜索结果（节目）
        self.lbl_shows = StrongBodyLabel(self._t("manual_shows"))
        self.show_list = QListWidget(self)
        self.show_list.itemClicked.connect(self._on_show_selected)
        root.addWidget(self.lbl_shows)
        root.addWidget(self.show_list, 1)

        # 集表格：季编号 | 集编号 | 集标题
        self.lbl_episodes = StrongBodyLabel(self._t("manual_episodes"))
        self.ep_table = TableWidget(self)
        self.ep_table.setColumnCount(3)
        self.ep_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ep_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ep_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ep_table.verticalHeader().setVisible(False)
        self.ep_table.horizontalHeader().setStretchLastSection(True)
        self.ep_table.setBorderVisible(True)
        self.ep_table.setBorderRadius(8)
        self.ep_table.setHorizontalHeaderLabels(
            [self._t("col_season"), self._t("col_episode"), self._t("col_episode_title")])
        root.addWidget(self.lbl_episodes)
        root.addWidget(self.ep_table, 2)

        # 提示 + 按钮
        self.lbl_hint = BodyLabel(self._t("manual_multiselect_hint"))
        root.addWidget(self.lbl_hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_cancel = PushButton(self)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_send = PrimaryPushButton(self)
        self.btn_send.setEnabled(False)
        self.btn_send.clicked.connect(self._on_send)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_send)
        root.addLayout(btn_row)

    def _retranslate(self) -> None:
        self.title_label.setText(self._t("manual_match_title"))
        self.btn_search.setText(self._t("manual_search"))
        self.lbl_shows.setText(self._t("manual_shows"))
        self.lbl_episodes.setText(self._t("manual_episodes"))
        self.lbl_hint.setText(self._t("manual_multiselect_hint"))
        self.btn_cancel.setText(self._t("cancel"))
        self.btn_send.setText(self._t("manual_send"))
        self.ep_table.setHorizontalHeaderLabels(
            [self._t("col_season"), self._t("col_episode"), self._t("col_episode_title")])

    # ── 搜索 / 加载 / 发送 ────────────────────────────────────────────────

    def _on_search(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            return
        self._shows = self.provider.search(query, media_type="tv",
                                           language=self._tmdb_lang)
        self.show_list.clear()
        for m in self._shows:
            item = QListWidgetItem(self._show_label(m))
            item.setData(Qt.ItemDataRole.UserRole, m)
            self.show_list.addItem(item)
        self.ep_table.setRowCount(0)
        self.btn_send.setEnabled(False)

    @staticmethod
    def _show_label(m: MediaMatch) -> str:
        year = f" ({m.year})" if m.year else ""
        return f"{m.title_user}{year} · {m.title_orig}"

    def _on_show_selected(self, item: QListWidgetItem) -> None:
        show: MediaMatch = item.data(Qt.ItemDataRole.UserRole)
        self.selected_show = show
        episodes = self.provider.get_tv_seasons(show.tmdb_id, language=self._tmdb_lang)
        self.ep_table.setRowCount(0)
        for ep in episodes:
            row = self.ep_table.rowCount()
            self.ep_table.insertRow(row)
            self.ep_table.setItem(row, 0, QTableWidgetItem(str(ep.get("season", ""))))
            self.ep_table.setItem(row, 1, QTableWidgetItem(str(ep.get("episode", ""))))
            self.ep_table.setItem(row, 2, QTableWidgetItem(ep.get("name", "")))
            for c in range(3):
                self.ep_table.item(row, c).setData(
                    Qt.ItemDataRole.UserRole, (ep.get("season"), ep.get("episode")))
        self.btn_send.setEnabled(self.ep_table.rowCount() > 0)

    def _on_send(self) -> None:
        rows = sorted({i.row() for i in self.ep_table.selectedItems()})
        if not rows:
            return
        episodes: List[dict] = []
        for r in rows:
            season, episode = self.ep_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            title = self.ep_table.item(r, 2).text()
            episodes.append({"season": season, "episode": episode, "name": title})
        self.selected_episodes = episodes
        self.accept()
