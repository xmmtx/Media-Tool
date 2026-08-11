"""应用可写数据目录。

- 开发模式（``python main.py``）：沿用 ``src/db``，行为不变。
- 安装版（PyInstaller frozen）：写到 ``%APPDATA%\\MediaTool``，避免写入
  Program Files 只读目录失败（config.json / manual_queue.json / logs /
  subgroups.json 用户副本）。

:func:`app_data_dir` 返回可写根目录（已自动创建）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否 PyInstaller 打包运行（frozen）。"""
    return bool(getattr(sys, "frozen", False))


def app_data_dir() -> Path:
    """返回可写数据目录：安装版 ``%APPDATA%/MediaTool``，开发版 ``src/db``。"""
    if is_frozen():
        base = Path(os.environ.get("APPDATA") or Path.home())
        d = base / "MediaTool"
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path(__file__).resolve().parent  # src/db
