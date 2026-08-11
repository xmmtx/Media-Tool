"""全局日志系统。

- 启动时调用 :func:`setup_logging`：把上次会话的 ``logs/latest.log`` 归档为
  ``logs/YYYY-MM-DD-N.zip``（压缩包内文件名为 ``YYYY-MM-DD-N.log``），再写新的
  ``logs/latest.log``；控制台仅显示 WARNING 以上。
- 设置页"启用日志"开关可动态启停文件写入（:func:`set_logging_enabled`）。
"""

from __future__ import annotations

import logging
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# 日志目录：安装版写到 %APPDATA%/MediaTool/logs（Program Files 只读），开发版沿用 src/logs
if getattr(sys, "frozen", False):
    LOGS_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "MediaTool" / "logs"
else:
    LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
_LATEST_NAME = "latest.log"

_FMT = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S")

_FILE_HANDLER: logging.Handler | None = None


def _archive_previous(now: datetime | None = None) -> None:
    """启动归档：把已有 ``latest.log`` 压缩成 ``YYYY-MM-DD-N.zip``。

    序号 N 为当天已归档数 +1；压缩包内文件名为 ``YYYY-MM-DD-N.log``。
    """
    latest = LOGS_DIR / _LATEST_NAME
    if not latest.exists() or latest.stat().st_size == 0:
        return
    now = now or datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    n = 1
    for p in LOGS_DIR.glob(f"{date_str}-*.zip"):
        try:
            idx = int(p.stem.rsplit("-", 1)[1])
        except (ValueError, IndexError):
            continue
        n = max(n, idx + 1)
    archive = f"{date_str}-{n}"
    with zipfile.ZipFile(LOGS_DIR / f"{archive}.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(latest, arcname=f"{archive}.log")
    try:
        latest.unlink(missing_ok=True)
    except OSError as e:
        # Windows 下文件被其他程序（如编辑器）占用时无法删除：不阻塞启动，
        # 保留原文件，本次日志追加到其后（下次启动再尝试归档）
        logging.getLogger("logging_setup").warning(
            "latest.log 无法删除（%s），本次日志将追加到原文件", e)


def set_logging_enabled(enabled: bool, level: int = logging.INFO) -> None:
    """动态启停文件日志（设置页"启用日志"开关）。"""
    global _FILE_HANDLER
    root = logging.getLogger()
    if enabled and _FILE_HANDLER is None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        _archive_previous()
        _FILE_HANDLER = logging.FileHandler(LOGS_DIR / _LATEST_NAME, encoding="utf-8")
        _FILE_HANDLER.setLevel(level)
        _FILE_HANDLER.setFormatter(_FMT)
        root.addHandler(_FILE_HANDLER)
        root.info("日志已启用")
    elif not enabled and _FILE_HANDLER is not None:
        root.removeHandler(_FILE_HANDLER)
        try:
            _FILE_HANDLER.close()
        except Exception:
            pass
        _FILE_HANDLER = None
        root.warning("日志已禁用")


def setup_logging(level: int = logging.INFO, filename: str = _LATEST_NAME,
                  enabled: bool = True) -> logging.Logger:
    """初始化根 logger：文件（latest.log，启动前归档旧档）+ 控制台。

    ``enabled=False`` 时只保留控制台输出，不写文件（对应设置页"启用日志"）。
    """
    global _FILE_HANDLER
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    _FILE_HANDLER = None
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(_FMT)
    console.setLevel(logging.WARNING)  # 控制台只显示 WARNING 以上，避免刷屏
    root.addHandler(console)

    if enabled:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        _archive_previous()
        _FILE_HANDLER = logging.FileHandler(LOGS_DIR / filename, encoding="utf-8")
        _FILE_HANDLER.setLevel(level)
        _FILE_HANDLER.setFormatter(_FMT)
        root.addHandler(_FILE_HANDLER)
    root.info("=== application started ===")
    return root

