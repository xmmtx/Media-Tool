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

# ── Qt 消息过滤：只静默已知无害警告，其余 Qt 警告照常输出 ──────────
# libpng iCCP（资源图片 sRGB profile 配置问题）与 QFont 字号计算警告
# 均在 Qt 侧产生，且无独立 category，只能按文本过滤。
import logging as _logging

from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

_HARMLESS_WARNINGS = ("libpng warning", "iCCP", "Point size")


def _qt_message_handler(mode: QtMsgType, context, message: str) -> None:
    if mode == QtMsgType.QtWarningMsg and any(
        t in (message or "") for t in _HARMLESS_WARNINGS
    ):
        return
    loc = f"{context.file}:{context.line} " if context.file else ""
    cat = f"({context.category}) " if context.category else ""
    sys.stderr.write(f"{loc}{cat}{message}\n")


qInstallMessageHandler(_qt_message_handler)


LOGGER_LEVEL = os.environ.get("MEDIA_TOOL_LOG_LEVEL", "INFO")


def main() -> int:
    import logging
    import signal

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from db import ConfigStore
    from ui.fonts import bundled_font_family, resolve_font_family, apply_font_family
    from ui.main_window import MainWindow, enable_high_dpi

    setup_logging(getattr(logging, LOGGER_LEVEL.upper(), logging.INFO))
    logger = logging.getLogger("main")
    enable_high_dpi()                    # 必须在 QApplication 之前
    app = QApplication(sys.argv)

    # 终端 Ctrl+C 退出：Qt 事件循环阻塞时不会执行 Python 字节码，
    # SIGINT 处理器需靠定时器周期性唤醒才能被调用 → 触发 app.quit()
    _sigint_timer = QTimer()
    _sigint_timer.timeout.connect(lambda: None)
    _sigint_timer.start(200)

    def _on_sigint(*_args) -> None:
        logger.info("收到 Ctrl+C，退出应用")
        app.quit()

    signal.signal(signal.SIGINT, _on_sigint)

    # 加载内置字体（reference）并按配置应用默认字体
    bundled_font_family()
    cfg = ConfigStore()
    apply_font_family(resolve_font_family(cfg.get("font_family", "")))

    window = MainWindow(config=cfg)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
