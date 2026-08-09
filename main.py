"""媒体管理器入口。

- 在创建 QApplication 之前启用 High-DPI 自动缩放（``PassThrough``）。
- 把 ``src/`` 加入 ``sys.path``，保证从任何目录运行都能导入。
- 启动 ``src.ui.MainWindow``（PyQt6）。

运行：``python main.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    from ui.main_window import MainWindow, enable_high_dpi

    enable_high_dpi()                    # 必须在 QApplication 之前
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
