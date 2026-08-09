"""提供者抽象基类与统一数据模型。

设计目标：TMDB 为默认主提供者，Bangumi / MusicBrainz 等可通过实现
:class:`BaseProvider` 无缝挂接（Module 4 手动干预也基于同一接口）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MediaMatch:
    """一条媒体搜索结果（电影或剧集）。"""

    provider: str
    media_type: str          # "movie" | "tv"
    tmdb_id: int
    title_orig: str          # 原名（对应 {title_orig}）
    title_user: str          # 本地化标题（对应 {title_user}，随语言参数变化）
    year: Optional[int] = None
    overview: str = ""
    poster_url: str = ""
    # 剧集专用
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_title_orig: Optional[str] = None
    episode_title_user: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """转换为表达式引擎可直接消费的字典（空值剔除）。"""
        d: dict = {
            "title_orig": self.title_orig,
            "title_user": self.title_user,
        }
        if self.year is not None:
            d["year"] = self.year
        if self.season is not None:
            d["season"] = self.season
        if self.episode is not None:
            d["episode"] = self.episode
        return d


class BaseProvider(ABC):
    """媒体元数据提供者接口。

    子类实现 :meth:`search`；可选的 :meth:`available` 表示该提供者
    是否已具备调用条件（如已配置 API Key）。
    """

    name: str = "base"

    def __init__(self, config=None) -> None:
        self.config = config

    @property
    def available(self) -> bool:
        """提供者是否可用（默认可用，子类可覆写）。"""
        return True

    def _cfg(self, dotted_key: str, default=None):
        """从配置读取点路径值；config 为 None 时返回默认值。"""
        if self.config is None:
            return default
        getter = getattr(self.config, "get", None)
        if getter is None:
            return self.config.get(dotted_key, default) if isinstance(self.config, dict) else default
        return getter(dotted_key, default)

    @abstractmethod
    def search(
        self,
        query: str,
        year: Optional[int] = None,
        media_type: str = "movie",
        language: str = "zh-CN",
    ) -> List[MediaMatch]:
        """按标题（可选年份/类型/语言）搜索，返回候选列表。"""
        raise NotImplementedError
