"""MusicBrainz 提供者：录音（recording）元数据搜索。

通过 ``BaseProvider`` 接口挂接，供音乐场景作为补充检索源。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import List

from .base import BaseProvider, MediaMatch

_MB_API = "https://musicbrainz.org/ws/2"
_MB_UA = "Media-Tool/1.0 (https://github.com/media-tool)"


class MusicBrainzProvider(BaseProvider):
    """MusicBrainz 录音搜索提供者（无需 API Key，遵守其限速要求）。"""

    name = "musicbrainz"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self.ua = str(self._cfg("musicbrainz.user_agent", "") or "") or _MB_UA

    def search(
        self,
        query: str,
        year: int | None = None,
        media_type: str = "music",
        language: str = "en",
    ) -> List[MediaMatch]:
        if not query:
            return []
        params = {"query": f'recording:"{query}"', "fmt": "json", "limit": 10}
        url = f"{_MB_API}/recording/?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": self.ua, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return []
        return self._parse(data.get("recordings", []))

    @staticmethod
    def _parse(items: list) -> List[MediaMatch]:
        """把 MusicBrainz 录音响应条目转换为 MediaMatch（纯函数，可单测）。"""
        matches: List[MediaMatch] = []
        for it in items or []:
            title = it.get("title") or ""
            artists: List[str] = []
            for ac in it.get("artist-credit") or []:
                if isinstance(ac, dict) and ac.get("name"):
                    artists.append(str(ac["name"]))
            date = it.get("first-release-date") or ""
            year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
            matches.append(
                MediaMatch(
                    provider="musicbrainz",
                    media_type="music",
                    tmdb_id=0,
                    title_orig=title,
                    title_user=title,
                    year=year,
                    extra={"artist": "、".join(artists)},
                )
            )
        return matches
