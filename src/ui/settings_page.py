"""设置页面：界面语言、TMDB API Key、LLM 配置等可选项。

- 语言与 TMDB/LLM 均通过右下角「保存」按钮统一落盘；有改动时保存按钮亮起。
- 「取消」放弃未保存的改动并恢复为已保存的值。
- 密钥输入框默认以密码形式（只显示字符数量），眼睛按钮可查看明文。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
)

from db import ConfigStore
from i18n import I18n

# 语言下拉的可选 code；"system" 表示跟随系统语言
_LANG_CODES = ("system", "zh_CN", "zh_TW", "en_US")


class SettingsPage(QWidget):
    """应用设置页面：保存到 ``config.json``。"""

    language_changed = pyqtSignal(str)

    def __init__(self, config: ConfigStore, i18n: I18n,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.i18n = i18n
        self._build_ui()
        self._load_values_from_config()
        self._retranslate()
        self.btn_save.setEnabled(False)

    def _t(self, key: str, **kw) -> str:
        return self.i18n.t(key, **kw)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        self.title_label = SubtitleLabel(self._t("nav_settings"))
        root.addWidget(self.title_label)

        card = CardWidget(self)
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

        # ── TMDB ──────────────────────────────────────────────────
        self.lbl_tmdb = StrongBodyLabel(self._t("settings_tmdb"))
        self.tmdb_edit = PasswordLineEdit(self)
        self.tmdb_edit.textChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_tmdb)
        lay.addWidget(self.tmdb_edit)

        # ── LLM ───────────────────────────────────────────────────
        self.lbl_llm = StrongBodyLabel(self._t("settings_llm"))
        self.llm_enable = SwitchButton(self._t("settings_llm_enable"), self)
        self.llm_enable.checkedChanged.connect(self._mark_dirty)
        lay.addWidget(self.lbl_llm)
        lay.addWidget(self.llm_enable)

        self.llm_provider_combo = ComboBox(self)
        self.llm_provider_combo.addItems(["openai", "anthropic"])
        self.llm_provider_combo.currentIndexChanged.connect(self._mark_dirty)
        lay.addWidget(self.llm_provider_combo)

        self.llm_base_edit = LineEdit(self)
        self.llm_base_edit.setPlaceholderText("https://api.openai.com/v1")
        self.llm_base_edit.textChanged.connect(self._mark_dirty)
        lay.addWidget(self.llm_base_edit)

        self.llm_key_edit = PasswordLineEdit(self)
        self.llm_key_edit.textChanged.connect(self._mark_dirty)
        lay.addWidget(self.llm_key_edit)

        self.llm_model_edit = LineEdit(self)
        self.llm_model_edit.textChanged.connect(self._mark_dirty)
        lay.addWidget(self.llm_model_edit)

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

        root.addWidget(card, 1)

    def _retranslate(self) -> None:
        self.title_label.setText(self._t("nav_settings"))
        self.lbl_lang.setText(self._t("settings_language"))
        self.lbl_tmdb.setText(self._t("settings_tmdb"))
        self.lbl_llm.setText(self._t("settings_llm"))
        self.llm_enable.setText(self._t("settings_llm_enable"))
        self.btn_save.setText(self._t("settings_save"))
        self.btn_cancel.setText(self._t("settings_cancel"))
        # 更新语言下拉的"跟随系统"文案并保持当前选中
        cur = self.lang_combo.currentData() or self.config.get("language", "zh_CN")
        self.lang_combo.blockSignals(True)
        self.lang_combo.setItemText(0, self._t("settings_lang_system"))
        self.lang_combo.blockSignals(False)
        idx = self._index_of_data(self.lang_combo, cur)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)

    # ── 语言辅助 ──────────────────────────────────────────────────────────

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

    # ── 保存 / 取消 / 改动检测 ────────────────────────────────────────────

    def _mark_dirty(self, *_args) -> None:
        """任一设置变化时点亮保存按钮。"""
        self.btn_save.setEnabled(True)

    def _load_values_from_config(self) -> None:
        """从 config 重新加载全部控件值（供构造与取消使用），不触发改动标记。"""
        cur = self.config.get("language", "zh_CN")
        idx = self._index_of_data(self.lang_combo, cur)
        self.lang_combo.blockSignals(True)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.blockSignals(False)

        self.tmdb_edit.blockSignals(True)
        self.tmdb_edit.setText(self.config.get("tmdb.api_key", ""))
        self.tmdb_edit.blockSignals(False)

        self.llm_enable.blockSignals(True)
        self.llm_enable.setChecked(bool(self.config.get("llm.enabled", False)))
        self.llm_enable.blockSignals(False)

        self.llm_provider_combo.blockSignals(True)
        self.llm_provider_combo.setCurrentText(self.config.get("llm.provider", "openai"))
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

    def _save(self) -> None:
        """保存全部设置到 config.json；语言变化时广播重译。"""
        lang = self.lang_combo.currentData() or "zh_CN"
        old_lang = self.config.get("language", "zh_CN")
        self.config.set("language", lang)
        self.config.set("tmdb.api_key", self.tmdb_edit.text().strip())
        self.config.set("llm.enabled", self.llm_enable.isChecked())
        self.config.set("llm.provider", self.llm_provider_combo.currentText())
        self.config.set("llm.base_url", self.llm_base_edit.text().strip())
        self.config.set("llm.api_key", self.llm_key_edit.text().strip())
        self.config.set("llm.model", self.llm_model_edit.text().strip())

        self.btn_save.setEnabled(False)
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
        self.btn_save.setEnabled(False)
