"""单个媒体类型的页面：独立文件列表 + 控制面板 + 后台批处理。

每个媒体类型（电影 / 节目 / 音乐）拥有自己的 :class:`MediaPage`，
文件拖入后只保留在该页面的文件列表里；控制面板的格式表达式、
处理方式、输出目录、封面注入等均按本页面独立配置。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TableWidget,
)

from core.formatters.expression_engine import evaluate
from core.processing import Processor, ProcessingOptions, QueueItem
from core.providers import MediaMatch
from i18n import I18n
from .components.preview_box import PreviewBox
from .manual_match import ManualMatchDialog

KIND_INDEX = {"movie": 0, "tv": 1, "music": 2}
DEFAULT_FORMATS = {
    "movie": "{title_orig} ({year}) - {title_user} - [{group} {resolution}]",
    "tv": "{title_orig} S{season_2d}E{episode_2d} {title_user} - [{group} {resolution}]",
    "music": "{artist} - {title}",
}


class ProcessingThread(QThread):
    """后台批处理线程：逐文件调用 Processor 并汇报进度。"""

    item_done = pyqtSignal(object)          # QueueItem
    progress = pyqtSignal(int)
    finished_all = pyqtSignal(int, int, int)  # ok, manual, error

    def __init__(self, processor: Processor, files: List[str],
                 options: ProcessingOptions) -> None:
        super().__init__()
        self.processor = processor
        self.files = files
        self.options = options

    def run(self) -> None:
        total = len(self.files)
        ok = manual = err = 0
        for i, path in enumerate(self.files):
            item = self.processor.process_file(path, self.options)
            if item.status == "ok":
                ok += 1
            elif item.status == "manual":
                manual += 1
            else:
                err += 1
            self.item_done.emit(item)
            self.progress.emit(int((i + 1) / total * 100) if total else 100)
        self.finished_all.emit(ok, manual, err)


class DropCard(CardWidget):
    """支持外部文件拖放的 Fluent 卡片容器。

    内部容纳文件表格；表格自身不接收拖放，OLE 拖放事件会向上传播到
    此卡片，命中后经 :attr:`files_dropped` 信号发出路径列表。
    """

    files_dropped = pyqtSignal(list)  # List[str]

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class MediaPage(QWidget):
    """单个媒体类型的页面：文件列表 + 控制面板 + 批处理。"""

    def __init__(self, kind: str, processor: Processor, i18n: I18n,
                 config, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.processor = processor
        self.i18n = i18n
        self.config = config
        self._paths: List[str] = []          # 与表格行一一对应
        self._manual_map: Dict[str, QueueItem] = {}
        self._thread: Optional[ProcessingThread] = None
        self._build_ui()
        self._retranslate()

    # ── 翻译辅助 ──────────────────────────────────────────────────────────

    def _t(self, key: str, **kw) -> str:
        return self.i18n.t(key, **kw)

    # ── UI 构建 ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # 页面标题
        self.title_label = SubtitleLabel(self._t("mode_" + self.kind))
        root.addWidget(self.title_label)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ── 文件列表卡片（可拖放） ────────────────────────────────
        self.drop_card = DropCard(self)
        card_lay = QVBoxLayout(self.drop_card)
        card_lay.setContentsMargins(8, 8, 8, 8)
        card_lay.setSpacing(4)

        self.table = TableWidget(self.drop_card)
        self.table.setColumnCount(3)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        card_lay.addWidget(self.table, 1)

        # 列表为空时的拖放提示
        self.drop_hint = BodyLabel(self._t("drop_hint"))
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_lay.addWidget(self.drop_hint, 1)

        self.drop_card.files_dropped.connect(self._on_files_dropped)
        split.addWidget(self.drop_card)

        # ── 右侧控制面板 ──────────────────────────────────────────
        self.panel = CardWidget(self)
        p = QVBoxLayout(self.panel)
        p.setContentsMargins(16, 16, 16, 16)
        p.setSpacing(8)

        self.lbl_format = StrongBodyLabel(self._t("format_label"))
        self.fmt_edit = LineEdit(self)
        self.fmt_edit.setText(DEFAULT_FORMATS.get(self.kind, ""))
        self.fmt_edit.setPlaceholderText("{title_orig} ({year}) - [{group}]")
        self.fmt_edit.textChanged.connect(self._update_preview)
        p.addWidget(self.lbl_format)
        p.addWidget(self.fmt_edit)

        self.lbl_mode = StrongBodyLabel(self._t("process"))
        self.mode_op_combo = ComboBox(self)
        self.mode_op_combo.addItems(
            [self._t("mode_rename"), self._t("mode_copy"), self._t("mode_hardlink")])
        p.addWidget(self.lbl_mode)
        p.addWidget(self.mode_op_combo)

        self.lbl_out = StrongBodyLabel(self._t("output_dir_label"))
        out_row = QHBoxLayout()
        self.out_edit = LineEdit(self)
        self.btn_out = PushButton(self)
        self.btn_out.clicked.connect(self._choose_output_dir)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(self.btn_out)
        p.addWidget(self.lbl_out)
        p.addLayout(out_row)

        # 注入封面开关
        self.cover_switch = SwitchButton(self._t("cover_inject_label"), self)
        self.cover_switch.setChecked(bool(self.config.get("music.inject_cover", False)))
        p.addWidget(self.cover_switch)
        cover_row = QHBoxLayout()
        self.cover_edit = LineEdit(self)
        self.cover_edit.setPlaceholderText(self._t("cover_path_label"))
        self.btn_cover = PushButton(self)
        self.btn_cover.clicked.connect(self._choose_cover)
        cover_row.addWidget(self.cover_edit, 1)
        cover_row.addWidget(self.btn_cover)
        p.addLayout(cover_row)

        self.preview = PreviewBox()
        p.addWidget(self.preview)

        self.btn_process = PrimaryPushButton(self)
        self.btn_process.clicked.connect(self._start_process)
        p.addWidget(self.btn_process)
        p.addStretch()

        split.addWidget(self.panel)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        # ── 底部：状态 + 进度 + 撤销 ──────────────────────────────
        bottom = QHBoxLayout()
        self.lbl_status = BodyLabel(self._t("status_ready", count=0))
        self.progress = ProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setFixedWidth(200)
        self.progress.hide()
        self.btn_undo = PushButton(self)
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self._undo)
        bottom.addWidget(self.lbl_status, 1)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.btn_undo)
        root.addLayout(bottom)

        self._update_preview()
        self._update_drop_hint()

    def _retranslate(self) -> None:
        self.title_label.setText(self._t("mode_" + self.kind))
        self.lbl_format.setText(self._t("format_label"))
        self.lbl_mode.setText(self._t("process"))
        self.lbl_out.setText(self._t("output_dir_label"))
        self.cover_switch.setText(self._t("cover_inject_label"))
        self.cover_edit.setPlaceholderText(self._t("cover_path_label"))
        self.drop_hint.setText(self._t("drop_hint"))
        self.btn_process.setText(self._t("start_processing"))
        self.btn_undo.setText(self._t("undo"))
        # 处理方式下拉保持选中项，仅重设文本
        idx = self.mode_op_combo.currentIndex()
        self.mode_op_combo.setItemText(0, self._t("mode_rename"))
        self.mode_op_combo.setItemText(1, self._t("mode_copy"))
        self.mode_op_combo.setItemText(2, self._t("mode_hardlink"))
        self.mode_op_combo.setCurrentIndex(max(0, idx))
        self.table.setHorizontalHeaderLabels(
            [self._t("col_old_name"), self._t("col_new_name"), self._t("col_status")])

    # ── 文件列表 ──────────────────────────────────────────────────────────

    def _add_row(self, path: str) -> None:
        self._paths.append(path)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(os.path.basename(path)))
        self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.setItem(row, 2, QTableWidgetItem("pending"))
        self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, path)
        self.lbl_status.setText(self._t("status_ready", count=len(self._paths)))
        self._update_drop_hint()

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, self._t("add_files"))
        for f in files:
            self._add_row(f)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self._t("add_folder"))
        if not folder:
            return
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                self._add_row(path)

    def _on_files_dropped(self, paths: List[str]) -> None:
        """拖入文件/文件夹：文件直接入列，文件夹递归收集其中的文件。

        ``QUrl.toLocalFile`` 可能返回正斜杠路径，这里统一用
        :func:`os.path.abspath` 规范化为与按钮添加一致的形式，保证去重可靠。
        """
        existing = set(self._paths)
        for p in paths:
            p = os.path.abspath(p)
            if os.path.isdir(p):
                for root, _dirs, files in os.walk(p):
                    for name in files:
                        full = os.path.join(root, name)
                        if full not in existing:
                            self._add_row(full)
                            existing.add(full)
            elif os.path.isfile(p) and p not in existing:
                self._add_row(p)
                existing.add(p)

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
            del self._paths[r]
        self._update_drop_hint()

    def _clear(self) -> None:
        self.table.setRowCount(0)
        self._paths.clear()
        self._manual_map.clear()
        self.lbl_status.setText(self._t("status_ready", count=0))
        self._update_drop_hint()

    def _update_drop_hint(self) -> None:
        """列表为空时显示拖放提示，否则显示表格。"""
        empty = not self._paths
        self.drop_hint.setVisible(empty)
        self.table.setVisible(not empty)

    # ── 处理 ──────────────────────────────────────────────────────────────

    def _options(self) -> ProcessingOptions:
        mode = ["rename", "copy", "hardlink"][self.mode_op_combo.currentIndex()]
        return ProcessingOptions(
            kind=self.kind,
            format=self.fmt_edit.text(),
            mode=mode,
            output_dir=self.out_edit.text().strip() or None,
            language=self.config.get("language", "zh_CN"),
            inject_cover=self.cover_switch.isChecked(),
            cover_path=self.cover_edit.text().strip() or None,
        )

    def _start_process(self) -> None:
        paths = list(self._paths)
        if not paths:
            QMessageBox.information(self, self._t("err_title"), self._t("err_no_files"))
            return
        self.btn_process.setEnabled(False)
        self.btn_undo.setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self._thread = ProcessingThread(self.processor, paths, self._options())
        self._thread.item_done.connect(self._on_item_done)
        self._thread.progress.connect(self.progress.setValue)
        self._thread.finished_all.connect(self._on_all_done)
        self._thread.start()

    def _on_item_done(self, item: QueueItem) -> None:
        try:
            row = self._paths.index(item.path)
        except ValueError:
            return
        self.table.item(row, 1).setText(item.new_name)
        status_item = self.table.item(row, 2)
        status_item.setText(item.status)
        status_item.setForeground(
            Qt.GlobalColor.green if item.status == "ok"
            else Qt.GlobalColor.red if item.status == "error"
            else Qt.GlobalColor.darkYellow)
        if item.status == "manual":
            self._manual_map[item.path] = item

    def _on_all_done(self, ok: int, manual: int, err: int) -> None:
        self.btn_process.setEnabled(True)
        self.btn_undo.setEnabled(bool(self.processor.operator.history))
        self.progress.hide()
        self.lbl_status.setText(
            self._t("status_done", ok=ok, manual=manual, error=err))

    def _on_cell_double_clicked(self, row: int, _col: int) -> None:
        path = self._paths[row] if row < len(self._paths) else None
        if not path:
            return
        item = self._manual_map.get(path)
        if item is None or item.status != "manual":
            return
        kind = item.kind
        query = item.info.title if item.info and item.info.title else ""
        dialog = ManualMatchDialog(
            self.processor.tmdb, query=query, media_type=kind,
            language=self.i18n.lang, parent=self)
        if dialog.exec() and dialog.selected:
            match: MediaMatch = dialog.selected
            new_item = self.processor.reprocess(item, forced_match=match)
            self._on_item_done(new_item)
            self.lbl_status.setText(
                self._t("status_done", ok=1, manual=0, error=0))

    # ── 撤销 / 预览 / 目录 ────────────────────────────────────────────────

    def _undo(self) -> None:
        n = self.processor.operator.undo_all()
        if n:
            self.lbl_status.setText(self._t("status_done", ok=n, manual=0, error=0))
        self.btn_undo.setEnabled(bool(self.processor.operator.history))

    def _update_preview(self) -> None:
        fmt = self.fmt_edit.text()
        if not fmt:
            self.preview.set_preview("")
            return
        sample = {
            "movie": {"title_orig": "Dune", "title_user": "沙丘", "year": 2021,
                      "group": "SweetSub", "resolution": "1920x1080"},
            "tv": {"title_orig": "うまよん", "title_user": "赛马娘四格",
                   "season": 1, "episode": 1, "group": "NekomoeKissaten",
                   "resolution": "1920x1080"},
            "music": {"artist": "歌手A、歌手B", "title": "示例歌曲",
                      "album": "专辑", "year": 2022},
        }[self.kind]
        self.preview.set_preview(evaluate(fmt, sample))

    def _choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self._t("output_dir_label"))
        if folder:
            self.out_edit.setText(folder)

    def _choose_cover(self) -> None:
        file, _ = QFileDialog.getOpenFileName(self, self._t("cover_path_label"))
        if file:
            self.cover_edit.setText(file)
