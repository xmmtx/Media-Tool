"""媒体管理器入口。

- 在创建 QApplication 之前启用 High-DPI 自动缩放（``PassThrough``）。
- 把 ``src/`` 加入 ``sys.path``，保证从任何目录运行都能导入。
- 启动 ``src.ui.MainWindow``（PyQt6）。

运行：``python main.py``
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# 抑制 Qt 的无害警告（libpng iCCP / QFont 等），真实问题由本项目日志记录
os.environ.setdefault("QT_LOGGING_RULES", "*.warning=false")


LOGGER_LEVEL = os.environ.get("MEDIA_TOOL_LOG_LEVEL", "INFO")


def main() -> int:
    import logging

    from logging_setup import setup_logging
    from PyQt6.QtWidgets import QApplication

    from db import ConfigStore
    from ui.fonts import bundled_font_family, resolve_font_family, apply_font_family
    from ui.main_window import MainWindow, enable_high_dpi

    setup_logging(getattr(logging, LOGGER_LEVEL.upper(), logging.INFO))
    enable_high_dpi()                    # 必须在 QApplication 之前
    app = QApplication(sys.argv)

    # 加载内置字体（reference）并按配置应用默认字体
    bundled_font_family()
    cfg = ConfigStore()
    apply_font_family(resolve_font_family(cfg.get("font_family", "")))

    window = MainWindow(config=cfg)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
