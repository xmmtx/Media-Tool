"""手动干预队列持久化（JSON）。

把待人工处理的失败项（电影匹配失败 / 组未识别 / 音乐缺标签）写入
``manual_queue.json``，应用重启后可恢复继续处理。
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from .json_store import JsonStore
from .paths import app_data_dir


class ManualQueueStore(JsonStore):
    """手动队列的 JSON 持久化：``{"items": [ {path, kind, format, ...}, ... ]}``。"""

    DEFAULT = {"items": []}

    def __init__(self, path: Path | None = None) -> None:
        path = path or app_data_dir() / "manual_queue.json"
        super().__init__(path, default_data=self.DEFAULT)

    def save_items(self, items: List[dict]) -> None:
        with self._lock:
            self._data["items"] = items
            self.save()

    def load_items(self) -> List[dict]:
        return list(self._data.get("items", []))
