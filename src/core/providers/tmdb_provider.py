"""TMDB 提供者（TMDB API v3，标准库 urllib 实现，零第三方依赖）。

- API Key 从配置 ``tmdb.api_key`` 读取（对应 ``src/db/config.json``）。
- ``search()`` 支持电影/剧集，返回带原名与本地化标题的候选列表。
- 解析逻辑独立为 ``_parse_matches``，便于离线单元测试。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import List, Optional

from .base import BaseProvider, MediaMatch

TMDB_API = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"


class TMDBProvider(BaseProvider):
    name = "tmdb"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self.api_key = str(self._cfg("tmdb.api_key", "") or "")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    # ── 网络请求 ──────────────────────────────────────────────────────────

    def _request(self, path: str, params: dict) -> dict:
        params = dict(params)
        params["api_key"] = self.api_key
        url = f"{TMDB_API}{path}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ── 搜索 ──────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        year: Optional[int] = None,
        media_type: str = "movie",
        language: str = "zh-CN",
    ) -> List[MediaMatch]:
        if not self.available or not query:
            return []
        path = "/search/movie" if media_type == "movie" else "/search/tv"
        params: dict = {"query": query, "language": language}
        if year:
            params["year" if media_type == "movie" else "first_air_date_year"] = year
        try:
            data = self._request(path, params)
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return []
        return self._parse_matches(media_type, data.get("results", []))

    @staticmethod
    def _parse_matches(media_type: str, items: list) -> List[MediaMatch]:
        """把 TMDB 搜索响应条目转换为 MediaMatch（纯函数，可单测）。"""
        is_movie = media_type == "movie"
        matches: List[MediaMatch] = []
        for it in items or []:
            title_orig = it.get("original_title" if is_movie else "original_name") or ""
            title_user = it.get("title" if is_movie else "name") or ""
            if not title_user:
                title_user = title_orig
            date = it.get("release_date" if is_movie else "first_air_date") or ""
            year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
            poster = f"{IMG_BASE}{it['poster_path']}" if it.get("poster_path") else ""
            matches.append(
                MediaMatch(
                    provider="tmdb",
                    media_type=media_type,
                    tmdb_id=int(it.get("id") or 0),
                    title_orig=title_orig,
                    title_user=title_user,
                    year=year,
                    overview=it.get("overview") or "",
                    poster_url=poster,
                )
            )
        return matches

    # ── 剧集信息 ──────────────────────────────────────────────────────────

    def get_episode(
        self,
        tv_id: int,
        season: int,
        episode: int,
        language: str = "zh-CN",
    ) -> Optional[MediaMatch]:
        """获取某一集的本地化标题（用于 {title_user} 的剧集维度信息）。"""
        if not self.available or not tv_id:
            return None
        path = f"/tv/{tv_id}/season/{season}/episode/{episode}"
        try:
            data = self._request(path, {"language": language})
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return None
        if not data.get("name"):
            return None
        return MediaMatch(
            provider="tmdb",
            media_type="tv",
            tmdb_id=int(data.get("id") or tv_id),
            title_orig=data.get("name", ""),
            title_user=data.get("name", ""),
            season=season,
            episode=episode,
            episode_title_user=data.get("name", ""),
        )
