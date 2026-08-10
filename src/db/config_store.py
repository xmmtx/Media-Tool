"""应用配置，JSON 持久化（独立于字幕组字典的另一个 JSON 文件）。

数据结构（``config.json``）::

    {
      "language": "zh_CN",
      "tmdb": {"api_key": ""},
      "llm": {
        "enabled": false,
        "provider": "openai",
        "base_url": "",
        "api_key": "",
        "model": ""
      },
      "file_ops": {"fallback_on_cross_device": "copy"},
      "formats": {
        "movie": "{title_orig} ({year}) - {title_user} - [{group} {resolution}]",
        "tv": "{title_orig} S{season_2d}E{episode_2d} {title_user} - [{group} {resolution}]"
      },
      "music": {"inject_cover": true}
    }

- 通过点路径访问：``config.get("llm.api_key")`` / ``config.set("tmdb.api_key", "...")``。
- 新增配置项只需修改 ``DEFAULT``，已存在的文件加载时会自动合并补齐缺失键。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .json_store import JsonStore


class ConfigStore(JsonStore):
    """应用配置存储。"""

    DEFAULT: Dict = {
        "language": "zh_CN",
        "font_family": "",  # 空表示使用内置字体（reference 字体）
        "tmdb": {"api_key": ""},
        "llm": {
            "enabled": False,
            "provider": "openai",
            "base_url": "",
            "api_key": "",
            "model": "",
        },
        "file_ops": {"fallback_on_cross_device": "copy"},
        "formats": {
            "movie": "{title_orig} ({year}) - {title_user} - [{group} {resolution}]",
            "tv": "{title_orig} S{season_2d}E{episode_2d} {title_user} - [{group} {resolution}]",
        },
        "music": {"inject_cover": True, "artist_separators": ""},
        "output": {
            "mode": "custom",  # custom | library
            "roots": {"movie": "", "tv": "", "music": ""},
        },
    }

    def __init__(self, path: Optional[Path] = None) -> None:
        path = path or Path(__file__).resolve().parent / "config.json"
        super().__init__(path, default_data=self.DEFAULT)

    # ── 点路径访问 ────────────────────────────────────────────────────────

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """按点路径取值，如 ``get("llm.api_key")``。"""
        node: Any = self.data
        for part in dotted_key.split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(part)
            if node is None:
                return default
        return node

    def set(self, dotted_key: str, value: Any) -> None:
        """按点路径设值并落盘，如 ``set("tmdb.api_key", "xxx")``。"""
        parts = dotted_key.split(".")
        with self._lock:
            node: Any = self.data
            for part in parts[:-1]:
                nxt = node.get(part) if isinstance(node, dict) else None
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[part] = nxt
                node = nxt
            node[parts[-1]] = value
            self.save()
