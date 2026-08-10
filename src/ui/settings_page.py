"""设置页面：界面语言、字体、TMDB API Key、LLM 配置等可选项。

- 语言 / 字体 / TMDB / LLM 均通过右下角「保存」按钮统一落盘；有改动时保存按钮亮起，
  恢复为已保存的值时自动置灰。
- 「取消」放弃未保存的改动并恢复为已保存的值。
- 密钥输入框默认以密码形式（只显示字符数量），单击眼睛按钮切换明文。
"""

from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
)

from ui.fonts import (apply_font_family, bundled_font_family,
                      resolve_font_family, system_font_families)
from db import ConfigStore
from i18n import I18n

# 语言下拉的可选 code；"system" 表示跟随系统语言
_LANG_CODES = ("system", "zh_CN", "zh_TW", "en_US")
# AI 提供方（内部 code，显示名走 i18n，注意大小写）
PROVIDERS = ("openai", "anthropic", "deepseek", "qwen",
             "moonshot", "doubao", "zhipu", "siliconflow")


class TogglePasswordLineEdit(PasswordLineEdit):
    """点击眼睛按钮切换明文/密文（默认密文）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPasswordVisible(False)
        self.viewButton.clicked.connect(self._toggle)

    def _toggle(self) -> None:
        self.setPasswordVisible(not self.isPasswordVisible())

    def eventFilter(self, obj, e):
        # 不拦截 viewButton 的鼠标事件，交给 clicked 信号实现点击切换
        if obj is self.viewButton:
            return False
        return super().eventFilter(obj, e)


class SettingsPage(QWidget):
    """应用设置页面：保存到 ``config.json``。"""

    language_changed = pyqtSignal(str)

    def __init__(self, config: ConfigStore, i18n: I18n,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.i18n = i18n
        self._saved: Dict = {}
        self._build_ui()
        self._load_values_from_config()
        self._retranslate()
        self._saved = self._snapshot()
        self.btn_save.setEnabled(False)

    def _t(self, key: str, **kw) -> str:
        return self.i18n.t(key, **kw)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        self.title_label = SubtitleLabel(self._t("nav_settings"))
        root.addWidget(self.title_label)

        # 内容放入滚动区：内容放得下时滚动条自动隐藏，超出时显示
        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        card = CardWidget()
        self.scroll.setWidget(card)
        root.addWidget(self.scroll, 1)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)

        # ── 界面语言 ──────────────────────────────────────────────
        self.lbl_lang = StrongBodyLabel(self._t("settings_language"))
        self.lang_combo = ComboBox(self)
        for code in _LANG_CODES:
            self.lang_combo.addItem(self._lang_label(code), userData=code)
        self.lang_combo.currentIndexChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_lang)
        lay.addWidget(self.lang_combo)

        # ── 字体 ──────────────────────────────────────────────────
        self.lbl_font = StrongBodyLabel(self._t("settings_font"))
        self.font_combo = ComboBox(self)
        self.font_combo.addItem(self._font_default_label(), userData="")
        for fam in system_font_families():
            self.font_combo.addItem(fam, userData=fam)
        self.font_combo.currentIndexChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_font)
        lay.addWidget(self.font_combo)

        # ── TMDB ──────────────────────────────────────────────────
        self.lbl_tmdb = StrongBodyLabel(self._t("settings_tmdb"))
        self.tmdb_edit = TogglePasswordLineEdit(self)
        self.tmdb_edit.textChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_tmdb)
        lay.addWidget(self.tmdb_edit)

        # ── LLM ───────────────────────────────────────────────────
        self.lbl_llm = StrongBodyLabel(self._t("settings_llm"))
        self.llm_enable = SwitchButton(self._t("settings_llm_enable"), self)
        self.llm_enable.checkedChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_llm)
        lay.addWidget(self.llm_enable)

        self.lbl_llm_provider = StrongBodyLabel(self._t("llm_provider"))
        self.llm_provider_combo = ComboBox(self)
        for code in PROVIDERS:
            self.llm_provider_combo.addItem(self._t("provider_" + code), userData=code)
        self.llm_provider_combo.currentIndexChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_llm_provider)
        lay.addWidget(self.llm_provider_combo)

        self.lbl_llm_base = StrongBodyLabel(self._t("llm_base_url"))
        self.llm_base_edit = LineEdit(self)
        self.llm_base_edit.setPlaceholderText("https://api.openai.com/v1")
        self.llm_base_edit.textChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_llm_base)
        lay.addWidget(self.llm_base_edit)

        self.lbl_llm_key = StrongBodyLabel(self._t("settings_llm_api_key"))
        self.llm_key_edit = TogglePasswordLineEdit(self)
        self.llm_key_edit.textChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_llm_key)
        lay.addWidget(self.llm_key_edit)

        self.lbl_llm_model = StrongBodyLabel(self._t("settings_llm_model"))
        self.llm_model_edit = LineEdit(self)
        self.llm_model_edit.textChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_llm_model)
        lay.addWidget(self.llm_model_edit)

        # ── 输出目录 ──────────────────────────────────────────────
        self.lbl_output_mode = StrongBodyLabel(self._t("output_mode_label"))
        self.output_mode_combo = ComboBox(self)
        self.output_mode_combo.addItem(self._t("output_mode_custom"), userData="custom")
        self.output_mode_combo.addItem(self._t("output_mode_library"), userData="library")
        self.output_mode_combo.currentIndexChanged.connect(self._on_output_mode_changed)
        lay.addWidget(self.lbl_output_mode)
        lay.addWidget(self.output_mode_combo)

        # 媒体库根目录（仅 library 模式显示）
        self.roots_box = QWidget(self)
        rl = QVBoxLayout(self.roots_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        self.root_labels: Dict[str, StrongBodyLabel] = {}
        self.root_edits: Dict[str, LineEdit] = {}
        self.root_btns: Dict[str, PushButton] = {}
        for code in ("movie", "tv_anime", "tv_drama", "tv_doc", "music"):
            row = QHBoxLayout()
            lbl = StrongBodyLabel(self._t("output_root_" + code))
            edit = LineEdit(self)
            btn = PushButton(self)
            btn.setText("…")
            btn.clicked.connect(lambda _=False, c=code: self._choose_root(c))
            row.addWidget(lbl)
            row.addWidget(edit, 1)
            row.addWidget(btn)
            rl.addLayout(row)
            self.root_labels[code] = lbl
            self.root_edits[code] = edit
            self.root_btns[code] = btn
        lay.addWidget(self.roots_box)

        # ── 音乐 ──────────────────────────────────────────────────
        self.lbl_artist_sep = StrongBodyLabel(self._t("artist_separator_label"))
        self.artist_sep_edit = LineEdit(self)
        self.artist_sep_edit.setPlaceholderText("、,，")
        self.artist_sep_edit.textChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_artist_sep)
        lay.addWidget(self.artist_sep_edit)

        # ── 底部按钮行（右下角）：保存 + 取消 ──────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_save = PrimaryPushButton(self)
        self.btn_cancel = PushButton(self)
        self.btn_save.clicked.connect(self._save)
        self.btn_cancel.clicked.connect(self._cancel)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_cancel)
        lay.addLayout(btn_row)
        lay.addStretch()

    def _retranslate(self) -> None:
        self.title_label.setText(self._t("nav_settings"))
        self.lbl_lang.setText(self._t("settings_language"))
        self.lbl_font.setText(self._t("settings_font"))
        self.lbl_tmdb.setText(self._t("settings_tmdb"))
        self.lbl_llm.setText(self._t("settings_llm"))
        self.llm_enable.setText(self._t("settings_llm_enable"))
        self.lbl_llm_provider.setText(self._t("llm_provider"))
        self.lbl_llm_base.setText(self._t("llm_base_url"))
        self.lbl_llm_key.setText(self._t("settings_llm_api_key"))
        self.lbl_llm_model.setText(self._t("settings_llm_model"))
        self.btn_save.setText(self._t("settings_save"))
        self.btn_cancel.setText(self._t("settings_cancel"))
        self.lbl_output_mode.setText(self._t("output_mode_label"))
        self.output_mode_combo.blockSignals(True)
        self.output_mode_combo.setItemText(0, self._t("output_mode_custom"))
        self.output_mode_combo.setItemText(1, self._t("output_mode_library"))
        self.output_mode_combo.blockSignals(False)
        for code in ("movie", "tv_anime", "tv_drama", "tv_doc", "music"):
            self.root_labels[code].setText(self._t("output_root_" + code))
        self.lbl_artist_sep.setText(self._t("artist_separator_label"))
        # 语言下拉：更新"跟随系统"文案并保持选中
        cur = self.lang_combo.currentData() or self.config.get("language", "zh_CN")
        self.lang_combo.blockSignals(True)
        self.lang_combo.setItemText(0, self._t("settings_lang_system"))
        self.lang_combo.blockSignals(False)
        idx = self._index_of_data(self.lang_combo, cur)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        # 字体下拉：更新"默认"文案并保持选中
        cur_font = self.font_combo.currentData() or self.config.get("font_family", "")
        self.font_combo.blockSignals(True)
        self.font_combo.setItemText(0, self._font_default_label())
        self.font_combo.blockSignals(False)
        f_idx = self._index_of_data(self.font_combo, cur_font)
        self.font_combo.setCurrentIndex(f_idx if f_idx >= 0 else 0)
        # 提供方下拉：更新显示名并保持选中
        cur_prov = self.llm_provider_combo.currentData()
        self.llm_provider_combo.blockSignals(True)
        for i, code in enumerate(PROVIDERS):
            self.llm_provider_combo.setItemText(i, self._t("provider_" + code))
        self.llm_provider_combo.blockSignals(False)
        p_idx = self._index_of_data(self.llm_provider_combo, cur_prov)
        if p_idx >= 0:
            self.llm_provider_combo.setCurrentIndex(p_idx)

    # ── 语言 / 字体辅助 ──────────────────────────────────────────────────

    @staticmethod
    def _index_of_data(combo, code: str) -> int:
        for i in range(combo.count()):
            if combo.itemData(i) == code:
                return i
        return -1

    def _lang_label(self, code: str) -> str:
        if code == "system":
            return self._t("settings_lang_system")
        return code

    def _font_default_label(self) -> str:
        name = bundled_font_family() or "System"
        return self._t("settings_font_default", name=name)

    # ── 输出目录 / 音乐辅助 ──────────────────────────────────────────────

    def _on_output_mode_changed(self, _idx: int) -> None:
        self._update_roots_visibility()
        self._mark_dirty()

    def _update_roots_visibility(self) -> None:
        is_library = self.output_mode_combo.currentData() == "library"
        self.roots_box.setVisible(is_library)

    def _choose_root(self, code: str) -> None:
        folder = QFileDialog.getExistingDirectory(self, self._t("output_root_" + code))
        if folder:
            self.root_edits[code].setText(folder)
            self._mark_dirty()

    # ── 快照 / 改动检测 / 保存 / 取消 ────────────────────────────────────

    def _snapshot(self) -> Dict:
        """当前 config 的已保存值快照，用于判定是否有未保存改动。"""
        return {
            "language": self.config.get("language", "zh_CN"),
            "font_family": self.config.get("font_family", ""),
            "tmdb.api_key": self.config.get("tmdb.api_key", ""),
            "llm.enabled": bool(self.config.get("llm.enabled", False)),
            "llm.provider": self.config.get("llm.provider", "openai"),
            "llm.base_url": self.config.get("llm.base_url", ""),
            "llm.api_key": self.config.get("llm.api_key", ""),
            "llm.model": self.config.get("llm.model", ""),
            "output.mode": self.config.get("output.mode", "custom"),
            "output.roots.movie": (self.config.get("output.roots", {}) or {}).get("movie", ""),
            "output.roots.tv_anime": (self.config.get("output.roots", {}) or {}).get("tv_anime", ""),
            "output.roots.tv_drama": (self.config.get("output.roots", {}) or {}).get("tv_drama", ""),
            "output.roots.tv_doc": (self.config.get("output.roots", {}) or {}).get("tv_doc", ""),
            "output.roots.music": (self.config.get("output.roots", {}) or {}).get("music", ""),
            "music.artist_separators": self.config.get("music.artist_separators", ""),
        }

    def _current(self) -> Dict:
        """各控件当前值。"""
        return {
            "language": self.lang_combo.currentData() or "zh_CN",
            "font_family": self.font_combo.currentData() or "",
            "tmdb.api_key": self.tmdb_edit.text().strip(),
            "llm.enabled": self.llm_enable.isChecked(),
            "llm.provider": self.llm_provider_combo.currentData()
            or self.llm_provider_combo.currentText(),
            "llm.base_url": self.llm_base_edit.text().strip(),
            "llm.api_key": self.llm_key_edit.text().strip(),
            "llm.model": self.llm_model_edit.text().strip(),
            "output.mode": self.output_mode_combo.currentData() or "custom",
            "output.roots.movie": self.root_edits["movie"].text().strip(),
            "output.roots.tv_anime": self.root_edits["tv_anime"].text().strip(),
            "output.roots.tv_drama": self.root_edits["tv_drama"].text().strip(),
            "output.roots.tv_doc": self.root_edits["tv_doc"].text().strip(),
            "output.roots.music": self.root_edits["music"].text().strip(),
            "music.artist_separators": self.artist_sep_edit.text().strip(),
        }

    def _mark_dirty(self, *_args) -> None:
        """任一设置变化时对比快照：与已保存一致则置灰，否则亮起。"""
        self.btn_save.setEnabled(self._current() != self._saved)

    def _load_values_from_config(self) -> None:
        """从 config 重新加载全部控件值（供构造与取消使用），不触发改动标记。"""
        cur = self.config.get("language", "zh_CN")
        idx = self._index_of_data(self.lang_combo, cur)
        self.lang_combo.blockSignals(True)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.blockSignals(False)

        cur_font = self.config.get("font_family", "")
        f_idx = self._index_of_data(self.font_combo, cur_font)
        self.font_combo.blockSignals(True)
        self.font_combo.setCurrentIndex(f_idx if f_idx >= 0 else 0)
        self.font_combo.blockSignals(False)

        self.tmdb_edit.blockSignals(True)
        self.tmdb_edit.setText(self.config.get("tmdb.api_key", ""))
        self.tmdb_edit.blockSignals(False)

        self.llm_enable.blockSignals(True)
        self.llm_enable.setChecked(bool(self.config.get("llm.enabled", False)))
        self.llm_enable.blockSignals(False)

        prov = self.config.get("llm.provider", "openai")
        p_idx = self._index_of_data(self.llm_provider_combo, prov)
        self.llm_provider_combo.blockSignals(True)
        self.llm_provider_combo.setCurrentIndex(p_idx if p_idx >= 0 else 0)
        self.llm_provider_combo.blockSignals(False)

        self.llm_base_edit.blockSignals(True)
        self.llm_base_edit.setText(self.config.get("llm.base_url", ""))
        self.llm_base_edit.blockSignals(False)

        self.llm_key_edit.blockSignals(True)
        self.llm_key_edit.setText(self.config.get("llm.api_key", ""))
        self.llm_key_edit.blockSignals(False)

        self.llm_model_edit.blockSignals(True)
        self.llm_model_edit.setText(self.config.get("llm.model", ""))
        self.llm_model_edit.blockSignals(False)

        om = self.config.get("output.mode", "custom")
        om_idx = self._index_of_data(self.output_mode_combo, om)
        self.output_mode_combo.blockSignals(True)
        self.output_mode_combo.setCurrentIndex(om_idx if om_idx >= 0 else 0)
        self.output_mode_combo.blockSignals(False)
        roots = self.config.get("output.roots", {}) or {}
        for code in ("movie", "tv_anime", "tv_drama", "tv_doc", "music"):
            self.root_edits[code].blockSignals(True)
            self.root_edits[code].setText(str(roots.get(code, "")))
            self.root_edits[code].blockSignals(False)
        self.artist_sep_edit.blockSignals(True)
        self.artist_sep_edit.setText(self.config.get("music.artist_separators", ""))
        self.artist_sep_edit.blockSignals(False)
        self._update_roots_visibility()

    def _save(self) -> None:
        """保存全部设置到 config.json；语言/字体变化时应用并广播。"""
        lang = self.lang_combo.currentData() or "zh_CN"
        old_lang = self.config.get("language", "zh_CN")
        font_family = self.font_combo.currentData() or ""
        provider = self.llm_provider_combo.currentData() \
            or self.llm_provider_combo.currentText()
        self.config.set("language", lang)
        self.config.set("font_family", font_family)
        self.config.set("tmdb.api_key", self.tmdb_edit.text().strip())
        self.config.set("llm.enabled", self.llm_enable.isChecked())
        self.config.set("llm.provider", provider)
        self.config.set("llm.base_url", self.llm_base_edit.text().strip())
        self.config.set("llm.api_key", self.llm_key_edit.text().strip())
        self.config.set("llm.model", self.llm_model_edit.text().strip())
        roots = {
            "movie": self.root_edits["movie"].text().strip(),
            "tv_anime": self.root_edits["tv_anime"].text().strip(),
            "tv_drama": self.root_edits["tv_drama"].text().strip(),
            "tv_doc": self.root_edits["tv_doc"].text().strip(),
            "music": self.root_edits["music"].text().strip(),
        }
        self.config.set("output.mode", self.output_mode_combo.currentData() or "custom")
        self.config.set("output.roots", roots)
        self.config.set("music.artist_separators", self.artist_sep_edit.text().strip())

        self._saved = self._snapshot()
        self.btn_save.setEnabled(False)
        # 应用字体（配置为空时回退内置字体）
        apply_font_family(resolve_font_family(font_family))
        if lang != old_lang:
            self.language_changed.emit(lang)

        InfoBar.success(
            title=self._t("settings_saved"),
            content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self.window(),
        )

    def _cancel(self) -> None:
        """放弃未保存的改动，恢复为已保存的值。"""
        self._load_values_from_config()
        self._saved = self._snapshot()
        self.btn_save.setEnabled(False)
