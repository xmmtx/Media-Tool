"""JSON 文件持久化存储基类。

- 原子写入：先写临时文件再 ``os.replace``，避免中途崩溃损坏数据。
- 线程安全：内部持有一把 ``RLock``，所有读写/落盘操作均可安全地在
  后台线程（QThread / ThreadPool）中调用。
- 自动建目录；文件缺失时用默认数据初始化；文件损坏时备份后重建。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Union


class JsonStore:
    """单个 JSON 文件的读写封装。

    - 根节点可以是 ``dict`` 或 ``list``。
    - ``default_data`` 提供默认结构：当文件不存在时写入；当文件存在且为
      dict 时，会与默认值做浅合并，保证新增配置项也能自动补齐。
    """

    def __init__(
        self,
        path: Union[str, Path],
        default_data: Union[Dict, list, None] = None,
        indent: int = 2,
    ) -> None:
        self.path = Path(path)
        self.indent = indent
        self._default = default_data
        self._lock = threading.RLock()
        self._data: Union[Dict, list] = default_data if default_data is not None else {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.load()

    # ── 读写 ──────────────────────────────────────────────────────────────

    def load(self) -> "JsonStore":
        with self._lock:
            if not self.path.exists():
                self.save()
                return self
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._backup_corrupt()
                raw = {}
            if isinstance(self._default, dict) and isinstance(raw, dict):
                merged = dict(self._default)
                merged.update(raw)
                self._data = merged
            else:
                self._data = raw
            return self

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=self.indent),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)

    def _backup_corrupt(self) -> None:
        try:
            bak = self.path.with_suffix(self.path.suffix + ".bak")
            self.path.replace(bak)
        except OSError:
            pass

    # ── 访问 ──────────────────────────────────────────────────────────────

    @property
    def data(self) -> Union[Dict, list]:
        """返回内部数据引用（修改后需调用 ``save()`` 才会落盘）。"""
        with self._lock:
            return self._data
