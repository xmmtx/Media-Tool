"""字幕组列表页面：展示 subgroups.json 中的组及别名、来源。"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    PushButton,
    SubtitleLabel,
    TableWidget,
)

from db import SubgroupStore
from i18n import I18n

_COLS = ("name", "aliases", "source")  # 列名索引


class GroupsPage(QWidget):
    """字幕组字典列表页面。"""

    def __init__(self, subgroups: SubgroupStore, i18n: I18n,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.subgroups = subgroups
        self.i18n = i18n
        self._build_ui()
        self._retranslate()
        self.reload()

    def _t(self, key: str, **kw) -> str:
        return self.i18n.t(key, **kw)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        self.title_label = SubtitleLabel(self._t("nav_groups"))
        root.addWidget(self.title_label)

        # 顶部工具条：计数 + 刷新
        top = QHBoxLayout()
        self.lbl_count = BodyLabel("")
        self.btn_refresh = PushButton(self)
        self.btn_refresh.clicked.connect(self.reload)
        top.addWidget(self.lbl_count, 1)
        top.addWidget(self.btn_refresh)
        root.addLayout(top)

        self.table = TableWidget(self)
        self.table.setColumnCount(3)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        root.addWidget(self.table, 1)

    def _retranslate(self) -> None:
        self.title_label.setText(self._t("nav_groups"))
        self.btn_refresh.setText(self._t("refresh"))
        self.table.setHorizontalHeaderLabels(
            [self._t("col_group_name"), self._t("col_group_aliases"),
             self._t("col_group_source")])

    def reload(self) -> None:
        """从 subgroups.json 重新加载组列表。"""
        self.table.setRowCount(0)
        keys = self.subgroups.names()
        self.lbl_count.setText(self._t("groups_count", count=len(keys)))
        for key in sorted(keys):
            meta = self.subgroups.get(key) or {}
            row = self.table.rowCount()
            self.table.insertRow(row)
            rename_to = (meta.get("rename_to") or key) if isinstance(meta, dict) else key
            aliases = meta.get("aliases") or [] if isinstance(meta, dict) else []
            source = meta.get("source", "") if isinstance(meta, dict) else ""
            self.table.setItem(row, 0, QTableWidgetItem(rename_to))
            self.table.setItem(row, 1, QTableWidgetItem("、".join(aliases)))
            self.table.setItem(row, 2, QTableWidgetItem(str(source)))
