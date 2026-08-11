"""单个媒体类型的页面：独立文件列表 + 控制面板 + 后台批处理。

每个媒体类型（电影 / 节目 / 音乐）拥有自己的 :class:`MediaPage`，
文件拖入后只保留在该页面的文件列表里；控制面板的格式表达式、
处理方式、输出目录、封面注入等均按本页面独立配置。
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger("ui.media_page")

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QSplitter,
    QStackedWidget,
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

from core.extractors.media_extractor import extract_from_filename
from core.formatters.expression_engine import evaluate
from core.processing import Processor, ProcessingOptions, QueueItem
from core.providers import MediaMatch
from i18n import I18n
from .components.preview_box import PreviewBox
from .episode_match import EpisodeMatchDialog
from .manual_match import ManualMatchDialog

KIND_INDEX = {"movie": 0, "tv": 1, "music": 2}
DEFAULT_FORMATS = {
    "movie": "{title_orig} ({year}) - {title_user} - [{group} {resolution}]",
    "tv": "{title_orig} S{season_2d}E{episode_2d} {title_user} - [{group} {resolution}]",
    "music": "{artist} - {title}",
}

# processing 产生的英文 manual 原因 → i18n key
_REASON_KEYS = {
    "subgroup not recognized": "reason_subgroup",
    "cannot parse season/episode": "reason_parse_se_ep",
    "cannot parse title from filename": "reason_parse_title",
    "no TMDB movie match": "reason_no_movie",
    "no TMDB TV match": "reason_no_tv",
    "cannot determine TV type (anime/drama/documentary)": "reason_tv_type",
    "missing title/artist in metadata tags": "reason_missing_tags",
    "format produced empty name": "reason_empty_name",
    "cannot parse season/episode from subtitle": "reason_parse_subtitle",
    "no matching video for subtitle": "reason_no_video",
}


class ProcessingThread(QThread):
    """后台线程：匹配（dry_run）或执行（pending 项）。

    - ``pending=None``：对 ``files`` 逐文件 ``process_file``（是否执行文件操作
      由 options.dry_run 决定）。
    - 传入 ``pending`` 列表：对每个项 ``execute_item``（执行已匹配项的操作）。
    """

    item_done = pyqtSignal(object)          # QueueItem
    progress = pyqtSignal(int)
    finished_all = pyqtSignal(int, int, int)  # ok, manual, error

    def __init__(self, processor: Processor, files: List[str],
                 options: ProcessingOptions,
                 pending: Optional[List[QueueItem]] = None) -> None:
        super().__init__()
        self.processor = processor
        self.files = files
        self.options = options
        self.pending = pending

    def run(self) -> None:
        ok = manual = err = 0
        if self.pending is not None:
            total = len(self.pending)
            for i, item in enumerate(self.pending):
                result = self.processor.execute_item(item, self.options)
                if result.status == "ok":
                    ok += 1
                elif result.status == "manual":
                    manual += 1
                else:
                    err += 1
                self.item_done.emit(result)
                self.progress.emit(int((i + 1) / total * 100) if total else 100)
        else:
            # 视频先处理（登记匹配结果），字幕后处理（跟随同集视频）
            ordered = sorted(self.files,
                             key=lambda p: self.processor.is_subtitle(p))
            total = len(ordered)
            for i, path in enumerate(ordered):
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
        self._pending: List[QueueItem] = []  # 已匹配待执行项
        self._phase = "idle"                 # idle | match | execute
        self._thread: Optional[ProcessingThread] = None
        self._build_ui()
        self._retranslate()

    # ── 翻译辅助 ──────────────────────────────────────────────────────────

    def _t(self, key: str, **kw) -> str:
        return self.i18n.t(key, **kw)

    def _i18n_reason(self, reason: str) -> str:
        """把 processing 产生的英文 reason 映射为本地化文案。"""
        key = _REASON_KEYS.get(reason)
        return self._t(key) if key else reason

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

        # 空列表提示 / 表格 用 QStackedWidget 切换（避免 setVisible 状态问题）
        self.drop_stack = QStackedWidget(self.drop_card)
        self.drop_hint = BodyLabel(self._t("drop_hint"))
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_stack.addWidget(self.drop_hint)
        self.drop_stack.addWidget(self.table)
        card_lay.addWidget(self.drop_stack, 1)

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

        # 注入封面：目前仅音乐启用；电影/剧集暂时关闭并隐藏（后续可能开发）
        is_music = self.kind == "music"
        self.cover_switch = SwitchButton(self._t("cover_inject_label"), self)
        self.cover_switch.setChecked(
            is_music and bool(self.config.get("music.inject_cover", False)))
        self.cover_switch.setVisible(is_music)
        p.addWidget(self.cover_switch)
        cover_row = QHBoxLayout()
        self.cover_edit = LineEdit(self)
        self.cover_edit.setPlaceholderText(self._t("cover_path_label"))
        self.cover_edit.setVisible(is_music)
        self.btn_cover = PushButton(self)
        self.btn_cover.setVisible(is_music)
        self.btn_cover.clicked.connect(self._choose_cover)
        cover_row.addWidget(self.cover_edit, 1)
        cover_row.addWidget(self.btn_cover)
        p.addLayout(cover_row)

        self.preview = PreviewBox()
        p.addWidget(self.preview)

        # 节目页匹配：手动匹配 / 自动匹配（仅 TV 显示）
        self.match_box = QWidget(self)
        mb = QVBoxLayout(self.match_box)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(4)
        self.lbl_match = StrongBodyLabel(self._t("match_label"))
        match_row = QHBoxLayout()
        self.btn_manual_match = PushButton(self)
        self.btn_auto_match = PushButton(self)
        self.btn_manual_match.clicked.connect(self._on_manual_match)
        self.btn_auto_match.clicked.connect(self._on_auto_match)
        match_row.addWidget(self.btn_manual_match)
        match_row.addWidget(self.btn_auto_match)
        mb.addWidget(self.lbl_match)
        mb.addLayout(match_row)
        p.addWidget(self.match_box)
        self.match_box.setVisible(self.kind == "tv")

        self.btn_process = PrimaryPushButton(self)
        self.btn_process.clicked.connect(self._execute_pending)
        self.btn_process.setEnabled(False)  # 匹配完成后才亮起
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
        self.lbl_match.setText(self._t("match_label"))
        self.btn_manual_match.setText(self._t("btn_manual_match"))
        self.btn_auto_match.setText(self._t("btn_auto_match"))
        self.btn_process.setText(self._t("btn_execute"))
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
        logger.info("添加文件: %s", path)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(os.path.basename(path)))
        self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.setItem(row, 2, QTableWidgetItem(self._t("st_pending")))
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
        logger.info("拖放 %d 个路径", len(paths))
        existing = set(self._paths)
        for p in paths:
            p = os.path.abspath(p)
            if os.path.isdir(p):
                for root, _dirs, files in os.walk(p):
                    for name in files:
                        full = os.path.join(root, name)
                        # 仅收集可处理的媒体文件（视频/字幕/音频），
                        # 滤掉 .nfo/.jpg/.7z 等交给 Jellyfin 刮削的附属文件
                        if not Processor.is_media(full):
                            continue
                        if full not in existing:
                            self._add_row(full)
                            existing.add(full)
            elif os.path.isfile(p) and Processor.is_media(p) and p not in existing:
                self._add_row(p)
                existing.add(p)

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        removed = {self._paths[r] for r in rows}
        for r in rows:
            self.table.removeRow(r)
            del self._paths[r]
        self._pending = [p for p in self._pending if p.path not in removed]
        self.btn_process.setEnabled(bool(self._pending))
        logger.info("移除 %d 个选中文件", len(rows))
        self._update_drop_hint()

    def _clear(self) -> None:
        self.table.setRowCount(0)
        self._paths.clear()
        self._manual_map.clear()
        self._pending.clear()
        self.btn_process.setEnabled(False)
        self.lbl_status.setText(self._t("status_ready", count=0))
        logger.info("清空文件列表")
        self._update_drop_hint()

    def _update_drop_hint(self) -> None:
        """列表为空时显示拖放提示，否则显示表格。"""
        self.drop_stack.setCurrentIndex(0 if not self._paths else 1)

    def showEvent(self, event) -> None:
        """页面显示时按输出模式刷新"输出目录"输入框的可见性。"""
        super().showEvent(event)
        self._update_output_visibility()
        logger.info("页面显示: %s (文件 %d 个)", self.kind, len(self._paths))

    def _update_output_visibility(self) -> None:
        """自定义模式显示"输出目录"输入框；媒体库模式隐藏（由 Jellyfin 结构决定）。"""
        is_custom = self.config.get("output.mode", "custom") != "library"
        self.lbl_out.setVisible(is_custom)
        self.out_edit.setVisible(is_custom)
        self.btn_out.setVisible(is_custom)

    # ── 处理 ──────────────────────────────────────────────────────────────

    def _options(self) -> ProcessingOptions:
        mode = ["rename", "copy", "hardlink"][self.mode_op_combo.currentIndex()]
        output_mode = self.config.get("output.mode", "custom")
        roots = self.config.get("output.roots", {}) or {}
        return ProcessingOptions(
            kind=self.kind,
            format=self.fmt_edit.text(),
            mode=mode,
            output_dir=self.out_edit.text().strip() or None,
            output_mode=output_mode,
            library_roots=dict(roots) if isinstance(roots, dict) else None,
            language=self.i18n.lang,  # 已解析的实际语言（system 也已解析）
            inject_cover=self.cover_switch.isChecked() if self.kind == "music" else False,
            cover_path=self.cover_edit.text().strip() or None,
        )

    def _on_auto_match(self) -> None:
        """自动匹配：解析 + TMDB 搜索，预览目标名（dry_run），不执行文件操作。"""
        self._fallback_manual = True
        self._start_match()

    def _on_manual_match(self) -> None:
        """手动匹配：搜索节目 → 多选集 → 发送到节目页匹配。"""
        if not self._paths:
            QMessageBox.information(self, self._t("err_title"), self._t("err_no_files"))
            return
        query = ""
        for path in self._paths:
            info = extract_from_filename(path)
            if info.title:
                query = info.title
                break
        dlg = EpisodeMatchDialog(self.processor.tmdb, query,
                                 self.i18n.lang, self.i18n, parent=self)
        if dlg.exec() and dlg.selected_episodes:
            self._apply_episode_match(dlg.selected_show, dlg.selected_episodes,
                                      list(self._paths))

    def _open_manual_match_for_manual_items(self) -> None:
        """自动匹配失败后：打开手动匹配，仅处理 manual 队列中的项。"""
        targets = list(self._manual_map.keys())
        if not targets:
            return
        query = ""
        for path in targets:
            info = extract_from_filename(path)
            if info.title:
                query = info.title
                break
        dlg = EpisodeMatchDialog(self.processor.tmdb, query,
                                 self.i18n.lang, self.i18n, parent=self)
        if dlg.exec() and dlg.selected_episodes:
            self._apply_episode_match(dlg.selected_show, dlg.selected_episodes, targets)

    def _apply_episode_match(self, show, episodes, paths) -> None:
        """按 (season, episode) 把选中的集匹配到文件（dry_run，待用户点执行）。"""
        ep_index = {(e["season"], e["episode"]): e["name"] for e in episodes}
        show_title = (show.title_orig if show else "") or ""
        logger.info("手动匹配: 剧集=%s 选中 %d 集", show_title, len(episodes))
        options = self._options()
        options.dry_run = True
        self._phase = "match"
        matched = 0
        for path in list(paths):
            info = extract_from_filename(path)
            title = ep_index.get((info.season, info.episode))
            if title is None:
                continue
            forced: Dict[str, object] = {"title_user": title}
            if show_title:
                forced["title_orig"] = show_title
            if info.season is not None:
                forced["season"] = info.season
            if info.episode is not None:
                forced["episode"] = info.episode
            item = self.processor.process_file(path, options, forced_values=forced)
            self._on_item_done(item)
            if item.status == "ok":
                self._manual_map.pop(path, None)
            matched += 1
        self.btn_process.setEnabled(bool(self._pending))
        self.lbl_status.setText(self._t("manual_matched", count=matched))

    def _start_match(self) -> None:
        """匹配阶段：dry_run 处理所有文件（只算目标名，不做文件操作）。"""
        self._fallback_manual = False
        paths = list(self._paths)
        if not paths:
            QMessageBox.information(self, self._t("err_title"), self._t("err_no_files"))
            return
        logger.info("开始匹配 %d 个文件 (kind=%s)", len(paths), self.kind)
        self._phase = "match"
        self._pending = []
        self.processor.reset_match_cache()  # 清空上一批视频匹配缓存
        options = self._options()
        options.dry_run = True
        self._set_busy(True)
        self._thread = ProcessingThread(self.processor, paths, options)
        self._thread.item_done.connect(self._on_item_done)
        self._thread.progress.connect(self.progress.setValue)
        self._thread.finished_all.connect(self._on_all_done)
        self._thread.start()

    def _execute_pending(self) -> None:
        """执行阶段：对已匹配（pending）项执行真实文件操作。"""
        items = [i for i in self._pending if i.status == "ok"]
        if not items:
            QMessageBox.information(self, self._t("err_title"), self._t("err_no_matched"))
            return
        logger.info("执行 %d 个已匹配项 (mode=%s)", len(items), self._options().mode)
        self._phase = "execute"
        options = self._options()
        options.dry_run = False
        self._set_busy(True)
        self._thread = ProcessingThread(self.processor, [], options, pending=items)
        self._thread.item_done.connect(self._on_item_done)
        self._thread.progress.connect(self.progress.setValue)
        self._thread.finished_all.connect(self._on_all_done)
        self._thread.start()

    def _set_busy(self, busy: bool) -> None:
        """切换忙碌状态：禁用操作按钮 + 进度条显隐。"""
        self.btn_auto_match.setEnabled(not busy)
        self.btn_manual_match.setEnabled(not busy)
        self.btn_process.setEnabled(not busy and bool(self._pending))
        self.btn_undo.setEnabled(not busy and bool(self.processor.operator.history))
        if busy:
            self.progress.setValue(0)
            self.progress.show()
        else:
            self.progress.hide()

    def _on_item_done(self, item: QueueItem) -> None:
        try:
            row = self._paths.index(item.path)
        except ValueError:
            return
        self.table.item(row, 1).setText(item.new_name)
        status_item = self.table.item(row, 2)
        # 状态 + manual 原因均 i18n
        status_text = self._t("st_" + item.status)
        if item.status == "manual" and item.reason:
            status_text = f"{status_text}: {self._i18n_reason(item.reason)}"
        status_item.setText(status_text)
        status_item.setToolTip(self._i18n_reason(item.reason) if item.reason else "")
        status_item.setForeground(
            Qt.GlobalColor.green if item.status == "ok"
            else Qt.GlobalColor.red if item.status == "error"
            else Qt.GlobalColor.darkYellow)
        if item.status == "ok":
            if self._phase == "match" and item not in self._pending:
                self._pending.append(item)
            elif self._phase == "execute":
                self._pending = [p for p in self._pending if p is not item]
        elif item.status == "manual":
            self._manual_map[item.path] = item
        # 匹配阶段：有可执行项即点亮执行按钮
        if self._phase != "execute":
            self.btn_process.setEnabled(bool(self._pending))

    def _on_all_done(self, ok: int, manual: int, err: int) -> None:
        phase = self._phase
        self._phase = "idle"
        self._set_busy(False)
        if phase == "match":
            self.lbl_status.setText(
                self._t("status_matched", ok=len(self._pending),
                        manual=manual, error=err))
            # 自动匹配失败（存在 manual 项）→ 自动打开手动匹配
            if getattr(self, "_fallback_manual", False) and manual > 0:
                self._fallback_manual = False
                self._open_manual_match_for_manual_items()
        else:
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
