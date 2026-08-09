"""标签 + 开关 的一行组件（如“注入封面”开关）。"""

from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QWidget


class ToggleRow(QWidget):
    """左侧文字 + 右侧开关的一行控件。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label = QLabel(text)
        self.switch = QCheckBox()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)
        lay.addStretch()
        lay.addWidget(self.switch)

    def setText(self, text: str) -> None:
        self.label.setText(text)

    def isChecked(self) -> bool:
        return self.switch.isChecked()

    def setChecked(self, checked: bool) -> None:
        self.switch.setChecked(checked)

    def onToggled(self, fn) -> None:
        self.switch.toggled.connect(fn)
