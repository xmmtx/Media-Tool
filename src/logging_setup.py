"""全局日志系统：写入 ``logs/app.log``（滚动）并同步输出到控制台。

- 启动时调用 :func:`setup_logging`，此后各模块用 ``logging.getLogger(__name__)`` 记录。
- 日志覆盖配置修改、字幕组修改、文件匹配各环节与 UI 变化，便于排查。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def setup_logging(level: int = logging.INFO, filename: str = "app.log") -> logging.Logger:
    """初始化根 logger：文件（滚动）+ 控制台。重复调用不会重复加 handler。"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return root
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(LOGS_DIR / filename, maxBytes=5 * 1024 * 1024,
                             backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(level)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.WARNING)  # 控制台只显示 WARNING 以上，避免刷屏

    root.addHandler(fh)
    root.addHandler(sh)
    root.info("=== application started ===")
    return root
