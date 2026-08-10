"""字幕组列表页面：展示与编辑 subgroups.json 中的组（添加 / 编辑 / 删除）。"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
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
    SubtitleLabel,
    TableWidget,
)

from db import SubgroupStore
from i18n import I18n

_SOURCES = ("manual", "seed", "llm")


class GroupEditDialog(QDialog):
    """添加 / 编辑字幕组的对话框。"""

    def __init__(self, title: str, i18n: I18n, group: Optional[dict] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        data = group or {}

        form = QFormLayout(self)
        form.setSpacing(12)

        self.name_edit = LineEdit(self)
        self.name_edit.setText(data.get("name", ""))
        self.rename_edit = LineEdit(self)
        self.rename_edit.setText(data.get("rename_to", ""))
        self.source_combo = ComboBox(self)
        for s in _SOURCES:
            self.source_combo.addItem(s)
        self.source_combo.setCurrentText(data.get("source", "manual"))

        # 别名：动态多行（每行一个输入框 + 删除按钮，点加号追加新行）
        self.aliases_box = QWidget(self)
        self.aliases_lay = QVBoxLayout(self.aliases_box)
        self.aliases_lay.setContentsMargins(0, 0, 0, 0)
        self.aliases_lay.setSpacing(4)
        self._alias_rows = []  # List[(row_widget, LineEdit)]
        initial = data.get("aliases") or [""]
        for a in initial:
            self._add_alias_row(str(a))
        self.btn_add_alias = PushButton(self)
        self.btn_add_alias.clicked.connect(lambda: self._add_alias_row(""))
        self.aliases_lay.addWidget(self.btn_add_alias)

        form.addRow(self._t("groups_name"), self.name_edit)
        form.addRow(self._t("groups_rename_to"), self.rename_edit)
        form.addRow(self._t("groups_aliases"), self.aliases_box)
        form.addRow(self._t("groups_source"), self.source_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self._t("ok"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._t("cancel"))
        form.addRow(buttons)

    def _t(self, key: str, **kw) -> str:
        return self.i18n.t(key, **kw)

    def _add_alias_row(self, text: str = "") -> None:
        """追加一行别名输入框（带删除按钮），插到"添加别名"按钮之前。"""
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        edit = LineEdit(row)
        edit.setText(text)
        btn = PushButton(row)
        btn.setText("－")
        btn.setFixedWidth(36)
        btn.setToolTip(self._t("groups_remove_alias"))
        btn.clicked.connect(lambda _=False, r=row: self._remove_alias_row(r))
        h.addWidget(edit, 1)
        h.addWidget(btn)
        self.aliases_lay.insertWidget(self.aliases_lay.count() - 1, row)
        self._alias_rows.append((row, edit))

    def _remove_alias_row(self, row) -> None:
        """删除一行别名；至少保留一行（此时清空文本）。"""
        if len(self._alias_rows) <= 1:
            _r, edit = self._alias_rows[0]
            edit.setText("")
            return
        self.aliases_lay.removeWidget(row)
        row.deleteLater()
        self._alias_rows = [(r, e) for r, e in self._alias_rows if r is not row]

    def values(self) -> dict:
        aliases = [e.text().strip() for _r, e in self._alias_rows if e.text().strip()]
        return {
            "name": self.name_edit.text().strip(),
            "rename_to": self.rename_edit.text().strip(),
            "aliases": aliases,
            "source": self.source_combo.currentText(),
        }


class GroupsPage(QWidget):
    """字幕组字典列表页面（可增删改）。"""

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

        # 顶部工具条：计数 + 添加 / 编辑 / 删除 / 刷新
        top = QHBoxLayout()
        self.lbl_count = BodyLabel("")
        self.btn_add = PrimaryPushButton(self)
        self.btn_edit = PushButton(self)
        self.btn_delete = PushButton(self)
        self.btn_refresh = PushButton(self)
        self.btn_add.clicked.connect(self._add_group)
        self.btn_edit.clicked.connect(self._edit_group)
        self.btn_delete.clicked.connect(self._delete_group)
        self.btn_refresh.clicked.connect(self.reload)
        top.addWidget(self.lbl_count, 1)
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_edit)
        top.addWidget(self.btn_delete)
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
        self.btn_add.setText(self._t("groups_add"))
        self.btn_edit.setText(self._t("groups_edit"))
        self.btn_delete.setText(self._t("groups_delete"))
        self.btn_refresh.setText(self._t("refresh"))
        self.table.setHorizontalHeaderLabels(
            [self._t("col_group_name"), self._t("col_group_short"),
             self._t("col_group_aliases")])

    # ── 加载 / 选中 ───────────────────────────────────────────────────────

    def reload(self) -> None:
        """从 subgroups.json 重新加载：全称(主键) | 简称(rename_to) | 别名。

        主键存于第 0 列 UserRole。
        """
        self.table.setRowCount(0)
        keys = self.subgroups.names()
        self.lbl_count.setText(self._t("groups_count", count=len(keys)))
        for key in sorted(keys):
            meta = self.subgroups.get(key) or {}
            row = self.table.rowCount()
            self.table.insertRow(row)
            full = key                                    # 全称 = 主键（原名）
            short = meta.get("rename_to") or key if isinstance(meta, dict) else key
            aliases = meta.get("aliases") or [] if isinstance(meta, dict) else []
            item0 = QTableWidgetItem(full)
            item0.setData(Qt.ItemDataRole.UserRole, key)
            self.table.setItem(row, 0, item0)
            self.table.setItem(row, 1, QTableWidgetItem(short))
            self.table.setItem(row, 2, QTableWidgetItem("、".join(aliases)))

    def _selected_key(self) -> Optional[str]:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if not rows:
            return None
        item = self.table.item(rows[0], 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ── 增删改 ────────────────────────────────────────────────────────────

    def _add_group(self) -> None:
        dlg = GroupEditDialog(self._t("groups_add"), self.i18n, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.values()
            if v["name"]:
                self.subgroups.add(
                    v["name"], aliases=v["aliases"],
                    source=v["source"] or "manual",
                    rename_to=v["rename_to"] or None)
                self.reload()

    def _edit_group(self) -> None:
        key = self._selected_key()
        if not key:
            return
        meta = self.subgroups.get(key) or {}
        data = {
            "name": key,
            "rename_to": meta.get("rename_to", ""),
            "aliases": meta.get("aliases", []),
            "source": meta.get("source", "manual"),
        }
        dlg = GroupEditDialog(self._t("groups_edit"), self.i18n, group=data, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        if not v["name"]:
            return
        if v["name"] != key:
            # 主键变化：删除旧组再新增
            self.subgroups.remove(key)
            self.subgroups.add(
                v["name"], aliases=v["aliases"],
                source=v["source"] or "manual",
                rename_to=v["rename_to"] or None)
        else:
            self.subgroups.update(
                key, rename_to=v["rename_to"] or None,
                aliases=v["aliases"], source=v["source"] or None)
        self.reload()

    def _delete_group(self) -> None:
        key = self._selected_key()
        if not key:
            return
        ret = QMessageBox.question(
            self, self._t("err_title"),
            self._t("groups_confirm_delete", name=key),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            self.subgroups.remove(key)
            self.reload()
