"""手动匹配对话框（Module 4）：搜索 TMDB 并提供“应用匹配”。

- 搜索框 + 实时搜索 + 结果列表（原名/本地化标题/年份）。
- 用户选择一条后点“应用匹配”，把选中的 :class:`MediaMatch` 通过
  ``selected`` 属性交付给调用方，用于对失败队列项 ``reprocess``。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.localize import tmdb_lang
from core.providers import BaseProvider, MediaMatch


class ManualMatchDialog(QDialog):
    """TMDB / Provider 手动匹配对话框。"""

    def __init__(
        self,
        provider: BaseProvider,
        query: str = "",
        media_type: str = "movie",
        language: str = "zh-CN",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.provider = provider
        self.media_type = media_type
        self.language = language
        self._tmdb_lang = tmdb_lang(language)  # i18n code → TMDB 语言代码
        self.selected: Optional[MediaMatch] = None
        self.setWindowTitle("Manual Match")
        self.resize(520, 420)
        self._build_ui()
        if query:
            self.search_edit.setText(query)
            self._on_search()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search title / year…")
        self.search_edit.returnPressed.connect(self._on_search)
        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(self._on_search)
        row.addWidget(QLabel("Query:"))
        row.addWidget(self.search_edit, 1)
        row.addWidget(self.btn_search)
        lay.addLayout(row)

        self.results = QListWidget()
        lay.addWidget(self.results, 1)

        self.lbl_hint = QLabel("Select a result and click Apply Match.")
        lay.addWidget(self.lbl_hint)

        btn_row = QHBoxLayout()
        self.btn_apply = QPushButton("Apply Match")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_apply)
        btn_row.addWidget(self.btn_cancel)
        lay.addLayout(btn_row)

        self.results.itemSelectionChanged.connect(
            lambda: self.btn_apply.setEnabled(bool(self.results.currentItem()))
        )

    # ── 行为 ──────────────────────────────────────────────────────────────

    def _on_search(self) -> None:
        query = self.search_edit.text().strip()
        self.results.clear()
        self.btn_apply.setEnabled(False)
        if not query or not self.provider.available:
            return
        for m in self.provider.search(query, media_type=self.media_type,
                                      language=self._tmdb_lang):
            year = f"({m.year})" if m.year else ""
            text = f"{m.title_user} {year}  [原: {m.title_orig}]"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, m)
            self.results.addItem(item)

    def _on_apply(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            self.selected = item.data(Qt.ItemDataRole.UserRole)
            self.accept()
