"""只读预览框（展示生成的示例文件名）。"""

from __future__ import annotations

from PyQt6.QtWidgets import QPlainTextEdit, QWidget


class PreviewBox(QPlainTextEdit):
    """多行只读文本，用于展示示例/预览。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumHeight(120)
        self.setPlaceholderText("")

    def set_preview(self, text: str) -> None:
        self.setPlainText(text)
