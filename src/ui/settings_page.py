"""设置页面：界面语言、TMDB API Key、LLM 配置等可选项。"""

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
    PrimaryPushButton,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
)

from db import ConfigStore
from i18n import I18n


class SettingsPage(QWidget):
    """应用设置页面：保存到 ``config.json``。"""

    language_changed = pyqtSignal(str)

    def __init__(self, config: ConfigStore, i18n: I18n,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.i18n = i18n
        self._build_ui()
        self._retranslate()

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
        for code in I18n.available_langs():
            self.lang_combo.addItem(code)
        self.lang_combo.setCurrentText(self.config.get("language", "zh_CN"))
        lay.addWidget(self.lbl_lang)
        lay.addWidget(self.lang_combo)

        # ── TMDB ──────────────────────────────────────────────────
        self.lbl_tmdb = StrongBodyLabel(self._t("settings_tmdb"))
        self.tmdb_edit = LineEdit(self)
        self.tmdb_edit.setPlaceholderText(self._t("settings_tmdb_ph"))
        self.tmdb_edit.setText(self.config.get("tmdb.api_key", ""))
        lay.addWidget(self.lbl_tmdb)
        lay.addWidget(self.tmdb_edit)

        # ── LLM ───────────────────────────────────────────────────
        self.lbl_llm = StrongBodyLabel(self._t("settings_llm"))
        self.llm_enable = SwitchButton(self._t("settings_llm_enable"), self)
        self.llm_enable.setChecked(bool(self.config.get("llm.enabled", False)))
        lay.addWidget(self.lbl_llm)
        lay.addWidget(self.llm_enable)

        self.llm_provider_combo = ComboBox(self)
        self.llm_provider_combo.addItems(["openai", "anthropic"])
        self.llm_provider_combo.setCurrentText(
            self.config.get("llm.provider", "openai"))
        lay.addWidget(self.llm_provider_combo)

        self.llm_base_edit = LineEdit(self)
        self.llm_base_edit.setPlaceholderText("https://api.openai.com/v1")
        self.llm_base_edit.setText(self.config.get("llm.base_url", ""))
        lay.addWidget(self.llm_base_edit)

        self.llm_key_edit = LineEdit(self)
        self.llm_key_edit.setPlaceholderText(self._t("settings_llm_api_key"))
        self.llm_key_edit.setText(self.config.get("llm.api_key", ""))
        lay.addWidget(self.llm_key_edit)

        self.llm_model_edit = LineEdit(self)
        self.llm_model_edit.setPlaceholderText(self._t("settings_llm_model"))
        self.llm_model_edit.setText(self.config.get("llm.model", ""))
        lay.addWidget(self.llm_model_edit)

        # ── 保存 ──────────────────────────────────────────────────
        self.btn_save = PrimaryPushButton(self)
        self.btn_save.clicked.connect(self._save)
        lay.addWidget(self.btn_save)
        lay.addStretch()

        root.addWidget(card, 1)

    def _retranslate(self) -> None:
        self.title_label.setText(self._t("nav_settings"))
        self.lbl_lang.setText(self._t("settings_language"))
        self.lbl_tmdb.setText(self._t("settings_tmdb"))
        self.tmdb_edit.setPlaceholderText(self._t("settings_tmdb_ph"))
        self.lbl_llm.setText(self._t("settings_llm"))
        self.llm_enable.setText(self._t("settings_llm_enable"))
        self.llm_key_edit.setPlaceholderText(self._t("settings_llm_api_key"))
        self.llm_model_edit.setPlaceholderText(self._t("settings_llm_model"))
        self.btn_save.setText(self._t("settings_save"))

    def _save(self) -> None:
        """保存全部设置到 config.json 并广播语言变化。"""
        lang = self.lang_combo.currentText()
        old_lang = self.config.get("language", "zh_CN")
        self.config.set("language", lang)
        self.config.set("tmdb.api_key", self.tmdb_edit.text().strip())
        self.config.set("llm.enabled", self.llm_enable.isChecked())
        self.config.set("llm.provider", self.llm_provider_combo.currentText())
        self.config.set("llm.base_url", self.llm_base_edit.text().strip())
        self.config.set("llm.api_key", self.llm_key_edit.text().strip())
        self.config.set("llm.model", self.llm_model_edit.text().strip())

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
