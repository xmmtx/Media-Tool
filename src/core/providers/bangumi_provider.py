"""Bangumi（bgm.tv）提供者：番剧/剧集元数据搜索。

通过 ``BaseProvider`` 接口挂接，供手动匹配等场景作为 TMDB 之外的备选源。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import List

from .base import BaseProvider, MediaMatch

_BANGUMI_API = "https://api.bgm.tv"
_BANGUMI_UA = "Media-Tool/1.0 (https://github.com/media-tool)"


class BangumiProvider(BaseProvider):
    """Bangumi 番剧搜索提供者（无需 API Key）。"""

    name = "bangumi"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self.ua = str(self._cfg("bangumi.user_agent", "") or "") or _BANGUMI_UA

    def search(
        self,
        query: str,
        year: int | None = None,
        media_type: str = "tv",
        language: str = "zh-CN",
    ) -> List[MediaMatch]:
        if not query:
            return []
        params = {"type": 2, "responseGroup": "small"}  # type=2 动画
        url = f"{_BANGUMI_API}/search/subject/{urllib.parse.quote(query)}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": self.ua, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return []
        return self._parse(data.get("list", []))

    @staticmethod
    def _parse(items: list) -> List[MediaMatch]:
        """把 Bangumi 搜索响应条目转换为 MediaMatch（纯函数，可单测）。"""
        matches: List[MediaMatch] = []
        for it in items or []:
            name = it.get("name") or ""
            name_cn = it.get("name_cn") or name
            date = it.get("date") or ""
            year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
            matches.append(
                MediaMatch(
                    provider="bangumi",
                    media_type="tv",
                    tmdb_id=int(it.get("id") or 0),
                    title_orig=name,
                    title_user=name_cn,
                    year=year,
                    overview=it.get("summary") or "",
                )
            )
        return matches
