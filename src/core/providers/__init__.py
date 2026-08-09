"""src.core.providers — TMDB / LLM 等元数据与识别提供者。"""

from .bangumi_provider import BangumiProvider
from .base import BaseProvider, MediaMatch
from .llm_provider import LlmSubgroupProvider
from .musicbrainz_provider import MusicBrainzProvider
from .tmdb_provider import TMDBProvider

__all__ = [
    "BaseProvider",
    "MediaMatch",
    "TMDBProvider",
    "LlmSubgroupProvider",
    "BangumiProvider",
    "MusicBrainzProvider",
    "PROVIDERS",
    "get_provider",
]

# 可无缝挂接的提供者注册表（Module 4 手动匹配可切换源）
PROVIDERS = {
    "tmdb": TMDBProvider,
    "bangumi": BangumiProvider,
    "musicbrainz": MusicBrainzProvider,
}


def get_provider(name: str, config=None) -> BaseProvider | None:
    """按名称实例化提供者；未知名称返回 None。"""
    cls = PROVIDERS.get(name)
    return cls(config) if cls else None
